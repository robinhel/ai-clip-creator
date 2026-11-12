"""Enkel redigerare: trimma ett klipp och byt rubrik. Testvideor görs med ffmpeg i tmp_path."""

from __future__ import annotations

import json
import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api, editor, editor_api, finish
from campaign.caption import build_caption
from campaign.cliplog import ClipLog
from campaign.media import probe
from campaign.profile import load_profile
from campaign.workflow import finish_vod

from .conftest import PAKET, make_clip, make_overlay, needs_ffmpeg

pytestmark = needs_ffmpeg

RAW = "clip_01_no_way.mp4"


@pytest.fixture
def setup(settings, monkeypatch):
    """En kampanj med gränserna 3–8 s, overlay, en körning med spel och ett färdigt klipp på 10 s."""
    monkeypatch.setitem(finish.ENCODER_ARGS, "libx264", ["-c:v", "libx264", "-preset", "ultrafast"])  # snabbare test
    config = (PAKET / "config.timmy.yaml").read_text(encoding="utf-8")
    config = config.replace("min_seconds: 15", "min_seconds: 3").replace("max_seconds: 45", "max_seconds: 8")
    (settings.root / "config.timmy.yaml").write_text(config, encoding="utf-8")
    profile_path = settings.profiles_dir / "skiimasktimmy.yaml"
    profile_path.write_text(profile_path.read_text(encoding="utf-8").replace("min_seconds: 7 ", "min_seconds: 3 "),
                            encoding="utf-8")
    make_overlay(settings.root / "overlays" / "skiimasktimmy.png")
    make_clip(settings.out_dir / "123" / RAW, seconds=10)
    picks = settings.work_dir / "123" / "picks.json"
    picks.parent.mkdir(parents=True)
    picks.write_text(json.dumps([{"candidate": {}, "pick": {
        "start_offset_s": 100, "end_offset_s": 110, "title_es": "No way", "score": 80, "reason": "Hook."}}]))
    log = ClipLog(settings.data_dir / "klipp.db")
    log.add_run(1, "123", "skiimasktimmy", "Bodycam")
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    (result,) = finish_vod("123", profile, settings, log, game="Bodycam", review=False)
    log.update(result["id"], format="funny")
    monkeypatch.delenv("CLIPSMITH_API_KEY", raising=False)
    app = FastAPI()
    app.state.active_run_id = None
    app.include_router(api.router)
    app.include_router(editor_api.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    return TestClient(app), log, result["id"]


def _paths(settings):
    vod = settings.out_dir / "123"
    return vod / RAW, vod / "klara" / RAW, vod / "original" / RAW


# ---------- 1. trimning ----------


def test_trim_gives_the_right_length_and_keeps_the_original(setup, settings) -> None:
    client, log, clip_id = setup
    raw, done, original = _paths(settings)
    raw_bytes = raw.read_bytes()
    info = client.get(f"/campaign/clips/{clip_id}/edit").json()
    assert info["editable"] is True and info["reason"] is None
    assert (info["min_s"], info["max_s"]) == (3, 8)
    assert info["original"]["duration_s"] == pytest.approx(10.0, abs=0.1)
    assert (info["original"]["start_s"], info["original"]["url"]) == (100.0, f"/campaign/clips/{clip_id}/original")
    assert info["trim"]["in_s"] == 0.0
    assert info["trim"]["out_s"] == pytest.approx(10.0, abs=0.1)
    assert not original.parent.exists()  # GET skapar inget

    response = client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0})
    assert response.status_code == 200, response.text
    assert "?v=" in response.json()["video_url"]
    assert probe(done)["duration"] == pytest.approx(6.0, abs=0.1)
    clip = log.get(clip_id)
    assert (clip["start_s"], clip["end_s"]) == (101.0, 107.0)
    assert original.read_bytes() == raw_bytes
    assert client.get(f"/campaign/clips/{clip_id}/edit").json()["trim"] == {"in_s": 1.0, "out_s": 7.0}

    # Andra trimningen utgår från originalet: 4,5–9,5 finns bara där, inte i det trimmade klippet på 6 s.
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 4.5, "out_s": 9.5}).status_code == 200
    assert probe(done)["duration"] == pytest.approx(5.0, abs=0.1)
    clip = log.get(clip_id)
    assert (clip["start_s"], clip["end_s"]) == (104.5, 109.5)
    assert original.read_bytes() == raw_bytes
    assert sorted(p.name for p in original.parent.iterdir()) == ["clip_01_no_way.json", RAW]
    assert not [p for p in done.parent.parent.rglob(".*")]  # inga tmp-filer kvar


# ---------- 2. fel intervall ----------


@pytest.mark.parametrize("body", [
    {"in_s": 5.0, "out_s": 4.0}, {"in_s": -1.0, "out_s": 5.0}, {"in_s": 0.0, "out_s": 11.0},
    {"in_s": 0.0, "out_s": 2.0}, {"in_s": 0.0, "out_s": 9.5}, {"title": "   "},
])
def test_bad_ranges_are_refused_and_change_nothing(setup, settings, body) -> None:
    client, log, clip_id = setup
    raw, done, original = _paths(settings)
    before = (log.get(clip_id), done.read_bytes(), raw.read_bytes())
    response = client.post(f"/campaign/clips/{clip_id}/edit", json=body)
    assert response.status_code == 400
    assert response.json()["detail"]
    assert (log.get(clip_id), done.read_bytes(), raw.read_bytes()) == before
    assert not original.parent.exists()


# ---------- 3. postade och nekade ----------


