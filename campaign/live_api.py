"""Bevaka live per kampanj: knappen på kampanjsidan och läget för liveklippen."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from clipsmith.api.deps import verify_api_key

from .api import _describe_profile, _profile_names, get_settings
from .config import Settings
from .live import LiveState

router = APIRouter(prefix="/campaign", tags=["campaign"], dependencies=[Depends(verify_api_key)])
SettingsDep = Annotated[Settings, Depends(get_settings)]


class Peak(BaseModel):
    n: int
    at: str
    vod_s: float
    status: str  # vantar | hamtar | ko | kor | klar | fel | hoppad
    run_id: int | None = None
    reason: str | None = None


class LiveWatch(BaseModel):
    profile: str
    channel: str
    watching: bool
    live: bool
    vod_id: str | None = None
    error: str | None = None
    peaks: list[Peak] = []


class WatchBody(BaseModel):
    watching: bool


def _channel(name: str, settings: Settings) -> str:
    try:
        return _describe_profile(name, settings)["channel"]
    except (SystemExit, Exception):  # en trasig fil har ingen kanal att bevaka
        return ""


def _watch(name: str, channel: str, settings: Settings) -> LiveWatch:
    return LiveWatch(profile=name, channel=channel, **LiveState(settings).channel(name))


@router.get(
    "/live", summary="Bevaka live: läget och topparna för varje kampanj med en Twitch-kanal"
)
def list_live(settings: SettingsDep) -> list[LiveWatch]:
    channels = {name: _channel(name, settings) for name in _profile_names(settings)}
    return [_watch(name, channel, settings) for name, channel in channels.items() if channel]


@router.put("/live/{profile}", summary="Slå på eller av Bevaka live för en kampanj")
def set_live(profile: str, body: WatchBody, settings: SettingsDep) -> LiveWatch:
    if profile not in _profile_names(settings):
        raise HTTPException(404, f"Kampanjen {profile} finns inte.")
    channel = _channel(profile, settings)
    if body.watching and not channel:
        raise HTTPException(400, "Kampanjen har ingen Twitch-kanal att bevaka.")
    LiveState(settings).set_watching(profile, body.watching)
    return _watch(profile, channel, settings)
