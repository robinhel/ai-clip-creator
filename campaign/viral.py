"""Virala klipp: andras klipp som gick bättre än vanligt, så att appen lär sig vad som fungerar.

Två källor hämtas av collect: kanalens Twitch-klipp (Helix) och kampanjens bästa klipp på Content Rewards (via
examples.fetch). Robin kan också lägga till en egen länk med add_link; bara då hämtas något från TikTok eller
Instagram, med yt-dlp, och videon raderas direkt efter mätningen.

Avvikare: visningar per dag delat med medianen av visningar per dag i samma källa. Bara klipp med datum räknas,
och bara klipp med minst MIN_VIEWS visningar får ett avvikartal.
Listan sparas i klippdata/viral/<profil>.json.
"""

from __future__ import annotations

import json
import logging
import os
import re
import statistics
import tempfile
import threading
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from clipsmith.config.loaders import load_secrets
from clipsmith.twitch.client import TwitchClient

from . import examples, locate
from .config import load_settings
from .profile import Profile, load_profile

log = logging.getLogger(__name__)

TIKTOK_ID = re.compile(r"tiktok\.com/.*/video/(\d+)")
DAY_S = 86400
TWITCH_DAYS = 60  # VOD:er försvinner efter 7–60 dagar; äldre klipp går inte att hitta i strömmen
MIN_VIEWS = 1000  # färre visningar än så räknas inte som avvikare (Robins val), men ingår i medianen
# fält som en ny hämtning inte vet något om och som ska följa med från den sparade listan
KEEP = ("measured", "taste")
# fält som följer med från den sparade listan när den nya hämtningen saknar värde (CR-klippens plats)
KEEP_IF_MISSING = ("location",)
LINK_HOSTS = ("tiktok.com", "instagram.com", "youtube.com", "youtu.be")
MAX_BYTES = 200 * 1024 * 1024  # yt-dlp hämtar inte större filer
# en läs-ändra-skriv av listan i taget (collect, add_link och taste.analyse). Låset gäller bara inom
# serverns process; CLI:t i en annan process skyddas inte av det.
LOCK = threading.Lock()


def _now() -> datetime:
    return datetime.now(UTC)


def _profile(profile: Profile | str) -> Profile:
    if isinstance(profile, Profile):
        return profile
    return load_profile(profile, load_settings().profiles_dir)


def _path(profile: Profile | str) -> Path:
    name = profile.name if isinstance(profile, Profile) else str(profile)
    return load_settings().data_dir / "viral" / f"{name}.json"


