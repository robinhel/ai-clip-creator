"""Bildgranskning: Claude tittar på några bildrutor ur klippet."""

from __future__ import annotations

import base64
from pathlib import Path

from .brain import Brain
from .media import grab_frames
from .profile import Profile

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "facecam_visible": {
            "type": "boolean",
            "description": "True only if the streamer's face (webcam) is clearly visible in every frame.",
        },
        "text_visible": {
            "type": "boolean",
            "description": "True only if the required on-screen text is fully readable in every frame.",
        },
        "overlay_visible": {
            "type": "boolean",
            "description": "True if a channel overlay or branding graphic is visible.",
        },
        "problems": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short, concrete visual problems. Empty if none.",
        },
    },
    "required": ["facecam_visible", "text_visible", "overlay_visible", "problems"],
}

SYSTEM = (
    "You check frames from a vertical short-form clip made from a Twitch stream. "
    "Report only what you can see. If you are unsure whether something is visible, answer false."
)


def review_clip(
    clip: Path, profile: Profile, brain: Brain, model: str, frames_dir: Path, count: int = 4
) -> dict:
    """Returnerar Claudes svar enligt REVIEW_SCHEMA."""
    frames = grab_frames(clip, frames_dir, count=count)
    content: list[dict] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.standard_b64encode(frame.read_bytes()).decode("ascii"),
            },
        }
        for frame in frames
    ]
    content.append(
        {
            "type": "text",
            "text": (
                f"These are {len(frames)} frames from one clip, in order.\n"
                f"Streamer: {profile.streamer}.\n"
                f"Required on-screen text: {profile.text!r}.\n"
                "Check: (1) the streamer's facecam is visible in every frame, "
                "(2) the required text is readable in every frame, "
                "(3) a channel overlay or branding graphic is visible, "
                "(4) nothing looks broken: black bars, a cut-off face, text covering the face, "
                "a stretched picture."
            ),
        }
    )
    return brain.ask(
        purpose="granska",
        model=model,
        system=SYSTEM,
        content=content,
        schema=REVIEW_SCHEMA,
        max_tokens=600,
    )
