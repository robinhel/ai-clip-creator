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

router = APIRouter(prefix="/campaign", tags=["campaign"])
SettingsDep = Annotated[Settings, Depends(get_settings)]


class ScheduleBody(BaseModel):
    platform: Literal["youtube", "tiktok"]
    at: datetime  # ISO-tid med tidszon; sparas som UTC
    account: str | None = None  # YouTubes channel_id eller TikToks open_id
    fields: dict = {}


@router.get("/schedule", summary="Schemalagda poster: väntande först, sedan de 20 senast avslutade")
def list_schedule(settings: SettingsDep) -> list[dict]:
    return Schedule(settings).list()


@router.post("/clips/{clip_id}/schedule", status_code=201, summary="Schemalägg uppladdningen av ett godkänt klipp",
             dependencies=[Depends(verify_api_key)])
def schedule_clip(clip_id: int, body: ScheduleBody, settings: SettingsDep) -> dict:
    try:
        return Schedule(settings).add(clip_id, body.platform, body.at, body.account, body.fields)
    except ScheduleError as error:
        raise HTTPException(error.status, str(error)) from error


@router.delete("/schedule/{post_id}", summary="Avbryt en väntande post", dependencies=[Depends(verify_api_key)])
def cancel_schedule(post_id: int, settings: SettingsDep) -> dict:
    try:
        return Schedule(settings).cancel(post_id)
    except ScheduleError as error:
        raise HTTPException(error.status, str(error)) from error
