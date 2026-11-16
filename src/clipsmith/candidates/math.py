"""Sliding-window chat density scoring, separated for testability."""

from __future__ import annotations

import re
from bisect import bisect_left
from itertools import accumulate

from ..models.chat import ChatMessage

_REPEATS = re.compile(r"([a-z])\1+", re.IGNORECASE)
_LAUGH = re.compile(r"(?<!\w)a?(?:ha){2,}h?(?!\w)", re.IGNORECASE)


def _squeeze(text: str) -> str:
    """Chat spam as one word: "WWWWWW" and "wWw" become "w", "LMAOOOO" "lmao", "ahahah" "haha"."""
    return _LAUGH.sub("haha", _REPEATS.sub(r"\1", text))


def _reaction_pattern(words: list[str]) -> re.Pattern[str] | None:
    """Whole words, ignoring case: "w" matches "W" and "w!" but not "wow". "??" matches anywhere."""
    parts = [
        ("(?<!\\w)" if w[0].isalnum() else "")
        + re.escape(w)
        + ("(?!\\w)" if w[-1].isalnum() else "")
        for w in (_squeeze(w.strip()) for w in words)
        if w
    ]
    return re.compile("|".join(parts), re.IGNORECASE) if parts else None


def compute_density_scores(
    messages: list[ChatMessage],
    *,
    window_s: float = 15.0,
    peak_multiplier: float = 4.0,
    step_s: float = 5.0,
    reaction_words: list[str] | None = None,
    reaction_weight: float = 0.0,
    author_cap: float | None = None,
    surge_gain: float = 0.0,
) -> list[tuple[float, float]]:
    """Return (t_center, score) for windows that exceed peak_multiplier × baseline.

    Score = messages_in_window + 0.5 × hype_emotes_in_window
    + reaction_weight × messages_with_a_reaction_word, normalised by
    baseline, so higher-than-baseline windows get a proportional score boost.

    author_cap: one sender adds at most this much to a window, so one spammer is not a hot chat.
    Messages without an author are not capped.
    surge_gain: the rise over the previous window counts this much extra, so a sudden jump
    scores higher than the same level reached slowly. The defaults change nothing.
    """
    if not messages:
        return []
    ordered = sorted(messages, key=lambda m: m.time_in_seconds)
    times = [m.time_in_seconds for m in ordered]
    pattern = _reaction_pattern(reaction_words or []) if reaction_weight else None
    # Running totals, so each window is two bisects instead of a pass over the whole chat.
    hype = [0, *accumulate(m.hype_emote_count for m in ordered)]
    if pattern:
        reacts = [0, *accumulate(int(bool(pattern.search(_squeeze(m.message)))) for m in ordered)]
    else:
        reacts = [0] * (len(ordered) + 1)

    t_min, t_max = times[0], times[-1]
    total_duration = max(t_max - t_min, 1.0)

    # Baseline: average score per window_s across the whole VOD, reactions included,
    # so reactions that are common all stream long do not lower the bar everywhere.
    baseline = (len(messages) + reaction_weight * reacts[-1]) * window_s / total_duration

    if baseline < 1.0:
        baseline = 1.0  # avoid div-by-zero for very sparse chats

    def window_score(start: float) -> float:
        lo, hi = bisect_left(times, start), bisect_left(times, start + window_s)
        if author_cap is None:
            return hi - lo + 0.5 * (hype[hi] - hype[lo]) + reaction_weight * (reacts[hi] - reacts[lo])
        per_author: dict[str, float] = {}
        free = 0.0
        for i in range(lo, hi):
            weight = (
                1
                + 0.5 * ordered[i].hype_emote_count
                + reaction_weight * (reacts[i + 1] - reacts[i])
            )
            if ordered[i].author:
                per_author[ordered[i].author] = per_author.get(ordered[i].author, 0.0) + weight
            else:
                free += weight
        return free + sum(min(w, author_cap) for w in per_author.values())

    results: list[tuple[float, float]] = []
    t = t_min
    while t <= t_max:
        raw_score = window_score(t)
        if surge_gain:
            raw_score += surge_gain * max(0.0, raw_score - window_score(t - window_s))
        ratio = raw_score / baseline

        if ratio >= peak_multiplier:
            center = t + window_s / 2
            results.append((center, raw_score))

        t += step_s

    return results
