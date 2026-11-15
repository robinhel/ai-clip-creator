"""LLM prompt templates and candidate-message builders.

Ersätter src/clipsmith/llm/prompts.py i din fork (setup_klipp.py kopierar in den).
Originalet var skrivet för en spansk streamer och klipp på cirka 150 sekunder.
Den här versionen är för engelska gaming-streams och korta klipp.

Alla namn och funktioner är desamma som i originalet, så resten av clipsmith
fungerar som förut. Fältet heter fortfarande `title_es`, men innehåller nu en
engelsk rubrik. Byt namn på fältet i fas 2 (se docs/klipp/OMBYGGNAD.md).

Längderna nedan ska stämma med clip.min_seconds och clip.max_seconds i config.
"""

from __future__ import annotations

from ..models.candidates import CandidateMoment

CLIP_MIN_S = 15
CLIP_MAX_S = 45

_STREAM_INTRO = f"""\
You select moments from an English-language Twitch gaming stream for short vertical clips
(TikTok, YouTube Shorts, Instagram Reels).

You get one candidate moment: viewer and audio signals plus the transcript around it.
Decide whether it works as a standalone clip of {CLIP_MIN_S} to {CLIP_MAX_S} seconds.

A good clip:
- is one self-contained moment that a stranger understands without context
- hooks in the first 2 seconds: start right before the action or the line, not on small talk
- ends right after the payoff or the streamer's reaction, not later
- is carried by the streamer's reaction: clutch plays, close escapes, funny fails, rage,
  jump scares, honest hot takes, banter with chat

Reject (include: false) dead air, menu or loading time, mid-story filler, and anything that
needs earlier context to make sense. Prefer fewer, better clips.

"""  # nosec B608 - prompttext till Claude, ingen SQL

# The answer's JSON and rules. A campaign's own instructions (llm.instructions) replace the intro and
# _TRANSCRIPT_RULE.
_ANSWER_HEAD = f"""\
Respond ONLY with a valid JSON object matching this schema (no markdown, no extra text):
{{
  "include": <bool>,           // true = make a clip, false = skip
  "start_offset_s": <number>,  // VOD seconds where the clip starts
  "end_offset_s": <number>,    // VOD seconds where the clip ends ({CLIP_MIN_S}-{CLIP_MAX_S} s after start)
  "title_es": <string>,        // 3-7 word English title, no hashtags, no spoilers
  "reason": <string>,          // 1-2 sentences explaining your decision
  "score": <integer>           // 0-99: hook, flow and chance to go viral on TikTok together
}}

Rules:
- Times are absolute VOD seconds. The transcript lines show offsets relative to the candidate center.
"""

# Twitch only: a campaign's own instructions say how to choose start and end.
_TRANSCRIPT_RULE = "- Choose start and end from what is said in the transcript. Do not just centre a fixed window.\n"

_ANSWER_TAIL = """\
- If include is false, still fill start_offset_s, end_offset_s and title_es with a best estimate.
- score is one overall grade: how strong the hook is, how well the clip flows, and how likely it
  is to go viral on TikTok. 90+ is rare and exceptional, 50 is average, below 30 is weak.\
"""

ANSWER_FORMAT = _ANSWER_HEAD + _ANSWER_TAIL
SYSTEM_PROMPT = _STREAM_INTRO + _ANSWER_HEAD + _TRANSCRIPT_RULE + _ANSWER_TAIL

SYSTEM_PROMPT_V2 = f"""\
You select moments from an English-language Twitch gaming stream for TikTok/Shorts/Reels.

Given one candidate with viewer signals and transcript, decide if it works as a standalone
{CLIP_MIN_S}-{CLIP_MAX_S} s clip.

Reply ONLY with valid JSON (no markdown):
{{"include":<bool>,"start_offset_s":<n>,"end_offset_s":<n>,"title_es":<str 3-7 English words>,"reason":<str 1 sentence>,"score":<int 0-99 hook+flow+TikTok viral chance>}}

Include ONLY moments with a clear hook in the first 2 seconds and a payoff or strong reaction.
Start right before the action, end right after the reaction.
Skip: dead air, filler, anything needing prior context.\
"""  # nosec B608 - prompttext till Claude, ingen SQL


