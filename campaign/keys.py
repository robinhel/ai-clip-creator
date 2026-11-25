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

from . import estimate, local_whisper, streams
from .api import SSD_MISSING, _describe_profile, _profile_names, get_settings, storage
from .budget import BudgetExceeded, budget_from_settings
from .config import BERGET_MISSING, ENGINES, LOCAL_MODELS, Settings, load_settings

router = APIRouter(prefix="/campaign", tags=["campaign"])

GROUPS = [
    ("twitch", "Twitch", [("TWITCH_CLIENT_ID", "Client ID"), ("TWITCH_CLIENT_SECRET", "Client secret")]),
    ("anthropic", "Claude", [("ANTHROPIC_API_KEY", "API key")]),
    ("berget", "Berget (transcription)", [("BERGET_API_KEY", "API key")]),
    ("tiktok", "TikTok", [("TIKTOK_CLIENT_KEY", "Client key"), ("TIKTOK_CLIENT_SECRET", "Client secret")]),
    ("youtube", "YouTube", [("YOUTUBE_CLIENT_ID", "Client ID"), ("YOUTUBE_CLIENT_SECRET", "Client secret")]),
]
NAMES = {name: label for _, _, keys in GROUPS for name, label in keys}
TWITCH = ("TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET")
# Synliga ASCII-tecken utom " ' # och $ (python-dotenv byter ${X} mot en annan variabel); inga blanksteg.
VALUE = re.compile(r"[!%&(-~]{1,300}")
_env_lock = threading.Lock()  # två Spara samtidigt får inte skriva över varandras rader i .env


LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}


def host_name(host: str) -> str:
    """Värdnamnet i en Host-header, utan port och med små bokstäver: "[::1]:8000" -> "[::1]"."""
    host = host.strip().lower()
    return host if host.endswith("]") else host.rsplit(":", 1)[0]


def is_local(request: Request) -> bool:
    """True only for a request from the computer where the server runs, even if it listens on 0.0.0.0.
    Host-namnet kontrolleras också: en webbsida vars namn pekar på 127.0.0.1 (DNS rebinding) är ingen lokal klient."""
    if host_name(request.headers.get("host", "")) not in LOCAL_HOSTS:
        return False
    try:
        address = ipaddress.ip_address(request.client.host if request.client else "")
    except ValueError:  # inte en IP-adress, till exempel testclient
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:  # ::ffff:127.0.0.1
        address = address.ipv4_mapped
    return address.is_loopback


def _proxy_net() -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    """TRUSTED_PROXY_NET: the Compose network's subnet (docker-compose.yml sets 172.29.0.0/24).
    None when it is missing, unreadable or not a private network: then no proxy is trusted."""
    try:
        net = ipaddress.ip_network(os.getenv("TRUSTED_PROXY_NET", "").strip())
    except ValueError:
        return None
    return net if net.is_private else None


def is_trusted_proxy(request: Request) -> bool:
    """True for the dashboard's proxy in Docker: Host is a name in TRUSTED_PROXY_HOSTS (Compose sets ``api``)
    and the caller's address is in TRUSTED_PROXY_NET (the Compose network). The proxy has already checked
    Host and Origin. Lokala namn räknas aldrig här: de kräver loopback, annars kunde vem som helst på nätet
    skriva Host: localhost."""
    net = _proxy_net()
    names = {host_name(h) for h in os.getenv("TRUSTED_PROXY_HOSTS", "").split(",") if h.strip()} - LOCAL_HOSTS
    if net is None or host_name(request.headers.get("host", "")) not in names:
        return False
    try:
        return ipaddress.ip_address(request.client.host if request.client else "") in net
    except ValueError:
        return False


def only_local(request: Request) -> None:
    """Nycklar och kontoinloggning: bara datorn där servern körs, med loopback-IP och lokal Host,
    eller webbens proxy i Docker (TRUSTED_PROXY_HOSTS och TRUSTED_PROXY_NET)."""
    if not (is_local(request) or is_trusted_proxy(request)):
        raise HTTPException(403, "This can only be done from the computer where the server runs.")


def env_path(root: Path) -> Path:
    """Filen där nycklarna sparas: KLIPP_ENV_FILE (Docker: data/.env, som är monterad), annars .env i projektet."""
    return Path(os.environ.get("KLIPP_ENV_FILE", "").strip() or root / ".env")


