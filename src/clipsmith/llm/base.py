"""Shared types and Protocol for clip-picker LLM providers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Protocol, Self, runtime_checkable

from ..models.candidates import CandidateMoment
from .prompts import (
    CLIP_PICK_JSON_SCHEMA,
    SYSTEM_PROMPT,
    build_candidate_prompt,
    build_stream_context,
)

__all__ = [
    "ClipPick",
    "ClipPicker",
    "LLMUnavailable",
    "SYSTEM_PROMPT",
    "CLIP_PICK_JSON_SCHEMA",
    "build_candidate_prompt",
    "build_stream_context",
]


class LLMUnavailable(RuntimeError):
    """The LLM cannot answer (wrong key, down, overloaded). Stops the run instead of giving no clips."""


@dataclass
class ClipPick:
    include: bool
    start_offset_s: float
    end_offset_s: float
    title_es: str
    reason: str
    score: int = 0  # 0-99: hook, flow and chance to go viral on TikTok. 0 for old answers.
    format: str | None = None  # id of the campaign format the clip fits, when formats were given

    @property
    def duration_s(self) -> float:
        return self.end_offset_s - self.start_offset_s

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls: type[Self], d: dict) -> Self:
        return cls(
            include=bool(d.get("include", False)),
            start_offset_s=float(d.get("start_offset_s", 0.0)),
            end_offset_s=float(d.get("end_offset_s", 0.0)),
            title_es=str(d.get("title_es", "")),
            reason=str(d.get("reason", "")),
            score=min(max(round(float(d.get("score") or 0)), 0), 99),
            format=str(d["format"]) if d.get("format") else None,
        )

    @classmethod
    def from_json(cls: type[Self], text: str) -> Self:
        # Strip markdown code fences if the LLM wrapped in ```json...```
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(line for line in lines if not line.startswith("```")).strip()
        return cls.from_dict(json.loads(text))


@runtime_checkable
class ClipPicker(Protocol):
    def pick(
        self,
        transcript_window: str,
        candidate: CandidateMoment,
        stream_context: str,
    ) -> ClipPick | None:
        """Evaluate one candidate moment.

        Returns ClipPick with include=True if clip-worthy, include=False to skip,
        or None if the provider failed (caller should treat as skip).
        """
        ...
