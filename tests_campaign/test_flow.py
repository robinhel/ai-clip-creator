"""Flödet som OpusClip: VOD-fakta, betyg, leta efter, längd och förlopp per steg.
Inga riktiga AI-, Berget- eller Twitch-anrop."""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from campaign import api, estimate
from campaign.cliplog import ClipLog

from .conftest import PAKET, make_clip, needs_ffmpeg
from .test_api import client  # noqa: F401  (fixture)

VOD = "2925991945"


@pytest.fixture
def started(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(api, "run_campaign_vod", lambda *a, **k: calls.append((a, k)))
    return calls


@pytest.fixture
def roomy_disk(monkeypatch):
    monkeypatch.setattr(estimate, "disk_gb", lambda path: (100.0, 228.0))


# ---------- klipploggen ----------


def test_old_cliplog_is_upgraded_without_losing_data(tmp_path) -> None:
    path = tmp_path / "klipp.db"
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE clips (id INTEGER PRIMARY KEY AUTOINCREMENT, vod_id TEXT, profile TEXT,
        start_s REAL, end_s REAL, title TEXT, format TEXT, signals TEXT, file TEXT, caption TEXT,
        status TEXT, problems TEXT, warnings TEXT, review TEXT, platform TEXT, url TEXT,
        views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER, note TEXT, created TEXT, updated TEXT)""")
    db.execute("CREATE TABLE runs (run_id INTEGER PRIMARY KEY, vod_id TEXT, profile TEXT, game TEXT)")
    db.execute("INSERT INTO clips (vod_id, title, status, views) VALUES ('1', 'Gammal', 'inskickad', 3000)")
    db.execute("INSERT INTO runs VALUES (1, '1', 'skiimasktimmy', 'Bodycam')")
    db.commit()
    db.close()

    log = ClipLog(path)
    clip = log.get(1)
    assert (clip["title"], clip["status"], clip["views"]) == ("Gammal", "inskickad", 3000)
    assert clip["score"] is None
    assert log.run(1)["game"] == "Bodycam"
    assert log.run(1)["title"] is None
    ClipLog(path)  # att öppna igen ska inte lägga till kolumnerna en gång till

    log.add_run(2, VOD, "skiimasktimmy", None, focus="fails", min_s=15, max_s=30, title="T",
                channel_name="SkiiMaskTimmy", thumbnail_url="https://x/y.jpg", duration_s=13950)
    run = log.run(2)
    assert (run["focus"], run["min_s"], run["max_s"], run["duration_s"]) == ("fails", 15, 30, 13950)
    assert run["created"]


# ---------- betyg ----------


def test_picks_bring_score_and_reason_to_the_cliplog(tmp_path) -> None:
    from campaign.workflow import load_picks

    picks = tmp_path / "picks.json"
    picks.write_text(json.dumps([
        {"candidate": {"sources": ["chat"]},
         "pick": {"start_offset_s": 1, "end_offset_s": 20, "title_es": "A", "score": 88, "reason": "Big hook."}},
        {"candidate": {}, "pick": {"start_offset_s": 30, "end_offset_s": 50, "title_es": "B"}},
    ]), encoding="utf-8")
    first, old = load_picks(picks)
    assert (first["score"], first["reason"]) == (88, "Big hook.")
    assert (old["score"], old["reason"]) == (None, "")


@needs_ffmpeg
def test_finished_clip_keeps_claudes_score_and_reason(settings) -> None:
    from campaign.profile import load_profile
    from campaign.workflow import finish_vod

    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    make_clip(settings.out_dir / "123" / "clip_01_no_way.mp4")
    picks = settings.work_dir / "123" / "picks.json"
    picks.parent.mkdir(parents=True)
    picks.write_text(json.dumps([{"candidate": {}, "pick": {
        "start_offset_s": 10, "end_offset_s": 30, "title_es": "No way", "score": 91, "reason": "Clutch."}}]))
    log = ClipLog(settings.data_dir / "klipp.db")
    finish_vod("123", load_profile("skiimasktimmy", settings.profiles_dir), settings, log, review=False)
    clip = log.list()[0]
    assert (clip["score"], clip["reason"]) == (91, "Clutch.")


# ---------- leta efter och längd ----------


def test_campaign_run_passes_focus_and_its_own_clip_length(settings, monkeypatch) -> None:
    from campaign import pipeline
    from campaign.profile import load_profile

    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    mp4 = settings.work_dir / "123" / "123.mp4"
    mp4.parent.mkdir(parents=True)
    mp4.touch()
    (mp4.parent / "transcript.json").write_text("{}", encoding="utf-8")
    calls: dict = {}
    monkeypatch.setattr(pipeline, "download_vod", lambda vod_id, work_dir: mp4)
    monkeypatch.setattr(pipeline, "process_vod", lambda video, cfg, secrets, **k: calls.update(
        process=(cfg, k)))
    monkeypatch.setattr(pipeline, "finish_vod", lambda *a, **k: [])

    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    pipeline.make_clips("123", profile, settings, game=None, emit=lambda *a: None,
                        focus="jump scares", min_s=15, max_s=30)
    cfg, kwargs = calls["process"]
    assert kwargs["focus"] == "jump scares"
    assert (cfg.clip.min_seconds, cfg.clip.max_seconds) == (15, 30)
    assert pipeline.clipsmith_config(profile, settings).clip.max_seconds == 45  # filen är orörd

    pipeline.make_clips("123", profile, settings, game=None, emit=lambda *a: None)
    cfg, kwargs = calls["process"]
    assert kwargs["focus"] == ""
    assert cfg.clip.max_seconds == 45


# ---------- VOD-fakta ----------


def test_vod_facts_estimate_and_campaigns(client, settings, fake_twitch, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(estimate, "disk_gb", lambda path: (9.7, 228.0))
    vod = client.get(f"/campaign/vod/{VOD}").json()
    assert vod["title"] == "3x Playtest Key GIVEAWAY"
    assert (vod["channel"], vod["channel_name"], vod["game"]) == ("skiimasktimmy", "SkiiMaskTimmy", "Bodycam")
    assert vod["duration_s"] == 13950
    assert vod["created_at"] == "2026-11-11T18:01:44Z"
    assert vod["thumbnail_url"].endswith("640x360.jpg")
    assert vod["downloaded"] is False
    assert vod["campaigns"] == ["skiimasktimmy"]
    est = vod["estimate"]
    assert est["download_gb"] == pytest.approx(13.0, abs=0.1)
    assert est["disk_free_gb"] == 9.7
    assert est["fits"] is False
    assert est["minutes"] > 8
    assert 0 < est["cost_usd"] < 5

    client.get(f"/campaign/vod/{VOD}")
    assert fake_twitch.calls == 1  # svaret från Twitch ligger i minnet

    mp4 = settings.work_dir / VOD / f"{VOD}.mp4"
    mp4.parent.mkdir(parents=True)
    mp4.touch()
    vod = client.get(f"/campaign/vod/{VOD}").json()
    assert vod["downloaded"] is True
    assert vod["estimate"]["fits"] is True


def test_vod_from_another_channel_has_no_campaign_and_errors_are_clear(client, fake_twitch) -> None:  # noqa: F811
    fake_twitch.videos["111111111"] = {"title": "x", "lengthSeconds": 60, "createdAt": "", "game": None,
                                       "owner": {"login": "someoneelse", "displayName": "SomeoneElse"}}
    assert client.get("/campaign/vod/111111111").json()["campaigns"] == []
    assert client.get("/campaign/vod/999999999").status_code == 404
    assert client.get("/campaign/vod/abc").status_code == 422

    fake_twitch.error = OSError("nere")
    response = client.get("/campaign/vod/222222222")
    assert response.status_code == 502
    assert response.json()["detail"]


def test_estimate_fits_needs_ten_percent_and_one_gb_extra() -> None:
    size = estimate.estimate(13950, free_gb=100, downloaded=False)["download_gb"]
    assert estimate.estimate(13950, free_gb=size * 1.1 + 1.01, downloaded=False)["fits"] is True
    assert estimate.estimate(13950, free_gb=size * 1.1 + 0.9, downloaded=False)["fits"] is False
    assert estimate.estimate(13950, free_gb=0, downloaded=True)["fits"] is True


def test_profiles_show_the_campaigns_clip_length(client) -> None:  # noqa: F811
    timmy = next(p for p in client.get("/campaign/profiles").json() if p["name"] == "skiimasktimmy")
    assert (timmy["clip_min_s"], timmy["clip_max_s"]) == (15, 45)


# ---------- starta en körning ----------


def test_new_run_checks_channel_length_and_disk(client, settings, fake_twitch, started,  # noqa: F811
                                                monkeypatch) -> None:
    fake_twitch.videos["111111111"] = {"title": "x", "lengthSeconds": 60, "createdAt": "", "game": None,
                                       "owner": {"login": "someoneelse", "displayName": "SomeoneElse"}}
    body = {"profile": "skiimasktimmy", "vod_id": VOD}
    monkeypatch.setattr(estimate, "disk_gb", lambda path: (100.0, 228.0))

    wrong = client.post("/campaign/runs", json={**body, "vod_id": "111111111"})
    assert wrong.status_code == 400
    assert "SomeoneElse" in wrong.json()["detail"]
    for lengths in ({"min_s": 10, "max_s": 30}, {"min_s": 30, "max_s": 20}, {"max_s": 60}, {"min_s": 45}):
        response = client.post("/campaign/runs", json={**body, **lengths})
        assert response.status_code == 400, lengths
        assert "15" in response.json()["detail"] and "45" in response.json()["detail"]
    assert client.post("/campaign/runs", json={**body, "focus": "x" * 301}).status_code == 422

    monkeypatch.setattr(estimate, "disk_gb", lambda path: (9.7, 228.0))
    full = client.post("/campaign/runs", json=body)
    assert full.status_code == 400
    assert "Disken" in full.json()["detail"]
    assert started == []

    mp4 = settings.work_dir / VOD / f"{VOD}.mp4"
    mp4.parent.mkdir(parents=True)
    mp4.touch()
    assert client.post("/campaign/runs", json=body).status_code == 201  # redan nedladdad


def test_new_run_saves_vod_facts_and_passes_focus_and_length(client, settings, started,  # noqa: F811
                                                             roomy_disk) -> None:
    response = client.post("/campaign/runs", json={
        "profile": "skiimasktimmy", "vod_id": VOD, "game": "Bodycam",
        "focus": "  funny fails  ", "min_s": 15, "max_s": 30})
    assert response.status_code == 201
    run_id = response.json()["id"]
    _, kwargs = started[0]
    assert (kwargs["focus"], kwargs["min_s"], kwargs["max_s"]) == ("funny fails", 15, 30)

    meta = ClipLog(settings.data_dir / "klipp.db").run(run_id)
    assert meta["title"] == "3x Playtest Key GIVEAWAY"
    assert meta["channel_name"] == "SkiiMaskTimmy"
    assert meta["duration_s"] == 13950
    assert meta["thumbnail_url"].endswith(".jpg")
    assert (meta["focus"], meta["min_s"], meta["max_s"]) == ("funny fails", 15, 30)

    started.clear()
    client.app.state.active_run_id = None
    client.post("/campaign/runs", json={"profile": "skiimasktimmy", "vod_id": VOD})
    _, kwargs = started[0]
    assert (kwargs["focus"], kwargs["min_s"], kwargs["max_s"]) == ("", 15, 45)  # kampanjens gränser


# ---------- förloppet ----------


def _run_with_events(settings, stages: list[str], status: str = "running", minutes_ago: int = 30) -> int:
    from clipsmith.db.models import PipelineEvent, Run, RunStatus
    from clipsmith.db.session import get_session

    db = get_session()
    start = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    run = Run(vod_id=VOD, channel="skiimasktimmy", status=RunStatus(status), created_at=start)
    db.add(run)
    db.commit()
    for i, stage in enumerate(stages):
        db.add(PipelineEvent(run_id=run.id, stage=stage, pct=float(i * 10), created_at=start + timedelta(minutes=i)))
    if status == "failed":
        run.error = "Berget svarade inte"
    db.commit()
    run_id = run.id
    db.close()
    ClipLog(settings.data_dir / "klipp.db").add_run(run_id, VOD, "skiimasktimmy", "Bodycam",
                                                     duration_s=13950, title="T")
    return run_id


def test_run_detail_shows_steps_from_clipsmiths_events(client, settings) -> None:  # noqa: F811
    # Kampanjen skickar download och transcribe, sedan skickar clipsmith download igen.
    run_id = _run_with_events(settings, ["download", "transcribe", "download", "transcribe", "chat"])
    run = client.get(f"/campaign/runs/{run_id}").json()
    assert (run["status"], run["stage"], run["error"]) == ("running", "moments", None)
    assert run["pct"] == 40.0
    steps = {s["id"]: s for s in run["steps"]}
    assert [s["id"] for s in run["steps"]] == ["download", "transcribe", "moments", "select", "clip", "finish"]
    assert [s["status"] for s in run["steps"]] == ["done", "done", "running", "waiting", "waiting", "waiting"]
    assert steps["download"]["finished_at"] == steps["transcribe"]["started_at"]
    assert steps["select"]["started_at"] is None
    assert steps["select"]["label"] == "Claude väljer och betygsätter"
    assert all(isinstance(s["estimate_s"], int) for s in run["steps"])
    assert run["eta_s"] > 0
    assert run["cost_estimate_usd"] > 0
    assert run["title"] == "T"


def test_done_and_failed_runs_have_no_eta(client, settings) -> None:  # noqa: F811
    done = _run_with_events(settings, ["download", "transcribe", "select", "clip", "finish", "done"], "done")
    run = client.get(f"/campaign/runs/{done}").json()
    assert {s["status"] for s in run["steps"]} == {"done"}
    assert (run["stage"], run["pct"], run["eta_s"]) == ("done", 100.0, None)
    assert run["steps"][-1]["finished_at"]

    failed = _run_with_events(settings, ["download", "transcribe"], "failed")
    run = client.get(f"/campaign/runs/{failed}").json()
    assert [s["status"] for s in run["steps"]][:3] == ["done", "failed", "waiting"]
    assert (run["error"], run["eta_s"]) == ("Berget svarade inte", None)

    assert client.get("/campaign/runs/999").status_code == 404


def test_run_list_is_newest_first_with_status_and_clip_counts(client, settings) -> None:  # noqa: F811
    old = _run_with_events(settings, ["download", "done"], "done")
    new = _run_with_events(settings, ["download"], "running")
    log = ClipLog(settings.data_dir / "klipp.db")
    for status in ("renderad", "renderad", "godkand"):
        log.add(vod_id=VOD, profile="skiimasktimmy", file=f"/x/{status}.mp4", title="t", caption="c",
                status=status, score=70, reason="Good.")
    runs = client.get("/campaign/runs").json()
    assert [r["run_id"] for r in runs] == [new, old]
    assert runs[0]["status"] == "running"
    assert runs[1]["clip_counts"] == {"renderad": 2, "godkand": 1}
    assert {"focus", "min_s", "max_s", "thumbnail_url", "duration_s", "created", "pct"} <= set(runs[0])
    assert "steps" not in runs[0]


# ---------- klipp ----------


def test_clips_have_score_reason_and_filter_on_run(client, settings) -> None:  # noqa: F811
    run_id = _run_with_events(settings, ["download"])
    log = ClipLog(settings.data_dir / "klipp.db")
    log.add(vod_id=VOD, profile="skiimasktimmy", file="/x/a.mp4", title="a", caption="c", score=77, reason="Hook.")
    log.add(vod_id="123", profile="skiimasktimmy", file="/x/b.mp4", title="b", caption="c")

    clips = client.get("/campaign/clips", params={"run_id": run_id}).json()
    assert [(c["title"], c["score"], c["reason"]) for c in clips] == [("a", 77, "Hook.")]
    old = client.get("/campaign/clips", params={"vod_id": "123"}).json()[0]
    assert (old["score"], old["reason"]) == (None, "")
    assert client.get("/campaign/clips", params={"run_id": 999}).json() == []


def test_clip_transcript_is_the_part_inside_the_clip(client, settings) -> None:  # noqa: F811
    log = ClipLog(settings.data_dir / "klipp.db")
    clip_id = log.add(vod_id="123", profile="skiimasktimmy", file="/x/a.mp4", title="a", caption="c",
                      start_s=10.0, end_s=40.0)
    assert client.get(f"/campaign/clips/{clip_id}/transcript").json() == {"text": "", "segments": []}

    transcript = settings.work_dir / "123" / "transcript.json"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({"video_id": "123", "language": "en", "segments": [
        {"start": 5.0, "end": 9.0, "text": " before", "words": []},
        {"start": 9.0, "end": 12.0, "text": " No way", "words": []},
        {"start": 20.0, "end": 25.0, "text": " he got me!", "words": []},
        {"start": 41.0, "end": 45.0, "text": " after", "words": []},
    ]}), encoding="utf-8")
    result = client.get(f"/campaign/clips/{clip_id}/transcript").json()
    assert result["text"] == "No way he got me!"
    assert result["segments"] == [{"start": 9.0, "end": 12.0, "text": "No way"},
                                  {"start": 20.0, "end": 25.0, "text": "he got me!"}]
    assert client.get("/campaign/clips/999/transcript").status_code == 404


def test_summary_has_disk_space(client, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(estimate, "disk_gb", lambda path: (9.7, 228.0))
    assert client.get("/campaign/summary").json()["disk"] == {"free_gb": 9.7, "total_gb": 228.0}


def test_disk_gb_works_before_the_work_folder_exists(tmp_path) -> None:
    free, total = estimate.disk_gb(tmp_path / "work" / "missing")
    assert 0 < free <= total