def get_system_prompt(version: str = "v1", instructions: str = "") -> str:
    """Return the system prompt for the given version string, or the campaign's own instructions
    followed by the answer format."""
    if instructions.strip():
        return f"{instructions.strip()}\n\n{ANSWER_FORMAT}"
    return SYSTEM_PROMPT_V2 if version == "v2" else SYSTEM_PROMPT


# JSON schema returned by the LLM — used for validation and provider prompts.
CLIP_PICK_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "include": {
            "type": "boolean",
            "description": "true = make a clip from this moment, false = skip it",
        },
        "start_offset_s": {
            "type": "number",
            "description": "Seconds into the VOD where the clip should start",
        },
        "end_offset_s": {
            "type": "number",
            "description": (
                f"Seconds into the VOD where the clip should end "
                f"({CLIP_MIN_S}-{CLIP_MAX_S} s after start)"
            ),
        },
        "title_es": {
            "type": "string",
            "description": "3-7 word English title for TikTok/Shorts (catchy, no spoilers)",
        },
        "reason": {
            "type": "string",
            "description": "1-2 sentence explanation of why this moment is (or isn't) clip-worthy",
        },
        "score": {
            "type": "integer",
            "minimum": 0,
            "maximum": 99,
            "description": (
                "One overall grade 0-99 for hook, flow and chance to go viral on TikTok"
            ),
        },
    },
    "required": ["include", "start_offset_s", "end_offset_s", "title_es", "reason", "score"],
    "additionalProperties": False,
}


def build_candidate_prompt(transcript_window: str, candidate: CandidateMoment) -> str:
    """Build the per-candidate user message sent to any LLM provider."""
    signals = "\n".join(f"- {r}" for r in candidate.reasons)
    return (
        f"## Candidate moment\n"
        f"Center: t={candidate.t_center:.1f}s (VOD seconds)\n"
        f"Score: {candidate.score:.1f}\n"
        f"Signal sources: {', '.join(candidate.sources)}\n\n"
        f"### Viewer signals\n{signals}\n\n"
        f"### Transcript window (±60s around center)\n"
        f"{transcript_window}\n\n"
        f"Respond with JSON only."
    )


def build_stream_context(
    channel: str,
    vod_title: str,
    vod_duration: str,
    *,
    focus: str = "",
    clip_range: tuple[int, int] | None = None,
    game: str = "",
    language: str = "English",
    formats: list[dict] | None = None,
) -> str:
    """Build the stable per-VOD context block sent before candidate prompts.

    formats: the campaign's clip formats (id, name, description). Claude then answers with "format".
    """
    context = (
        f"Stream context:\n"
        f"Channel: {channel}\n"
        f"VOD title: {vod_title}\n"
        + (f"Game: {game}\n" if game else "")
        + f"Duration: {vod_duration}\n"
        f"Language: {language}\n"
        f"Platform: Twitch (clips for TikTok/YouTube Shorts)\n"
    )
    if language.lower() not in ("english", "en"):
        context += f"Write title_es in {language}, not English. This applies instead of the instructions.\n"
    if formats:
        lines = [f"- {f['id']}: {f.get('name') or f['id']}. {f.get('description') or ''}".rstrip()
                 for f in formats]
        context += (
            "Clip formats for this campaign. Only include moments that fit one of them, and add the "
            'field "format" to your JSON with the id of the format the clip fits best:\n'
            + "\n".join(lines) + "\n"
        )
    if clip_range:
        context += (
            f"Clip length for this run: end_offset_s must be {clip_range[0]}-{clip_range[1]} "
            f"seconds after start_offset_s. This applies instead of the clip length in the "
            f"instructions.\n"
        )
    if focus.strip():
        context += f"The user is especially looking for: {focus.strip()}\n"
    return context
