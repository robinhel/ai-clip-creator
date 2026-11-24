"""Klipp utan kampanj (_ingen): dold i listorna, och kan aldrig godkännas eller postas."""

from __future__ import annotations

import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api, profiles_api, schedule
from campaign.cliplog import ClipLog
from campaign.live import LiveState
from campaign.live_api import router as live_router
from campaign.profile import load_profile
from campaign.rules import NO_CAMPAIGN, check_clip

from .conftest import PAKET

KEY = {"X-Api-Key": "nyckel"}
NO_CAMPAIGN_TEXT = "The clip has no campaign. Choose a campaign first."


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
    app.include_router(profiles_api.router)
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
        with pytest.raises(schedule.ScheduleError, match="no campaign"):
            schedule.clip_file(hidden, log.get(clip_id), platform)


def test_a_free_live_part_is_run_without_twitch_and_with_the_channel_name(
    hidden, monkeypatch
) -> None:
    """Delens id (<stream-id>_live_<n>) är ingen VOD: körningen frågar inte Twitch om den. Claude får
    kanalens namn, inte den dolda kampanjens etikett."""
    from campaign import pipeline, vodinfo
    from clipsmith.models.transcript import Segment, Transcript

    monkeypatch.chdir(hidden.root)  # config.live.yaml har work_dir ./work
    part = "555_live_1"
    mp4 = hidden.root / "work" / part / f"{part}.mp4"
    mp4.parent.mkdir(parents=True)
    mp4.touch()
    segment = Segment(start=1.0, end=2.0, text="no way", words=[])
    transcript = Transcript(video_id=part, language="en", segments=[segment])
    (mp4.parent / "transcript.json").write_text(transcript.to_json(), encoding="utf-8")
    (mp4.parent / "chat.json").write_text('{"video_id": "555_live_1", "messages": []}')
    ClipLog(hidden.data_dir / "klipp.db").add_run(
        7, part, NO_CAMPAIGN, None, title="SPEED (live, topp 1)", channel_name="ishowspeed"
    )
    monkeypatch.setattr(pipeline, "download_vod", lambda *a, **k: pytest.fail("ingen nedladdning"))
    monkeypatch.setattr(pipeline, "_twitch_ids", lambda *a: pytest.fail("ingen Twitch"))
    monkeypatch.setattr(vodinfo.twitch, "get_video", lambda *a: pytest.fail("ingen Twitch"))
    monkeypatch.setattr(vodinfo.twitch, "gql_query", lambda *a: pytest.fail("ingen Twitch"))
    seen: dict = {}

    def process_vod(video, cfg, secrets, **kwargs):
        seen["video"], seen["skip_chat"] = video, kwargs["skip_chat"]
        (mp4.parent / "candidates.json").write_text("[]", encoding="utf-8")

    monkeypatch.setattr(pipeline, "process_vod", process_vod)
    profile = load_profile(NO_CAMPAIGN, hidden.profiles_dir)
    _, why = pipeline.make_clips(
        part, profile, hidden, game=None, emit=lambda *e: None, run_id=7, local=True
    )
    assert why == "No candidates were found."
    video = seen["video"]
    assert (video.url, video.user_id, video.user_login) == ("", "", "ishowspeed")
    assert seen["skip_chat"] is True  # chatten från live-klipparen, inte Twitchs chattreplay


def _hidden_clip(hidden, status: str, profile: str = NO_CAMPAIGN) -> tuple[ClipLog, int]:
    """Ett klipp utan problem i loggen, som om problemet hade tagits bort."""
    log = ClipLog(hidden.data_dir / "klipp.db")
    path = hidden.out_dir / "555_live_1" / "klipp.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"mp4")
    clip_id = log.add(vod_id="555_live_1", profile=profile, file=str(path), title="t",
                      caption="c", status=status, problems=[])
    return log, clip_id


@pytest.fixture
def cli_main(hidden, monkeypatch):
    from campaign import cli

    monkeypatch.setattr(cli, "load_settings", lambda: hidden)
    return cli.main


@pytest.mark.parametrize("status", ["godkand", "postad", "inskickad", "analytics_inskickad", "betald"])
def test_the_command_line_cannot_approve_or_post_a_clip_without_a_campaign(
    hidden, cli_main, capsys, status
) -> None:
    log, clip_id = _hidden_clip(hidden, "renderad")
    assert cli_main(["mark", str(clip_id), status]) == 1
    assert NO_CAMPAIGN_TEXT in capsys.readouterr().out
    assert log.get(clip_id)["status"] == "renderad"
    assert cli_main(["mark", str(clip_id), "nekad"]) == 0  # neka går alltid


