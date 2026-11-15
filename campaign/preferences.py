"""Robins val i appen som inte är nycklar. Sparas i klippdata/preferences.json.

submit_helper: hjälpen för inskick till Content Rewards. På: ett inlägg skapas av sig självt när appen får
adressen efter en uppladdning, och notisen säger när de 30 minuterna går ut. Av: som före hjälpen.
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


def _path(settings: Settings) -> Path:
    return settings.data_dir / "preferences.json"


def submit_helper(settings: Settings) -> bool:
    """Saknas filen eller nyckeln, eller går filen inte att läsa: på."""
    try:
        value = json.loads(_path(settings).read_text(encoding="utf-8")).get("submit_helper")
    except (OSError, ValueError, AttributeError):
        return True
    return value if isinstance(value, bool) else True


def save_submit_helper(settings: Settings, on: bool) -> None:
    write_atomic(_path(settings), json.dumps({"submit_helper": on}).encode("utf-8"))


def add_post(settings: Settings, clip_id: int, platform: str, url: str, created: str | None = None) -> None:
    """Hjälpen: inlägget skapas när appen får adressen, och created blir publiceringstiden. En gång per plattform;
    ett fel här får aldrig göra en lyckad uppladdning till ett fel."""
    if not submit_helper(settings):
        return
    try:
        ClipLog(settings.data_dir / "klipp.db").add_post(clip_id, platform, url, created)
    except (sqlite3.IntegrityError, SystemExit):  # finns redan ett inlägg på plattformen, eller klippet finns inte
        pass
    except sqlite3.Error:
        log.warning("Inlägget för klipp %s kunde inte sparas; uppladdningen lyckades ändå", clip_id, exc_info=True)
