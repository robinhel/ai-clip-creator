"""Föreslå ny text: samma klipp på ett annat konto behöver en egen rubrik och caption (Content Rewards), och en ny
version kan behöva en egen hook (texten överst i bilden).

Claude via Brain, så att budgeten gäller. Captionen byggs av kampanjens mall (build_caption), så att taggraden,
must_include och hashtags följer kampanjens regler.
"""

from __future__ import annotations

from typing import Any

from .brain import Brain
from .budget import BudgetExceeded, budget_from_settings
from .caption import build_caption, check_caption
from .cliplog import ClipLog
from .config import Settings
from .editor import _game
from .profile import Profile, load_profile
from .schedule import NAMES, Schedule, _norm
from .versions import hook_problem, hooks_used, plain_spaces

TRIES = 2  # ett svar som redan finns ger ett nytt försök, sedan 400
TITLE_MAX = 100  # YouTubes gräns; TikTok tar mer
SCHEMA = {"type": "object", "properties": {"title": {"type": "string", "description": "The new title, 3 to 10 words."}},
          "required": ["title"]}
SYSTEM = ("You write the title for a short vertical clip that is posted on several accounts. Each account needs its "
          "own wording for the same clip, so write a title that is clearly different from the ones already used, not "
          "the same words in another order. Stay true to what happens in the clip. Write 3 to 10 words in the "
          "language with code {language}. No hashtags, no @mentions, no quotes, no emojis, and never 'coming soon'.")
HOOK_SCHEMA = {"type": "object", "properties": {"hook": {"type": "string", "description": "The new hook, at most "
                                                         "about 60 characters."}}, "required": ["hook"]}
HOOK_SYSTEM = ("You write the hook: the text at the top of a short vertical clip, shown while it plays. The hook gives "
               "context or an opinion that makes people want to keep watching. It does not just describe what can be "
               "seen. The same clip is posted on several accounts, so write a hook that is clearly different from the "
               "hooks already used, not the same words in another order. Stay true to what happens in the clip. Write "
               "at most about 60 characters in the language with code {language}. No hashtags, no @mentions, no "
               "quotes, no emojis.")
QUOTES = "\"“”„«»"


class VariantError(Exception):
    """Fel med svensk text och HTTP-status för webben."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _clean(text: object, limit: int) -> str:
    """Claudes text utan < >, utan ord som börjar med # eller @, och högst limit tecken."""
    words = str(text or "").replace("<", "").replace(">", "").split()
    return " ".join(w for w in words if not w.startswith(("#", "@")))[:limit].strip()


def _ask(brain: Brain, **kwargs: Any) -> dict:
    """Brain.ask med svenska fel: budgeten 400, nätverksfel och fel nyckel 502."""
    try:
        return brain.ask(**kwargs)
    except BudgetExceeded as error:
        raise VariantError(str(error)) from error
    except Exception as error:  # nätverksfel, fel nyckel m.m.
        raise VariantError("Claude svarade inte. Försök igen om en stund.", 502) from error


def _setup(settings: Settings, clip: dict) -> tuple[Profile, Brain]:
    """Klippets kampanj och Claude. En kampanj som inte går att läsa, eller ingen nyckel, ger 400."""
    try:
        profile = load_profile(clip["profile"], settings.profiles_dir)
    except SystemExit as error:
        raise VariantError("Kampanjen går inte att läsa.") from error
    brain = Brain(settings, budget_from_settings(settings))
    if not brain.available:
        raise VariantError("Claude-nyckeln saknas. Lägg in den under Inställningar.")
    return profile, brain


def _about(clip: dict, game: str | None) -> str:
    return f"Clip title: {clip['title']}\nWhy the clip was picked: {clip['reason'] or '-'}\nGame: {game or '-'}\n"


def _listed(texts: list[str]) -> str:
    return "\n".join(f"- {text}" for text in texts) or "- none yet"


def suggest(settings: Settings, clip: dict, platform: str, account: str) -> dict:
    """Ny rubrik och caption för klippet på kontot, olika texterna på klippets andra konton på plattformen."""
    profile, brain = _setup(settings, clip)
    uses = Schedule(settings)._uses(clip["id"], platform)
    uses.pop(account, None)
    taken = [text for use in uses.values() for text in use["texts"]]
    game = _game(ClipLog(settings.data_dir / "klipp.db"), clip)
    tried: list[str] = []
    for _ in range(TRIES):
        content = (f"{_about(clip, game)}Platform: {NAMES[platform][1]}\n"
                   f"Titles and captions already used for this clip:\n{_listed([*taken, *tried])}")
        answer = _ask(brain, purpose="variant", model=settings.review_model,
                      system=SYSTEM.format(language=profile.language), content=content, schema=SCHEMA, max_tokens=200)
        title = _clean(answer.get("title"), TITLE_MAX)
        caption = build_caption(title, profile, game)
        if title and not {_norm(title), _norm(caption)} & {_norm(text) for text in taken}:
            break
        tried.append(title)
    else:
        raise VariantError("Claude föreslog en text som redan finns på ett annat konto. Försök igen.")
    problems = check_caption(caption, profile)
    if problems:
        raise VariantError(problems[0])
    return {"title": title, "caption": caption, "description": caption if platform == "youtube" else None}


def _clean_hook(answer: dict) -> str:
    """Hooken rensad som en rubrik, med vanliga blanksteg (som make) och utan citattecken runt."""
    return _clean(plain_spaces(str(answer.get("hook") or "")).strip(QUOTES), TITLE_MAX).strip(QUOTES).strip()


def _new_hook(hook: str, taken: list[str]) -> bool:
    """Hooken är inte tom, ryms i bilden och finns inte redan (samma jämförelse som schemat, _norm)."""
    return bool(hook) and hook_problem(hook) is None and _norm(hook) not in {_norm(text) for text in taken}


def suggest_hook(settings: Settings, clip: dict) -> dict:
    """Ny hook för en ny version av klippet (originalet, om klippet är en version). Den ryms i bilden och skiljer sig
    från hookarna på originalet och dess versioner."""
    log = ClipLog(settings.data_dir / "klipp.db")
    root = log.get(clip["version_of"]) if clip.get("version_of") else clip
    profile, brain = _setup(settings, root)
    taken = hooks_used(log, root)
    game = _game(log, root)
    tried: list[str] = []
    for _ in range(TRIES):
        content = f"{_about(root, game)}Hooks already used for this clip:\n{_listed([*taken, *tried])}"
        answer = _ask(brain, purpose="version_hook", model=settings.review_model,
                      system=HOOK_SYSTEM.format(language=profile.language), content=content, schema=HOOK_SCHEMA,
                      max_tokens=150)
        hook = _clean_hook(answer)
        if _new_hook(hook, taken):
            return {"hook": hook}
        tried.append(hook)
    raise VariantError("Claude föreslog en hook som redan finns eller inte ryms i bilden. Försök igen.")
