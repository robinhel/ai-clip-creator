"""Enkel redigerare: trimma ett klipp och byt rubrik. Testvideor görs med ffmpeg i tmp_path."""

from __future__ import annotations

import json
import os
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
    assert "Råklippet" in info["reason"]
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
