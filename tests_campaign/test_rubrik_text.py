"""Rubriken i bilden: Claudes headline, annars titeln. Captionen byggs alltid av titeln."""

from __future__ import annotations

import json

import pytest

from campaign import finish, workflow
from campaign.caption import headline_text
from campaign.cliplog import ClipLog
from campaign.profile import load_profile
from campaign.workflow import load_picks
from clipsmith.llm.base import ClipPick

from .conftest import make_clip, make_overlay, needs_ffmpeg


def test_the_headline_goes_on_the_picture() -> None:
    assert headline_text({"headline": "This WoW world is beautiful", "title": "WoW Forever"}) == (
        "This WoW world is beautiful"
    )


def test_without_a_headline_the_title_goes_on_the_picture() -> None:
    assert headline_text({"headline": "", "title": "No way"}) == "No way"
    assert headline_text({"title": "No way"}) == "No way"


@pytest.mark.parametrize("headline", ["   ", "\x00", " \n\t\x1f "])
def test_a_headline_of_only_blanks_or_control_characters_gives_the_title(headline) -> None:
    assert headline_text({"headline": headline, "title": "No way"}) == "No way"


def test_the_headline_is_trimmed_and_cleaned_of_control_characters() -> None:
    assert headline_text({"headline": "  Big\x00play \n", "title": "No way"}) == "Big play"


def test_the_placeholder_and_no_title_give_no_headline() -> None:
    assert headline_text({"title": "Moment 0:01:40"}) == ""
    assert headline_text({}) == ""


def test_load_picks_reads_the_headline_from_picks_json(tmp_path) -> None:
    pick = ClipPick(
        True, 100.0, 110.0, title="WoW Forever", headline="Humans pick up the hunter's bow"
    )
    path = tmp_path / "picks.json"
    path.write_text(json.dumps([{"candidate": {}, "pick": pick.to_dict()}]), encoding="utf-8")
    (loaded,) = load_picks(path)
    assert loaded["headline"] == "Humans pick up the hunter's bow"
    assert loaded["title"] == "WoW Forever"


def test_an_old_pick_without_headline_gives_an_empty_headline(tmp_path) -> None:
    path = tmp_path / "picks.json"
    path.write_text(
        json.dumps([{"pick": {"start_offset_s": 1, "end_offset_s": 9, "title_es": "Old"}}])
    )
    (loaded,) = load_picks(path)
    assert loaded["headline"] == ""


@needs_ffmpeg
def test_finish_vod_burns_the_headline_and_builds_the_caption_from_the_title(
    settings, monkeypatch
) -> None:
    monkeypatch.setitem(finish.ENCODER_ARGS, "libx264", ["-c:v", "libx264", "-preset", "ultrafast"])
    make_overlay(settings.root / "overlays" / "skiimasktimmy.png")
    make_clip(settings.out_dir / "123" / "clip_01_no_way.mp4", seconds=10)
    picks = settings.work_dir / "123" / "picks.json"
    picks.parent.mkdir(parents=True)
    picks.write_text(
        json.dumps(
            [
                {
                    "candidate": {},
                    "pick": {
                        "start_offset_s": 100,
                        "end_offset_s": 110,
                        "title": "No way",
                        "headline": "He really jumped that gap",
                    },
                }
            ]
        )
    )
    log = ClipLog(settings.data_dir / "klipp.db")
    log.add_run(1, "123", "skiimasktimmy", "Bodycam")
    titles = []
    real = workflow.finish_clip
    monkeypatch.setattr(
        workflow, "finish_clip", lambda *a, **k: titles.append(k.get("title")) or real(*a, **k)
    )
    profile = load_profile("skiimasktimmy", settings.profiles_dir)

    (result,) = workflow.finish_vod("123", profile, settings, log, game="Bodycam", review=False)

    assert titles == ["He really jumped that gap"]
    assert result["title"] == "No way"
    assert "No way" in result["caption"] and "jumped" not in result["caption"]
