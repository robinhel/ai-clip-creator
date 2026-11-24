"""Inställningar: nycklar som Robin klistrar in i appen. De sparas i .env och används direkt, utan omstart.

Ett nyckelvärde skickas aldrig tillbaka, loggas aldrig och står aldrig i ett felmeddelande.
"""

from __future__ import annotations

import ipaddress
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values
from fastapi import APIRouter, Body, Depends, HTTPException, Request

from clipsmith.api.deps import verify_api_key
from clipsmith.twitch.client import TwitchClient

from . import estimate, streams
from .api import SSD_MISSING, _describe_profile, _profile_names, get_settings, storage
from .budget import BudgetExceeded, budget_from_settings
from .config import Settings

router = APIRouter(prefix="/campaign", tags=["campaign"])

GROUPS = [
    ("twitch", "Twitch", [("TWITCH_CLIENT_ID", "Client ID"), ("TWITCH_CLIENT_SECRET", "Client secret")]),
    ("anthropic", "Claude", [("ANTHROPIC_API_KEY", "API-nyckel")]),
    ("berget", "Berget (transkribering)", [("BERGET_API_KEY", "API-nyckel")]),
    ("tiktok", "TikTok", [("TIKTOK_CLIENT_KEY", "Client key"), ("TIKTOK_CLIENT_SECRET", "Client secret")]),
    ("youtube", "YouTube", [("YOUTUBE_CLIENT_ID", "Client ID"), ("YOUTUBE_CLIENT_SECRET", "Client secret")]),
]
NAMES = {name: label for _, _, keys in GROUPS for name, label in keys}
TWITCH = ("TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET")
# Synliga ASCII-tecken utom " ' # och $ (python-dotenv byter ${X} mot en annan variabel); inga blanksteg.
VALUE = re.compile(r"[!%&(-~]{1,300}")
_env_lock = threading.Lock()  # två Spara samtidigt får inte skriva över varandras rader i .env


LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}


def is_local(request: Request) -> bool:
    """True only for a request from the computer where the server runs, even if it listens on 0.0.0.0.
    Host-namnet kontrolleras också: en webbsida vars namn pekar på 127.0.0.1 (DNS rebinding) är ingen lokal klient."""
    host = request.headers.get("host", "").lower()
    if (host.rsplit(":", 1)[0] if not host.endswith("]") else host) not in LOCAL_HOSTS:
        return False
    try:
        address = ipaddress.ip_address(request.client.host if request.client else "")
    except ValueError:  # inte en IP-adress, till exempel testclient
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:  # ::ffff:127.0.0.1
        address = address.ipv4_mapped
    return address.is_loopback


def _only_local(request: Request) -> None:
    """Bara datorn där servern körs får byta nycklarna."""
    if not is_local(request):
        raise HTTPException(403, "Keys can only be changed from the computer where the server runs.")


def _value(name: str, root: Path) -> str:
    """Värdet i processen, annars i .env. En tom variabel i processen räknas som saknad."""
    env = root / ".env"
    if os.environ.get(name, "").strip():
        return os.environ[name].strip()
    with _env_lock:
        return (dotenv_values(env).get(name) or "").strip() if env.exists() else ""


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
                                     "citattecken, # eller $, och högst 300 tecken.")
        clean[name] = value
    return clean


