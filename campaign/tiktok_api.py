"""Vägar för TikTok: koppla kontot, läget och uppladdning av godkända klipp som utkast."""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path
from time import monotonic
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from clipsmith.api.deps import verify_api_key

from . import editor
from .api import _get, _log, get_settings
from .config import Settings
from .tiktok import AUTH_URL, SCOPES, TikTok, TikTokError

router = APIRouter(prefix="/campaign", tags=["campaign"])
STATE_S = 600  # inloggningen måste vara klar inom 10 minuter
_pending: dict[str, tuple[float, str]] = {}  # state -> (när, code_verifier). Bara i minnet.


def _only_local(request: Request) -> None:
    """clipsmith serve lyssnar på 0.0.0.0. Ingen annan på nätverket får koppla in sitt eget konto."""
    if request.client is None or request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "TikTok kan bara kopplas från datorn där servern körs.")


@router.get("/tiktok/status", summary="Är TikTok kopplat, och hur många utkast väntar")
def tiktok_status(settings: Settings = Depends(get_settings)) -> dict:
    return TikTok(settings).status()


@router.get("/tiktok/connect", summary="Logga in på TikTok (öppnas direkt mot :8000)",
            dependencies=[Depends(_only_local)])
def tiktok_connect(settings: Settings = Depends(get_settings)) -> RedirectResponse:
    if not TikTok(settings).configured:
        raise HTTPException(400, "TikTok är inte inställt. Lägg in TIKTOK_CLIENT_KEY och "
                                 "TIKTOK_CLIENT_SECRET i .env och starta om servern.")
    now = monotonic()
    for state in [s for s, (at, _) in _pending.items() if now - at > STATE_S]:
        del _pending[state]
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)  # 86 tecken av A-Z a-z 0-9 - _
    _pending[state] = (now, verifier)
    query = urlencode({
        "client_key": settings.tiktok_client_key,
        "response_type": "code",
        "scope": SCOPES,
        "redirect_uri": settings.tiktok_redirect_uri,
        "state": state,
        # TikTok vill ha SHA256 som hex, inte base64url som i RFC 7636 (login-kit-desktop).
        "code_challenge": hashlib.sha256(verifier.encode()).hexdigest(),
        "code_challenge_method": "S256",
    })
    return RedirectResponse(f"{AUTH_URL}?{query}", status_code=302)


@router.get("/tiktok/callback/", summary="TikTok leder hit efter inloggningen",
            dependencies=[Depends(_only_local)])
def tiktok_callback(state: str = "", code: str = "", error: str = "", error_description: str = "",
                    settings: Settings = Depends(get_settings)) -> RedirectResponse:
    def back(reason: str | None = None) -> RedirectResponse:
        query = {"tiktok": "ok"} if reason is None else {"tiktok": "fel", "orsak": reason[:200]}
        return RedirectResponse(f"{settings.web_url}/campaigns?{urlencode(query)}", status_code=302)

    found = _pending.pop(state, None)  # state gäller en gång
    if found is None or monotonic() - found[0] > STATE_S:
        return back("Inloggningen gick ut eller kom inte från appen. Försök igen.")
    if error or not code:
        return back(f"TikTok: {error_description or error or 'ingen kod'}")
    try:
        TikTok(settings).connect(code, found[1])
    except TikTokError as failure:
        return back(str(failure))
    except Exception:  # nätverksfel m.m.; token får inte hamna i texten
        return back("TikTok svarade inte. Försök igen.")
    return back()


@router.post("/tiktok/disconnect", summary="Koppla bort TikTok", dependencies=[Depends(verify_api_key)])
def tiktok_disconnect(settings: Settings = Depends(get_settings)) -> dict:
    TikTok(settings).disconnect()
    return {"connected": False}


@router.post("/clips/{clip_id}/tiktok", status_code=202, summary="Ladda upp klippet som utkast i TikTok",
             dependencies=[Depends(verify_api_key)])
def upload_clip(clip_id: int, settings: Settings = Depends(get_settings)) -> dict:
    if editor.lock.locked():  # filen kan bytas mitt i uppladdningen
        raise HTTPException(409, "Ett klipp redigeras just nu. Vänta tills redigeringen är klar.")
    clip = _get(_log(settings), clip_id)
    if clip["status"] != "godkand":
        raise HTTPException(400, "Bara godkända klipp kan laddas upp till TikTok.")
    if clip["problems"]:
        raise HTTPException(400, "Klippet har problem. Rätta dem innan du laddar upp.")
    path = Path(clip["file"] or "").resolve()
    if not path.is_relative_to(settings.out_dir.resolve()) or path.suffix != ".mp4" or not path.exists():
        raise HTTPException(400, "Klippets fil finns inte. Gör klippet kampanjklart igen.")
    try:
        return TikTok(settings).upload(clip_id, path)
    except TikTokError as error:
        raise HTTPException(error.status, str(error)) from error


@router.get("/clips/{clip_id}/tiktok", summary="Senaste uppladdningen till TikTok för klippet")
def clip_upload(clip_id: int, settings: Settings = Depends(get_settings)) -> dict | None:
    _get(_log(settings), clip_id)
    return TikTok(settings).latest(clip_id)
