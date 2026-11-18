"""Robins val i appen som inte är nycklar. Sparas i klippdata/preferences.json.

submit_helper: hjälpen för inskick till Content Rewards. På: ett inlägg skapas av sig självt när appen får
adressen efter en uppladdning, och notisen säger när de 30 minuterna går ut. Av: som före hjälpen.

country: Robins land, för kampanjbetyget (campaign/scout_score.py). Kampanjer som inte betalar för visningar
därifrån sorteras bort. ISO-kod med två versaler, bara koderna i COUNTRIES. Sverige som standard.

Varje sparfunktion slår ihop med det som redan finns i filen, så att ett val aldrig skriver över ett annat.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from .cliplog import ClipLog
from .config import Settings
from .profile_edit import write_atomic

log = logging.getLogger(__name__)


COUNTRIES = {"SE": "Sverige", "NO": "Norge", "DK": "Danmark", "FI": "Finland", "IS": "Island", "DE": "Tyskland",
             "FR": "Frankrike", "ES": "Spanien", "IT": "Italien", "NL": "Nederländerna", "PL": "Polen",
             "GB": "Storbritannien", "IE": "Irland", "US": "USA", "CA": "Kanada", "AU": "Australien",
             "BR": "Brasilien", "MX": "Mexiko", "IN": "Indien"}
DEFAULT_COUNTRY = "SE"


def _path(settings: Settings) -> Path:
    return settings.data_dir / "preferences.json"


def _read(settings: Settings) -> dict:
    """Allt som är sparat. Saknas filen, eller går den inte att läsa: inget."""
    try:
        saved = json.loads(_path(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def _save(settings: Settings, **values: object) -> None:
    write_atomic(_path(settings), json.dumps({**_read(settings), **values}).encode("utf-8"))


def submit_helper(settings: Settings) -> bool:
    """Saknas filen eller nyckeln, eller går filen inte att läsa: på."""
    value = _read(settings).get("submit_helper")
    return value if isinstance(value, bool) else True


def save_submit_helper(settings: Settings, on: bool) -> None:
    _save(settings, submit_helper=on)


def country(settings: Settings) -> str:
    """Robins land. Saknas det, eller är det inte i listan: Sverige."""
    code = _read(settings).get("country")
    return code if isinstance(code, str) and code in COUNTRIES else DEFAULT_COUNTRY


def save_country(settings: Settings, code: str) -> None:
    """ValueError för en kod utanför listan."""
    if code not in COUNTRIES:
        raise ValueError(f"Landet {code!r} finns inte i listan.")
    _save(settings, country=code)


def add_post(settings: Settings, clip_id: int, platform: str, url: str, created: str | None = None,
             account: str | None = None) -> None:
    """Hjälpen: inlägget skapas när appen får adressen, och created blir publiceringstiden. En gång per konto;
    ett fel här får aldrig göra en lyckad uppladdning till ett fel."""
    if not submit_helper(settings):
        return
    try:
        ClipLog(settings.data_dir / "klipp.db").add_post(clip_id, platform, url, created, account)
    except (sqlite3.IntegrityError, SystemExit):  # finns redan ett inlägg på kontot, eller klippet finns inte
        pass
    except sqlite3.Error:
        log.warning("Inlägget för klipp %s kunde inte sparas; uppladdningen lyckades ändå", clip_id, exc_info=True)