@pytest.mark.parametrize("status", ["postad", "inskickad", "betald", "nekad"])
def test_posted_and_rejected_clips_cannot_be_edited(setup, settings, status) -> None:
    client, log, clip_id = setup
    log.update(clip_id, status=status)
    info = client.get(f"/campaign/clips/{clip_id}/edit").json()
    assert info["editable"] is False
    assert info["reason"]
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0}).status_code == 400
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"title": "Ny"}).status_code == 400
    assert log.get(clip_id)["status"] == status
    assert not _paths(settings)[2].parent.exists()


def test_clip_without_its_raw_clip_cannot_be_edited(setup, settings) -> None:
    client, _, clip_id = setup
    _paths(settings)[0].unlink()
    info = client.get(f"/campaign/clips/{clip_id}/edit").json()
    assert (info["editable"], info["original"]) == (False, None)
    assert "Råklippet" in info["reason"]
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"title": "Ny"}).status_code == 400
    assert client.get("/campaign/clips/999/edit").status_code == 404


# ---------- 4. status efter redigering ----------


def test_approved_clip_must_be_approved_again(setup, settings) -> None:
    client, log, clip_id = setup
    assert log.get(clip_id)["status"] == "renderad"
    log.update(clip_id, status="godkand")
    response = client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0})
    assert response.json()["status"] == "renderad"
    assert response.json()["problems"] == []


def test_problems_after_the_check_fail_the_clip_and_the_old_review_is_kept(setup, settings) -> None:
    client, log, clip_id = setup
    log.update(clip_id, review={"facecam_visible": False, "text_visible": True, "problems": []})
    response = client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0}).json()
    assert response["status"] == "underkand"
    assert "Bildgranskningen hittade ingen facecam i klippet." in response["problems"]
    assert log.get(clip_id)["review"] == {"facecam_visible": False, "text_visible": True, "problems": []}

    (settings.root / "overlays" / "skiimasktimmy.png").unlink()
    log.update(clip_id, review=None)
    response = client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 0.0, "out_s": 6.0}).json()
    assert response["status"] == "underkand"
    assert any("Overlayen saknas" in p for p in response["problems"])


# ---------- 5. rubrik och caption ----------


def test_new_title_builds_a_new_caption_from_the_template(setup, settings) -> None:
    client, log, clip_id = setup
    _, done, _ = _paths(settings)
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    assert log.get(clip_id)["caption"] == build_caption("No way", profile, "Bodycam")
    mtime = done.stat().st_mtime_ns
    response = client.post(f"/campaign/clips/{clip_id}/edit", json={"title": "  Huge clutch  "})
    assert response.status_code == 200, response.text
    clip = log.get(clip_id)
    assert clip["title"] == "Huge clutch"
    assert clip["caption"] == build_caption("Huge clutch", profile, "Bodycam")
    assert (done.with_suffix(".txt")).read_text(encoding="utf-8") == clip["caption"] + "\n"
    assert done.stat().st_mtime_ns == mtime  # bara rubriken: klippet görs inte om
    assert not _paths(settings)[2].parent.exists()


def test_hand_written_caption_is_kept(setup, settings) -> None:
    client, log, clip_id = setup
    own = "My own words twitch.tv/skiimasktimmy #skiimasktimmy #twitchclips #fps #gaming"
    log.update(clip_id, caption=own)
    client.post(f"/campaign/clips/{clip_id}/edit", json={"title": "Huge clutch", "in_s": 1.0, "out_s": 7.0})
    clip = log.get(clip_id)
    assert (clip["title"], clip["caption"]) == ("Huge clutch", own)


# ---------- 6. API-nyckeln, lås och körning ----------


def test_post_needs_the_api_key(setup, monkeypatch) -> None:
    client, log, clip_id = setup
    monkeypatch.setenv("CLIPSMITH_API_KEY", "nyckel")
    before = log.get(clip_id)
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"title": "Ny"}).status_code == 401
    assert log.get(clip_id) == before
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"title": "Ny"},
                       headers={"X-Api-Key": "nyckel"}).status_code == 200


def test_running_job_or_another_edit_gives_409(setup) -> None:
    client, _, clip_id = setup
    client.app.state.active_run_id = 5
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"title": "Ny"}).status_code == 409
    client.app.state.active_run_id = None
    assert editor.lock.acquire(blocking=False)
    try:
        assert client.post(f"/campaign/clips/{clip_id}/edit", json={"title": "Ny"}).status_code == 409
    finally:
        editor.lock.release()


# ---------- 7. originalet med Range ----------


def test_original_supports_range(setup, settings) -> None:
    client, _, clip_id = setup
    raw, _, original = _paths(settings)
    response = client.get(f"/campaign/clips/{clip_id}/original", headers={"Range": "bytes=0-99"})
    assert response.status_code == 206
    assert response.headers["content-type"] == "video/mp4"
    assert response.content == raw.read_bytes()[:100]

    raw_bytes = raw.read_bytes()
    client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0})
    assert client.get(f"/campaign/clips/{clip_id}/original").content == raw_bytes == original.read_bytes()


def test_raw_outside_the_out_folder_is_not_served(setup, settings, tmp_path) -> None:
    client, log, clip_id = setup
    outside = shutil.copy(_paths(settings)[1], tmp_path / RAW)
    log.update(clip_id, file=str(outside))
    assert client.get(f"/campaign/clips/{clip_id}/original").status_code == 404
    assert client.get(f"/campaign/clips/{clip_id}/edit").json()["editable"] is False
