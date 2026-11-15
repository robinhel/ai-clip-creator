"""Sliding-window chat density scoring, separated for testability."""

from __future__ import annotations

import re

from ..models.chat import ChatMessage


def _reaction_pattern(words: list[str]) -> re.Pattern[str] | None:
    """Whole words, ignoring case: "w" matches "W" and "w!" but not "wow". "??" matches anywhere."""
    parts = [
        ("(?<!\\w)" if w[0].isalnum() else "") + re.escape(w) + ("(?!\\w)" if w[-1].isalnum() else "")
        for w in (w.strip() for w in words) if w
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
) -> list[tuple[float, float]]:
    """Return (t_center, score) for windows that exceed peak_multiplier × baseline.

    Score = messages_in_window + 0.5 × hype_emotes_in_window
    + reaction_weight × messages_with_a_reaction_word, normalised by
    baseline, so higher-than-baseline windows get a proportional score boost.
    """
    if not messages:
        return []
    pattern = _reaction_pattern(reaction_words or []) if reaction_weight else None
    reacts = {id(m): bool(pattern.search(m.message)) for m in messages} if pattern else {}

    times = [m.time_in_seconds for m in messages]
    t_min, t_max = min(times), max(times)
    total_duration = max(t_max - t_min, 1.0)

    # Baseline: average messages per window_s across the whole VOD.
    baseline = len(messages) * window_s / total_duration

    if baseline < 1.0:
        baseline = 1.0  # avoid div-by-zero for very sparse chats

    results: list[tuple[float, float]] = []
    t = t_min
    while t <= t_max:
        window_msgs = [m for m in messages if t <= m.time_in_seconds < t + window_s]
        count = len(window_msgs)
        hype = sum(m.hype_emote_count for m in window_msgs)
        reactions = sum(1 for m in window_msgs if reacts.get(id(m)))
        raw_score = count + 0.5 * hype + reaction_weight * reactions
        ratio = raw_score / baseline

        if ratio >= peak_multiplier:
            center = t + window_s / 2
            results.append((center, raw_score))

        t += step_s

    return results
