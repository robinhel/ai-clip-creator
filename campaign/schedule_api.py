"""Vägar för schemalagda uppladdningar: lista, schemalägg ett godkänt klipp och avbryt en väntande post."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from clipsmith.api.deps import verify_api_key

from .api import get_settings
from .config import Settings
from .schedule import Schedule, ScheduleError
from .variant import VariantError, suggest

router = APIRouter(prefix="/campaign", tags=["campaign"])
SettingsDep = Annotated[Settings, Depends(get_settings)]


class ScheduleBody(BaseModel):
    platform: Literal["youtube", "tiktok"]
    at: datetime  # ISO-tid med tidszon; sparas som UTC
    account: str | None = None  # YouTubes channel_id eller TikToks open_id
    fields: dict = {}


@router.get("/schedule", summary="Scheduled posts: waiting first, then the 20 most recently finished")
def list_schedule(settings: SettingsDep) -> list[dict]:
    return Schedule(settings).list()


@router.post("/clips/{clip_id}/schedule", status_code=201, summary="Schedule the upload of an approved clip",
             dependencies=[Depends(verify_api_key)])
def schedule_clip(clip_id: int, body: ScheduleBody, settings: SettingsDep) -> dict:
    try:
        return Schedule(settings).add(clip_id, body.platform, body.at, body.account, body.fields)
    except ScheduleError as error:
        raise HTTPException(error.status, str(error)) from error


@router.get("/clips/{clip_id}/accounts", summary="The campaign's accounts for the clip: posted, scheduled or free")
def clip_accounts(clip_id: int, platform: Literal["tiktok", "youtube"], settings: SettingsDep) -> dict:
    schedule = Schedule(settings)
    try:
        clip = schedule.clips.get(clip_id)
    except SystemExit as error:
        raise HTTPException(404, "The clip does not exist.") from error
    return schedule.accounts_for(clip, platform)


class VariantBody(BaseModel):
    platform: Literal["youtube", "tiktok"]
    account: str  # YouTubes channel_id eller TikToks open_id


@router.post("/clips/{clip_id}/variant", summary="Suggest a new text for the clip on another account (Claude)",
             dependencies=[Depends(verify_api_key)])
def clip_variant(clip_id: int, body: VariantBody, settings: SettingsDep) -> dict:
    try:
        clip = Schedule(settings).clips.get(clip_id)
    except SystemExit as error:
        raise HTTPException(404, "The clip does not exist.") from error
    try:
        return suggest(settings, clip, body.platform, body.account)
    except VariantError as error:
        raise HTTPException(error.status, str(error)) from error


@router.delete("/schedule/{post_id}", summary="Cancel a waiting post", dependencies=[Depends(verify_api_key)])
def cancel_schedule(post_id: int, settings: SettingsDep) -> dict:
    try:
        return Schedule(settings).cancel(post_id)
    except ScheduleError as error:
        raise HTTPException(error.status, str(error)) from error
