"""De senaste streamsen (VOD:er av typen ARCHIVE) för en kanal. Via twitch-dl, så ingen Twitch-nyckel behövs."""

from __future__ import annotations

import re
from time import monotonic

from twitchdl import twitch

CACHE_S = 300
_cache: dict[str, tuple[float, list[dict]]] = {}  # kanal -> (när de hämtades, streams)
LOGIN = re.compile(r"[a-z0-9_]{1,25}")  # inloggningen hamnar i en GraphQL-fråga

QUERY = (
    '{{ user(login: "{login}") {{ login displayName videos(first: 8, type: ARCHIVE, sort: TIME) {{ edges {{ node {{ '
    "id title lengthSeconds createdAt game {{ name }} previewThumbnailURL(width: 640, height: 360) }} }} }} }} }}"
)


def _thumbnail(url: str | None) -> str | None:
    """Twitch ger en platshållare (/_404/) medan streamen pågår eller bearbetas. Den räknas som ingen bild."""
    return None if not url or "/_404/" in url else url


def channel_streams(login: str) -> list[dict]:
    """Högst 8 streams, nyast först. En kanal som inte finns ger en tom lista. Nätverksfel går vidare."""
    login = login.lower()
    if not LOGIN.fullmatch(login):
        return []
    hit = _cache.get(login)
    if hit and monotonic() - hit[0] < CACHE_S:
        return hit[1]
    user = twitch.gql_query(QUERY.format(login=login))["data"]["user"] or {}
    found = [
        {
            "vod_id": str(node["id"]),
            "channel": (user.get("login") or login).lower(),
            "channel_name": user.get("displayName") or user.get("login") or login,
            "title": node.get("title") or "",
            "game": (node.get("game") or {}).get("name"),
            "duration_s": int(node.get("lengthSeconds") or 0),
            "created_at": node.get("createdAt"),
            "thumbnail_url": _thumbnail(node.get("previewThumbnailURL")),
        }
        for node in (edge["node"] for edge in ((user.get("videos") or {}).get("edges") or []))
    ]
    _cache[login] = (monotonic(), found)
    return found
