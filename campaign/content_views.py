"""Hur många visningar kampanjens innehåll brukar få, för delen Visningar i kampanjbetyget (campaign/scout.py).

Två källor: kanalens bästa Twitch-klipp de senaste 30 dagarna (Helix, en sida) och kampanjens bästa klipp på
Content Rewards (den publika kampanjsidan, via examples.fetch). Twitch-kanalen kommer ur ClippingAlphas get_assets
och Content Rewards-sidan ur omdirigeringen bakom ClippingAlphas /go/-länk.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
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
TWITCH_DAYS = 30
TWITCH_CLIPS = 10  # Helix sorterar klippen efter visningar: en sida är kanalens tio bästa
USER_AGENT = "klipp-bevakning/0.1 (low rate, read only)"


def _login(value: object) -> str | None:
    return value.lower() if isinstance(value, str) and LOGIN.fullmatch(value) else None


def _login_in_url(url: object) -> str | None:
    """Inloggningen ur https://www.twitch.tv/<login>/videos?filter=archives&sort=time."""
    if not isinstance(url, str):
        return None
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in TWITCH_HOSTS:
        return None
    return _login(parsed.path.strip("/").split("/")[0])


def channel_from_assets(assets: dict) -> str | None:
    """Kampanjens Twitch-kanal ur get_assets: först kanalposten, annars kanalen på en video."""
    videos = [v for v in assets.get("videos") or [] if isinstance(v, dict) and v.get("platform") == "twitch"]
    channels = [_login_in_url(v.get("url")) for v in videos if v.get("kind") == "channel"]
    return next((login for login in channels + [_login(v.get("channel")) for v in videos] if login), None)


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


def campaign_clip_views(cr_url: str) -> list[int] | None:
    """Visningarna för kampanjens klipp på Content Rewards, högst en hämtning i timmen.
    None när sidan inte går att läsa."""
    try:
        data = examples.fetch(cr_url)
    except RuntimeError as error:
        log.warning("Content Rewards: visningarna för %s gick inte att läsa: %s", cr_url, error)
        return None
    return [clip["views"] for clip in data["clips"]]


def _rfc3339(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def twitch_clip_views(login: str, now: datetime) -> list[int] | None:
    """Visningarna för kanalens högst tio bästa klipp de senaste 30 dagarna. En tom lista när kanalen inte har några.
    None utan nycklar, när kanalen inte finns och vid fel."""
    secrets = load_secrets()
    if not (secrets.twitch_client_id and secrets.twitch_client_secret):
        log.warning("TWITCH_CLIENT_ID eller TWITCH_CLIENT_SECRET saknas: Twitch-visningarna räknas inte för %s", login)
        return None
    window = {"started_at": _rfc3339(now - timedelta(days=TWITCH_DAYS)), "ended_at": _rfc3339(now)}
    try:
        with TwitchClient(secrets.twitch_client_id, secrets.twitch_client_secret) as client:
            # En sida. get_clips bläddrar upp till tio sidor, och de bästa klippen ligger redan på den första.
            user_id = client.get_user_id(login)
            body = client._get("/clips", {"broadcaster_id": user_id, "first": TWITCH_CLIPS, **window})
    except Exception as error:  # nätverk, nycklar, kanalen finns inte: betyget räknas ändå
        log.warning("Twitch Helix svarade inte för %s (%s): visningarna räknas inte", login, type(error).__name__)
        return None
    return [int(clip.get("view_count") or 0) for clip in body.get("data") or []]
