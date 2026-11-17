"""Bildgranskning: Claude tittar på några bildrutor ur klippet."""

from __future__ import annotations

import base64
from pathlib import Path

from . import profile_edit
from .brain import Brain
from .media import grab_frames
from .profile import Profile

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "facecam_visible": {
            "type": "boolean",
            "description": "True if the streamer (a real person on their webcam) is visible in most frames.",
        },
        "text_visible": {
            "type": "boolean",
            "description": "True only if the required on-screen text is present and readable in every frame.",
        },
        "overlay_visible": {
            "type": "boolean",
            "description": "True if a channel overlay or branding graphic is visible.",
        },
        "face_covered": {
            "type": "boolean",
            "description": "True only if text or graphics cover the streamer's face in most frames.",
        },
        "notes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Other short visual observations that are not covered above. Empty if none.",
        },
    },
    "required": ["facecam_visible", "text_visible", "overlay_visible", "face_covered", "notes"],
}

SYSTEM = (
    "You check frames from a vertical short-form clip made from a Twitch stream. "
    "Report only what you can see. If you are unsure whether something is visible, answer false."
)


def stacked_split(profile: Profile, root: Path) -> float | None:
    """Andelen av höjden som streamerns kamera har överst, när kampanjens clipsmith-config är stacked."""
    try:
        reframe = profile_edit.read_config(profile_edit.config_file(root, profile.clipsmith_config)).reframe
    except ValueError:
        return None  # ingen eller trasig config: granska utan layout
    return reframe.split_ratio if reframe.mode == "stacked" else None


def _layout(split_ratio: float | None) -> str:
    if split_ratio is None:
        return ""
    top = round(split_ratio * 100)
    return (
        f"Layout: the top {top}% of each frame is the streamer's webcam. "
        f"The bottom {100 - top}% is the game. "
        "Judge the facecam only from the top panel. Text or screens inside the game "
        "(for example 'CAM DISCONNECTED', menus, black or static screens) belong to the game "
        "and say nothing about the streamer's webcam. Captions over the game are fine.\n"
    )


def review_clip(
    clip: Path, profile: Profile, brain: Brain, model: str, frames_dir: Path, count: int = 4,
    split_ratio: float | None = None,
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
                + _layout(split_ratio)
                + "Check: (1) the streamer is visible on the webcam in most frames, "
                "(2) the required text is readable in every frame, "
                "(3) a channel overlay or branding graphic is visible, "
                "(4) text or graphics cover the streamer's face. "
                "Put anything else you notice in notes."
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
