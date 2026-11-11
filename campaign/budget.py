"""Budgetspärr: loggar vad varje AI-anrop kostar och stoppar vid taket.

Priserna ligger i priser.yaml så att du kan ändra dem utan att röra koden.
Spärren här är ett extra skydd. Sätt också en utgiftsgräns i Claude Console.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import yaml

LONG_PROMPT_TOKENS = 100_000  # Haiku 5.5 kostar mer över den här gränsen
FALLBACK_PRICE = {"input": 8.0, "output": 40.0}  # okänd modell räknas dyrt, inte billigt


class BudgetExceeded(RuntimeError):
    """Månads- eller dagstaket är nått. Inga fler AI-anrop görs."""


class Budget:
    def __init__(
        self,
        db_path: Path,
        prices_path: Path,
        month_cap_usd: float,
        day_cap_usd: float,
        now=None,
    ):
        self.db_path = Path(db_path)
        self.month_cap = month_cap_usd
        self.day_cap = day_cap_usd
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.prices = self._load_prices(Path(prices_path))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS usage (
                    ts TEXT, model TEXT, purpose TEXT,
                    input_tokens INTEGER, output_tokens INTEGER,
                    cache_read INTEGER, cache_write INTEGER, usd REAL)"""
            )

    @staticmethod
    def _load_prices(path: Path) -> dict:
        if not path.exists():
            return {"models": {}, "default": FALLBACK_PRICE}
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data.setdefault("models", {})
        data.setdefault("default", FALLBACK_PRICE)
        return data

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read: int = 0,
        cache_write: int = 0,
    ) -> float:
        """Kostnad i dollar för ett anrop."""
        price = self.prices["models"].get(model, self.prices["default"])
        prompt_tokens = input_tokens + cache_read + cache_write
        if prompt_tokens > LONG_PROMPT_TOKENS and "over_100k" in price:
            price = {**price, **price["over_100k"]}
        per_token = 1_000_000
        return (
            input_tokens * price["input"]
            + output_tokens * price["output"]
            + cache_read * price.get("cache_read", price["input"])
            + cache_write * price.get("cache_write", price["input"])
        ) / per_token

    def record(
        self,
        model: str,
        purpose: str,
        input_tokens: int,
        output_tokens: int,
        cache_read: int = 0,
        cache_write: int = 0,
    ) -> float:
        """Spara ett anrop i loggen. Returnerar kostnaden i dollar."""
        usd = self.cost(model, input_tokens, output_tokens, cache_read, cache_write)
        with self._db() as db:
            db.execute(
                "INSERT INTO usage VALUES (?,?,?,?,?,?,?,?)",
                (
                    self._now().isoformat(),
                    model,
                    purpose,
                    input_tokens,
                    output_tokens,
                    cache_read,
                    cache_write,
                    usd,
                ),
            )
        return usd

    def _spent(self, prefix: str) -> float:
        with self._db() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(usd), 0) FROM usage WHERE ts LIKE ?", (prefix + "%",)
            ).fetchone()
        return float(row[0])

    def spent_month(self) -> float:
        return self._spent(self._now().strftime("%Y-%m"))

    def spent_today(self) -> float:
        return self._spent(self._now().strftime("%Y-%m-%d"))

    def check(self) -> None:
        """Kasta BudgetExceeded om något tak är nått."""
        month, day = self.spent_month(), self.spent_today()
        if month >= self.month_cap:
            raise BudgetExceeded(
                f"Månadstaket är nått: {month:.2f} av {self.month_cap:.2f} dollar."
            )
        if day >= self.day_cap:
            raise BudgetExceeded(f"Dagstaket är nått: {day:.2f} av {self.day_cap:.2f} dollar.")

    def summary(self) -> dict:
        with self._db() as db:
            rows = db.execute(
                "SELECT purpose, COUNT(*), SUM(usd) FROM usage WHERE ts LIKE ? GROUP BY purpose",
                (self._now().strftime("%Y-%m") + "%",),
            ).fetchall()
        return {
            "month_usd": self.spent_month(),
            "month_cap_usd": self.month_cap,
            "today_usd": self.spent_today(),
            "day_cap_usd": self.day_cap,
            "by_purpose": {p: {"calls": n, "usd": float(u)} for p, n, u in rows},
        }


def budget_from_settings(settings) -> Budget:
    """Bygg spärren från Settings (campaign.config.load_settings)."""
    return Budget(
        settings.data_dir / "budget.db",
        settings.prices_path,
        settings.budget_month_usd,
        settings.budget_day_usd,
    )
