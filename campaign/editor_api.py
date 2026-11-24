"""Vägar för redigeraren: läget för ett klipp, originalet för förhandsvisningen och själva redigeringen."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from clipsmith.api.deps import verify_api_key

from . import editor
from .api import _get, _log, _profile, _public, _start_lock, get_settings
from .cliplog import UploadRunning
from .config import Settings

router = APIRouter(prefix="/campaign", tags=["campaign"])


@router.get("/clips/{clip_id}/edit", summary="Can the clip be edited, and how is it trimmed")
def get_edit(clip_id: int, settings: Settings = Depends(get_settings)) -> dict:
    clip = _get(_log(settings), clip_id)
    return editor.edit_info(clip, _profile(clip["profile"], settings), settings)


@router.get("/clips/{clip_id}/original", summary="The raw clip before trimming (with Range support)")
def get_original(clip_id: int, settings: Settings = Depends(get_settings)) -> FileResponse:
    raw = editor.raw_path(_get(_log(settings), clip_id), settings)
    if raw is None or not raw.is_file():
        raise HTTPException(404, "The raw clip does not exist.")
    return FileResponse(editor.source_path(raw), media_type="video/mp4")


class WordIn(BaseModel):
    start: float
    end: float
    word: str  # tomt eller för långt ger 400 med svensk text i editor._check_words


class EditBody(BaseModel):
    in_s: float | None = None
    out_s: float | None = None
    title: str | None = Field(default=None, max_length=120)
    headline: str | None = Field(default=None, max_length=80)  # texten i bilden; None oförändrad, "" ger titeln
    words: list[WordIn] | None = None  # hela listan; fler än 1 000 ord ger 400 i editor._check_words


@router.post("/clips/{clip_id}/edit", summary="Trim the clip and change the headline",
             dependencies=[Depends(verify_api_key)])
def post_edit(clip_id: int, body: EditBody, request: Request, settings: Settings = Depends(get_settings)) -> dict:
    log = _log(settings)
    if not editor.lock.acquire(blocking=False):
        raise HTTPException(409, "Another edit is running. Wait until it is done.")
    editor.editing = clip_id
    try:
        with _start_lock:  # samma lås som när en körning startar, så att de inte kan börja samtidigt
            if getattr(request.app.state, "active_run_id", None) is not None:
                raise HTTPException(409, "A project is running. Wait until it is done.")
        clip = _get(log, clip_id)  # läs inuti låset
        profile = _profile(clip["profile"], settings)
        words = None if body.words is None else [w.model_dump() for w in body.words]
        editor.edit_clip(clip, profile, settings, log, in_s=body.in_s, out_s=body.out_s, title=body.title,
                         words=words, headline=body.headline)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except (editor.EditConflict, UploadRunning) as error:
        raise HTTPException(409, str(error)) from error
    except RuntimeError as error:  # ffmpeg misslyckades
        raise HTTPException(500, f"The clip could not be remade: {error}") from error
    finally:
        editor.editing = None
        editor.lock.release()
    return _public(log.get(clip_id), settings)
