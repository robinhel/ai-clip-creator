"""Fakta om en Twitch-VOD före körningen. Via twitch-dl, så ingen Twitch-nyckel behövs."""

from __future__ import annotations

from twitchdl import twitch

_cache: dict[str, dict] = {}  # VOD-id -> fakta. Ändras inte, så de behövs bara hämtas en gång.


class VodNotFound(Exception):
    pass


def vod_facts(vod_id: str) -> dict:
    """Titel, kanal, spel, längd, datum och bild. Nätverksfel går vidare till den som anropar."""
    if not vod_id.isdigit():  # id:t hamnar i en GraphQL-fråga
        raise VodNotFound(vod_id)
    if vod_id in _cache:
        return _cache[vod_id]
    video = twitch.get_video(vod_id)
    if not video:
        raise VodNotFound(vod_id)
    extra = twitch.gql_query(
        f'{{ video(id: "{vod_id}") {{ previewThumbnailURL(width: 640, height: 360) '
        f"owner {{ login displayName }} }} }}"
    )["data"]["video"] or {}
    owner = extra.get("owner") or video.get("owner") or video.get("creator") or {}
    facts = {
        "vod_id": vod_id,
        "title": video.get("title") or "",
        "channel": (owner.get("login") or "").lower(),
        "channel_name": owner.get("displayName") or owner.get("login") or "",
        "game": (video.get("game") or {}).get("name"),
        "duration_s": int(video.get("lengthSeconds") or 0),
        "created_at": video.get("createdAt"),
        "thumbnail_url": extra.get("previewThumbnailURL"),
    }
    _cache[vod_id] = facts
    return facts