def test_the_command_line_cannot_mark_a_clip_without_a_campaign_as_posted(
    hidden, cli_main, capsys
) -> None:
    log, clip_id = _hidden_clip(hidden, "godkand")  # även om klippet på något sätt blivit godkänt
    assert cli_main(["posted", str(clip_id), "https://www.tiktok.com/@a/video/1"]) == 1
    assert NO_CAMPAIGN_TEXT in capsys.readouterr().out
    assert (log.get(clip_id)["status"], log.posts(clip_id)) == ("godkand", [])
    # Kontroll: ett klipp i en riktig kampanj går att markera som postat som förut.
    log, timmy = _hidden_clip(hidden, "godkand", profile="skiimasktimmy")
    assert cli_main(["posted", str(timmy), "https://www.tiktok.com/@a/video/2"]) == 0
    assert log.get(timmy)["status"] == "postad"


def test_the_api_cannot_add_a_post_to_a_clip_without_a_campaign(client, hidden) -> None:
    log, clip_id = _hidden_clip(hidden, "godkand")
    answer = client.post(
        f"/campaign/clips/{clip_id}/posts",
        json={"platform": "tiktok", "url": "https://www.tiktok.com/@a/video/1"},
        headers=KEY,
    )
    assert (answer.status_code, answer.json()["detail"]) == (400, NO_CAMPAIGN_TEXT)
    assert log.posts(clip_id) == []


def test_the_api_cannot_approve_or_post_a_clip_without_a_campaign(client, hidden) -> None:
    log, clip_id = _hidden_clip(hidden, "renderad")
    answer = client.patch(f"/campaign/clips/{clip_id}", json={"status": "godkand"}, headers=KEY)
    assert (answer.status_code, answer.json()["detail"]) == (400, NO_CAMPAIGN_TEXT)
    assert log.get(clip_id)["status"] == "renderad"

    log, approved = _hidden_clip(hidden, "godkand")
    link = {"url": "https://www.tiktok.com/@a/video/1", "platform": "tiktok"}
    answer = client.patch(f"/campaign/clips/{approved}", json={"status": "postad", **link}, headers=KEY)
    assert (answer.status_code, answer.json()["detail"]) == (400, NO_CAMPAIGN_TEXT)
    assert (log.get(approved)["status"], log.posts(approved)) == ("godkand", [])

    log, posted = _hidden_clip(hidden, "postad")
    answer = client.patch(f"/campaign/clips/{posted}", json={"status": "inskickad"}, headers=KEY)
    assert (answer.status_code, answer.json()["detail"]) == (400, NO_CAMPAIGN_TEXT)

    answer = client.patch(f"/campaign/clips/{clip_id}", json={"status": "nekad"}, headers=KEY)
    assert (answer.status_code, answer.json()["status"]) == (200, "nekad")  # neka går alltid


@pytest.mark.parametrize("path", ["", "/yaml", "/overlay", "/reframe"])
def test_a_hidden_campaign_is_not_found(client, path) -> None:
    for name in (NO_CAMPAIGN, "_finns_inte"):
        assert client.get(f"/campaign/profiles/{name}{path}", headers=KEY).status_code == 404


def test_approve_on_the_command_line_skips_a_clip_without_a_campaign(hidden, cli_main, monkeypatch) -> None:
    log, clip_id = _hidden_clip(hidden, "renderad")
    monkeypatch.setattr("builtins.input", lambda *a: "j")
    assert cli_main(["approve"]) == 0
    assert log.get(clip_id)["status"] == "renderad"


def test_the_hidden_campaign_has_no_material_and_no_chat(client, monkeypatch) -> None:
    from campaign import chat

    monkeypatch.setattr(chat, "send", lambda *a, **k: pytest.fail("ingen Claude"))
    for path in ("/material", "/chats"):
        assert client.get(f"/campaign/profiles/{NO_CAMPAIGN}{path}", headers=KEY).status_code == 404
    answer = client.post(f"/campaign/profiles/{NO_CAMPAIGN}/chats", json={"message": "roliga"}, headers=KEY)
    assert answer.status_code == 404
    # Kontroll: en riktig kampanj har sitt material som förut.
    assert client.get("/campaign/profiles/skiimasktimmy/material", headers=KEY).status_code == 200


def test_a_project_cannot_be_started_for_the_hidden_campaign(client, hidden, monkeypatch) -> None:
    monkeypatch.setattr(api, "_facts", lambda *a, **k: pytest.fail("ingen Twitch"))
    monkeypatch.setattr(api, "run_campaign_vod", lambda *a, **k: pytest.fail("ingen körning"))
    answer = client.post("/campaign/runs", json={"profile": NO_CAMPAIGN, "vod_id": "2925991945"}, headers=KEY)
    assert (answer.status_code, answer.json()["detail"]) == (404, f"The campaign {NO_CAMPAIGN} does not exist.")
    assert ClipLog(hidden.data_dir / "klipp.db").runs() == []
