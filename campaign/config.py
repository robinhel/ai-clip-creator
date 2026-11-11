"""Inställningar från .env och miljövariabler."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv finns i requirements
    load_dotenv = None


@dataclass
class Settings:
    """Allt verktyget behöver veta om var filer ligger och vad som får kosta."""

    root: Path
    anthropic_api_key: str = ""
    review_model: str = "claude-haiku-5-5"
    budget_month_usd: float = 200.0
    budget_day_usd: float = 10.0
    encoder: str = "libx264"  # libx264 | h264_nvenc | h264_videotoolbox

    @property
    def work_dir(self) -> Path:
        return self.root / "work"

    @property
    def out_dir(self) -> Path:
        return self.root / "out"

    @property
    def data_dir(self) -> Path:
        return self.root / "klippdata"

    @property
    def prices_path(self) -> Path:
        return self.root / "priser.yaml"

    @property
    def profiles_dir(self) -> Path:
        return self.root / "profiles"


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        raise SystemExit(f"{name} i .env måste vara ett tal, inte {raw!r}")


def load_settings(root: str | Path | None = None) -> Settings:
    """Läs .env i projektmappen och bygg Settings."""
    root_path = Path(root or os.environ.get("KLIPP_ROOT") or Path.cwd()).resolve()
    if load_dotenv is not None:
        load_dotenv(root_path / ".env")
    return Settings(
        root=root_path,
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        review_model=os.environ.get("KLIPP_REVIEW_MODEL", "claude-haiku-5-5").strip(),
        budget_month_usd=_float("BUDGET_USD_PER_MONTH", 200.0),
        budget_day_usd=_float("BUDGET_USD_PER_DAY", 10.0),
        encoder=os.environ.get("KLIPP_ENCODER", "libx264").strip() or "libx264",
    )
