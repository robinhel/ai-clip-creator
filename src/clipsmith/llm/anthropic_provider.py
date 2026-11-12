"""Anthropic Claude provider with prompt caching.

The system prompt and stream-context block are marked cache_control=ephemeral,
so they are cached after the first call and reused for all subsequent candidates
within one VOD run — reducing cost ~10x on the stable prefix.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from ..models.candidates import CandidateMoment
from ..telemetry import LLM_CALL_DURATION, llm_call_duration, llm_calls_total, tracer
from .base import ClipPick, LLMUnavailable
from .prompts import get_system_prompt, build_candidate_prompt
from .retry import build_retry, RetryConfig

if TYPE_CHECKING:
    from campaign.budget import Budget

log = logging.getLogger(__name__)

# Seconds before each new try at 429, 5xx (529 overloaded) and network errors. Replaces retry_cfg's backoff.
WAITS_S = (5, 15, 30)


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


class AnthropicProvider:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "claude-sonnet-4-6",
        retry_cfg: RetryConfig | None = None,
        prompt_version: str = "v1",
        budget: Budget | None = None,
    ):
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for the Anthropic provider")
        try:
            import anthropic as _anthropic
        except ImportError as exc:
            raise ImportError("anthropic package required: pip install anthropic") from exc
        self._client = _anthropic.Anthropic(api_key=api_key, max_retries=0)  # WAITS_S does the retries
        self._api_error = _anthropic.APIError
        self._model = model
        self._retry_cfg = retry_cfg or RetryConfig()
        self._system_prompt = get_system_prompt(prompt_version)
        self._budget = budget

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
                retry = build_retry(
                    self._retry_cfg, candidate_id=candidate_id, provider="anthropic", waits=WAITS_S
                )
                response = None
                for attempt in retry:
                    with attempt:
                        response = self._client.messages.create(
                            model=self._model,
                            max_tokens=512,
                            system=[
                                {
                                    "type": "text",
                                    "text": self._system_prompt,
                                    "cache_control": {"type": "ephemeral"},
                                }
                            ],
                            messages=[
                                {
                                    "role": "user",
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": stream_context,
                                            "cache_control": {"type": "ephemeral"},
                                        },
                                        {
                                            "type": "text",
                                            "text": candidate_prompt,
                                        },
                                    ],
                                }
                            ],
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
                        "valja",
                        usage.input_tokens,
                        usage.output_tokens,
                        getattr(usage, "cache_read_input_tokens", 0) or 0,
                        getattr(usage, "cache_creation_input_tokens", 0) or 0,
                    )
                text = next((b.text for b in response.content if b.type == "text"), "")  # type: ignore[union-attr]
                pick = ClipPick.from_json(text)
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
                if isinstance(exc, self._api_error):  # key, account or Anthropic: every candidate would fail
                    raise LLMUnavailable(
                        f"Claude svarade inte: {_reason(exc)}. Kör igen när det fungerar."
                    ) from exc
                return None  # an answer that is not valid JSON: skip only this candidate
