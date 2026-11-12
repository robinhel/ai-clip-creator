"""Fakta om en Twitch-VOD före körningen. Via twitch-dl, så ingen Twitch-nyckel behövs."""

from __future__ import annotations

from time import monotonic

from twitchdl import twitch

CACHE_S = 600  # en VOD som fortfarande sänds blir längre, så fakta får vara högst 10 minuter gamla
_cache: dict[str, tuple[float, dict]] = {}  # VOD-id -> (när de hämtades, fakta)


class VodNotFound(Exception):
    pass


def vod_facts(vod_id: str, fresh: bool = False) -> dict:
    """Titel, kanal, spel, längd, datum och bild. Nätverksfel går vidare till den som anropar.

    fresh=True hoppar över cachen, t.ex. när en körning startas och längden måste stämma.
    """
    if not vod_id.isdigit():  # id:t hamnar i en GraphQL-fråga
        raise VodNotFound(vod_id)
    hit = _cache.get(vod_id)
    if hit and not fresh and monotonic() - hit[0] < CACHE_S:
        return hit[1]
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
    _cache[vod_id] = (monotonic(), facts)
    return facts
