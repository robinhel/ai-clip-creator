"""Föreslå ny text: samma klipp på ett annat konto behöver en egen rubrik och caption (Content Rewards).

Claude via Brain, så att budgeten gäller. Captionen byggs av kampanjens mall (build_caption), så att taggraden,
must_include och hashtags följer kampanjens regler.
"""

from __future__ import annotations

from .brain import Brain
from .budget import BudgetExceeded, budget_from_settings
from .caption import build_caption, check_caption
from .cliplog import ClipLog
from .config import Settings
from .editor import _game
from .profile import load_profile
from .schedule import NAMES, Schedule, _norm

TRIES = 2  # ett svar som redan finns ger ett nytt försök, sedan 400
TITLE_MAX = 100  # YouTubes gräns; TikTok tar mer
SCHEMA = {"type": "object", "properties": {"title": {"type": "string", "description": "The new title, 3 to 10 words."}},
          "required": ["title"]}
SYSTEM = ("You write the title for a short vertical clip that is posted on several accounts. Each account needs its "
          "own wording for the same clip, so write a title that is clearly different from the ones already used, not "
          "the same words in another order. Stay true to what happens in the clip. Write 3 to 10 words in the "
          "language with code {language}. No hashtags, no @mentions, no quotes, no emojis, and never 'coming soon'.")


class VariantError(Exception):
    """Fel med svensk text och HTTP-status för webben."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def suggest(settings: Settings, clip: dict, platform: str, account: str) -> dict:
    """Ny rubrik och caption för klippet på kontot, olika texterna på klippets andra konton på plattformen."""
    try:
        profile = load_profile(clip["profile"], settings.profiles_dir)
    except SystemExit as error:
        raise VariantError("Kampanjen går inte att läsa.") from error
    brain = Brain(settings, budget_from_settings(settings))
    if not brain.available:
        raise VariantError("Claude-nyckeln saknas. Lägg in den under Inställningar.")
    uses = Schedule(settings)._uses(clip["id"], platform)
    uses.pop(account, None)
    taken = [text for use in uses.values() for text in use["texts"]]
    game = _game(ClipLog(settings.data_dir / "klipp.db"), clip)
    tried: list[str] = []
    for _ in range(TRIES):
        used = "\n".join(f"- {text}" for text in [*taken, *tried]) or "- none yet"
        content = (f"Clip title: {clip['title']}\nWhy the clip was picked: {clip['reason'] or '-'}\n"
                   f"Game: {game or '-'}\nPlatform: {NAMES[platform][1]}\n"
                   f"Titles and captions already used for this clip:\n{used}")
        try:
            answer = brain.ask(purpose="variant", model=settings.review_model,
                               system=SYSTEM.format(language=profile.language), content=content, schema=SCHEMA,
                               max_tokens=200)
        except BudgetExceeded as error:
            raise VariantError(str(error)) from error
        except Exception as error:  # nätverksfel, fel nyckel m.m.
            raise VariantError("Claude svarade inte. Försök igen om en stund.", 502) from error
        words = str(answer.get("title") or "").replace("<", "").replace(">", "").split()
        title = " ".join(w for w in words if not w.startswith(("#", "@")))[:TITLE_MAX].strip()
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
