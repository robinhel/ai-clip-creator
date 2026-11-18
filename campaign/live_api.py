"""Bevaka live per kampanj och för en fri Twitch-kanal utan kampanj: knapparna och läget för liveklippen."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from clipsmith.api.deps import verify_api_key

from .api import _describe_profile, _profile_names, get_settings
from .config import Settings
from .live import FREE, LiveState
from .streams import LOGIN

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
    profile: str | None  # None: en fri kanal utan kampanj
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


def _watch_free(login: str, settings: Settings) -> LiveWatch:
    return LiveWatch(profile=None, channel=login, **LiveState(settings).channel(FREE + login))


@router.get(
    "/live",
    summary="Bevaka live: läget och topparna för varje kampanj med en Twitch-kanal, och fria kanaler",
)
def list_live(settings: SettingsDep) -> list[LiveWatch]:
    channels = {name: _channel(name, settings) for name in _profile_names(settings)}
    campaigns = [_watch(name, channel, settings) for name, channel in channels.items() if channel]
    free = [
        key.removeprefix(FREE) for key in LiveState(settings).watching() if key.startswith(FREE)
    ]
    return campaigns + [_watch_free(login, settings) for login in free]


@router.put(
    "/live/channel/{login}", summary="Slå på eller av Bevaka live för en Twitch-kanal utan kampanj"
)
def set_live_channel(login: str, body: WatchBody, settings: SettingsDep) -> LiveWatch:
    login = login.lower()
    if not LOGIN.fullmatch(login):
        raise HTTPException(
            400,
            "Det ser inte ut som en Twitch-kanal. Skriv kanalens namn, till exempel ishowspeed.",
        )
    LiveState(settings).set_watching(FREE + login, body.watching)
    return _watch_free(login, settings)


@router.put("/live/{profile}", summary="Slå på eller av Bevaka live för en kampanj")
def set_live(profile: str, body: WatchBody, settings: SettingsDep) -> LiveWatch:
    if profile not in _profile_names(settings):
        raise HTTPException(404, f"Kampanjen {profile} finns inte.")
    channel = _channel(profile, settings)
    if body.watching and not channel:
        raise HTTPException(400, "Kampanjen har ingen Twitch-kanal att bevaka.")
    LiveState(settings).set_watching(profile, body.watching)
    return _watch(profile, channel, settings)
