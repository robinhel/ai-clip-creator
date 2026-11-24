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
    summary="Watch live: the status and peaks for each campaign with a Twitch channel, and free channels",
)
def list_live(settings: SettingsDep) -> list[LiveWatch]:
    channels = {name: _channel(name, settings) for name in _profile_names(settings)}
    campaigns = [_watch(name, channel, settings) for name, channel in channels.items() if channel]
    # Också en fri kanal som inte bevakas längre, så att topparna finns kvar på Hem efter Sluta bevaka.
    free = [key.removeprefix(FREE) for key in LiveState(settings).keys() if key.startswith(FREE)]
    return campaigns + [_watch_free(login, settings) for login in free]


@router.put(
    "/live/channel/{login}", summary="Turn Watch live on or off for a Twitch channel without a campaign"
)
def set_live_channel(login: str, body: WatchBody, settings: SettingsDep) -> LiveWatch:
    login = login.lower()
    if not LOGIN.fullmatch(login):
        raise HTTPException(
            400,
            "That does not look like a Twitch channel. Enter the channel name, for example ishowspeed.",
        )
    LiveState(settings).set_watching(FREE + login, body.watching)
    return _watch_free(login, settings)


@router.put("/live/{profile}", summary="Turn Watch live on or off for a campaign")
def set_live(profile: str, body: WatchBody, settings: SettingsDep) -> LiveWatch:
    if profile not in _profile_names(settings):
        raise HTTPException(404, f"The campaign {profile} does not exist.")
    channel = _channel(profile, settings)
    if body.watching and not channel:
        raise HTTPException(400, "The campaign has no Twitch channel to watch.")
    LiveState(settings).set_watching(profile, body.watching)
    return _watch(profile, channel, settings)