def load(profile: Profile | str) -> list[dict]:
    path = _path(profile)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def save(profile: Profile | str, clips: list[dict]) -> None:
    path = _path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")  # ingen läser en halvskriven lista
    tmp.write_text(json.dumps(clips, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _clip(url: str, views: int, posted: datetime | None, **fields) -> dict:
    """Ett klipp i listans form. fields har source och det källan vet utöver url, visningar och datum."""
    age_days = max((_now() - posted).total_seconds() / DAY_S, 1.0) if posted else None
    return {
        "id": url,
        "url": url,
        "video_url": None,
        "title": "",
        "creator": None,
        "views": views,
        "posted_at": posted.isoformat() if posted else None,
        "views_per_day": views / age_days if age_days else None,
        "outlier": None,
        "duration": None,
        "location": None,
        "measured": False,
        **fields,
    }


def _twitch(profile: Profile) -> list[dict]:
    """Kanalens klipp ur Helix. Tom lista utan nycklar och vid fel."""
    secrets = load_secrets()
    if not (secrets.twitch_client_id and secrets.twitch_client_secret):
        log.warning(
            "TWITCH_CLIENT_ID or TWITCH_CLIENT_SECRET is missing: no Twitch clips for %s",
            profile.name,
        )
        return []
    try:
        with TwitchClient(secrets.twitch_client_id, secrets.twitch_client_secret) as client:
            raw = client.get_clips(
                client.get_user_id(profile.streamer.lower()),
                started_at=_now() - timedelta(days=TWITCH_DAYS),
                ended_at=_now(),  # utan ended_at ger Helix bara en vecka
            )
    except (
        Exception
    ) as error:  # nätverk, nycklar, kanalen finns inte: insamlingen går vidare utan Twitch
        log.warning(
            "Twitch Helix did not answer (%s): no Twitch clips for %s",
            type(error).__name__,
            profile.name,
        )
        return []
    return [
        _clip(
            c.url,
            c.view_count,
            datetime.fromisoformat(c.created_at.replace("Z", "+00:00")),
            source="twitch",
            id=c.id,
            title=c.title,
            creator=c.creator_name,
            duration=c.duration,
            location=locate.from_twitch(asdict(c)),
        )
        for c in raw
    ]


def _tiktok_posted(url: str | None) -> datetime | None:
    """Postdatumet ur ett TikTok-id: de övre bitarna är unix-sekunder."""
    match = TIKTOK_ID.search(url or "")
    return datetime.fromtimestamp(int(match[1]) >> 32, UTC) if match else None


def _content_rewards(profile: Profile) -> list[dict]:
    """Kampanjens bästa klipp på Content Rewards. Tom lista utan länk och när sidan inte går att läsa."""
    url = profile.content_rewards_url
    if not url:
        return []
    try:
        data = examples.fetch(url)
    except RuntimeError as error:
        log.warning("Content Rewards: no clips for %s: %s", profile.name, error)
        return []
    return [
        _clip(
            c["post_url"] or c["video_url"],
            c["views"],
            _tiktok_posted(c["post_url"]),
            source="content_rewards",
            video_url=c["video_url"],
            title=c["title"],
            creator=c["name"],
        )
        for c in data["clips"]
        if c.get("post_url") or c.get("video_url")
    ]


def _set_outliers(clips: list[dict], compared: list[dict]) -> None:
    """Avvikartalet: visningar per dag delat med medianen i compared. None utan datum och under MIN_VIEWS
    visningar; de klippen räknas ändå in i medianen, som ska visa det vanliga."""
    rates = [c["views_per_day"] for c in compared if c.get("views_per_day") is not None]
    median = statistics.median(rates) if rates else 0
    for c in clips:
        rate = c.get("views_per_day")
        counts = rate is not None and median and (c.get("views") or 0) >= MIN_VIEWS
        c["outlier"] = rate / median if counts else None


def _with_outliers(clips: list[dict]) -> list[dict]:
    """Räkna avvikare och sortera med de största först, klipp utan avvikartal sist.

    Twitch och Content Rewards jämförs inom sin källa, egna länkar med alla klipp i listan."""
    for source in {c["source"] for c in clips}:
        group = [c for c in clips if c["source"] == source]
        _set_outliers(group, clips if source == "link" else group)
    return sorted(
        clips, key=lambda c: c["outlier"] if c["outlier"] is not None else -1, reverse=True
    )


def collect(profile: Profile | str) -> list[dict]:
    """Hämta Twitch- och Content Rewards-klippen, räkna avvikare och spara. Länkar och mätningar behålls."""
    profile = _profile(profile)
    fresh = _twitch(profile) + _content_rewards(profile)
    with LOCK:
        old = {c["id"]: c for c in load(profile)}
        for clip in fresh:
            _keep(clip, old.get(clip["id"], {}))
        links = [c for c in old.values() if c.get("source") == "link"]
        clips = _with_outliers(fresh + links)
        save(profile, clips)
    return clips


def _keep(clip: dict, saved: dict) -> None:
    """Ta med det som bara den sparade listan vet: mätningen, och platsen när den nya saknar plats."""
    clip.update({key: saved[key] for key in KEEP if key in saved})
    clip.update({key: saved[key] for key in KEEP_IF_MISSING if clip.get(key) is None and saved.get(key)})


def _posted(info: dict) -> datetime | None:
    if info.get("timestamp"):
        return datetime.fromtimestamp(info["timestamp"], UTC)
    if info.get("upload_date"):
        return datetime.strptime(info["upload_date"], "%Y%m%d").replace(tzinfo=UTC)
    return None


def add_link(profile: Profile | str, url: str, views: int | None = None) -> dict:
    """Lägg till ett klipp från en länk som Robin ger: hämta fakta och video med yt-dlp, mät klippet, leta
    stället i strömmen och radera videon. views ersätter visningarna från sidan."""
    check_link(url)
    from .taste import measure

    with (
        tempfile.TemporaryDirectory() as tmp
    ):  # videon raderas med mappen, även när mätningen misslyckas
        info, video = _download(url, tmp)
        taste = measure(video)
        place = locate.try_locate({"id": url, "path": video, "duration": info.get("duration")})
    count = views if views is not None else int(info.get("view_count") or 0)
    clip = _clip(
        url,
        count,
        _posted(info),
        source="link",
        title=info.get("title") or "",
        creator=info.get("uploader"),
        duration=info.get("duration"),
        location=place,
        measured=True,
        taste=taste,
    )
    with LOCK:
        clips = _with_outliers([c for c in load(profile) if c["id"] != url] + [clip])
        save(profile, clips)
    return clip


def check_link(url: str) -> None:
    """Bara https-länkar till TikTok, Instagram och YouTube, med underdomäner. Annars ValueError."""
    if not _allowed_link(urlparse(url)):
        raise ValueError("The link must be an https link to TikTok, Instagram or YouTube.")


def _allowed_link(parts) -> bool:
    if parts.scheme != "https":
        return False
    host = (parts.hostname or "").lower()
    return host in LINK_HOSTS or host.endswith(tuple(f".{allowed}" for allowed in LINK_HOSTS))


def _download(url: str, folder: str) -> tuple[dict, str]:
    """Fakta och video med yt-dlp: fakta först, så att en spellista nekas innan något hämtas."""
    import yt_dlp
    from yt_dlp.utils import DownloadError

    opts = {
        "outtmpl": f"{folder}/%(id)s.%(ext)s",
        "quiet": True,
        "noplaylist": True,
        "extract_flat": "in_playlist",  # kanaler och spellistor nekas utan att varje video hämtas
        "max_filesize": MAX_BYTES,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info.get("_type") in ("playlist", "multi_video"):
                raise ValueError("The link is a playlist. Add one video at a time.")
            info = ydl.process_ie_result(info, download=True)
            video = ydl.prepare_filename(info)
    except DownloadError as error:
        raise RuntimeError(f"Could not download the video from the link: {error}") from error
    if not Path(video).exists():
        raise RuntimeError("The video was not downloaded. It may be larger than 200 MB.")
    return info, video
