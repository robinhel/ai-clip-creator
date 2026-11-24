"""Hur många visningar kampanjens innehåll brukar få, för delen Visningar i kampanjbetyget (campaign/scout_score.py).

Två källor: kanalens bästa Twitch-klipp de senaste 30 dagarna (Helix, en sida) och kampanjens bästa klipp på
Content Rewards (den publika kampanjsidan, via examples.fetch). Twitch-kanalen kommer ur ClippingAlphas get_assets,
eller ur Helix via en VOD i get_assets när kanalposten saknas. Content Rewards-sidan kommer ur omdirigeringen bakom
ClippingAlphas /go/-länk.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar
from urllib.parse import urlparse

import httpx

from clipsmith.config.loaders import load_secrets
from clipsmith.twitch.client import TwitchClient

from . import examples

log = logging.getLogger(__name__)

LOGIN = re.compile(r"[a-zA-Z0-9_]{3,25}")
TWITCH_HOSTS = {"twitch.tv", "www.twitch.tv"}
GO_PATH = re.compile(r"/go/\d+")
UUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
CR_CAMPAIGN = re.compile(rf"https://contentrewards\.com/c/campaigns/{UUID}")
VOD_PATH = re.compile(r"/videos/(\d+)")
TWITCH_DAYS = 30
TOP_CLIPS = 10  # betyget räknar de tio bästa klippen; Helix sorterar efter visningar, så en sida räcker
USER_AGENT = "klipp-bevakning/0.1 (low rate, read only)"
T = TypeVar("T")


def _login(value: object) -> str | None:
    return value.lower() if isinstance(value, str) and LOGIN.fullmatch(value) else None


def _twitch_path(url: object) -> str | None:
    """Sökvägen i en https-länk till Twitch, annars None."""
    if not isinstance(url, str):
        return None
    parsed = urlparse(url)
    return parsed.path if parsed.scheme == "https" and parsed.hostname in TWITCH_HOSTS else None


def _login_in_url(url: object) -> str | None:
    """Inloggningen ur https://www.twitch.tv/<login>/videos?filter=archives&sort=time."""
    path = _twitch_path(url)
    return None if path is None else _login(path.strip("/").split("/")[0])


def _vod_in_url(url: object) -> str | None:
    """Video-id ur https://www.twitch.tv/videos/<siffror>."""
    match = VOD_PATH.fullmatch(_twitch_path(url) or "")
    return match[1] if match else None


def _twitch_videos(assets: dict) -> list[dict]:
    return [v for v in assets.get("videos") or [] if isinstance(v, dict) and v.get("platform") == "twitch"]


def channel_from_assets(assets: dict) -> str | None:
    """Kampanjens Twitch-kanal ur get_assets: först kanalposten, annars kanalen på en video."""
    videos = _twitch_videos(assets)
    channels = [_login_in_url(v.get("url")) for v in videos if v.get("kind") == "channel"]
    return next((login for login in channels + [_login(v.get("channel")) for v in videos] if login), None)


def vod_from_assets(assets: dict) -> str | None:
    """Video-id för den första Twitch-VOD:en i get_assets, så att kanalen kan slås upp när kanalposten saknas."""
    return next((vod for vod in (_vod_in_url(v.get("url")) for v in _twitch_videos(assets)) if vod), None)


def go_link(url: object) -> str | None:
    """https://clippingalpha.com/go/<siffror> utan query, annars None."""
    parsed = urlparse(url if isinstance(url, str) else "")
    if parsed.scheme != "https" or parsed.hostname != "clippingalpha.com":
        return None
    return f"https://clippingalpha.com{parsed.path}" if GO_PATH.fullmatch(parsed.path) else None


def resolve_join(http: httpx.Client, join_url: str | None) -> str | None:
    """Content Rewards-sidan som /go/-länken leder till, utan query. Ett anrop, och omdirigeringen följs inte.
    None för andra länkar och svar, till exempel 404. Nätverksfel och tillfälliga fel (429 och 5xx) går vidare som
    httpx.HTTPError, så att länken provas igen. Cookies som /go/ sätter sparas inte i klienten."""
    link = go_link(join_url)
    if link is None:
        return None
    response = http.get(link, headers={"User-Agent": USER_AGENT}, follow_redirects=False)
    http.cookies.clear()  # spårningscookies (ca_vid, _fbp, _ttp) ska inte följa med till MCP-anropen
    if response.status_code == 429 or response.is_server_error:
        response.raise_for_status()
    if not response.is_redirect:
        return None
    target = response.headers["location"].split("?")[0].split("#")[0]
    return target if CR_CAMPAIGN.fullmatch(target) else None


def campaign_page(cr_url: str) -> dict | None:
    """Visningarna för kampanjens klipp på Content Rewards och priset per land, {"clip_views": [...], "geo": {...}},
    högst en hämtning i timmen. En ny kampanj utan klipp ger en tom lista. None när sidan inte går att läsa."""
    try:
        data = examples.fetch(cr_url)
    except RuntimeError as error:
        log.warning("Content Rewards: the views for %s could not be read: %s", cr_url, error)
        return None
    return {"clip_views": [clip["views"] for clip in data["clips"]], "geo": data.get("geo")}


def _rfc3339(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _helix(request: Callable[[TwitchClient], T], what: str) -> T | None:
    """Ett svar från Twitch Helix med appens nycklar, tolkat av request. None utan nycklar, vid fel och när svaret
    har en oväntad form; what säger vad som då uteblir."""
    secrets = load_secrets()
    if not (secrets.twitch_client_id and secrets.twitch_client_secret):
        log.warning("TWITCH_CLIENT_ID or TWITCH_CLIENT_SECRET is missing: %s", what)
        return None
    try:
        with TwitchClient(secrets.twitch_client_id, secrets.twitch_client_secret) as client:
            return request(client)
    except Exception as error:  # nätverk, nycklar, kanalen finns inte, oväntat svar: betyget räknas ändå
        log.warning("Twitch Helix did not answer as expected (%s): %s", type(error).__name__, what)
        return None


def twitch_clip_views(login: str, now: datetime) -> list[int] | None:
    """Visningarna för kanalens högst tio bästa klipp de senaste 30 dagarna. En tom lista när kanalen inte har några.
    None utan nycklar, när kanalen inte finns och vid fel."""
    window = {"started_at": _rfc3339(now - timedelta(days=TWITCH_DAYS)), "ended_at": _rfc3339(now)}

    def clips(client: TwitchClient) -> list[int]:
        # En sida. get_clips bläddrar upp till tio sidor, och de bästa klippen ligger redan på den första.
        user_id = client.get_user_id(login)
        body = client._get("/clips", {"broadcaster_id": user_id, "first": TOP_CLIPS, **window})
        return [int(clip.get("view_count") or 0) for clip in body.get("data") or []]

    return _helix(clips, f"the Twitch views are not counted for {login}")


def twitch_login_from_vod(video_id: str) -> str | None:
    """Kanalen som har VOD:en, ur Helix GET /videos?id=<id>. None utan nycklar, när videon inte finns, vid fel och
    när user_login inte är en giltig inloggning (samma regel som för kanalen i get_assets)."""

    def login(client: TwitchClient) -> str | None:
        data = client._get("/videos", {"id": video_id}).get("data") or []
        return _login(data[0].get("user_login")) if data else None

    return _helix(login, f"the channel for VOD {video_id} is not looked up")
