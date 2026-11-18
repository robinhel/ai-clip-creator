"""Mått i Statistik som appen räknar själv: om ett inlägg nådde kampanjens gräns inom 7 dagar, och klippets
längd. Robin skriver in de andra måtten för hand från TikTok Studio."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .cliplog import ClipLog
from .schedule import clip_seconds

WINDOW = timedelta(days=7)


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
    deadline = _moment(created) + WINDOW
    history = [(views, _moment(at)) for views, at in views_history]
    in_time = [views for views, at in history if at <= deadline]  # senast dag 7
    after = [views for views, at in history if at >= deadline]  # dag 7 eller senare
    if max(in_time, default=0) >= min_views:
        return True
    if min(after, default=min_views) < min_views:
        return False
    return None


def clip_length(log: ClipLog, clip: dict) -> float | None:
    """Klippets längd i sekunder. Räknas en gång ur filen (eller loggens tider när filen inte går att läsa)
    och sparas. None när båda saknas; då sparas inget och filen prövas igen nästa gång."""
    if clip["length_s"] is not None:
        return clip["length_s"]
    try:
        length = clip_seconds(clip, Path(clip["file"] or ""))
    except (
        RuntimeError,
        SystemExit,
    ):  # filen går inte att läsa och tiderna saknas, eller ffprobe saknas
        return None
    log.set_length(clip["id"], length)
    return length
