"""Tester för allt som inte behöver ffmpeg."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from campaign.brain import Brain
from campaign.budget import Budget, BudgetExceeded
from campaign.caption import build_caption, check_caption
from campaign.cliplog import ClipLog
from campaign.facit import parse_time, score
from campaign.profile import load_profile
from campaign.rules import check_clip

from .conftest import FakeClient

GOOD_INFO = {"width": 1080, "height": 1920, "duration": 20.0, "has_audio": True}
GOOD_MANIFEST = {"text_applied": True, "overlay_applied": True}


# --- budget ---------------------------------------------------------------


def test_cost_follows_price_list(budget):
    assert budget.cost("claude-sonnet-5-5", 1_000_000, 1_000_000) == pytest.approx(12.0)
    assert budget.cost("claude-opus-5-5", 1_000_000, 0) == pytest.approx(4.0)
    assert budget.cost("claude-haiku-5-5", 50_000, 0) == pytest.approx(0.005)


def test_haiku_costs_more_over_100k_tokens(budget):
    assert budget.cost("claude-haiku-5-5", 200_000, 0) == pytest.approx(0.10)


def test_unknown_model_is_priced_high(budget):
    assert budget.cost("okand-modell", 1_000_000, 0) == pytest.approx(8.0)


def test_month_cap_stops_calls(settings):
    budget = Budget(settings.data_dir / "b.db", settings.prices_path, month_cap_usd=1.0, day_cap_usd=50.0)
    budget.check()
    budget.record("claude-sonnet-5-5", "test", 600_000, 0)  # 1,20 dollar
    with pytest.raises(BudgetExceeded):
        budget.check()


def test_day_cap_stops_calls(settings):
    budget = Budget(settings.data_dir / "b.db", settings.prices_path, month_cap_usd=200.0, day_cap_usd=0.5)
    budget.record("claude-sonnet-5-5", "test", 300_000, 0)  # 0,60 dollar
    with pytest.raises(BudgetExceeded):
        budget.check()


def test_new_month_starts_from_zero(settings):
    clock = {"now": datetime(2026, 11, 30, 23, 0, tzinfo=timezone.utc)}
    budget = Budget(settings.data_dir / "b.db", settings.prices_path, 1.0, 50.0, now=lambda: clock["now"])
    budget.record("claude-sonnet-5-5", "test", 600_000, 0)
    with pytest.raises(BudgetExceeded):
        budget.check()
    clock["now"] = datetime(2026, 12, 1, 8, 0, tzinfo=timezone.utc)
    budget.check()
    assert budget.spent_month() == 0.0


# --- brain ----------------------------------------------------------------


def test_brain_returns_answer_and_logs_cost(settings, budget):
    client = FakeClient({"ok": True})
    brain = Brain(settings, budget, client=client)
    answer = brain.ask(purpose="test", model="claude-haiku-5-5", system="s", content="hej",
                       schema={"type": "object"})
    assert answer == {"ok": True}
    assert client.messages.calls[0]["tool_choice"] == {"type": "tool", "name": "svar"}
    assert budget.summary()["by_purpose"]["test"]["calls"] == 1


def test_brain_makes_no_call_when_budget_is_spent(settings):
    budget = Budget(settings.data_dir / "b.db", settings.prices_path, 0.0, 0.0)
    client = FakeClient({"ok": True})
    with pytest.raises(BudgetExceeded):
        Brain(settings, budget, client=client).ask(
            purpose="test", model="claude-haiku-5-5", system="s", content="hej", schema={})
    assert client.messages.calls == []


# --- profil och caption ---------------------------------------------------


def test_timmy_profile_loads(settings):
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    assert profile.text == "twitch.tv/skiimasktimmy"
    assert profile.min_seconds == 7
    assert profile.format_ids == ["clutch", "extraction", "funny", "first_impressions", "reactions", "community"]
    assert profile.min_views == 2000
    assert profile.engagement_min(1_000) == 0.9
    assert profile.engagement_min(50_000) == 0.8
    assert profile.engagement_min(500_000) == 0.5


def test_profile_reports_missing_overlay(settings):
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    assert any("Overlay" in p for p in profile.problems(settings.root))
    # Appen visar texten och länkar till fliken Overlay, där bilden laddas upp.
    missing = next(p for p in profile.problems(settings.root) if p.startswith("Overlay-bilden saknas"))
    assert "Ladda upp den under Overlay" in missing
    overlay = settings.root / "overlays" / "skiimasktimmy.png"
    overlay.parent.mkdir()
    overlay.write_bytes(b"png")
    assert profile.problems(settings.root) == []


def test_caption_has_link_hashtags_and_game(settings):
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    caption = build_caption("That was way too close", profile, game="Arc Raiders")
    assert "twitch.tv/skiimasktimmy" in caption
    for tag in ("#skiimasktimmy", "#twitchclips", "#fps", "#gaming", "#arcraiders"):
        assert tag in caption
    assert check_caption(caption, profile) == []
    with_https = caption.replace("twitch.tv/", "https://twitch.tv/")
    assert check_caption(with_https, profile) == []  # länken räknas som förut, även inuti en längre länk


def test_caption_check_finds_missing_parts(settings):
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    problems = check_caption("That was close #gaming", profile)
    assert any("twitch.tv/skiimasktimmy" in p for p in problems)
    assert any("#skiimasktimmy" in p for p in problems)


# --- regler ---------------------------------------------------------------


def test_good_clip_passes_rules(settings):
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    caption = build_caption("Close call", profile)
    review = {"facecam_visible": True, "text_visible": True, "overlay_visible": True, "problems": []}
    problems, _ = check_clip(GOOD_INFO, GOOD_MANIFEST, profile, caption, "clutch", review)
    assert problems == []


@pytest.mark.parametrize(
    "info, manifest, clip_format, expected",
    [
        ({**GOOD_INFO, "duration": 4.0}, GOOD_MANIFEST, "clutch", "minst 7"),
        ({**GOOD_INFO, "width": 1920, "height": 1080}, GOOD_MANIFEST, "clutch", "1080×1920"),
        ({**GOOD_INFO, "has_audio": False}, GOOD_MANIFEST, "clutch", "saknar ljud"),
        (GOOD_INFO, {"text_applied": False, "overlay_applied": True}, "clutch", "Texten"),
        (GOOD_INFO, {"text_applied": True, "overlay_applied": False}, "clutch", "Overlayen"),
        (GOOD_INFO, GOOD_MANIFEST, "podcast", "kampanjens lista"),
    ],
)
def test_rule_breaks_are_caught(settings, info, manifest, clip_format, expected):
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    caption = build_caption("Close call", profile)
    problems, _ = check_clip(info, manifest, profile, caption, clip_format)
    assert any(expected in p for p in problems)


def test_review_without_facecam_blocks_clip(settings):
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    caption = build_caption("Close call", profile)
    review = {"facecam_visible": False, "text_visible": True, "overlay_visible": True, "problems": []}
    problems, _ = check_clip(GOOD_INFO, GOOD_MANIFEST, profile, caption, "clutch", review)
    assert any("facecam" in p for p in problems)


# --- klipplogg ------------------------------------------------------------


def test_cliplog_follows_a_clip_to_payment(settings):
    log = ClipLog(settings.data_dir / "klipp.db")
    clip_id = log.add(vod_id="v1", profile="skiimasktimmy", file="a.mp4", title="Close call",
                      caption="c", start_s=100.0, end_s=130.0, signals=["audio_energy"])
    assert log.get(clip_id)["status"] == "renderad"
    log.update(clip_id, status="godkand", format="clutch")
    assert "posta klippet" in log.todo()[0]
    log.update(clip_id, status="postad", url="https://example.com/1")
    assert "skicka in länken" in log.todo()[0]
    log.update(clip_id, status="inskickad", views=2500, likes=40, comments=5)
    assert "skärminspelningen" in log.todo()[0]
    assert log.engagement(log.get(clip_id)) == pytest.approx(1.8)
    log.update(clip_id, status="betald")
    assert log.todo() == []


def test_cliplog_blocks_same_part_of_vod_twice(settings):
    log = ClipLog(settings.data_dir / "klipp.db")
    clip_id = log.add(vod_id="v1", profile="p", file="a.mp4", title="t", caption="c",
                      start_s=100.0, end_s=130.0)
    assert log.overlaps("v1", 110.0, 140.0)
    assert not log.overlaps("v1", 200.0, 230.0)
    assert not log.overlaps("annan-vod", 110.0, 140.0)
    log.update(clip_id, status="nekad", note="tråkigt")
    assert not log.overlaps("v1", 110.0, 140.0)


def test_cliplog_rejects_unknown_status(settings):
    log = ClipLog(settings.data_dir / "klipp.db")
    clip_id = log.add(vod_id="v1", profile="p", file="a.mp4", title="t", caption="c")
    with pytest.raises(ValueError):
        log.update(clip_id, status="klar")


# --- facit ----------------------------------------------------------------


def test_parse_time():
    assert parse_time("1:23:45") == 5025
    assert parse_time("83:45") == 5025
    assert parse_time(90) == 90.0
    with pytest.raises(ValueError):
        parse_time("1:2:3:4")


def test_facit_score_counts_hits_and_misses():
    moments = [{"time": 100.0, "note": "a"}, {"time": 500.0, "note": "b"}, {"time": 905.0, "note": "c"}]
    clips = [(90.0, 120.0), (880.0, 900.0)]
    result = score(moments, clips, tolerance=10.0)
    assert (result["hits"], result["total"]) == (2, 3)
    assert result["missed"][0]["note"] == "b"


# --- text på skärmen --------------------------------------------------------


def test_the_white_text_is_drawn_only_when_there_is_no_overlay(settings):
    """Overlayen har redan kanalens text. Annars står texten två gånger."""
    from campaign.finish import build_filter

    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    assert "drawtext" not in build_filter(profile, has_overlay=True, has_audio=True, font=None)
    assert "drawtext" in build_filter(profile, has_overlay=False, has_audio=True, font=None)


def test_the_signals_mode_placeholder_title_must_be_replaced(settings):
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    placeholder = build_caption("Moment 0:36:35", profile, game="Bodycam")
    assert any(p.startswith("Captionen har kvar platshållaren Moment 0:36:35") for p in check_caption(placeholder, profile))
    assert check_caption(build_caption("Moment of truth at 0:36", profile, game="Bodycam"), profile) == []
