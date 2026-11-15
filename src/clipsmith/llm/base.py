"""Shared types and Protocol for clip-picker LLM providers."""

from __future__ import annotations

import json
from dataclasses import InitVar, asdict, dataclass
from typing import TYPE_CHECKING, Protocol, Self, runtime_checkable

from ..models.candidates import CandidateMoment
from .prompts import (
    CLIP_PICK_JSON_SCHEMA,
    SYSTEM_PROMPT,
    build_candidate_prompt,
    build_stream_context,
)

__all__ = [
    "CLIP_PICK_JSON_SCHEMA",
    "SYSTEM_PROMPT",
    "ClipPick",
    "ClipPicker",
    "LLMUnavailable",
    "build_candidate_prompt",
    "build_stream_context",
]


class LLMUnavailable(RuntimeError):
    """The LLM cannot answer (wrong key, down, overloaded). Stops the run instead of giving no clips."""


SUBSCORES = ("hook", "engagement", "value", "shareability")


def _bounded(value: object, high: int) -> int:
    return min(max(round(float(value or 0)), 0), high)  # type: ignore[arg-type]


def _grade(value: object) -> int:
    """A part grade 0-25. One that is not a number becomes 0 instead of losing the candidate."""
    try:
        return _bounded(value, 25)
    except (TypeError, ValueError):
        return 0


def _seconds(value: object) -> float | None:
    return None if value is None else float(value)  # type: ignore[arg-type]


@dataclass
class ClipPick:
    include: bool
    start_offset_s: float
    end_offset_s: float
    title: str = ""
    reason: str = ""
    score: int = (
        0  # 0-100. v2: the subscores summed, then calibrated within the VOD. 0 for old answers.
    )
    format: str | None = None  # id of the campaign format the clip fits, when formats were given
    hook: int = 0  # 0-25 each (v2). 0 when the answer had none.
    engagement: int = 0
    value: int = 0
    shareability: int = 0
    payoff_start_s: float | None = None  # VOD seconds of the payoff, when Claude gave it
    payoff_end_s: float | None = None
    if TYPE_CHECKING:

        @property
        def title_es(self) -> str: ...  # the old name of title; set as a property below the class

    else:
        title_es: InitVar[str] = ""  # the old name of title, still accepted by the constructor

    def __post_init__(self, title_es: str) -> None:  # type: ignore[override]
        self.title = self.title or title_es

    @property
    def duration_s(self) -> float:
        return self.end_offset_s - self.start_offset_s

    def to_dict(self) -> dict:
        # title_es too: api/worker.py and older tools still read it from picks.json
        return {**asdict(self), "title_es": self.title}

    @classmethod
    def from_dict(cls: type[Self], d: dict) -> Self:
        subscores = {key: _grade(d.get(key)) for key in SUBSCORES}
        score = d.get("score")  # a saved score is the calibrated one; keep it
        return cls(
            include=bool(d.get("include", False)),
            start_offset_s=float(d.get("start_offset_s", 0.0)),
            end_offset_s=float(d.get("end_offset_s", 0.0)),
            title=str(d.get("title") or d.get("title_es") or ""),
            reason=str(d.get("reason", "")),
            score=_bounded(sum(subscores.values()) if score is None else score, 100),
            format=str(d["format"]) if d.get("format") else None,
            payoff_start_s=_seconds(d.get("payoff_start_s")),
            payoff_end_s=_seconds(d.get("payoff_end_s")),
            hook=subscores["hook"],
            engagement=subscores["engagement"],
            value=subscores["value"],
            shareability=subscores["shareability"],
        )

    @classmethod
    def from_json(cls: type[Self], text: str) -> Self:
        # Strip markdown code fences if the LLM wrapped in ```json...```
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(line for line in lines if not line.startswith("```")).strip()
        # Text after the JSON object (a short explanation) is ignored.
        start = text.find("{")
        obj, _ = json.JSONDecoder().raw_decode(text, max(start, 0))
        return cls.from_dict(obj)


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


# Read access under the old name, set after @dataclass so the constructor still takes title_es=.
ClipPick.title_es = property(lambda self: self.title)  # type: ignore[method-assign,assignment,misc]
