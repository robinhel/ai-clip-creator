"""Bygger och kontrollerar captions utan AI-anrop."""

from __future__ import annotations

import re

from .profile import Profile


def _game_tag(game: str) -> str:
    return "#" + re.sub(r"[^a-z0-9]", "", game.lower())


def _starts_with(line: str, start: str) -> bool:
    """Raden börjar med orden, hela ord: "#ad #blizzardpartnerx" räknas inte."""
    return (line.strip().lower() + " ").startswith(start.strip().lower() + " ")


def build_caption(title: str, profile: Profile, game: str | None = None) -> str:
    """Rubrik + obligatorisk länk + hashtags, enligt profilens mall."""
    text = profile.caption_template.format(title=title.strip())
    for must in profile.caption_must_include:
        if must.lower() not in text.lower():
            text = f"{text} {must}"
    tags = list(profile.hashtags)
    if game and profile.game_hashtag and len(_game_tag(game)) > 1:
        tags.append(_game_tag(game))
    seen: set[str] = set()
    unique = [t for t in tags if not (t.lower() in seen or seen.add(t.lower()))]
    if profile.caption_tag_line:  # texten på första raden, taggraden och hashtags på raden efter
        return f"{text.strip()}\n{' '.join([profile.caption_tag_line, *unique])}"
    return f"{text} {' '.join(unique)}".strip()


def check_caption(text: str, profile: Profile) -> list[str]:
    """Lista det som saknas i en caption enligt kampanjens regler."""
    low = text.lower()
    problems = []
    tag_line = profile.caption_tag_line
    if tag_line:
        first, *rest = text.strip().splitlines() or [""]
        if _starts_with(first, tag_line) or not any(_starts_with(line, tag_line) for line in rest):
            problems.append(f"Captionen saknar {tag_line!r} på en egen rad efter texten.")
    if profile.caption_max_extra_hashtags is not None:
        own = {tag.lower() for tag in tag_line.split()}
        extra = [tag for tag in re.findall(r"#\w+", text) if tag.lower() not in own]
        if len(extra) > profile.caption_max_extra_hashtags:
            problems.append(f"Captionen har {len(extra)} hashtags utöver {tag_line or 'de obligatoriska'}. "
                            f"Kampanjen tillåter högst {profile.caption_max_extra_hashtags}.")
    problems += [
        f"Captionen saknar {must!r}."
        for must in profile.caption_must_include
        if must.lower() not in low
    ]
    problems += [
        f"Captionen saknar hashtaggen {tag}." for tag in profile.hashtags if tag.lower() not in low
    ]
    return problems
