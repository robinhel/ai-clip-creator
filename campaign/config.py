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
    berget_api_key: str = ""
    transcribe_model: str = "Systran/faster-whisper-large-v3"  # KB-Whisper översätter engelska till svenska
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_redirect_uri: str = "http://127.0.0.1:8000/campaign/tiktok/callback/"
    web_url: str = "http://localhost:3000"  # dit TikTok-inloggningen leder tillbaka

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
        berget_api_key=os.environ.get("BERGET_API_KEY", "").strip(),
        transcribe_model=os.environ.get("KLIPP_TRANSCRIBE_MODEL", "").strip() or "Systran/faster-whisper-large-v3",
        tiktok_client_key=os.environ.get("TIKTOK_CLIENT_KEY", "").strip(),
        tiktok_client_secret=os.environ.get("TIKTOK_CLIENT_SECRET", "").strip(),
        tiktok_redirect_uri=os.environ.get("TIKTOK_REDIRECT_URI", "").strip()
        or "http://127.0.0.1:8000/campaign/tiktok/callback/",
        web_url=os.environ.get("KLIPP_WEB_URL", "").strip().rstrip("/") or "http://localhost:3000",
    )
