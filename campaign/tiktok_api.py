"""Vägar för TikTok: koppla ett eller flera konton, läget, och godkända klipp som utkast eller direkt postade."""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import secrets
from datetime import UTC, datetime
from time import monotonic
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from clipsmith.api.deps import verify_api_key

from . import editor
from .api import _get, _log, get_settings
from .config import Settings
from .schedule import Schedule, ScheduleError, clip_file, clip_seconds
from .tiktok import AUTH_URL, SCOPES, TikTok, TikTokError

router = APIRouter(prefix="/campaign", tags=["campaign"])
STATE_S = 600  # inloggningen måste vara klar inom 10 minuter
_pending: dict[str, tuple[float, str]] = {}  # state -> (när, code_verifier). Bara i minnet.


def _only_local(request: Request) -> None:
    """clipsmith serve lyssnar på 0.0.0.0. Ingen annan på nätverket får koppla in sitt eget konto."""
    try:
        address = ipaddress.ip_address(request.client.host if request.client else "")
    except ValueError:
        address = None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:  # ::ffff:127.0.0.1, samma dator
        address = address.ipv4_mapped
    if address is None or not address.is_loopback:
        raise HTTPException(403, "The account can only be disconnected from the computer where the server runs.")


@router.get("/tiktok/status", summary="Is TikTok connected, and how many drafts are waiting")
def tiktok_status(settings: Settings = Depends(get_settings)) -> dict:
    # Adressen som servern använder, så att webben visar samma som ska stå i utvecklarportalen.
    return {**TikTok(settings).status(), "redirect_uri": settings.tiktok_redirect_uri}


@router.get("/tiktok/connect", summary="Log in to TikTok (opens directly against :8000)",
            dependencies=[Depends(_only_local)])
def tiktok_connect(settings: Settings = Depends(get_settings)) -> RedirectResponse:
    if not TikTok(settings).configured:
        raise HTTPException(400, "TikTok is not set up. Add Client key and Client secret "
                                 "under Settings.")
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
        "disable_auto_auth": 1,  # TikTok frågar vilket konto, så att ett nytt konto kan läggas till
    })
    return RedirectResponse(f"{AUTH_URL}?{query}", status_code=302)


@router.get("/tiktok/callback/", summary="TikTok redirects here after login",
            dependencies=[Depends(_only_local)])
def tiktok_callback(state: str = "", code: str = "", error: str = "", error_description: str = "",
                    settings: Settings = Depends(get_settings)) -> RedirectResponse:
    def back(reason: str | None = None) -> RedirectResponse:
        query = {"tiktok": "ok"} if reason is None else {"tiktok": "fel", "orsak": reason[:200]}
        return RedirectResponse(f"{settings.web_url}/settings?{urlencode(query)}", status_code=302)

    found = _pending.pop(state, None)  # state gäller en gång
    if found is None or monotonic() - found[0] > STATE_S:
        return back("The login expired or did not come from the app. Try again.")
    if error or not code:
        return back(f"TikTok: {error_description or error or 'no code'}")
    try:
        TikTok(settings).connect(code, found[1])
    except TikTokError as failure:
        return back(str(failure))
    except Exception:  # nätverksfel m.m.; token får inte hamna i texten
        return back("TikTok did not answer. Try again.")
    return back()


@router.post("/tiktok/disconnect", summary="Disconnect all TikTok accounts", dependencies=[Depends(verify_api_key)])
def tiktok_disconnect(settings: Settings = Depends(get_settings)) -> dict:
    TikTok(settings).disconnect()
    return {"connected": False}


@router.post("/tiktok/accounts/{open_id}/disconnect", summary="Disconnect a TikTok account",
             dependencies=[Depends(verify_api_key)])
def tiktok_disconnect_account(open_id: str, settings: Settings = Depends(get_settings)) -> dict:
    try:
        TikTok(settings).disconnect(open_id)
    except TikTokError as error:
        raise HTTPException(error.status, str(error)) from error
    return TikTok(settings).status()


@router.get("/tiktok/accounts/{open_id}/creator_info", summary="The account and its options for direct posting")
def tiktok_creator_info(open_id: str, settings: Settings = Depends(get_settings)) -> dict:
    try:
        return TikTok(settings).creator_info(open_id)
    except TikTokError as error:
        raise HTTPException(error.status, str(error)) from error


class UploadBody(BaseModel):
    open_id: str | None = None  # vilket konto; utan val används det enda kopplade
    mode: Literal["draft", "direct"] = "draft"
    # Bara för direct; kontrolleras mot färsk creator_info i TikTok.upload.
    title: str | None = None
    privacy_level: str | None = None
    allow_comment: bool = False
    allow_duet: bool = False
    allow_stitch: bool = False
    brand_organic: bool = False
    brand_content: bool = False
    cover_s: float | None = None  # omslaget, sekunder in i klippet; bara direct


@router.post("/clips/{clip_id}/tiktok", status_code=202, summary="Send the clip to TikTok as a draft or directly",
             dependencies=[Depends(verify_api_key)])
def upload_clip(clip_id: int, body: UploadBody | None = None, settings: Settings = Depends(get_settings)) -> dict:
    if editor.lock.locked():  # filen kan bytas mitt i uppladdningen
        raise HTTPException(409, "A clip is being edited right now. Wait until the edit is done.")
    clip = _get(_log(settings), clip_id)
    try:
        path = clip_file(settings, clip, "tiktok", "Only approved clips can be uploaded to TikTok.")
        body = body or UploadBody()
        Schedule(settings).check_account(clip, "tiktok", body.open_id, datetime.now(UTC), counts=body.mode == "direct")
        account = body.open_id
        if account is None:  # det enda kopplade kontot; inte kopplat: upload ger felet
            with contextlib.suppress(TikTokError):
                account = TikTok(settings).choose(None)
        Schedule(settings).check_variant(clip, "tiktok", account, body.model_dump() if body.mode == "direct" else {})
    except ScheduleError as error:
        raise HTTPException(error.status, str(error)) from error
    post, duration_s = None, 0.0
    if body.mode == "direct":
        post = body.model_dump(exclude={"open_id", "mode"})
        duration_s = clip_seconds(clip, path)
    try:
        return TikTok(settings).upload(clip_id, path, body.open_id, post, duration_s)
    except TikTokError as error:
        raise HTTPException(error.status, str(error)) from error


@router.get("/clips/{clip_id}/tiktok", summary="The latest upload to TikTok for the clip")
def clip_upload(clip_id: int, settings: Settings = Depends(get_settings)) -> dict | None:
    _get(_log(settings), clip_id)
    return TikTok(settings).latest(clip_id)
