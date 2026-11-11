"""AnthropicProvider ska gå via budgetspärren (campaign.budget.Budget)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from campaign.budget import Budget, BudgetExceeded
from clipsmith.llm.anthropic_provider import AnthropicProvider
from clipsmith.models.candidates import CandidateMoment

pytestmark = pytest.mark.unit


def _response() -> SimpleNamespace:
    payload = {
        "include": True,
        "start_offset_s": 110.0,
        "end_offset_s": 135.0,
        "title_es": "big play",
        "reason": "hype",
    }
    usage = SimpleNamespace(
        input_tokens=1000,
        output_tokens=100,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(payload))], usage=usage
    )


def _provider(tmp_path, month_cap: float = 200.0) -> tuple[AnthropicProvider, Budget]:
    budget = Budget(tmp_path / "budget.db", tmp_path / "saknas.yaml", month_cap, 10.0)
    provider = AnthropicProvider("test-key", model="claude-sonnet-5-5", budget=budget)
    provider._client = MagicMock()
    provider._client.messages.create.return_value = _response()
    return provider, budget


def _candidate() -> CandidateMoment:
    return CandidateMoment(t_center=120.0, score=25.0, sources=["chat_density"], reasons=["x"])


def test_pick_records_cost_in_budget(tmp_path) -> None:
    provider, budget = _provider(tmp_path)
    assert provider.pick("text", _candidate(), "ctx") is not None
    s = budget.summary()
    assert s["by_purpose"]["valja"]["calls"] == 1
    assert s["month_usd"] > 0


def test_budget_exceeded_stops_before_call(tmp_path) -> None:
    provider, _ = _provider(tmp_path, month_cap=0.0)
    with pytest.raises(BudgetExceeded):
        provider.pick("text", _candidate(), "ctx")
    provider._client.messages.create.assert_not_called()
