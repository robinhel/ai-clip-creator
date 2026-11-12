"""Vägar för redigeraren: läget för ett klipp, originalet för förhandsvisningen och själva redigeringen."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from clipsmith.api.deps import verify_api_key

from . import editor
from .api import _get, _log, _profile, _public, _start_lock, get_settings
from .config import Settings

router = APIRouter(prefix="/campaign", tags=["campaign"])


@router.get("/clips/{clip_id}/edit", summary="Kan klippet redigeras, och hur är det trimmat")
def get_edit(clip_id: int, settings: Settings = Depends(get_settings)) -> dict:
    clip = _get(_log(settings), clip_id)
    return editor.edit_info(clip, _profile(clip["profile"], settings), settings)


@router.get("/clips/{clip_id}/original", summary="Råklippet före trimning (med Range-stöd)")
def get_original(clip_id: int, settings: Settings = Depends(get_settings)) -> FileResponse:
    raw = editor.raw_path(_get(_log(settings), clip_id), settings)
    if raw is None or not raw.is_file():
        raise HTTPException(404, "Råklippet finns inte.")
    return FileResponse(editor.source_path(raw), media_type="video/mp4")


class EditBody(BaseModel):
    in_s: float | None = None
    out_s: float | None = None
    title: str | None = Field(default=None, max_length=120)


@router.post("/clips/{clip_id}/edit", summary="Trimma klippet och byt rubrik",
             dependencies=[Depends(verify_api_key)])
def post_edit(clip_id: int, body: EditBody, request: Request, settings: Settings = Depends(get_settings)) -> dict:
    log = _log(settings)
    if not editor.lock.acquire(blocking=False):
        raise HTTPException(409, "En annan redigering pågår. Vänta tills den är klar.")
    try:
        with _start_lock:  # samma lås som när en körning startar, så att de inte kan börja samtidigt
            if getattr(request.app.state, "active_run_id", None) is not None:
                raise HTTPException(409, "En körning pågår. Vänta tills den är klar.")
        clip = _get(log, clip_id)  # läs inuti låset
        profile = _profile(clip["profile"], settings)
        editor.edit_clip(clip, profile, settings, log, in_s=body.in_s, out_s=body.out_s, title=body.title)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except editor.EditConflict as error:
        raise HTTPException(409, str(error)) from error
    except RuntimeError as error:  # ffmpeg misslyckades
        raise HTTPException(500, f"Klippet gick inte att göra om: {error}") from error
    finally:
        editor.lock.release()
    return _public(log.get(clip_id), settings)
