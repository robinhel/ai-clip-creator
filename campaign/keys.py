"""Inställningar: nycklar som Robin klistrar in i appen. De sparas i .env och används direkt, utan omstart.

Ett nyckelvärde skickas aldrig tillbaka, loggas aldrig och står aldrig i ett felmeddelande.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from clipsmith.api.deps import verify_api_key
from clipsmith.twitch.client import TwitchClient

from . import streams
from .api import get_settings
from .config import Settings

router = APIRouter(prefix="/campaign", tags=["campaign"])

GROUPS = [
    ("twitch", "Twitch", [("TWITCH_CLIENT_ID", "Client ID"), ("TWITCH_CLIENT_SECRET", "Client secret")]),
    ("anthropic", "Claude", [("ANTHROPIC_API_KEY", "API-nyckel")]),
    ("berget", "Berget (transkribering)", [("BERGET_API_KEY", "API-nyckel")]),
    ("tiktok", "TikTok", [("TIKTOK_CLIENT_KEY", "Client key"), ("TIKTOK_CLIENT_SECRET", "Client secret")]),
]
NAMES = {name: label for _, _, keys in GROUPS for name, label in keys}
TWITCH = ("TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET")
VALUE = re.compile(r"[!$-&(-~]{1,300}")  # synliga ASCII-tecken utom " ' och #; inga blanksteg


def _only_local(request: Request) -> None:
    """clipsmith serve lyssnar på 0.0.0.0. Ingen annan på nätverket får byta nycklarna."""
    if request.client is None or request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "Nycklarna kan bara ändras från datorn där servern körs.")


def _value(name: str, root: Path) -> str:
    """Värdet i processen, annars i .env. En tom variabel i processen räknas som saknad."""
    env = root / ".env"
    return os.environ.get(name, "").strip() or ((dotenv_values(env).get(name) or "").strip() if env.exists() else "")


def _groups(root: Path) -> list[dict]:
    return [{"id": gid, "label": label,
             "keys": [{"name": name, "label": key_label, "set": bool(_value(name, root))} for name, key_label in keys]}
            for gid, label, keys in GROUPS]


def _check(values: dict[str, Any]) -> dict[str, str]:
    clean = {}
    for name, value in values.items():
        if name not in NAMES:
            raise HTTPException(400, "Okänt namn på en nyckel. Bara nycklarna på sidan går att spara.")
        value = value.strip() if isinstance(value, str) else None
        if not value:
            raise HTTPException(400, f"{name} är tom. Klistra in nyckeln.")
        if not VALUE.fullmatch(value):
            raise HTTPException(400, f"{name} ser inte ut som en nyckel. Den får inte ha blanksteg, radbrytningar, "
                                     "citattecken eller #, och högst 300 tecken.")
        clean[name] = value
    return clean


def _write_env(path: Path, values: dict[str, str]) -> None:
    """Byt den första raden NAME= (även # NAME=) eller lägg till den sist. Övriga rader är orörda."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True) if path.exists() else []
    for name, value in values.items():
        pattern = re.compile(rf"\s*#?\s*{name}\s*=")
        i = next((i for i, line in enumerate(lines) if pattern.match(line)), None)
        if i is None:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(f"{name}={value}\n")
        else:
            lines[i] = f"{name}={value}" + ("\r\n" if lines[i].endswith("\r\n") else "\n")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".env.", suffix=".tmp")  # rättigheterna blir 600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write("".join(lines))
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def check_twitch(root: Path) -> dict:
    client_id, secret = (_value(name, root) for name in TWITCH)
    if not (client_id and secret):
        return {"ok": False, "message": "Fyll i både Client ID och Client secret."}
    try:
        with TwitchClient(client_id, secret) as client:
            client.get_user_id("twitch")
    except httpx.HTTPStatusError as error:  # Twitchs text kan innehålla nyckeln, så den skickas inte vidare
        if error.response.status_code in (400, 401, 403):
            return {"ok": False, "message": "Twitch godkände inte nycklarna. Kontrollera Client ID och secret."}
        return {"ok": False, "message": "Twitch svarar inte. Försök igen om en stund."}
    except Exception:  # nätverk m.m.
        return {"ok": False, "message": "Twitch svarar inte. Försök igen om en stund."}
    return {"ok": True, "message": "Nycklarna fungerar."}


@router.get("/keys", summary="Vilka nycklar som är sparade, utan värden")
def get_keys(settings: Settings = Depends(get_settings)) -> dict:
    return {"groups": _groups(settings.root)}


class KeysBody(BaseModel):
    values: dict[str, Any]  # Any: ett fel om typen ska inte citera värdet i ett 422-svar


@router.put("/keys", summary="Spara nycklar i .env", dependencies=[Depends(verify_api_key), Depends(_only_local)])
def put_keys(body: KeysBody, settings: Settings = Depends(get_settings)) -> dict:
    values = _check(body.values)
    _write_env(settings.root / ".env", values)
    # load_dotenv skriver inte över en variabel som redan finns i processen, så den sätts här.
    os.environ.update(values)
    twitch_changed = any(name in values for name in TWITCH)
    if twitch_changed:
        streams._cache.clear()  # tittarklippen räknas om med de nya nycklarna
    return {"groups": _groups(settings.root), "test": check_twitch(settings.root) if twitch_changed else None}


@router.post("/keys/test/twitch", summary="Prova Twitch-nycklarna",
             dependencies=[Depends(verify_api_key), Depends(_only_local)])
def post_test_twitch(settings: Settings = Depends(get_settings)) -> dict:
    return check_twitch(settings.root)
