"""Kampanjsidans material och chatt. Vägarna registreras på campaign.api.router, som importerar modulen sist."""

from __future__ import annotations

import re

import anthropic
from fastapi import BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from clipsmith.api.deps import get_db, verify_api_key
from clipsmith.db.models import Run, RunStatus

from . import chat, editor
from .api import _log, _profile, _start_lock, get_settings, router
from .budget import BudgetExceeded
from .config import Settings
from .pipeline import clipsmith_config

NO_VIDEO = "The video no longer exists."


def _campaign(profile_id: str, settings: Settings) -> str:
    """404 också för dolda kampanjer: de börjar med _, som _ingen för liveklipp utan kampanj."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_]{1,39}", profile_id) or not (settings.profiles_dir / f"{profile_id}.yaml").exists():
        raise HTTPException(404, f"The campaign {profile_id} does not exist.")
    return profile_id


def _chat(chat_id: int, settings: Settings) -> dict:
    found = chat.Chats(settings.data_dir / "klipp.db").get(chat_id)
    if found is None:
        raise HTTPException(404, "The chat does not exist.")
    return found


def _whole(chat_id: int, settings: Settings) -> dict:
    found = _chat(chat_id, settings)
    store = chat.Chats(settings.data_dir / "klipp.db")
    try:
        scope = _profile(found["profile"], settings).search_default_scope
    except HTTPException:
        scope = "latest"
    sources = chat.material(found["profile"], settings, _log(settings))
    return chat.public(found, store.hits(chat_id), sources, scope)


@router.get("/profiles/{profile_id}/material", summary="The campaign's videos, newest first")
def get_material(profile_id: str, settings: Settings = Depends(get_settings)) -> list[dict]:
    _campaign(profile_id, settings)
    return [{k: v for k, v in item.items() if k != "game"}
            for item in chat.material(profile_id, settings, _log(settings))]


@router.get("/material/{vod_id}/video", summary="Play a video from a campaign's material")
def material_video(vod_id: str, settings: Settings = Depends(get_settings)) -> FileResponse:
    if vod_id not in {run["vod_id"] for run in _log(settings).runs()}:
        raise HTTPException(404, "The video does not belong to any campaign.")
    path = settings.work_dir / vod_id / f"{vod_id}.mp4"
    if not path.is_file():
        raise HTTPException(404, NO_VIDEO)
    return FileResponse(path, media_type="video/mp4")


# ---------- chatten ----------


class NewChat(BaseModel):
    message: str = ""


class ChatMessage(BaseModel):
    message: str = ""
    confirm: bool = False


def _text(message: str) -> str:
    text = message.strip()
    if not text:
        raise HTTPException(400, "Write what you want clips of.")
    if len(text) > chat.MAX_MESSAGE:
        raise HTTPException(400, f"The message can be at most {chat.MAX_MESSAGE} characters.")
    return text


def _send(request: Request, settings: Settings, profile: str, **kwargs) -> int:
    """Ett meddelande till Claude. 409 medan ett projekt eller en annan sökning pågår, 402 vid budgettaket."""
    with _start_lock:
        if getattr(request.app.state, "active_run_id", None) is not None:
            raise HTTPException(409, "A project is running. Wait until it is done.")
        if not chat.lock.acquire(blocking=False):
            raise HTTPException(409, "Claude is already searching. Wait until the search is done.")
    try:
        return chat.send(settings, profile, **kwargs)
    except BudgetExceeded as error:
        raise HTTPException(402, f"{error} Nothing was saved.") from error
    except anthropic.APIError as error:  # nere, överbelastad eller fel i anropet: inget sparas
        raise HTTPException(502, f"Claude is not answering right now ({type(error).__name__}). Nothing was saved. "
                                 "Try again in a while.") from error
    except chat.ChatError as error:
        raise HTTPException(error.status, error.text) from error
    except SystemExit as error:  # en trasig profil
        raise HTTPException(400, f"The campaign {profile} cannot be read: {error}") from error
    finally:
        chat.lock.release()


@router.get("/profiles/{profile_id}/chats", summary="The campaign's chats, newest first")
def list_chats(profile_id: str, settings: Settings = Depends(get_settings)) -> list[dict]:
    _campaign(profile_id, settings)
    return chat.Chats(settings.data_dir / "klipp.db").list(profile_id)


@router.post("/profiles/{profile_id}/chats", status_code=201, summary="New chat with a first message",
             dependencies=[Depends(verify_api_key)])
def new_chat(profile_id: str, body: NewChat, request: Request, settings: Settings = Depends(get_settings)) -> dict:
    _campaign(profile_id, settings)
    chat_id = _send(request, settings, profile_id, message=_text(body.message))
    return _whole(chat_id, settings)


@router.get("/chats/{chat_id}", summary="A chat with messages and hits")
def get_chat(chat_id: int, settings: Settings = Depends(get_settings)) -> dict:
    return _whole(chat_id, settings)


@router.post("/chats/{chat_id}/messages", summary="Write in the chat, or say yes to an expensive search",
             dependencies=[Depends(verify_api_key)])
def chat_message(chat_id: int, body: ChatMessage, request: Request,
                 settings: Settings = Depends(get_settings)) -> dict:
    found = _chat(chat_id, settings)
    if body.confirm:
        _send(request, settings, found["profile"], chat_id=chat_id, confirm=True)
    else:
        _send(request, settings, found["profile"], chat_id=chat_id, message=_text(body.message))
    return _whole(chat_id, settings)


class HitIds(BaseModel):
    hit_ids: list[int]


@router.post("/chats/{chat_id}/clips", status_code=202, summary="Make clips of selected hits",
             dependencies=[Depends(verify_api_key)])
def chat_clips(chat_id: int, body: HitIds, request: Request, background_tasks: BackgroundTasks,
               db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    """En körning per video. Svaret har den första körningens id, och alla i run_ids."""
    found = _chat(chat_id, settings)
    store = chat.Chats(settings.data_dir / "klipp.db")
    hits = {hit["id"]: hit for hit in store.hits(chat_id)}
    wanted = list(dict.fromkeys(body.hit_ids))
    if not wanted or any(hit_id not in hits for hit_id in wanted):
        raise HTTPException(400, "Choose one or more hits from the chat.")
    if any(hits[hit_id]["status"] != "ny" for hit_id in wanted):
        raise HTTPException(400, "One of the hits is already clipped or being clipped.")
    profile = _profile(found["profile"], settings)
    try:
        clipsmith_config(profile, settings)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    log = _log(settings)
    sources = {item["vod_id"]: item for item in chat.material(profile.name, settings, log)}
    gone = [hit_id for hit_id in wanted if (source := sources.get(hits[hit_id]["vod_id"])) is None
            or (source["kind"] == "local" and not source["has_video"])]
    if gone:
        store.set_hits(gone, status="ny", problem=NO_VIDEO)
    groups: dict[str, list[int]] = {}
    for hit_id in wanted:
        if hit_id not in gone:
            groups.setdefault(hits[hit_id]["vod_id"], []).append(hit_id)
    if not groups:
        raise HTTPException(400, NO_VIDEO)

    with _start_lock:
        if request.app.state.active_run_id is not None:
            raise HTTPException(409, "A project is already running. Wait until it is done.")
        if editor.lock.locked() or chat.lock.locked():
            raise HTTPException(409, "A clip is being edited or Claude is searching. Wait until it is done.")
        runs = []
        for vod_id in groups:
            run = Run(vod_id=vod_id, channel=profile.streamer.lower(), status=RunStatus.pending)
            db.add(run)
            db.commit()
            db.refresh(run)
            runs.append(run)
        request.app.state.active_run_id = runs[0].id
    focus = next((m["text"] for m in found["messages"] if m["role"] == "user"), "")[:300]
    try:
        for run, vod_id in zip(runs, groups, strict=True):
            source = sources[vod_id]
            # origin chat: körningen tar inte över videons kort. En egen fil hamnar i kampanjens projekt.
            log.add_run(run.id, vod_id, profile.name, source["game"], focus=focus or None, title=source["title"],
                        channel_name=profile.streamer, thumbnail_url=None, duration_s=source["duration_s"],
                        origin="chat")
        store.set_hits([hit_id for ids in groups.values() for hit_id in ids], status="vald", problem=None)
    except Exception:  # annars står körningen som pågående tills servern startas om
        for run in runs:
            run.status, run.error = RunStatus.failed, "The project could not be saved in the clip log. Try again."
        db.commit()
        request.app.state.active_run_id = None
        raise
    jobs = [(run.id, vod_id, ids) for run, (vod_id, ids) in zip(runs, groups.items(), strict=True)]
    background_tasks.add_task(chat.make_clips, chat_id, jobs, profile.name, settings, app=request.app)
    return {"run_id": runs[0].id, "run_ids": [run.id for run in runs]}
