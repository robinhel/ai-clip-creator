"""Bygger och kontrollerar captions utan AI-anrop."""

from __future__ import annotations

import re

from .finish import hook_clean
from .profile import Profile

# Snabblägets rubrik (clipsmith selector._signal_picks). Robin skriver en egen innan klippet postas.
PLACEHOLDER = re.compile(r"\bMoment \d+:\d{2}:\d{2}\b")
# Alla problem från check_caption börjar så. "Captionen " är den svenska texten i klipp från före översättningen,
# som finns kvar i klipp.db.
PROBLEM_STARTS = ("Caption ", "Captionen ")


def headline_text(pick: dict) -> str:
    """Texten överst i bilden: Claudes headline, annars titeln. Rensad som i bilden (finish.hook_clean), så en
    headline med bara blanksteg, styrtecken eller emoji ger titeln. Snabblägets platshållare "Moment h:mm:ss"
    och en pick utan titel ger ingen text. Filnamnsreserven skickas inte hit, så den blir ingen text heller."""
    texts = [hook_clean(str(pick.get(key) or "")) for key in ("headline", "title")]
    text = next(filter(None, texts), "")
    return "" if PLACEHOLDER.search(text) else text


def _game_tag(game: str) -> str:
    return "#" + re.sub(r"[^a-z0-9]", "", game.lower())


def _starts_with(line: str, start: str) -> bool:
    """Raden börjar med orden, hela ord: "#ad #blizzardpartnerx" räknas inte."""
    return (line.strip().lower() + " ").startswith(start.strip().lower() + " ")


def _has(text: str, must: str) -> bool:
    """En tagg som @warcraft räknas bara som hela ordet, så @warcraftfan räknas inte. Annat, som en länk,
    räknas var det än står."""
    if must.startswith("@"):
        return re.search(rf"(?<![\w@]){re.escape(must)}(?!\w)", text, re.IGNORECASE) is not None
    return must.lower() in text.lower()


def build_caption(title: str, profile: Profile, game: str | None = None) -> str:
    """Rubrik + obligatorisk länk + hashtags, enligt profilens mall."""
    text = profile.caption_template.format(title=title.strip())
    for must in profile.caption_must_include:
        if not _has(text, must):
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
    placeholder = PLACEHOLDER.search(text)
    if placeholder:
        problems.append(f"Caption still has the placeholder {placeholder.group()}. Write your own text.")
    tag_line = profile.caption_tag_line
    must_text, where = text, ""
    if tag_line:
        first, *rest = text.strip().splitlines() or [""]
        if _starts_with(first, tag_line) or not any(_starts_with(line, tag_line) for line in rest):
            problems.append(f"Caption is missing {tag_line!r} on its own line after the text.")
        if re.search(r"#\w", first):
            problems.append(f"Caption has hashtags on the first line. They belong after {tag_line!r}.")
        must_text, where = first, " on the first line"  # taggar som @warcraft hör till texten
    if profile.caption_max_extra_hashtags is not None:
        own = {tag.lower() for tag in tag_line.split()}
        extra = [tag for tag in re.findall(r"#\w+", text) if tag.lower() not in own]
        if len(extra) > profile.caption_max_extra_hashtags:
            problems.append(f"Caption has {len(extra)} hashtags besides {tag_line or 'the required ones'}. "
                            f"The campaign allows at most {profile.caption_max_extra_hashtags}.")
    problems += [
        f"Caption is missing {must!r}{where}."
        for must in profile.caption_must_include
        if not _has(must_text, must)
    ]
    problems += [
        f"Caption is missing the hashtag {tag}." for tag in profile.hashtags if tag.lower() not in low
    ]
    return problems
