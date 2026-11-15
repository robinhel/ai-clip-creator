"""v2-valet i AnthropicProvider: delpoäng blir score, och ett extra anrop kalibrerar. Påhittad Claude."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from campaign.budget import Budget
from clipsmith.llm.anthropic_provider import AnthropicProvider
from clipsmith.llm.prompts import SYSTEM_PROMPT
from clipsmith.models.candidates import CandidateMoment

pytestmark = pytest.mark.unit


def _response(payload: dict) -> SimpleNamespace:
    usage = SimpleNamespace(
        input_tokens=1000,
        output_tokens=100,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(payload))], usage=usage
    )


def _provider(tmp_path, payload: dict, **kwargs) -> tuple[AnthropicProvider, Budget]:
    budget = Budget(tmp_path / "budget.db", tmp_path / "saknas.yaml", 200.0, 10.0)
    provider = AnthropicProvider("test-key", model="claude-sonnet-5-5", budget=budget, **kwargs)
    provider._client = MagicMock()
    provider._client.messages.create.return_value = _response(payload)
    return provider, budget


_ANSWER = {
    "include": True,
    "start_offset_s": 100.0,
    "end_offset_s": 120.0,
    "title": "Solo boss",
    "reason": "r",
    "hook": 22,
    "engagement": 18,
    "value": 15,
    "shareability": 20,
    "score": 99,
}


def _candidate() -> CandidateMoment:
    return CandidateMoment(t_center=110.0, score=25.0, sources=["chat_density"], reasons=["x"])


def test_v2_score_is_the_sum_of_the_subscores(tmp_path) -> None:
    provider, _ = _provider(tmp_path, _ANSWER)
    pick = provider.pick("text", _candidate(), "ctx")
    assert pick is not None and pick.score == 75 and pick.title == "Solo boss"
    system = provider._client.messages.create.call_args.kwargs["system"][0]["text"]
    assert '"hook"' in system


def test_legacy_keeps_todays_prompt_and_score(tmp_path) -> None:
    provider, _ = _provider(tmp_path, _ANSWER, mode="legacy")
    pick = provider.pick("text", _candidate(), "ctx")
    assert pick is not None and pick.score == 99
    assert provider._client.messages.create.call_args.kwargs["system"][0]["text"] == SYSTEM_PROMPT


def test_rank_returns_one_score_per_clip_and_goes_via_the_budget(tmp_path) -> None:
    provider, budget = _provider(tmp_path, {"scores": [88, 120, -4]})
    assert provider.rank(["a", "b", "c"], "ctx") == [88, 100, 0]
    assert budget.summary()["by_purpose"]["kalibrera"]["calls"] == 1
    text = json.dumps(provider._client.messages.create.call_args.kwargs["messages"])
    assert "a" in text and "same for every stream" in json.dumps(
        provider._client.messages.create.call_args.kwargs["system"]
    )


def test_rank_with_a_wrong_answer_gives_none(tmp_path) -> None:
    provider, _ = _provider(tmp_path, {"scores": [88]})
    assert provider.rank(["a", "b"], "ctx") is None


def test_rank_over_budget_skips_the_call_and_keeps_the_picks(tmp_path) -> None:
    provider, _ = _provider(tmp_path, {"scores": [1, 2]})
    provider._budget = Budget(tmp_path / "b2.db", tmp_path / "saknas.yaml", 0.0, 10.0)
    assert provider.rank(["a", "b"], "ctx") is None
    provider._client.messages.create.assert_not_called()


def test_get_provider_passes_the_selection_mode(monkeypatch) -> None:
    from clipsmith.llm import get_provider
    from clipsmith.settings import AppConfig

    monkeypatch.setattr("campaign.config.load_settings", lambda: SimpleNamespace())
    monkeypatch.setattr("campaign.budget.budget_from_settings", lambda s: None)
    cfg = AppConfig()
    cfg.clip.selection_mode = "legacy"
    provider = get_provider(cfg, SimpleNamespace(anthropic_api_key="k"))
    assert provider._system_prompt == SYSTEM_PROMPT  # type: ignore[attr-defined]


def test_v2_without_subscores_keeps_claudes_score(tmp_path) -> None:
    answer = {k: v for k, v in _ANSWER.items() if k not in ("hook", "engagement", "value", "shareability")}
    provider, _ = _provider(tmp_path, {**answer, "score": 70})
    pick = provider.pick("text", _candidate(), "ctx")
    assert pick is not None and pick.score == 70


def test_a_subscore_that_is_not_a_number_becomes_0(tmp_path) -> None:
    provider, _ = _provider(tmp_path, {**_ANSWER, "hook": "high"})
    pick = provider.pick("text", _candidate(), "ctx")
    assert pick is not None and pick.hook == 0 and pick.score == 53


def test_rank_sends_one_line_per_clip(tmp_path) -> None:
    provider, _ = _provider(tmp_path, {"scores": [50, 60]})
    provider.rank(["Big\nplay | starts: so\nthen", "B"], "ctx")
    text = provider._client.messages.create.call_args.kwargs["messages"][0]["content"][0]["text"]
    assert "1. Big play | starts: so then\n2. B" in text


def test_v2_stream_context_names_the_field_title() -> None:
    from clipsmith.llm.prompts import build_stream_context, get_system_prompt

    ctx = build_stream_context("timmy", "t", "1h", language="sv", mode="v2")
    assert "Write title in sv" in ctx and "title_es" not in ctx
    assert "title_es" not in get_system_prompt("v1", "", mode="v2")