def _value(name: str, root: Path) -> str:
    """Värdet i processen, annars i .env. En tom variabel i processen räknas som saknad."""
    env = env_path(root)
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
            raise HTTPException(400, "Unknown key name. Only the keys on the page can be saved.")
        value = value.strip() if isinstance(value, str) else None
        if not value:
            raise HTTPException(400, f"{name} is empty. Paste the key.")
        if not VALUE.fullmatch(value):
            raise HTTPException(400, f"{name} does not look like a key. It cannot contain spaces, line breaks, "
                                     "quotes, # or $, and can be at most 300 characters.")
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
        return {"ok": False, "message": "Fill in both Client ID and Client secret."}
    try:
        with TwitchClient(client_id, secret) as client:
            client.get_user_id("twitch")
    except httpx.HTTPStatusError as error:  # Twitchs text kan innehålla nyckeln, så den skickas inte vidare
        if error.response.status_code in (400, 401, 403):
            return {"ok": False, "message": "Twitch did not accept the keys. Check Client ID and secret."}
        return {"ok": False, "message": "Twitch is not answering. Try again in a while."}
    except Exception:  # nätverk m.m.
        return {"ok": False, "message": "Twitch is not answering. Try again in a while."}
    return {"ok": True, "message": "The keys work."}


@router.get("/keys", summary="Which keys are saved, without values")
def get_keys(settings: Settings = Depends(get_settings)) -> dict:
    return {"groups": _groups(settings.root)}


@router.put("/keys", summary="Save keys in .env", dependencies=[Depends(verify_api_key), Depends(only_local)])
def put_keys(body: Any = Body(None), settings: Settings = Depends(get_settings)) -> dict:
    # Any och egen kontroll: FastAPIs 422-svar citerar fel indata, och den kan vara en nyckel.
    if not (isinstance(body, dict) and isinstance(body.get("values"), dict)):
        raise HTTPException(400, 'Send the keys as {"values": {"NAME": "value"}}.')
    values = _check(body["values"])
    with _env_lock:
        _write_env(env_path(settings.root), values)
        # load_dotenv skriver inte över en variabel som redan finns i processen, så den sätts här.
        os.environ.update(values)
    twitch_changed = any(name in values for name in TWITCH)
    if twitch_changed:
        streams._cache.clear()  # tittarklippen räknas om med de nya nycklarna
    return {"groups": _groups(settings.root), "test": check_twitch(settings.root) if twitch_changed else None}


@router.post("/keys/test/twitch", summary="Test the Twitch keys",
             dependencies=[Depends(verify_api_key), Depends(only_local)])
def post_test_twitch(settings: Settings = Depends(get_settings)) -> dict:
    return check_twitch(settings.root)


# ---------- transkribering: Berget eller en lokal Whisper-modell ----------

def _transcription(settings: Settings) -> dict:
    """Valet i klartext (inget av det är hemligt), och för varje lokal modell storleken och om den redan ligger
    i cachen."""
    return {"engine": settings.engine, "model": settings.local_whisper_model,
            "berget_key": bool(settings.berget_api_key),
            "models": [{"id": name, "size_gb": size, "downloaded": local_whisper.is_downloaded(name)}
                       for name, size in LOCAL_MODELS.items()]}


@router.get("/transcription", summary="Who transcribes: Berget or a local Whisper model")
def get_transcription(settings: Settings = Depends(get_settings)) -> dict:
    return _transcription(settings)


@router.put("/transcription", summary="Choose Berget or a local Whisper model",
            dependencies=[Depends(verify_api_key), Depends(only_local)])
