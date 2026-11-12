"""Kampanjlagrets API, som webbappen använder. Inga riktiga AI- eller nätverksanrop."""

from __future__ import annotations

import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api
from campaign.cliplog import ClipLog

from .conftest import PAKET, make_clip, make_overlay, needs_ffmpeg

CAPTION = "No way 👉 Catch him live: twitch.tv/skiimasktimmy #skiimasktimmy #twitchclips #fps #gaming"


@pytest.fixture
def client(settings, tmp_path, monkeypatch):
    from clipsmith.db.session import init_db

    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    settings.anthropic_api_key = "test"
    settings.berget_api_key = "test"
    init_db(tmp_path / "clipsmith.db")
    app = FastAPI()
    app.state.active_run_id = None
    app.include_router(api.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    return TestClient(app)


def _clip(settings, status="renderad", problems=None, clip_format=None, file=None) -> int:
    log = ClipLog(settings.data_dir / "klipp.db")
    return log.add(
        vod_id="123", profile="skiimasktimmy", file=file or str(settings.out_dir / "123" / "klara" / "clip_01_a.mp4"),
        title="No way", caption=CAPTION, status=status, start_s=10.0, end_s=40.0,
        clip_format=clip_format, problems=problems or [],
    )


def test_profiles_show_what_is_missing_and_if_a_run_can_start(client, settings) -> None:
    profiles = client.get("/campaign/profiles").json()
    timmy = next(p for p in profiles if p["name"] == "skiimasktimmy")
    assert timmy["ready"] is True
    assert timmy["channel"] == "skiimasktimmy"
    assert any("Overlay-bilden saknas" in p for p in timmy["problems"])
    assert "clutch" in [f["id"] for f in timmy["formats"]]

    (settings.root / "config.timmy.yaml").unlink()
    timmy = next(p for p in client.get("/campaign/profiles").json() if p["name"] == "skiimasktimmy")
    assert timmy["ready"] is False
    assert any("config.timmy.yaml" in p for p in timmy["problems"])


def test_clip_with_problems_cannot_be_approved(client, settings) -> None:
    clip_id = _clip(settings, status="underkand", problems=["Overlayen saknas."], clip_format="clutch")
    response = client.patch(f"/campaign/clips/{clip_id}", json={"status": "godkand"})
    assert response.status_code == 400
    assert client.get("/campaign/clips").json()[0]["status"] == "underkand"


def test_approving_needs_a_format_from_the_campaign(client, settings) -> None:
    clip_id = _clip(settings)
    assert client.patch(f"/campaign/clips/{clip_id}", json={"status": "godkand"}).status_code == 400
    assert client.patch(f"/campaign/clips/{clip_id}", json={"format": "nope"}).status_code == 400

    response = client.patch(f"/campaign/clips/{clip_id}", json={"status": "godkand", "format": "funny"})
    assert response.status_code == 200
    assert response.json()["status"] == "godkand"
    assert response.json()["format"] == "funny"


def test_status_follows_the_flow_and_posting_needs_a_link(client, settings) -> None:
    clip_id = _clip(settings, status="godkand", clip_format="funny")
    other = _clip(settings, clip_format="funny")
    assert client.patch(f"/campaign/clips/{other}", json={"status": "postad", "url": "https://x.y/1",
                                                          "platform": "tiktok"}).status_code == 400
    assert client.patch(f"/campaign/clips/{clip_id}", json={"status": "postad"}).status_code == 400
    assert client.patch(f"/campaign/clips/{clip_id}", json={"status": "postad", "url": "https://x.y/1",
                                                            "platform": "myspace"}).status_code == 400

    posted = client.patch(f"/campaign/clips/{clip_id}", json={
        "status": "postad", "url": "https://www.tiktok.com/@me/video/1", "platform": "tiktok"}).json()
    assert (posted["status"], posted["platform"]) == ("postad", "tiktok")
    assert client.patch(f"/campaign/clips/{clip_id}", json={"status": "inskickad"}).status_code == 200


def test_changed_caption_is_checked_again(client, settings) -> None:
    clip_id = _clip(settings)
    broken = client.patch(f"/campaign/clips/{clip_id}", json={"caption": "No way #fps"}).json()
    assert broken["status"] == "underkand"
    assert any("twitch.tv/skiimasktimmy" in p for p in broken["problems"])

    fixed = client.patch(f"/campaign/clips/{clip_id}", json={"caption": CAPTION}).json()
    assert fixed["status"] == "renderad"
    assert fixed["problems"] == []


def test_clip_list_has_engagement_and_can_filter_on_status(client, settings) -> None:
    clip_id = _clip(settings, status="godkand", clip_format="funny")
    _clip(settings)
    client.patch(f"/campaign/clips/{clip_id}", json={"views": 4000, "likes": 20, "comments": 6, "shares": 2})

    clips = client.get("/campaign/clips", params={"status": "godkand"}).json()
    assert [c["id"] for c in clips] == [clip_id]
    assert clips[0]["engagement"] == pytest.approx(0.7)
    assert clips[0]["engagement_min"] == 0.9
    assert clips[0]["filename"] == "clip_01_a.mp4"
    assert clips[0]["video_url"] == f"/campaign/clips/{clip_id}/file"
    assert "file" not in clips[0]


@needs_ffmpeg
def test_clip_file_is_served_but_only_from_the_out_folder(client, settings, tmp_path) -> None:
    mp4 = make_clip(settings.out_dir / "123" / "klara" / "clip_01_a.mp4", seconds=1)
    inside = _clip(settings, file=str(mp4))
    outside = _clip(settings, file=str(make_clip(tmp_path / "elsewhere.mp4", seconds=1)))

    response = client.get(f"/campaign/clips/{inside}/file", params={"download": 1})
    assert response.status_code == 200
    assert response.content == mp4.read_bytes()
    assert "attachment" in response.headers["content-disposition"]
    assert client.get(f"/campaign/clips/{outside}/file").status_code == 404


def test_starting_a_run_saves_its_campaign_and_uses_the_campaign_runner(client, monkeypatch) -> None:
    started: list[tuple] = []
    monkeypatch.setattr(api, "run_campaign_vod", lambda *args, **kwargs: started.append((args, kwargs)))

    assert client.post("/campaign/runs", json={"profile": "nope", "vod_id": "2925991945"}).status_code == 400
    assert client.post("/campaign/runs", json={"profile": "skiimasktimmy", "vod_id": "abc"}).status_code == 422

    response = client.post("/campaign/runs", json={"profile": "skiimasktimmy", "vod_id": "2925991945",
                                                   "game": "Bodycam"})
    assert response.status_code == 201
    run = response.json()
    assert (run["vod_id"], run["channel"]) == ("2925991945", "skiimasktimmy")
    assert len(started) == 1
    assert client.get("/campaign/runs").json() == [
        {"run_id": run["id"], "profile": "skiimasktimmy", "game": "Bodycam", "vod_id": "2925991945"}
    ]


def test_summary_counts_clips_per_status(client, settings) -> None:
    _clip(settings)
    _clip(settings, status="godkand", clip_format="funny")
    summary = client.get("/campaign/summary").json()
    assert summary["counts"] == {"renderad": 1, "godkand": 1}
    assert summary["budget"]["month_cap_usd"] == 200.0
    assert len(summary["todo"]) == 1


@needs_ffmpeg
def test_finish_again_redoes_a_failed_clip_in_the_same_row(settings) -> None:
    from campaign.profile import load_profile
    from campaign.workflow import finish_vod

    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    make_clip(settings.out_dir / "123" / "clip_01_no_way.mp4")
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    log = ClipLog(settings.data_dir / "klipp.db")

    first = finish_vod("123", profile, settings, log, review=False)
    assert first[0]["problems"]
    make_overlay(settings.root / "overlays" / "skiimasktimmy.png")
    second = finish_vod("123", profile, settings, log, review=False)

    assert second[0]["id"] == first[0]["id"]
    assert second[0]["problems"] == []
    assert [c["status"] for c in log.list()] == ["renderad"]


def test_campaign_run_uses_the_campaigns_settings_and_berget(settings, monkeypatch) -> None:
    from campaign import pipeline
    from campaign.profile import load_profile

    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    monkeypatch.chdir(settings.root)
    settings.berget_api_key = "test"
    mp4 = settings.work_dir / "123" / "123.mp4"
    calls: dict = {}

    def fake_download(vod_id, work_dir):
        mp4.parent.mkdir(parents=True, exist_ok=True)
        mp4.touch()
        return type("R", (), {"mp4_path": mp4})()

    monkeypatch.setattr(pipeline, "download_vod", fake_download)
    monkeypatch.setattr(pipeline, "transcribe_vod", lambda *a, **k: calls.setdefault("transcribe", k))
    monkeypatch.setattr(pipeline, "process_vod", lambda video, cfg, secrets, **k: calls.update(
        process=(video, cfg, k)))
    monkeypatch.setattr(pipeline, "finish_vod", lambda *a, **k: calls.setdefault("finish", k) and [])

    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    pipeline.make_clips("123", profile, settings, game="Bodycam", emit=lambda *a: None)

    assert calls["transcribe"]["language"] == "en"
    assert calls["transcribe"]["model"] == settings.transcribe_model
    video, cfg, kwargs = calls["process"]
    assert video.user_login == "skiimasktimmy"
    assert cfg.channels == ["skiimasktimmy"]
    assert cfg.reframe.webcam_rect == [1542, 770, 316, 224]
    assert kwargs["skip_download"] and kwargs["skip_transcribe"]
    assert calls["finish"]["game"] == "Bodycam"

    (mp4.parent / "transcript.json").write_text("{}", encoding="utf-8")
    calls.clear()
    pipeline.make_clips("123", profile, settings, game=None, emit=lambda *a: None)
    assert "transcribe" not in calls
