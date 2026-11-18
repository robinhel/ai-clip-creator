"""Mått i Statistik som appen räknar själv: om ett inlägg nådde kampanjens gräns inom 7 dagar, och klippets
längd. Robin skriver in de andra måtten för hand från TikTok Studio."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .cliplog import POST_STATUSES, ClipLog
from .schedule import clip_seconds


def _moment(value: str) -> datetime:
    """En ISO-tid som _now() skriver den. Utan tidszon räknas den som UTC."""
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def reached_min_7d(
    created: str | None, views_history: list[tuple[int, str]], min_views: int
) -> bool | None:
    """Nådde inlägget min_views inom 7 dagar från created? True: ett sparat värde senast dag 7 når gränsen.
    False: ett värde från dag 7 eller senare ligger under gränsen. True vinner om båda gäller. None när
    det inte går att säga än, när inget är sparat eller när kampanjen saknar gräns."""
    if min_views <= 0 or not created:
        return None
    deadline = _moment(created) + timedelta(days=7)
    history = [(views, _moment(at)) for views, at in views_history]
    in_time = [views for views, at in history if at <= deadline]  # senast dag 7
    after = [views for views, at in history if at >= deadline]  # dag 7 eller senare
    if max(in_time, default=0) >= min_views:
        return True
    if min(after, default=min_views) < min_views:
        return False
    return None


def _times(clip: dict) -> float | None:
    """Klippets längd enligt loggens tider, None när de saknas."""
    if clip["start_s"] is None or clip["end_s"] is None:
        return None
    return clip["end_s"] - clip["start_s"]


def clip_length(log: ClipLog, clip: dict) -> float | None:
    """Klippets längd i sekunder, för ett klipp med inlägg. Räknas en gång ur filen (eller loggens tider när
    filen saknas eller inte går att läsa) och sparas. Utan ffprobe: loggens tider, men inget sparas. None när
    båda saknas; då sparas inget och filen prövas igen nästa gång. Ett klipp utan inlägg: radens värde."""
    if clip["length_s"] is not None or clip["status"] not in POST_STATUSES:
        return clip["length_s"]
    try:
        length = clip_seconds(clip, Path(clip["file"])) if clip["file"] else _times(clip)
    except SystemExit:  # ffprobe saknas
        return _times(clip)
    except RuntimeError:  # filen går inte att läsa och tiderna saknas
        return None
    if length is not None:
        log.set_length(clip["id"], length, clip["updated"])
    return length
