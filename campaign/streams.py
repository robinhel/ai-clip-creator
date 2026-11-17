"""De senaste streamsen (VOD:er av typen ARCHIVE) för en kanal, med ett betyg för vilken som är bäst att klippa.

Streamsen kommer via twitch-dl, så ingen Twitch-nyckel behövs för dem. Tittarklippen kräver Helix-nycklarna.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import UTC, datetime
from time import monotonic

from twitchdl import twitch

from clipsmith.config.loaders import load_secrets
from clipsmith.twitch.client import TwitchClient

log = logging.getLogger(__name__)

CACHE_S = 300
_cache: dict[str, tuple[float, list[dict]]] = {}  # kanal -> (när de hämtades, streams)
LOGIN = re.compile(r"[a-z0-9_]{1,25}")  # inloggningen hamnar i en GraphQL-fråga

QUERY = (
    '{{ user(login: "{login}") {{ login displayName videos(first: 8, type: ARCHIVE, sort: TIME) {{ edges {{ node {{ '
    "id title lengthSeconds createdAt game {{ name }} previewThumbnailURL(width: 640, height: 360) "
    "viewCount status }} }} }} }} }}"
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
            "view_count": int(node.get("viewCount") or 0),
            "live": node.get("status") == "RECORDING",
        }
        for node in (edge["node"] for edge in ((user.get("videos") or {}).get("edges") or []))
    ]
    counts = _viewer_clips(login, [s["created_at"] for s in found if s["created_at"]])
    for stream in found:
        stream["viewer_clips"] = None if counts is None else counts.get(stream["vod_id"], 0)
    _cache[login] = (monotonic(), found)
    return found


def _viewer_clips(login: str, created: list[str]) -> Counter[str] | None:
    """Klipp som tittarna gjort, per VOD, sedan den äldsta streamen. None när Helix inte går att fråga."""
    if not created:
        return Counter()
    secrets = load_secrets()
    if not (secrets.twitch_client_id and secrets.twitch_client_secret):
        log.warning("TWITCH_CLIENT_ID eller TWITCH_CLIENT_SECRET saknas: tittarklippen räknas inte för %s", login)
        return None
    try:
        with TwitchClient(secrets.twitch_client_id, secrets.twitch_client_secret) as client:
            # ended_at behövs: utan det slutar Helix en vecka efter started_at.
            clips = client.get_clips(client.get_user_id(login), started_at=datetime.fromisoformat(min(created)),
                                     ended_at=datetime.now(UTC))
    except Exception as error:  # nätverk, nycklar, Twitch: betyget räknas ändå, utan tittarklipp
        log.warning("Twitch Helix svarade inte för %s (%s): tittarklippen räknas inte", login, type(error).__name__)
        return None
    return Counter(clip.video_id for clip in clips if clip.video_id)


def _number(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def score(found: list[dict], games: set[str]) -> list[dict]:
    """Betyg 0–100 för en kanals streams: tittarklipp (50), visningar mot kanalens bästa (30), spel (20).
    Längden räknas inte. `games` är kampanjernas preferred_games i små bokstäver."""
    top = max((s["view_count"] for s in found), default=0)
    out = []
    for stream in found:
        clips, views = stream["viewer_clips"] or 0, stream["view_count"]
        parts = {"clips": round(50 * min(clips, 20) / 20), "views": round(30 * views / top) if top else 0,
                 "game": 20 if (stream["game"] or "").lower() in games else 0}
        reason = [text for text, points in (
            (f"{_number(clips)} tittarklipp", parts["clips"]),
            (f"{_number(views)} {'visning' if views == 1 else 'visningar'}", parts["views"]),
            (stream["game"], parts["game"])) if points]
        out.append({**stream, "score": sum(parts.values()), "score_parts": parts,
                    "reason": ", ".join(reason) or "Inga tydliga signaler"})
    return out


def recommend(found: list[dict]) -> None:
    """Märk högst en stream: bäst betyg bland de som inte pågår och saknar körning. Lika: den nyaste."""
    candidates = [s for s in found if not s["live"] and s["run_id"] is None]
    best = max(candidates, key=lambda s: (s["score"], s["created_at"] or ""), default=None)
    for stream in found:
        stream["recommended"] = stream is best and stream["score"] > 0
