"""Tests for ClipPick parsing and duration clamping."""

from __future__ import annotations

import json

import pytest

from clipsmith.llm.base import ClipPick
from clipsmith.selection.selector import _clamp_duration
from clipsmith.settings import ClipConfig


def _cfg(**kwargs: object) -> ClipConfig:
    defaults: dict[str, object] = {
        "min_seconds": 15,
        "max_seconds": 30,
        "preroll_s": 25,
        "postroll_s": 10,
    }
    defaults.update(kwargs)
    return ClipConfig(**defaults)  # type: ignore[arg-type]


# ── ClipPick parsing ──────────────────────────────────────────────────────────


def test_clip_pick_from_json_plain() -> None:
    raw = json.dumps(
        {
            "include": True,
            "start_offset_s": 290.0,
            "end_offset_s": 318.0,
            "title_es": "Reacción épica",
            "reason": "Huge chat spike, very funny moment.",
        }
    )
    pick = ClipPick.from_json(raw)
    assert pick.include is True
    assert pick.start_offset_s == pytest.approx(290.0)
    assert pick.end_offset_s == pytest.approx(318.0)
    assert pick.title_es == "Reacción épica"


def test_clip_pick_from_json_strips_markdown_fences() -> None:
    raw = '```json\n{"include":false,"start_offset_s":0,"end_offset_s":0,"title_es":"n/a","reason":"not funny"}\n```'
    pick = ClipPick.from_json(raw)
    assert pick.include is False


def test_clip_pick_duration() -> None:
    pick = ClipPick(
        include=True, start_offset_s=100.0, end_offset_s=125.0, title_es="t", reason="r"
    )
    assert pick.duration_s == pytest.approx(25.0)


def test_clip_pick_roundtrip() -> None:
    original = ClipPick(
        include=True, start_offset_s=50.0, end_offset_s=78.5, title_es="Momento", reason="good"
    )
    restored = ClipPick.from_dict(original.to_dict())
    assert restored.include == original.include
    assert restored.start_offset_s == pytest.approx(original.start_offset_s)
    assert restored.title_es == original.title_es


# ── duration clamping ─────────────────────────────────────────────────────────


def test_clamp_too_short_extends_end() -> None:
    pick = ClipPick(
        include=True, start_offset_s=100.0, end_offset_s=109.0, title_es="t", reason="r"
    )
    clamped = _clamp_duration(pick, _cfg(min_seconds=15, max_seconds=30))
    assert clamped.end_offset_s == pytest.approx(115.0)


def test_clamp_too_long_trims_end() -> None:
    pick = ClipPick(
        include=True, start_offset_s=100.0, end_offset_s=145.0, title_es="t", reason="r"
    )
    clamped = _clamp_duration(pick, _cfg(min_seconds=15, max_seconds=30))
    assert clamped.end_offset_s == pytest.approx(130.0)


def test_clamp_ok_unchanged() -> None:
    pick = ClipPick(
        include=True, start_offset_s=100.0, end_offset_s=120.0, title_es="t", reason="r"
    )
    clamped = _clamp_duration(pick, _cfg(min_seconds=15, max_seconds=30))
    assert clamped.end_offset_s == pytest.approx(120.0)


# ── score ─────────────────────────────────────────────────────────────────────


def test_clip_pick_score_defaults_to_zero_for_old_answers() -> None:
    raw = '{"include":true,"start_offset_s":1,"end_offset_s":20,"title_es":"t","reason":"r"}'
    assert ClipPick.from_json(raw).score == 0


@pytest.mark.parametrize(
    ("given", "expected"), [(87, 87), ("42", 42), (150, 100), (-5, 0), (55.6, 56)]
)
def test_clip_pick_score_is_clamped_to_0_100(given: object, expected: int) -> None:
    assert ClipPick.from_dict({"include": True, "score": given}).score == expected


