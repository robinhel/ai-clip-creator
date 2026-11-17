"""Anthropic Claude provider with prompt caching.

The system prompt and stream-context block are marked cache_control=ephemeral,
so they are cached after the first call and reused for all subsequent candidates
within one VOD run — reducing cost ~10x on the stable prefix.
"""

from __future__ import annotations

import base64
import json
import logging
import subprocess  # nosec B404
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ..models.candidates import CandidateMoment
from ..telemetry import LLM_CALL_DURATION, llm_call_duration, llm_calls_total, tracer
from .base import SUBSCORES, ClipPick, LLMUnavailable, _bounded
from .prompts import RANK_PROMPT, build_candidate_prompt, get_system_prompt
from .retry import RetryConfig, build_retry

if TYPE_CHECKING:
    from campaign.budget import Budget

log = logging.getLogger(__name__)

# Seconds before each new try at 429, 5xx (529 overloaded) and network errors. Replaces retry_cfg's backoff.
WAITS_S = (5, 15, 30)


def _frame(video: Path, at: float, width: int = 512) -> bytes:
    """One small JPEG from the video at `at` seconds. Empty when the time is past the end."""
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-ss",
        f"{at:.2f}",
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:-2",
        "-q:v",
        "5",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, check=False)  # nosec B603 — fixed args, own path
    return result.stdout if result.returncode == 0 else b""  # ffmpeg fails when at is past the end


def _reason(exc: Exception) -> str:
    """Short Swedish cause of an Anthropic error, for the run's error text."""
    import anthropic

    if isinstance(exc, anthropic.APIConnectionError):
        return "Anthropic gick inte att nå (nätverket)"
    status = getattr(exc, "status_code", None)
    if status == 401:
        return "fel API-nyckel (401)"
    if status == 403:
        return "nyckeln får inte använda Claude (403)"
    if status == 429:
        return "för många anrop (429)"
    if status == 529:
        return "Claude är överbelastat (529)"
    body = getattr(exc, "body", None)
    message = body.get("error", {}).get("message") if isinstance(body, dict) else None
    return f"fel {status}, {message[:150]}" if message else f"fel {status}"


# Newer models (Sonnet 5.5) think first in their own block, about 800 tokens before a JSON answer of
# about 150. 1024 cut the JSON. Only the tokens used are paid for.
MAX_TOKENS = 4096


