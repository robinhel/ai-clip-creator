"""Vägar för en ny version av ett godkänt klipp för ett annat konto, och bilderna versionen kan använda.

Vägarna registreras på campaign.api.router, som importerar den här modulen sist.
"""

from __future__ import annotations

from typing import Literal

from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from clipsmith.api.deps import verify_api_key

from . import editor, versions
from .api import _get, _log, _public, _start_lock, get_settings, router
from .config import Settings

Platform = Literal["tiktok", "youtube"]


class VersionBody(BaseModel):
    platform: Platform
    account: str  # TikToks open_id eller YouTubes channel_id
    font: str | None = None  # None: samma font som originalet
    start_cut_s: float = 0.0
    overlay: str | None = None  # None: kampanjens overlay
    sticker: dict | None = None  # {file, position, start_s, duration_s}; kontrolleras i versions.make
    mirror: bool = False
    speed: float = 1.0  # en av versions.SPEEDS
    filter: str = "none"  # en av versions.FILTERS
    brightness: float = 0.0
    saturation: float = 0.0


@router.get("/clips/{clip_id}/versions/options", summary="Valen för en ny version till ett annat konto")
def version_options(clip_id: int, platform: Platform, account: str, settings: Settings = Depends(get_settings)) -> dict:
    log = _log(settings)
    return versions.options(settings, log, _get(log, clip_id), platform, account)


@router.post("/clips/{clip_id}/versions", status_code=201, summary="Gör en ny version av klippet för ett annat konto",
             dependencies=[Depends(verify_api_key)])
def make_version(clip_id: int, body: VersionBody, request: Request, settings: Settings = Depends(get_settings)) -> dict:
    log = _log(settings)
    if not editor.lock.acquire(blocking=False):  # samma lås som redigeraren: råklippet kan bytas under tiden
        raise HTTPException(409, "En annan redigering pågår. Vänta tills den är klar.")
    editor.editing = clip_id
    try:
        with _start_lock:
            if getattr(request.app.state, "active_run_id", None) is not None:
                raise HTTPException(409, "En körning pågår. Vänta tills den är klar.")
        clip = _get(log, clip_id)
        new_id = versions.make(settings, log, clip, body.platform, body.account, body.font, body.start_cut_s,
                               body.overlay, body.sticker, body.mirror, body.speed, body.filter,
                               body.brightness, body.saturation)
    except versions.VersionError as error:
        raise HTTPException(error.status, str(error)) from error
    except RuntimeError as error:  # ffmpeg misslyckades
        raise HTTPException(500, f"Versionen gick inte att göra: {error}") from error
    finally:
        editor.editing = None
        editor.lock.release()
    return _public(log.get(new_id), settings)


def _image(settings: Settings, folder: str, name: str) -> FileResponse:
    base = (settings.root / folder).resolve()
    path = (base / name).resolve()
    if not path.is_relative_to(base) or path.suffix.lower() != ".png" or not path.is_file():
        raise HTTPException(404, "Bilden finns inte.")
    return FileResponse(path, media_type="image/png")


@router.get("/stickers/{name}", summary="Ett klistermärke (PNG) för förhandsvisning")
def sticker_file(name: str, settings: Settings = Depends(get_settings)) -> FileResponse:
    return _image(settings, "stickers", name)


@router.get("/overlays/{name}", summary="En overlay (PNG) för förhandsvisning")
def overlay_file(name: str, settings: Settings = Depends(get_settings)) -> FileResponse:
    return _image(settings, "overlays", name)
