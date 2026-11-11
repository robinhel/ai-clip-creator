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

SYSTEM_PROMPT = f"""\
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

Respond ONLY with a valid JSON object matching this schema (no markdown, no extra text):
{{
  "include": <bool>,           // true = make a clip, false = skip
  "start_offset_s": <number>,  // VOD seconds where the clip starts
  "end_offset_s": <number>,    // VOD seconds where the clip ends ({CLIP_MIN_S}-{CLIP_MAX_S} s after start)
  "title_es": <string>,        // 3-7 word English title, no hashtags, no spoilers
  "reason": <string>           // 1-2 sentences explaining your decision
}}

Rules:
- Times are absolute VOD seconds. The transcript lines show offsets relative to the candidate center.
- Choose start and end from what is said in the transcript. Do not just centre a fixed window.
- If include is false, still fill start_offset_s, end_offset_s and title_es with a best estimate.\
"""

SYSTEM_PROMPT_V2 = f"""\
You select moments from an English-language Twitch gaming stream for TikTok/Shorts/Reels.

Given one candidate with viewer signals and transcript, decide if it works as a standalone
{CLIP_MIN_S}-{CLIP_MAX_S} s clip.

Reply ONLY with valid JSON (no markdown):
{{"include":<bool>,"start_offset_s":<n>,"end_offset_s":<n>,"title_es":<str 3-7 English words>,"reason":<str 1 sentence>}}

Include ONLY moments with a clear hook in the first 2 seconds and a payoff or strong reaction.
Start right before the action, end right after the reaction.
Skip: dead air, filler, anything needing prior context.\
"""


def get_system_prompt(version: str = "v1") -> str:
    """Return the system prompt for the given version string."""
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
    },
    "required": ["include", "start_offset_s", "end_offset_s", "title_es", "reason"],
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


def build_stream_context(channel: str, vod_title: str, vod_duration: str) -> str:
    """Build the stable per-VOD context block sent before candidate prompts."""
    return (
        f"Stream context:\n"
        f"Channel: {channel}\n"
        f"VOD title: {vod_title}\n"
        f"Duration: {vod_duration}\n"
        f"Language: English\n"
        f"Platform: Twitch (clips for TikTok/YouTube Shorts)\n"
    )
