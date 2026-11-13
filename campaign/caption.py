"""Bygger och kontrollerar captions utan AI-anrop."""

from __future__ import annotations

import re

from .profile import Profile


def _game_tag(game: str) -> str:
    return "#" + re.sub(r"[^a-z0-9]", "", game.lower())


def _starts_with(text: str, start: str) -> bool:
    """Captionen börjar med orden, hela ord: "#ad #blizzardpartnerx" räknas inte."""
    return (text.strip().lower() + " ").startswith(start.strip().lower() + " ")


def build_caption(title: str, profile: Profile, game: str | None = None) -> str:
    """Rubrik + obligatorisk länk + hashtags, enligt profilens mall."""
    text = profile.caption_template.format(title=title.strip())
    if profile.caption_starts_with and not _starts_with(text, profile.caption_starts_with):
        text = f"{profile.caption_starts_with} {text}"
    for must in profile.caption_must_include:
        if must.lower() not in text.lower():
            text = f"{text} {must}"
    tags = list(profile.hashtags)
    if game and profile.game_hashtag and len(_game_tag(game)) > 1:
        tags.append(_game_tag(game))
    seen: set[str] = set()
    unique = [t for t in tags if not (t.lower() in seen or seen.add(t.lower()))]
    return f"{text} {' '.join(unique)}".strip()


def check_caption(text: str, profile: Profile) -> list[str]:
    """Lista det som saknas i en caption enligt kampanjens regler."""
    low = text.lower()
    problems = []
    if profile.caption_starts_with and not _starts_with(text, profile.caption_starts_with):
        problems.append(f"Captionen måste börja med {profile.caption_starts_with!r}.")
    problems += [
        f"Captionen saknar {must!r}."
        for must in profile.caption_must_include
        if must.lower() not in low
    ]
    problems += [
        f"Captionen saknar hashtaggen {tag}." for tag in profile.hashtags if tag.lower() not in low
    ]
    return problems