def _write_env(path: Path, values: dict[str, str]) -> None:
    """Byt den första raden NAME=, annars den första # NAME=, annars läggs den till sist. Övriga rader är orörda.
    Anropas under _env_lock."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True) if path.exists() else []
    for name, value in values.items():
        real, comment = re.compile(rf"\s*{name}\s*="), re.compile(rf"\s*#\s*{name}\s*=")
        i = next((i for i, line in enumerate(lines) if real.match(line)),
                 next((i for i, line in enumerate(lines) if comment.match(line)), None))
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


@router.put("/keys", summary="Spara nycklar i .env", dependencies=[Depends(verify_api_key), Depends(_only_local)])
def put_keys(body: Any = Body(None), settings: Settings = Depends(get_settings)) -> dict:
    # Any och egen kontroll: FastAPIs 422-svar citerar fel indata, och den kan vara en nyckel.
    if not (isinstance(body, dict) and isinstance(body.get("values"), dict)):
        raise HTTPException(400, 'Skicka nycklarna som {"values": {"NAMN": "värde"}}.')
    values = _check(body["values"])
    with _env_lock:
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


# ---------- kontroll: samma som python -m campaign check-setup, plus SSD:n ----------

FILTERS = ("drawtext", "subtitles")  # finish.py ritar texten med drawtext; clipsmith bränner in undertexter
HOURS_OK = 4  # en vanlig stream: ledigt utrymme som räcker för den räknas som ok


def _ffmpeg_filters(ffmpeg: str) -> str:
    try:
        return subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, check=False,
                              timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _row(check_id: str, label: str, ok: bool, detail: str, href: str | None = None) -> dict:
    row = {"id": check_id, "label": label, "ok": ok, "detail": detail}
    return {**row, "href": href} if href else row


def _tools() -> list[dict]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        rows = [_row("ffmpeg", "ffmpeg", False, "ffmpeg hittades inte. Installera det: brew install ffmpeg-full.")]
    else:
        names = {line.split()[1] for line in _ffmpeg_filters(ffmpeg).splitlines() if len(line.split()) > 1}
        missing = [name for name in FILTERS if name not in names]
        rows = [_row("ffmpeg", "ffmpeg", not missing,
                     f"ffmpeg saknar {' och '.join(missing)}. Installera ffmpeg-full: brew install ffmpeg-full."
                     if missing else "Hittad, med drawtext och subtitles.")]
    found = shutil.which("ffprobe") is not None
    return [*rows, _row("ffprobe", "ffprobe", found,
                        "Hittad." if found else "ffprobe hittades inte. Installera det: brew install ffmpeg-full.")]


def _campaigns(settings: Settings) -> list[dict]:
    """Samma kod och samma läge som Kampanjer (_describe_profile). Nycklarna har egna rader här."""
    rows = []
    for name in _profile_names(settings):
        href = f"/campaigns/{name}"
        try:
            profile = _describe_profile(name, settings)
        except (SystemExit, Exception):  # felets text kan citera filen, så den visas inte
            rows.append(_row(f"profile:{name}", f"Kampanjen {name}", False, "Kampanjens fil går inte att läsa.", href))
            continue
        if profile["active"]:
            problems = [p for p in profile["problems"] if p not in profile["key_problems"]]
            rows.append(_row(f"profile:{name}", f"Kampanjen {profile['streamer']}", profile["state"] == "Redo",
                             " ".join([f"{profile['state']}.", *problems]), href))
    return rows


def _budget(settings: Settings) -> dict:
    budget = budget_from_settings(settings)
    try:
        budget.check()
    except BudgetExceeded as error:
        return _row("budget", "Budget", False, str(error))
    s = budget.summary()

    def usd(value: float) -> str:
        return f"{value:.2f}".replace(".", ",") + " US$"

    return _row("budget", "Budget", True, f"{usd(s['month_usd'])} av {usd(s['month_cap_usd'])} den här månaden, "
                                          f"{usd(s['today_usd'])} av {usd(s['day_cap_usd'])} i dag.")


def _space(disk: dict) -> list[dict]:
    if not disk["connected"]:
        return [_row("ssd", "SSD:n", False, SSD_MISSING),
                _row("disk", "Ledigt utrymme", False, f"Okänt. {SSD_MISSING}")]
    hours = max(disk["free_gb"] - 1, 0) / 1.1 / estimate.download_gb(3600)  # samma marginal som körningen
    need = estimate.download_gb(HOURS_OK * 3600) * 1.1 + 1

    def gb(value: float) -> str:
        return f"{value:g}".replace(".", ",")  # svenskt decimalkomma

    detail = (f"{gb(disk['free_gb'])} GB ledigt av {gb(disk['total_gb'])} GB. Det räcker för ungefär "
              f"{int(hours)} timmar VOD.")
    if hours < HOURS_OK:
        detail += f" En stream på {HOURS_OK} timmar behöver ungefär {need:.0f} GB. Ta bort gamla VOD:er i work/."
    return [_row("ssd", "SSD:n", True, "work/ och out/ går att nå."),
            _row("disk", "Ledigt utrymme", hours >= HOURS_OK, detail)]


@router.get("/setup", summary="Kontroll: ffmpeg, nycklar, kampanjer, budget, SSD och ledigt utrymme")
def get_setup(settings: Settings = Depends(get_settings)) -> dict:
    keys = [_row(check_id, label, bool(_value(name, settings.root)),
                 "Sparad." if _value(name, settings.root) else f"Saknas. Klistra in den i rutan {box} längre ner.",
                 f"#{check_id}")
            for check_id, name, label, box in (("anthropic", "ANTHROPIC_API_KEY", "Claude-nyckeln", "Claude"),
                                               ("berget", "BERGET_API_KEY", "Berget-nyckeln (transkribering)",
                                                "Berget"))]
    return {"checks": [*_tools(), *keys, *_campaigns(settings), _budget(settings), *_space(storage(settings))]}
