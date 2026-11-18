"""Klipp utan kampanj (_ingen): dold i listorna, och kan aldrig godkännas eller postas."""

from __future__ import annotations

import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api, schedule
from campaign.cliplog import ClipLog
from campaign.live import LiveState
from campaign.live_api import router as live_router
from campaign.profile import load_profile
from campaign.rules import NO_CAMPAIGN, check_clip

from .conftest import PAKET

KEY = {"X-Api-Key": "nyckel"}
NO_CAMPAIGN_TEXT = "Klippet har ingen kampanj. Välj kampanj först."


@pytest.fixture
def hidden(settings):
    shutil.copy(PAKET / "profiles" / "_ingen.yaml", settings.profiles_dir / "_ingen.yaml")
    shutil.copy(PAKET / "config.live.yaml", settings.root / "config.live.yaml")
    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    return settings


@pytest.fixture
def client(hidden, tmp_path, monkeypatch):
    from clipsmith.db.session import init_db

    monkeypatch.setenv("CLIPSMITH_API_KEY", "nyckel")
    init_db(tmp_path / "clipsmith.db")
    app = FastAPI()
    app.state.active_run_id = None
    app.include_router(api.router)
    app.include_router(live_router)
    app.dependency_overrides[api.get_settings] = lambda: hidden
    return TestClient(app)


def test_the_hidden_campaign_loads_with_a_center_frame_and_burnt_in_subtitles(hidden) -> None:
    from campaign.pipeline import clipsmith_config

    profile = load_profile(NO_CAMPAIGN, hidden.profiles_dir)
    assert profile.name == "_ingen"
    assert (profile.overlay_required, profile.text_required, profile.hook_text) == (
        False,
        False,
        False,
    )
    assert (profile.game_hashtag, profile.caption_must_include, profile.hashtags) == (False, [], [])
    assert profile.problems(hidden.root) == []
    cfg = clipsmith_config(profile, hidden)
    assert (cfg.channels, cfg.reframe.mode, cfg.caption.enabled) == ([], "center", True)
    assert (cfg.clip.min_seconds, cfg.clip.max_seconds) == (15, 45)


def test_the_hidden_campaign_is_not_listed(client, hidden) -> None:
    names = [p["name"] for p in client.get("/campaign/profiles", headers=KEY).json()]
    assert names == ["skiimasktimmy"]
    LiveState(hidden).set_watching(NO_CAMPAIGN, True)  # även om läget skulle ha nyckeln
    live = client.get("/campaign/live", headers=KEY).json()
    assert NO_CAMPAIGN not in [w["profile"] for w in live]
    answer = client.put(f"/campaign/live/{NO_CAMPAIGN}", json={"watching": True}, headers=KEY)
    assert answer.status_code == 404


def test_a_clip_without_a_campaign_has_a_problem(hidden) -> None:
    profile = load_profile(NO_CAMPAIGN, hidden.profiles_dir)
    info = {"width": 1080, "height": 1920, "duration": 20.0, "has_audio": True}
    problems, _ = check_clip(info, {}, profile, "Speed går bananas")
    assert problems == [NO_CAMPAIGN_TEXT]
    timmy = load_profile("skiimasktimmy", hidden.profiles_dir)
    problems, _ = check_clip(
        info,
        {"text_applied": True, "overlay_applied": True},
        timmy,
        "x twitch.tv/skiimasktimmy #skiimasktimmy #twitchclips #fps #gaming",
    )
    assert NO_CAMPAIGN_TEXT not in problems


def test_a_clip_without_a_campaign_cannot_be_uploaded(hidden) -> None:
    log = ClipLog(hidden.data_dir / "klipp.db")
    path = hidden.out_dir / "555_live_1" / "klipp.mp4"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"mp4")
    # Även om klippet på något sätt blivit godkänt.
    clip_id = log.add(
        vod_id="555_live_1",
        profile=NO_CAMPAIGN,
        file=str(path),
        title="t",
        caption="c",
        status="godkand",
    )
    for platform in ("youtube", "tiktok"):
        with pytest.raises(schedule.ScheduleError, match="ingen kampanj"):
            schedule.clip_file(hidden, log.get(clip_id), platform)
