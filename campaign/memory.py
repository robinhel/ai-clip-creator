"""Minnet för Claudes val: kampanjens klipp som nådde visningar, de som inte gjorde det, och Robins ja och nej.

Texten läggs till i focus, efter "The user is especially looking for:". Den ingår i det cachade kontextblocket
och kostar tokens i varje val, så den hålls kort: några titlar per grupp, nyast först.
"""

from __future__ import annotations

from .cliplog import POST_STATUSES, ClipLog

PER_GROUP = 3  # titlar per grupp
TITLE_MAX = 60  # tecken per titel


def _title(clip: dict) -> str:
    title = " ".join((clip["title"] or "").split())
    return f"'{title[:TITLE_MAX - 1] + '…' if len(title) > TITLE_MAX else title}'"


def selection_memory(log: ClipLog, profile: str, min_views: int = 2000) -> str:
    """En kort engelsk text om kampanjens tidigare klipp, eller tom när det inte finns några att lära av."""
    clips = [c for c in log.list() if c["profile"] == profile]
    newest = sorted(clips, key=lambda c: (c["updated"] or "", c["id"]), reverse=True)
    counted = [c for c in clips if c["status"] in POST_STATUSES and c["views"] is not None]
    hits = sorted((c for c in counted if c["views"] >= min_views), key=lambda c: -c["views"])[:PER_GROUP]
    misses = sorted((c for c in counted if c["views"] < min_views), key=lambda c: c["views"])[:PER_GROUP]
    approved = [c for c in newest if c["status"] == "godkand"
                or (c["status"] in POST_STATUSES and c["views"] is None)][:PER_GROUP]
    rejected = [c for c in newest if c["status"] == "nekad"][:PER_GROUP]

    parts = []
    if hits:
        parts.append(f"Reached {min_views} views: " + ", ".join(f"{_title(c)} ({c['views']})" for c in hits) + ".")
    if misses:
        parts.append(f"Stayed under {min_views} views: "
                     + ", ".join(f"{_title(c)} ({c['views']})" for c in misses) + ".")
    if approved:
        parts.append("The user approved: " + ", ".join(map(_title, approved)) + ".")
    if rejected:
        parts.append("The user rejected: " + ", ".join(map(_title, rejected)) + ".")
    if not parts:
        return ""
    return "moments like the earlier clips that worked. Earlier clips for this campaign, as a guide and not " \
           "as templates. " + " ".join(parts)