class AnthropicProvider:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "claude-sonnet-4-6",
        retry_cfg: RetryConfig | None = None,
        prompt_version: str = "v1",
        budget: Budget | None = None,
        instructions: str = "",
        frames: int = 0,
        video: Path | None = None,
        frame_span_s: float = 45.0,
        mode: str = "v2",
    ):
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for the Anthropic provider")
        try:
            import anthropic as _anthropic
        except ImportError as exc:
            raise ImportError("anthropic package required: pip install anthropic") from exc
        self._client = _anthropic.Anthropic(
            api_key=api_key, max_retries=0
        )  # WAITS_S does the retries
        self._api_error = _anthropic.APIError
        self._model = model
        self._retry_cfg = retry_cfg or RetryConfig()
        self._system_prompt = get_system_prompt(prompt_version, instructions, mode=mode)
        self._mode = mode
        self._budget = budget
        self._frames = frames
        self._video = video
        self._frame_span_s = frame_span_s

    def _frame_blocks(self, t_center: float) -> list[dict]:
        """A text naming the frame times, then the frames, spread evenly over the candidate's window."""
        if not self._frames or self._video is None:
            return []
        start = max(
            0.0, t_center - self._frame_span_s / 2
        )  # near the start the window moves, no repeats
        times = [start + self._frame_span_s * (i + 0.5) / self._frames for i in range(self._frames)]
        shots = [(at, _frame(self._video, at)) for at in times]
        shots = [(at, data) for at, data in shots if data]  # a time after the end gives no frame
        if not shots:
            return []
        label = ", ".join(f"t={at:.1f}s" for at, _ in shots)
        return [
            {"type": "text", "text": f"Frames from the video, in order, at {label} (VOD seconds):"}
        ] + [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.b64encode(data).decode("ascii"),
                },
            }
            for _, data in shots
        ]

    def _ask(self, system: str, content: list[dict], *, candidate_id: str, purpose: str) -> str:
        """One Claude call with retries; the cost goes to the budget. Returns the answer's text."""
        retry = build_retry(
            self._retry_cfg, candidate_id=candidate_id, provider="anthropic", waits=WAITS_S
        )
        response = None
        for attempt in retry:
            with attempt:
                response = self._client.messages.create(
                    model=self._model,
                    max_tokens=MAX_TOKENS,
                    system=[
                        {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
                    ],
                    messages=[{"role": "user", "content": content}],  # type: ignore[misc,list-item]
                )
        assert response is not None
        usage = response.usage
        log.debug(
            "Anthropic usage: input=%d cached_read=%d cached_write=%d output=%d",
            usage.input_tokens,
            getattr(usage, "cache_read_input_tokens", 0),
            getattr(usage, "cache_creation_input_tokens", 0),
            usage.output_tokens,
        )
        if self._budget is not None:
            self._budget.record(
                self._model,
                purpose,
                usage.input_tokens,
                usage.output_tokens,
                getattr(usage, "cache_read_input_tokens", 0) or 0,
                getattr(usage, "cache_creation_input_tokens", 0) or 0,
            )
        if getattr(response, "stop_reason", None) == "max_tokens":
            log.warning(
                "llm_cut provider=anthropic candidate=%s: svaret klipptes av vid max_tokens",
                candidate_id,
            )
        return next((b.text for b in response.content if b.type == "text"), "")  # type: ignore[union-attr]

    def rank(self, lines: list[str], stream_context: str) -> list[int] | None:
        """Final scores 0-100 for the VOD's accepted clips, compared side by side. None when it fails:
        the first scores stay, and the picks are kept."""
        # one line per clip: a line break in a title or the transcript would look like the next clip
        clips = "\n".join(f"{i}. {' '.join(line.split())}" for i, line in enumerate(lines, 1))
        try:
            if self._budget is not None:
                self._budget.check()  # over budget: no call, the picks are already paid for
            text = self._ask(
                RANK_PROMPT,
                [{"type": "text", "text": f"{stream_context}\n## Accepted clips\n{clips}"}],
                candidate_id="rank",
                purpose="kalibrera",
            )
            start = text.find("{")
            scores = json.JSONDecoder().raw_decode(text, max(start, 0))[0]["scores"]
            if len(scores) != len(lines):
                raise ValueError(f"{len(scores)} scores for {len(lines)} clips")
            return [_bounded(score, 100) for score in scores]
        except Exception as exc:  # noqa: BLE001 — any failure keeps the first scores
            log.warning("llm_rank_failed provider=anthropic error=%s", exc)
            return None

    def pick(
        self,
        transcript_window: str,
        candidate: CandidateMoment,
        stream_context: str,
    ) -> ClipPick | None:
        candidate_id = f"{candidate.t_center:.1f}"
        candidate_prompt = build_candidate_prompt(transcript_window, candidate)
        t0 = time.monotonic()
        with tracer.start_as_current_span(
            "llm.call",
            attributes={
                "provider": "anthropic",
                "model": self._model,
                "candidate_id": candidate_id,
            },
        ):
            if self._budget is not None:
                self._budget.check()  # BudgetExceeded stoppar körningen, fångas inte nedan
            try:
                images = self._frame_blocks(candidate.t_center)
                text = self._ask(
                    self._system_prompt,
                    [
                        {
                            "type": "text",
                            "text": stream_context,
                            "cache_control": {"type": "ephemeral"},
                        },
                        *images,
                        {"type": "text", "text": candidate_prompt},
                    ],
                    candidate_id=candidate_id,
                    purpose="valja",
                )
                pick = ClipPick.from_json(text)
                subscores = [getattr(pick, key) for key in SUBSCORES]
                # no part grades: Claude's own score stays
                if self._mode == "v2" and any(subscores):
                    pick.score = sum(subscores)
                elapsed = time.monotonic() - t0
                llm_call_duration.record(elapsed, {"provider": "anthropic"})
                LLM_CALL_DURATION.labels(provider="anthropic").observe(elapsed)
                llm_calls_total.add(
                    1, {"provider": "anthropic", "outcome": "include" if pick.include else "skip"}
                )
                log.info(
                    "llm_pick provider=anthropic candidate=%s include=%s elapsed_ms=%d",
                    candidate_id,
                    pick.include,
                    round(elapsed * 1000),
                )
                return pick
            except Exception as exc:
                log.warning(
                    "llm_failed provider=anthropic candidate=%s error=%s", candidate_id, exc
                )
                if isinstance(
                    exc, self._api_error
                ):  # key, account or Anthropic: every candidate would fail
                    raise LLMUnavailable(
                        f"Claude svarade inte: {_reason(exc)}. Kör igen när det fungerar."
                    ) from exc
                return None  # an answer that is not valid JSON: skip only this candidate