def test_prompt_and_schema_ask_for_score() -> None:
    from clipsmith.llm.prompts import CLIP_PICK_JSON_SCHEMA, SYSTEM_PROMPT, SYSTEM_PROMPT_V2

    assert '"score"' in SYSTEM_PROMPT
    assert '"score"' in SYSTEM_PROMPT_V2
    assert "TikTok" in CLIP_PICK_JSON_SCHEMA["properties"]["score"]["description"]
    assert CLIP_PICK_JSON_SCHEMA["properties"]["score"]["maximum"] == 99
    assert "score" in CLIP_PICK_JSON_SCHEMA["required"]


# ── title, subscores and payoff (v2) ─────────────────────────────────────────


def test_title_is_read_from_title_and_old_title_es() -> None:
    assert ClipPick.from_dict({"title": "New"}).title == "New"
    assert ClipPick.from_dict({"title_es": "Old"}).title == "Old"


def test_old_title_es_still_works_in_the_constructor_and_as_attribute() -> None:
    pick = ClipPick(include=True, start_offset_s=0, end_offset_s=20, title_es="Old", reason="r")
    assert pick.title == "Old"
    assert pick.title_es == "Old"


def test_picks_json_keeps_title_es_for_old_readers() -> None:
    d = ClipPick(include=True, start_offset_s=0, end_offset_s=20, title="T").to_dict()
    assert d["title"] == "T" and d["title_es"] == "T"


def test_subscores_are_0_25_and_give_the_score_when_there_is_none() -> None:
    pick = ClipPick.from_dict(
        {"include": True, "hook": 22, "engagement": 30, "value": -3, "shareability": "10"}
    )
    assert (pick.hook, pick.engagement, pick.value, pick.shareability) == (22, 25, 0, 10)
    assert pick.score == 57


def test_a_saved_score_wins_over_the_sum() -> None:
    # picks.json holds the calibrated score; reading it back must not undo the calibration
    pick = ClipPick.from_dict({"include": True, "hook": 20, "engagement": 20, "score": 91})
    assert pick.score == 91


def test_payoff_times_and_roundtrip() -> None:
    pick = ClipPick.from_dict({"include": True, "payoff_start_s": "12.5", "payoff_end_s": 15})
    assert (pick.payoff_start_s, pick.payoff_end_s) == (12.5, 15.0)
    assert ClipPick.from_dict({"include": True}).payoff_start_s is None
    again = ClipPick.from_dict(pick.to_dict())
    assert again == pick


def test_scored_prompt_asks_for_subscores_title_and_payoff() -> None:
    from clipsmith.llm.prompts import SYSTEM_PROMPT, get_system_prompt

    scored = get_system_prompt("v1", "", mode="v2")
    for key in (
        "hook",
        "engagement",
        "value",
        "shareability",
        "title",
        "payoff_start_s",
        "payoff_end_s",
    ):
        assert f'"{key}"' in scored
    assert "first sentence" in scored
    assert "what is said in the transcript" in scored  # Twitch rule kept without own instructions
    own = get_system_prompt("v1", "Campaign X rules.", mode="v2")
    assert own.startswith("Campaign X rules.") and '"hook"' in own
    assert "what is said in the transcript" not in own and "3-10" not in own
    # legacy is exactly today's prompt
    assert get_system_prompt("v1", "", mode="legacy") == SYSTEM_PROMPT
    assert get_system_prompt("v1") == SYSTEM_PROMPT


@pytest.mark.parametrize("bad", [float("inf"), "1e999", "abc"])
def test_a_grade_or_score_that_is_not_a_number_becomes_0(bad: object) -> None:
    pick = ClipPick.from_dict({"include": True, "hook": bad, "score": bad})
    assert (pick.hook, pick.score) == (0, 0)


def test_snap_keeps_the_payoff_when_the_start_snaps_earlier() -> None:
    from clipsmith.selection.selector import _snap_to_sentences

    pick = ClipPick(
        include=True, start_offset_s=110, end_offset_s=140, payoff_start_s=109, payoff_end_s=139
    )
    out = _snap_to_sentences(
        pick, ([105.0, 110.0], [136.0, 141.0]), ClipConfig(min_seconds=10, max_seconds=30)
    )
    assert out.end_offset_s >= 139 and out.duration_s <= 30