def put_transcription(body: Any = Body(None), settings: Settings = Depends(get_settings)) -> dict:
    if not isinstance(body, dict) or not ({"engine", "model"} & body.keys()) or body.keys() - {"engine", "model"}:
        raise HTTPException(400, 'Send {"engine": "berget" | "local", "model": "small" | "medium" | "large-v3"}.')
    values = {}
    if "engine" in body:
        if body["engine"] not in ENGINES:
            raise HTTPException(400, "Choose berget or local.")
        values["TRANSCRIBE_ENGINE"] = body["engine"]
    if "model" in body:
        if body["model"] not in LOCAL_MODELS:
            raise HTTPException(400, f"Choose one of the models: {', '.join(LOCAL_MODELS)}.")
        values["LOCAL_WHISPER_MODEL"] = body["model"]
    with _env_lock:
        _write_env(env_path(settings.root), values)
        os.environ.update(values)
    return _transcription(load_settings(settings.root))


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
        rows = [_row("ffmpeg", "ffmpeg", False, "ffmpeg was not found. Install it: brew install ffmpeg-full.")]
    else:
        names = {line.split()[1] for line in _ffmpeg_filters(ffmpeg).splitlines() if len(line.split()) > 1}
        missing = [name for name in FILTERS if name not in names]
        rows = [_row("ffmpeg", "ffmpeg", not missing,
                     f"ffmpeg lacks {' and '.join(missing)}. Install ffmpeg-full: brew install ffmpeg-full."
                     if missing else "Found, with drawtext and subtitles.")]
    found = shutil.which("ffprobe") is not None
    return [*rows, _row("ffprobe", "ffprobe", found,
                        "Found." if found else "ffprobe was not found. Install it: brew install ffmpeg-full.")]


def _campaigns(settings: Settings) -> list[dict]:
    """Samma kod och samma läge som Kampanjer (_describe_profile). Nycklarna har egna rader här."""
    rows = []
    for name in _profile_names(settings):
        href = f"/campaigns/{name}"
        try:
            profile = _describe_profile(name, settings)
        except (SystemExit, Exception):  # felets text kan citera filen, så den visas inte
            rows.append(_row(f"profile:{name}", f"Campaign {name}", False, "The campaign file cannot be read.", href))
            continue
        if profile["active"]:
            problems = [p for p in profile["problems"] if p not in profile["key_problems"]]
            rows.append(_row(f"profile:{name}", f"Campaign {profile['streamer']}", profile["state"] == "Ready",
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
        return f"US${value:.2f}"

    return _row("budget", "Budget", True, f"{usd(s['month_usd'])} of {usd(s['month_cap_usd'])} this month, "
                                          f"{usd(s['today_usd'])} of {usd(s['day_cap_usd'])} today.")


def _space(disk: dict) -> list[dict]:
    if not disk["connected"]:
        return [_row("ssd", "SSD", False, SSD_MISSING),
                _row("disk", "Free space", False, f"Unknown. {SSD_MISSING}")]
    hours = max(disk["free_gb"] - 1, 0) / 1.1 / estimate.download_gb(3600)  # samma marginal som körningen
    need = estimate.download_gb(HOURS_OK * 3600) * 1.1 + 1

    def gb(value: float) -> str:
        return f"{value:g}"

    detail = (f"{gb(disk['free_gb'])} GB free of {gb(disk['total_gb'])} GB. That is enough for about "
              f"{int(hours)} hours of VOD.")
    if hours < HOURS_OK:
        detail += f" A {HOURS_OK}-hour stream needs about {need:.0f} GB. Delete old VODs in work/."
    return [_row("ssd", "SSD", True, "work/ and out/ can be reached."),
            _row("disk", "Free space", hours >= HOURS_OK, detail)]


@router.get("/setup", summary="Check: ffmpeg, keys, campaigns, budget, SSD and free space")
def get_setup(settings: Settings = Depends(get_settings)) -> dict:
    claude = bool(_value("ANTHROPIC_API_KEY", settings.root))
    keys = [_row("anthropic", "The Claude key", claude,
                 "Saved." if claude else "Missing. Paste it in the Claude box further down.", "#anthropic"),
            _transcription_row(settings)]
    return {"checks": [*_tools(), *keys, *_campaigns(settings), _budget(settings), *_space(storage(settings))]}


def _transcription_row(settings: Settings) -> dict:
    """Berget behöver nyckeln. Den lokala modellen behöver ingen; den laddas ner vid första körningen."""
    if settings.engine == "local":
        model = settings.local_whisper_model
        ready = "downloaded" if local_whisper.is_downloaded(model) else (
            f"downloads about {LOCAL_MODELS[model]:g} GB the first time")
        return _row("berget", "Transcription", True, f"Local Whisper {model} on this computer, {ready}.",
                    "#transcription")
    if _value("BERGET_API_KEY", settings.root):
        return _row("berget", "Transcription", True, "Berget, the key is saved.", "#berget")
    return _row("berget", "Transcription", False, BERGET_MISSING, "#berget")
