"""Bildgranskningen: prompten berättar layouten, och bara verkliga regelbrott stoppar klippet.
Påhittad Brain, inga riktiga Claude-anrop."""

from __future__ import annotations

import shutil

from campaign import review
from campaign.caption import build_caption
from campaign.profile import load_profile
from campaign.rules import check_clip

from .conftest import PAKET

GOOD_INFO = {"width": 1080, "height": 1920, "duration": 30.0, "has_audio": True}
GOOD_MANIFEST = {"text_applied": True, "overlay_applied": True}
GOOD = {"facecam_visible": True, "text_visible": True, "overlay_visible": True, "face_covered": False,
        "notes": []}


class FakeBrain:
    def __init__(self):
        self.calls: list[dict] = []

    def ask(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return GOOD


def _ask(settings, monkeypatch, tmp_path, split_ratio):
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"\xff\xd8\xff\xd9")
    monkeypatch.setattr(review, "grab_frames", lambda clip, out, count=4: [frame] * count)
    brain = FakeBrain()
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    assert review.review_clip(tmp_path / "c.mp4", profile, brain, "m", tmp_path, split_ratio=split_ratio) == GOOD
    (call,) = brain.calls
    text = "\n".join(part["text"] for part in call["content"] if part["type"] == "text")
    return call, text


def test_prompt_tells_the_stacked_layout(settings, monkeypatch, tmp_path) -> None:
    call, text = _ask(settings, monkeypatch, tmp_path, 0.4)
    assert "top 40%" in text and "bottom 60%" in text
    assert "CAM DISCONNECTED" in text  # spelets egna skärmar räknas inte som kameran
    assert "most frames" in call["schema"]["properties"]["facecam_visible"]["description"]
    assert set(call["schema"]["required"]) >= {"facecam_visible", "text_visible", "face_covered", "notes"}


def test_prompt_without_a_stacked_layout_has_no_panels(settings, monkeypatch, tmp_path) -> None:
    _, text = _ask(settings, monkeypatch, tmp_path, None)
    assert "top 40%" not in text


def test_split_ratio_comes_from_the_campaigns_stacked_config(settings) -> None:
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    assert review.stacked_split(profile, settings.root) is None  # ingen config-fil
    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    assert review.stacked_split(profile, settings.root) == 0.4
    path = settings.root / "config.timmy.yaml"
    path.write_text(path.read_text().replace("  mode: stacked", "  mode: center "))
    assert review.stacked_split(profile, settings.root) is None


def _check(settings, answer):
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    return check_clip(GOOD_INFO, GOOD_MANIFEST, profile, build_caption("Close call", profile), "clutch", answer)


def test_observations_that_are_not_rule_breaks_are_warnings(settings) -> None:
    notes = ["Subtitles cover part of the game.", "The head is close to the top edge."]
    problems, warnings = _check(settings, {**GOOD, "notes": notes})
    assert problems == []
    assert [w for w in warnings if w.startswith("Bildgranskning:")] == [f"Bildgranskning: {n}" for n in notes]


def test_old_reviews_with_a_problems_list_give_warnings(settings) -> None:
    old = {"facecam_visible": True, "text_visible": True, "overlay_visible": True, "problems": ["Undertext."]}
    problems, warnings = _check(settings, old)
    assert problems == [] and "Bildgranskning: Undertext." in warnings


def test_real_rule_breaks_are_problems(settings) -> None:
    assert any("facecam" in p for p in _check(settings, {**GOOD, "facecam_visible": False})[0])
    assert any("texten" in p for p in _check(settings, {**GOOD, "text_visible": False})[0])
    assert any("ansiktet" in p for p in _check(settings, {**GOOD, "face_covered": True})[0])
