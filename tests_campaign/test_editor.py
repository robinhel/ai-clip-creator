"""Enkel redigerare: trimma ett klipp och byt rubrik. Testvideor görs med ffmpeg i tmp_path."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api, editor, editor_api, finish, subtitles, workflow
from campaign.caption import build_caption
from campaign.cliplog import ClipLog
from campaign.media import probe
from campaign.profile import load_profile
from campaign.workflow import finish_vod

from clipsmith.settings import CaptionConfig, ReframeConfig

from .conftest import PAKET, make_clip, make_overlay, needs_ffmpeg, needs_subtitles

pytestmark = needs_ffmpeg

RAW = "clip_01_no_way.mp4"


@pytest.fixture
def setup(settings, monkeypatch):
    """En kampanj med gränserna 3–8 s, overlay, en körning med spel och ett färdigt klipp på 10 s
    (för långt, så det är underkänt tills det trimmas)."""
    monkeypatch.setitem(finish.ENCODER_ARGS, "libx264", ["-c:v", "libx264", "-preset", "ultrafast"])  # snabbare test
    config = (PAKET / "config.timmy.yaml").read_text(encoding="utf-8")
    config = config.replace("min_seconds: 15", "min_seconds: 3").replace("max_seconds: 45", "max_seconds: 8")
    (settings.root / "config.timmy.yaml").write_text(config, encoding="utf-8")
    profile_path = settings.profiles_dir / "skiimasktimmy.yaml"
    profile_path.write_text(profile_path.read_text(encoding="utf-8").replace("min_seconds: 7 ", "min_seconds: 3 ")
                            .replace("max_seconds: 60 ", "max_seconds: 8  "), encoding="utf-8")
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
    assert (info["min_s"], info["max_s"], info["speed"]) == (3, 8, 1.0)  # ett Twitch-klipp har ingen val-fil
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
    {"in_s": 0.0, "out_s": 8.3}, {"in_s": 0.0, "out_s": 2.8},  # ingen marginal, samma gränser som webben
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


def test_range_errors_have_a_decimal_point(setup) -> None:
    client, _, clip_id = setup
    short = client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 0.0, "out_s": 2.5}).json()["detail"]
    assert short == "The clip will be 2.5 s. The campaign allows 3–8 s."
    outside = client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 5.0, "out_s": 4.0}).json()["detail"]
    assert re.match(r"Start and end must be within the clip: 0 to 10(\.\d+)? s,", outside), outside


# ---------- 3. postade och nekade ----------


@pytest.mark.parametrize("status", ["postad", "inskickad", "analytics_inskickad", "betald", "nekad"])
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
    assert "raw clip" in info["reason"]
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"title": "Ny"}).status_code == 400
    assert client.get("/campaign/clips/999/edit").status_code == 404


# ---------- 4. status efter redigering ----------


def test_approved_clip_must_be_approved_again(setup, settings) -> None:
    client, log, clip_id = setup
    assert log.get(clip_id)["status"] == "underkand"  # 10 s, kampanjen tillåter 8
    log.update(clip_id, status="godkand")
    response = client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0})
    assert response.json()["status"] == "renderad"
    assert response.json()["problems"] == []


def _scheduled(settings, clip_id, status: str) -> int:
    """En schemalagd uppladdning för klippet, som om Robin hade lagt den på Att posta."""
    from campaign.schedule import Schedule

    Schedule(settings)  # skapar tabellen
    with sqlite3.connect(settings.data_dir / "klipp.db") as db:
        return db.execute("INSERT INTO scheduled_posts (clip_id, platform, account, at, fields, status, created, "
                          "updated) VALUES (?, 'youtube', 'UC1', '2030-01-01T10:00:00+00:00', '{}', ?, '', '')",
                          (clip_id, status)).lastrowid


def _scheduled_status(settings, post_id: int) -> str:
    with sqlite3.connect(settings.data_dir / "klipp.db") as db:
        return db.execute("SELECT status FROM scheduled_posts WHERE id = ?", (post_id,)).fetchone()[0]


def test_editing_an_approved_clip_cancels_the_waiting_upload(setup, settings) -> None:
    client, log, clip_id = setup
    log.update(clip_id, status="godkand")
    post_id = _scheduled(settings, clip_id, "waiting")
    response = client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "renderad"
    assert _scheduled_status(settings, post_id) == "cancelled"


def test_editing_is_refused_while_the_approved_clip_uploads(setup, settings) -> None:
    client, log, clip_id = setup
    log.update(clip_id, status="godkand")
    post_id = _scheduled(settings, clip_id, "running")
    _, done, _ = _paths(settings)
    before = done.read_bytes()
    response = client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0})
    assert response.status_code == 409 and "being uploaded" in response.json()["detail"]
    assert (log.get(clip_id)["status"], done.read_bytes()) == ("godkand", before)  # filen byts inte under uppladdningen
    assert _scheduled_status(settings, post_id) == "running"
    assert not [p for p in settings.out_dir.rglob(".*")]


def test_problems_after_the_check_fail_the_clip_and_the_old_review_is_kept(setup, settings) -> None:
    client, log, clip_id = setup
    log.update(clip_id, review={"facecam_visible": False, "text_visible": True, "problems": []})
    response = client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0}).json()
    assert response["status"] == "underkand"
    assert "The image review found no facecam in the clip." in response["problems"]
    assert log.get(clip_id)["review"] == {"facecam_visible": False, "text_visible": True, "problems": []}

    (settings.root / "overlays" / "skiimasktimmy.png").unlink()
    log.update(clip_id, review=None)
    response = client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 0.0, "out_s": 6.0}).json()
    assert response["status"] == "underkand"
    assert any("The overlay is missing" in p for p in response["problems"])


def test_a_twitch_clip_without_sound_is_still_refused_after_edit(setup, settings) -> None:
    client, _, clip_id = setup
    raw, _, _ = _paths(settings)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30", "-t", "10",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(raw)], check=True)
    response = client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0}).json()
    assert response["status"] == "underkand"
    assert any("The clip has no sound." in p for p in response["problems"]), response["problems"]


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
    answer = client.post(f"/campaign/clips/{clip_id}/edit", json={"title": "Ny"})
    assert (answer.status_code, answer.json()["detail"]) == (409, "A project is running. Wait until it is done.")
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


def test_finish_again_keeps_the_edited_title_and_times(setup, settings) -> None:
    client, log, clip_id = setup
    _, done, _ = _paths(settings)
    response = client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0, "title": "Huge clutch"})
    assert response.status_code == 200, response.text
    log.update(clip_id, status="underkand")
    finish_vod("123", load_profile("skiimasktimmy", settings.profiles_dir), settings, log, game="Bodycam",
               review=False)
    clip = log.get(clip_id)
    assert (clip["title"], clip["start_s"], clip["end_s"]) == ("Huge clutch", 101.0, 107.0)
    assert probe(done)["duration"] == pytest.approx(6.0, abs=0.1)


def test_finish_again_on_a_new_raw_clip_uses_the_picks_not_the_old_edit(setup, settings) -> None:
    client, log, clip_id = setup
    raw, _, _ = _paths(settings)
    client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0, "title": "Huge clutch"})
    # En ny körning av samma VOD skriver ett nytt råklipp med samma namn; original/clip_01_no_way.json finns kvar.
    os.remove(raw)
    make_clip(raw, seconds=10)
    log.update(clip_id, status="underkand")
    finish_vod("123", load_profile("skiimasktimmy", settings.profiles_dir), settings, log, game="Bodycam",
               review=False)
    clip = log.get(clip_id)
    assert (clip["title"], clip["start_s"], clip["end_s"]) == ("No way", 100.0, 110.0)


# ---------- granskningen av redigeraren ----------


def _state(settings, log, clip_id):
    raw, done, original = _paths(settings)
    return log.get(clip_id)["status"], raw.read_bytes(), done.read_bytes(), original.parent.exists()


def test_rounded_end_from_the_web_is_accepted_and_does_not_re_encode(setup, settings) -> None:
    client, log, _ = setup
    raw = make_clip(settings.out_dir / "124" / "clip_01_long.mp4", seconds=31.27)
    done = raw.parent / "klara" / raw.name
    done.parent.mkdir()
    shutil.copy(raw, done)
    clip_id = log.add(vod_id="124", profile="skiimasktimmy", file=str(done), title="Long", caption="c",
                      start_s=500.0, end_s=531.27)
    duration = client.get(f"/campaign/clips/{clip_id}/edit").json()["original"]["duration_s"]
    assert abs(duration - 31.27) < 0.04
    mtime = done.stat().st_mtime_ns
    for body in ({"in_s": 0.0, "out_s": 31.3}, {"in_s": 0.0, "out_s": 31.3, "title": "Longer"}):
        response = client.post(f"/campaign/clips/{clip_id}/edit", json=body)
        assert response.status_code == 200, response.text
        assert done.stat().st_mtime_ns == mtime  # inget kodades om
        assert not (raw.parent / "original").exists()
    assert log.get(clip_id)["title"] == "Longer"
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 0.0, "out_s": 31.4}).status_code == 400


def test_end_a_little_past_the_original_is_cut_to_its_length(setup, settings) -> None:
    client, log, clip_id = setup
    duration = client.get(f"/campaign/clips/{clip_id}/edit").json()["original"]["duration_s"]
    response = client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 3.0, "out_s": round(duration + 0.04, 2)})
    assert response.status_code == 200, response.text
    assert log.get(clip_id)["end_s"] == pytest.approx(100.0 + duration, abs=0.001)
    assert client.get(f"/campaign/clips/{clip_id}/edit").json()["trim"]["out_s"] == duration


def test_a_failing_check_after_the_swap_leaves_no_old_yes(setup, settings, monkeypatch) -> None:
    client, log, clip_id = setup
    log.update(clip_id, status="godkand")

    def broken(*args, **kwargs):
        raise RuntimeError("trasig")

    monkeypatch.setattr(editor, "check_clip", broken)
    response = client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0})
    assert response.status_code == 500
    assert log.get(clip_id)["status"] != "godkand"


@pytest.mark.parametrize("step", ["_trim", "finish_clip"])
def test_failed_render_changes_nothing(setup, settings, monkeypatch, step) -> None:
    client, log, clip_id = setup
    log.update(clip_id, status="godkand")
    before = _state(settings, log, clip_id)

    def broken(*args, **kwargs):
        raise RuntimeError("ffmpeg misslyckades")

    monkeypatch.setattr(editor, step, broken)
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0}).status_code == 500
    assert _state(settings, log, clip_id) == before
    assert not [p for p in settings.out_dir.rglob(".*")]


def test_title_only_on_an_approved_clip_needs_a_new_yes(setup) -> None:
    client, log, clip_id = setup
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0}).json()["status"] == "renderad"
    log.update(clip_id, status="godkand")
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"title": "Ny"}).json()["status"] == "renderad"


def test_clip_posted_during_the_edit_is_not_changed(setup, settings, monkeypatch) -> None:
    client, log, clip_id = setup
    raw, done, _ = _paths(settings)
    before = (raw.read_bytes(), done.read_bytes())
    real = editor.finish_clip

    def finish_and_post(*args, **kwargs):
        result = real(*args, **kwargs)
        log.update(clip_id, status="postad")  # som om klippet postades under tiden
        return result

    monkeypatch.setattr(editor, "finish_clip", finish_and_post)
    response = client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0})
    assert response.status_code == 409
    assert log.get(clip_id)["status"] == "postad"
    assert (raw.read_bytes(), done.read_bytes()) == before
    assert not _paths(settings)[2].parent.exists()


def test_other_writes_wait_for_a_running_edit(setup, settings, fake_twitch, monkeypatch) -> None:
    from campaign import estimate, tiktok_api
    from clipsmith.db.session import init_db

    client, log, clip_id = setup
    init_db(settings.root / "clipsmith.db")  # POST /runs skapar en körning i clipsmiths databas
    client.app.include_router(tiktok_api.router)
    settings.anthropic_api_key = settings.berget_api_key = "test"
    monkeypatch.setattr(estimate, "disk_gb", lambda path: (100.0, 228.0))
    monkeypatch.setattr(api, "run_campaign_vod", lambda *a, **k: None)
    monkeypatch.setattr(api, "finish_run", lambda *a, **k: None)
    log.update(clip_id, status="godkand")
    other = log.add(vod_id="123", profile="skiimasktimmy", file="x.mp4", title="Annat", caption="c",
                    status="godkand", clip_format="funny")
    assert editor.lock.acquire(blocking=False)
    editor.editing = clip_id
    try:
        # Bara klippet som redigeras är låst. Andra klipp går att ändra under tiden.
        assert client.patch(f"/campaign/clips/{other}", json={"status": "nekad"}).status_code == 200
        assert client.patch(f"/campaign/clips/{clip_id}", json={"status": "nekad"}).status_code == 409
        assert client.patch(f"/campaign/clips/{clip_id}", json={"caption": "x"}).status_code == 409
        assert client.patch(f"/campaign/clips/{clip_id}", json={"note": "bra"}).status_code == 200
        assert client.post(f"/campaign/clips/{clip_id}/tiktok").status_code == 409
        run = {"profile": "skiimasktimmy", "vod_id": "2925991945"}
        assert client.post("/campaign/runs", json=run).status_code == 409
        assert client.post("/campaign/runs/1/finish").status_code == 409
        assert client.app.state.active_run_id is None
    finally:
        editor.editing = None
        editor.lock.release()
    assert log.get(clip_id)["status"] == "godkand"
    assert client.patch(f"/campaign/clips/{clip_id}", json={"status": "nekad"}).status_code == 200
    assert client.post("/campaign/runs/1/finish").status_code == 202


def test_limits_are_the_same_as_in_the_rule_check(setup, settings) -> None:
    client, _, clip_id = setup
    config = settings.root / "config.timmy.yaml"
    config.write_text(config.read_text().replace("max_seconds: 8", "max_seconds: 45"))
    info = client.get(f"/campaign/clips/{clip_id}/edit").json()
    assert (info["min_s"], info["max_s"]) == (3, 8)  # profilens, som check_clip använder


def test_the_original_json_is_written_after_the_raw_clip(setup, settings) -> None:
    client, _, clip_id = setup
    raw, _, original = _paths(settings)
    client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0})
    meta = json.loads(original.with_suffix(".json").read_text())
    assert (meta["raw_size"], meta["raw_mtime_ns"]) == (raw.stat().st_size, raw.stat().st_mtime_ns)


def test_an_old_original_is_not_used_for_a_new_raw_clip(setup, settings) -> None:
    client, _, clip_id = setup
    raw, _, original = _paths(settings)
    client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0})
    old_original = original.read_bytes()
    # En ny körning av samma VOD skriver ett nytt råklipp med samma namn (clipsmith tömmer inte out/<vod>/).
    os.remove(raw)
    make_clip(raw, seconds=5)
    info = client.get(f"/campaign/clips/{clip_id}/edit").json()
    assert info["original"]["duration_s"] == pytest.approx(5.0, abs=0.1)
    assert info["trim"] == {"in_s": 0.0, "out_s": info["original"]["duration_s"]}
    assert client.get(f"/campaign/clips/{clip_id}/original").content == raw.read_bytes()

    new_raw = raw.read_bytes()
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 0.5, "out_s": 4.5}).status_code == 200
    assert original.read_bytes() == new_raw != old_original


# ---------- undertexten som ord (väg B) ----------

WORDS = [{"start": 0.5, "end": 0.9, "word": "Bro,"}, {"start": 1.0, "end": 1.5, "word": "no"},
         {"start": 2.0, "end": 2.6, "word": "wei"}, {"start": 6.0, "end": 6.5, "word": "wow"}]
BURNED = ("The subtitles are burned into this clip, so words cannot be corrected. It works for clips cut "
          "from now on.")


def _words(settings) -> None:
    raw = _paths(settings)[0]
    subtitles.save_words(raw, WORDS, CaptionConfig(enabled=True, style="box"), ReframeConfig(mode="center"), raw)


def test_an_old_clip_has_no_editable_subtitles(setup) -> None:
    client, _, clip_id = setup
    info = client.get(f"/campaign/clips/{clip_id}/edit").json()
    assert info["subtitles"] == {"editable": False, "reason": BURNED, "words": [], "style": None}
    answer = client.post(f"/campaign/clips/{clip_id}/edit", json={"words": WORDS})
    assert answer.status_code == 400 and answer.json()["detail"] == BURNED


def test_a_campaign_without_subtitles_says_so(setup, settings) -> None:
    client, _, clip_id = setup
    config = settings.root / "config.timmy.yaml"
    config.write_text(config.read_text(encoding="utf-8").replace("caption:\n  enabled: true",
                                                                 "caption:\n  enabled: false"), encoding="utf-8")
    info = client.get(f"/campaign/clips/{clip_id}/edit").json()
    assert info["subtitles"] == {"editable": False, "reason": "The campaign has no subtitles.", "words": [],
                                 "style": None}


def test_a_clip_with_a_word_file_shows_its_words(setup, settings) -> None:
    client, _, clip_id = setup
    _words(settings)
    assert client.get(f"/campaign/clips/{clip_id}/edit").json()["subtitles"] == {
        "editable": True, "reason": None, "words": WORDS, "style": "box"}


def test_finish_burns_the_words_only_when_the_word_file_belongs_to_the_raw_clip(setup, settings,
                                                                                 monkeypatch) -> None:
    _, log, clip_id = setup
    raw = _paths(settings)[0]
    seen: list = []
    real = workflow.finish_clip

    def spy(*args, **kwargs):
        ass = kwargs.get("subtitles")
        seen.append(ass and ass.read_text(encoding="utf-8-sig"))
        return real(*args, **kwargs)

    monkeypatch.setattr(workflow, "finish_clip", spy)
    log.update(clip_id, status="underkand")
    finish_vod("123", load_profile("skiimasktimmy", settings.profiles_dir), settings, log, review=False)
    assert seen == [None]  # ingen ordfil: ingen undertext, den kan redan vara inbränd

    _words(settings)
    log.update(clip_id, status="underkand")
    finish_vod("123", load_profile("skiimasktimmy", settings.profiles_dir), settings, log, review=False)
    assert seen[1] and "WEI" in seen[1]
    assert not list(raw.parent.glob(".*.ass"))  # den tillfälliga filen är borta


def test_a_corrected_word_is_burned_and_survives_a_new_trim(setup, settings, monkeypatch) -> None:
    client, log, clip_id = setup
    raw, done, _ = _paths(settings)
    _words(settings)
    burned: list = []
    real = editor.finish_clip

    def spy(*args, **kwargs):
        ass = kwargs.get("subtitles")
        burned.append(ass and ass.read_text(encoding="utf-8-sig"))
        return real(*args, **kwargs)

    monkeypatch.setattr(editor, "finish_clip", spy)
    fixed = [*WORDS[:2], {"start": 2.0, "end": 2.6, "word": "way!"}]  # wei rättat, wow borttaget
    answer = client.post(f"/campaign/clips/{clip_id}/edit", json={"words": fixed})
    assert answer.status_code == 200, answer.text
    assert "WAY!" in burned[-1] and "WEI" not in burned[-1] and "WOW" not in burned[-1]
    assert client.get(f"/campaign/clips/{clip_id}/edit").json()["subtitles"]["words"] == fixed

    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0}).status_code == 200
    assert "WAY!" in burned[-1] and "BRO" not in burned[-1]  # undertexten för 1–7 s
    assert client.get(f"/campaign/clips/{clip_id}/edit").json()["subtitles"]["words"] == fixed
    assert subtitles.load_words(raw, editor.source_path(raw))["words"] == fixed
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 0.5, "out_s": 6.0}).status_code == 200
    assert client.get(f"/campaign/clips/{clip_id}/edit").json()["subtitles"]["words"] == fixed
    assert not [p for p in done.parent.parent.rglob(".*")]  # inga tmp-filer kvar


def test_same_words_again_do_not_re_encode(setup, settings, monkeypatch) -> None:
    client, _, clip_id = setup
    _words(settings)
    calls: list = []
    monkeypatch.setattr(editor, "finish_clip", lambda *a, **k: calls.append(k))
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"words": WORDS}).status_code == 200
    assert calls == []


@pytest.mark.parametrize("words", [
    [{"start": 0.5, "end": 0.9, "word": "   "}],
    [{"start": 0.5, "end": 0.9, "word": "x" * 41}],
    [{"start": 0.5, "end": 0.9, "word": "two\nlines"}],
    [{"start": 0.5, "end": 0.9, "word": "tab\there"}],
    [{"start": 0.5, "end": 0.9, "word": "nul\x00"}],
    [{"start": 0.9, "end": 0.9, "word": "a"}],
    [{"start": -0.1, "end": 0.9, "word": "a"}],
    [{"start": 9.0, "end": 10.6, "word": "a"}],
    [{"start": 2.0, "end": 2.5, "word": "a"}, {"start": 1.0, "end": 1.5, "word": "b"}],
    [{"start": 0.001 * i, "end": 0.001 * i + 0.0005, "word": "a"} for i in range(1001)],
])
def test_bad_words_are_refused_and_change_nothing(setup, settings, words) -> None:
    client, log, clip_id = setup
    raw, done, _ = _paths(settings)
    _words(settings)
    word_file = subtitles.words_path(raw)
    before = (log.get(clip_id), done.read_bytes(), raw.read_bytes(), word_file.read_bytes())
    answer = client.post(f"/campaign/clips/{clip_id}/edit", json={"words": words})
    assert answer.status_code == 400 and isinstance(answer.json()["detail"], str)
    assert (log.get(clip_id), done.read_bytes(), raw.read_bytes(), word_file.read_bytes()) == before


@needs_subtitles
def test_identity_survives_the_copy_of_the_first_trim(setup, settings) -> None:
    client, _, clip_id = setup
    raw, _, original = _paths(settings)
    _words(settings)
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0}).status_code == 200
    assert (original.stat().st_size, original.stat().st_mtime_ns) == tuple(
        json.loads(subtitles.words_path(raw).read_text())[k] for k in ("raw_size", "raw_mtime_ns"))



def test_a_word_file_for_an_older_raw_clip_says_so(setup, settings) -> None:
    client, _, clip_id = setup
    raw = _paths(settings)[0]
    _words(settings)
    data = json.loads(subtitles.words_path(raw).read_text())
    subtitles.words_path(raw).write_text(json.dumps({**data, "raw_size": data["raw_size"] + 1}))
    info = client.get(f"/campaign/clips/{clip_id}/edit").json()
    assert info["subtitles"] == {"editable": False, "words": [], "style": None,
                                 "reason": "The word list no longer belongs to the clip, so words cannot be corrected."}


def test_words_are_saved_only_after_the_new_clip_is_in_place(setup, settings, monkeypatch) -> None:
    _, log, clip_id = setup
    raw, done, _ = _paths(settings)
    _words(settings)
    before = subtitles.words_path(raw).read_bytes()
    real = os.replace

    def fail_on_done(src, dst):
        if str(dst) == str(done):
            raise OSError("disken är full")
        return real(src, dst)

    monkeypatch.setattr(editor.os, "replace", fail_on_done)
    fixed = [*WORDS[:2], {"start": 2.0, "end": 2.6, "word": "way!"}]
    with pytest.raises(OSError):
        editor.edit_clip(log.get(clip_id), load_profile("skiimasktimmy", settings.profiles_dir), settings, log,
                         words=fixed)
    assert subtitles.words_path(raw).read_bytes() == before


# ---------- tidskartan: tystnad och utfyllnadsord ----------


def _plans(module, monkeypatch) -> list:
    seen: list = []
    real = module.finish_clip

    def spy(*args, **kwargs):
        seen.append(kwargs.get("plan"))
        return real(*args, **kwargs)

    monkeypatch.setattr(module, "finish_clip", spy)
    return seen


def test_an_old_clip_without_a_word_file_is_finished_without_pieces(setup, settings, monkeypatch) -> None:
    _, log, clip_id = setup
    seen = _plans(workflow, monkeypatch)
    log.update(clip_id, status="underkand")
    finish_vod("123", load_profile("skiimasktimmy", settings.profiles_dir), settings, log, review=False)
    assert seen == [None]


def test_the_edit_and_the_run_cut_the_same_filler_word(setup, settings, monkeypatch) -> None:
    client, log, clip_id = setup
    raw = _paths(settings)[0]
    words = [*WORDS[:3], {"start": 3.0, "end": 3.4, "word": "uh"}, WORDS[3]]
    subtitles.save_words(raw, words, CaptionConfig(enabled=True, style="box"), ReframeConfig(mode="center"), raw)
    edits = _plans(editor, monkeypatch)
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0}).status_code == 200
    assert edits[-1]["pieces"][:2] == [(0.0, 2.0), (2.4, edits[-1]["pieces"][1][1])]  # 3,0–3,4 i originalet
    assert edits[-1]["fillers_cut"] == 1
    runs = _plans(workflow, monkeypatch)
    log.update(clip_id, status="underkand")
    finish_vod("123", load_profile("skiimasktimmy", settings.profiles_dir), settings, log, review=False)
    assert runs == [edits[-1]]


# ---------- tidskartan: hook först ----------


def test_the_hook_stays_when_words_are_corrected_and_comes_first_when_it_fits(setup, settings, monkeypatch) -> None:
    client, _, clip_id = setup
    raw = _paths(settings)[0]
    subtitles.save_words(raw, WORDS, CaptionConfig(enabled=True, style="box"), ReframeConfig(mode="center"), raw,
                         hook=[3.5, 5.0])
    plans = _plans(editor, monkeypatch)
    fixed = [*WORDS[:2], {"start": 2.0, "end": 2.6, "word": "way!"}, WORDS[3]]
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"words": fixed}).status_code == 200
    assert plans[-1] is None  # 10 s + 1,5 s hook är längre än kampanjens 8 s
    assert json.loads(subtitles.words_path(raw).read_text())["hook"] == [3.5, 5.0]
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 0.5, "out_s": 6.5}).status_code == 200
    assert plans[-1]["cold_open"] and plans[-1]["pieces"] == [(3.0, 4.5), (0.0, plans[-1]["pieces"][1][1])]
    assert json.loads(subtitles.words_path(raw).read_text())["hook"] == [3.5, 5.0]
