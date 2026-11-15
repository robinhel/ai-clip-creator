"""Vägar för YouTube: koppla en eller flera kanaler, läget och uppladdning av godkända klipp som Shorts."""

from __future__ import annotations

import base64
import hashlib
import secrets
from time import monotonic
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from clipsmith.api.deps import verify_api_key

from . import editor
from .api import _get, _log, get_settings
from .config import Settings
from .schedule import ScheduleError, clip_file, clip_seconds
from .tiktok_api import _only_local
from .youtube import AUTH_URL, SCOPES, YouTube, YouTubeError

router = APIRouter(prefix="/campaign", tags=["campaign"])
STATE_S = 600  # inloggningen måste vara klar inom 10 minuter
_pending: dict[str, tuple[float, str]] = {}  # state -> (när, code_verifier). Bara i minnet.


@router.get("/youtube/status", summary="Kopplade YouTube-kanaler och dagens uppladdningar")
def youtube_status(settings: Settings = Depends(get_settings)) -> dict:
    return YouTube(settings).status()


@router.get("/youtube/connect", summary="Logga in på Google (öppnas direkt mot :8000)",
            dependencies=[Depends(_only_local)])
def youtube_connect(settings: Settings = Depends(get_settings)) -> RedirectResponse:
    if not YouTube(settings).configured:
        raise HTTPException(400, "YouTube är inte inställt. Lägg in YOUTUBE_CLIENT_ID och YOUTUBE_CLIENT_SECRET "
                                 "under Inställningar.")
    now = monotonic()
    for state in [s for s, (at, _) in _pending.items() if now - at > STATE_S]:
        del _pending[state]
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)  # 86 tecken av A-Z a-z 0-9 - _
    _pending[state] = (now, verifier)
    query = urlencode({
        "client_id": settings.youtube_client_id,
        "redirect_uri": settings.youtube_redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",  # refresh_token
        "prompt": "consent select_account",  # välj kanal, och refresh_token även andra gången
        "include_granted_scopes": "true",
        "state": state,
        # Google följer RFC 7636: base64url utan = (TikTok vill ha hex).
        "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode(),
        "code_challenge_method": "S256",
    })
    return RedirectResponse(f"{AUTH_URL}?{query}", status_code=302)


@router.get("/youtube/callback/", summary="Google leder hit efter inloggningen",
            dependencies=[Depends(_only_local)])
def youtube_callback(state: str = "", code: str = "", error: str = "",
                     settings: Settings = Depends(get_settings)) -> RedirectResponse:
    def back(reason: str | None = None) -> RedirectResponse:
        query = {"youtube": "ok"} if reason is None else {"youtube": "fel", "orsak": reason[:200]}
        return RedirectResponse(f"{settings.web_url}/settings?{urlencode(query)}", status_code=302)

    found = _pending.pop(state, None)  # state gäller en gång
    if found is None or monotonic() - found[0] > STATE_S:
        return back("Inloggningen gick ut eller kom inte från appen. Försök igen.")
    if error or not code:
        return back(f"Google: {error or 'ingen kod'}")
    try:
        YouTube(settings).connect(code, found[1])
    except YouTubeError as failure:
        return back(str(failure))
    except Exception:  # nätverksfel m.m.; token får inte hamna i texten
        return back("YouTube svarade inte. Försök igen.")
    return back()


@router.post("/youtube/accounts/{channel_id}/disconnect", summary="Koppla bort en YouTube-kanal",
             dependencies=[Depends(verify_api_key)])
def youtube_disconnect(channel_id: str, settings: Settings = Depends(get_settings)) -> dict:
    try:
        YouTube(settings).disconnect(channel_id)
    except YouTubeError as error:
        raise HTTPException(error.status, str(error)) from error
    return YouTube(settings).status()


class YouTubeBody(BaseModel):
    channel_id: str | None = None  # vilken kanal; utan val används den enda kopplade
    title: str | None = None
    description: str | None = None
    # Inget förval. Kontrolleras i YouTube.upload, så att ett fel blir 400 med svensk text.
    privacy: str | None = None
    made_for_kids: bool | None = None
    notify_subscribers: bool = True


@router.post("/clips/{clip_id}/youtube", status_code=202, summary="Ladda upp klippet som en YouTube Short",
             dependencies=[Depends(verify_api_key)])
def youtube_upload_clip(clip_id: int, body: YouTubeBody, settings: Settings = Depends(get_settings)) -> dict:
    if editor.lock.locked():  # filen kan bytas mitt i uppladdningen
        raise HTTPException(409, "Ett klipp redigeras just nu. Vänta tills redigeringen är klar.")
    clip = _get(_log(settings), clip_id)
    try:
        path = clip_file(settings, clip, "Bara godkända klipp kan laddas upp till YouTube.")
    except ScheduleError as error:
        raise HTTPException(error.status, str(error)) from error
    duration_s = clip_seconds(clip, path)
    try:
        return YouTube(settings).upload(clip_id, path, body.channel_id, body.model_dump(exclude={"channel_id"}),
                                        duration_s)
    except YouTubeError as error:
        raise HTTPException(error.status, str(error)) from error


@router.get("/clips/{clip_id}/youtube", summary="Senaste uppladdningen till YouTube för klippet")
def youtube_clip_upload(clip_id: int, settings: Settings = Depends(get_settings)) -> dict | None:
    _get(_log(settings), clip_id)
    return YouTube(settings).latest(clip_id)
