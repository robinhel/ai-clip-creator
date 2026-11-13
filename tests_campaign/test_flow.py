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
    monkeypatch.setattr(pipeline, "download_vod", lambda vod_id, work_dir, **k: mp4)
    monkeypatch.setattr(pipeline, "process_vod", lambda video, cfg, secrets, **k: calls.update(
        process=(cfg, k)) or (mp4.parent / "candidates.json").write_text("[]"))
    monkeypatch.setattr(pipeline, "finish_vod", lambda *a, **k: [])

    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    pipeline.make_clips("123", profile, settings, game=None, emit=lambda *a: None,
                        focus="jump scares", min_s=15, max_s=30)
    cfg, kwargs = calls["process"]
    assert kwargs["skip_select"]  # kampanjlagret väljer själv; focus till Claude provas i test_natt_korning.py
    assert (cfg.clip.min_seconds, cfg.clip.max_seconds) == (15, 30)
    assert pipeline.clipsmith_config(profile, settings).clip.max_seconds == 45  # filen är orörd

    pipeline.make_clips("123", profile, settings, game=None, emit=lambda *a: None)
    cfg, kwargs = calls["process"]
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
    monkeypatch.setattr(api, "probe", lambda path: {"duration": 13950.0})  # hela VOD:en finns
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
    for lengths in ({"min_s": 10, "max_s": 30}, {"min_s": 30, "max_s": 20}, {"max_s": 60}, {"min_s": 46}):
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
    monkeypatch.setattr(api, "probe", lambda path: {"duration": 13950.0})  # hela VOD:en finns
    assert client.post("/campaign/runs", json=body).status_code == 201  # redan nedladdad


def test_new_run_saves_vod_facts_and_passes_focus_and_length(client, settings, started) -> None:  # noqa: F811
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


def _run_with_events(settings, stages: list[str], status: str = "running", minutes_ago: int = 30,
                     vod: str = VOD) -> int:
    from clipsmith.db.models import PipelineEvent, Run, RunStatus
    from clipsmith.db.session import get_session

    db = get_session()
    start = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    run = Run(vod_id=vod, channel="skiimasktimmy", status=RunStatus(status), created_at=start)
    db.add(run)
    db.commit()
    for i, stage in enumerate(stages):
        db.add(PipelineEvent(run_id=run.id, stage=stage, pct=float(i * 10), created_at=start + timedelta(minutes=i)))
    if status == "failed":
        run.error = "Berget svarade inte"
    db.commit()
    run_id = run.id
    db.close()
    ClipLog(settings.data_dir / "klipp.db").add_run(run_id, vod, "skiimasktimmy", "Bodycam",
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
    # Olika streams: samma stream och kampanj visas som ett projekt (den senaste körningen).
    old = _run_with_events(settings, ["download", "done"], "done", vod="2925070238")
    new = _run_with_events(settings, ["download"], "running")
    log = ClipLog(settings.data_dir / "klipp.db")
    for status in ("renderad", "renderad", "godkand"):
        log.add(vod_id="2925070238", profile="skiimasktimmy", file=f"/x/{status}.mp4", title="t", caption="c",
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
    assert client.get("/campaign/summary").json()["disk"] == {"free_gb": 9.7, "total_gb": 228.0, "connected": True,
                                                              "problem": None}


def test_disk_gb_works_before_the_work_folder_exists(tmp_path) -> None:
    free, total = estimate.disk_gb(tmp_path / "work" / "missing")
    assert 0 < free <= total


# ---------- granskningens fynd ----------


def test_stream_context_says_the_runs_length_replaces_the_instructions() -> None:
    from clipsmith.llm.prompts import build_stream_context

    ctx = build_stream_context("timmy", "t", "1h", clip_range=(20, 30))
    assert "end_offset_s must be 20-30 seconds after start_offset_s" in ctx
    assert "instead of the clip length in the instructions" in ctx


def test_finish_again_after_done_counts_steps_from_the_new_events() -> None:
    start = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
    first = [(stage, 10.0, start + timedelta(minutes=i), "") for i, stage in
             enumerate(["download", "transcribe", "chat", "select", "clip", "finish", "done"])]
    again = start + timedelta(hours=2)
    progress = estimate.progress("running", [*first, ("finish", 90.0, again, "")], 13950, now=again)
    steps = {s["id"]: s for s in progress["steps"]}
    assert steps["finish"]["status"] == "running"
    assert steps["finish"]["started_at"] == again.isoformat()
    assert [s["status"] for s in progress["steps"]][:5] == ["done"] * 5
    assert progress["stage"] == "finish"

    finished = estimate.progress("done", [*first, ("finish", 90.0, again, ""), ("done", 100.0, again, "")], 13950)
    assert {s["id"]: s for s in finished["steps"]}["finish"]["started_at"] == again.isoformat()


def test_run_that_fails_before_any_event_fails_the_first_step() -> None:
    progress = estimate.progress("failed", [], 13950)
    assert [s["status"] for s in progress["steps"]] == ["failed"] + ["waiting"] * 5
    assert progress["eta_s"] is None


def test_new_caption_and_a_new_status_in_one_patch_is_refused(client, settings) -> None:  # noqa: F811
    from .test_api import CAPTION, _clip

    clip_id = _clip(settings, status="godkand", clip_format="funny")
    response = client.patch(f"/campaign/clips/{clip_id}", json={
        "caption": CAPTION + " #new", "status": "postad", "url": "https://www.tiktok.com/@me/video/1", "platform": "tiktok"})
    assert response.status_code == 400
    assert response.json()["detail"] == "Spara captionen först. Sedan behövs ett nytt ja."
    assert ClipLog(settings.data_dir / "klipp.db").get(clip_id)["caption"] == CAPTION
    assert client.patch(f"/campaign/clips/{clip_id}", json={"caption": "x", "status": "nekad"}).status_code == 200


def test_vod_cache_expires_and_a_new_run_fetches_fresh_facts(client, fake_twitch, started,  # noqa: F811
                                                             monkeypatch) -> None:
    from campaign import vodinfo

    clock = {"t": 1000.0}
    monkeypatch.setattr(vodinfo, "monotonic", lambda: clock["t"])
    client.get(f"/campaign/vod/{VOD}")
    clock["t"] += 9 * 60
    client.get(f"/campaign/vod/{VOD}")
    assert fake_twitch.calls == 1
    clock["t"] += 2 * 60
    client.get(f"/campaign/vod/{VOD}")
    assert fake_twitch.calls == 2

    fake_twitch.videos[VOD]["lengthSeconds"] = 18150
    assert client.post("/campaign/runs", json={"profile": "skiimasktimmy", "vod_id": VOD}).status_code == 201
    assert fake_twitch.calls == 3
    assert len(started) == 1
    assert ClipLog(client.app.dependency_overrides[api.get_settings]().data_dir / "klipp.db").runs()[-1][
        "duration_s"] == 18150


def test_vod_without_owner_matches_no_campaign(client, settings, fake_twitch, started) -> None:  # noqa: F811
    (settings.profiles_dir / "trasig.yaml").write_text("name: [", encoding="utf-8")  # kanal "" i /profiles
    fake_twitch.videos["333333333"] = {"title": "x", "lengthSeconds": 60, "createdAt": "", "game": None,
                                       "owner": None}
    vod = client.get("/campaign/vod/333333333").json()
    assert vod["channel"] == ""
    assert vod["campaigns"] == []
    response = client.post("/campaign/runs", json={"profile": "skiimasktimmy", "vod_id": "333333333"})
    assert response.status_code == 400
    assert "kanal" in response.json()["detail"]
    assert started == []


def test_twitch_down_when_starting_a_run_gives_502_and_no_run(client, fake_twitch, started) -> None:  # noqa: F811
    from clipsmith.db.models import Run
    from clipsmith.db.session import get_session

    fake_twitch.error = OSError("nere")
    response = client.post("/campaign/runs", json={"profile": "skiimasktimmy", "vod_id": VOD})
    assert response.status_code == 502
    assert started == []
    db = get_session()
    assert db.query(Run).count() == 0
    db.close()
    assert client.get("/campaign/runs").json() == []
    assert client.app.state.active_run_id is None


def test_adding_columns_that_another_process_just_added_is_fine(tmp_path, monkeypatch) -> None:
    from campaign import cliplog

    path = tmp_path / "klipp.db"
    ClipLog(path)
    # Låtsas att en annan process lade till kolumnen mellan PRAGMA och ALTER TABLE.
    monkeypatch.setitem(cliplog.NEW_COLUMNS, "clips", {"score": "INTEGER", "reason": "TEXT"})
    real = sqlite3.connect

    def connect(*args, **kwargs):
        db = real(*args, **kwargs)
        db.row_factory = sqlite3.Row
        return _NoColumns(db)

    monkeypatch.setattr(cliplog.sqlite3, "connect", connect)
    ClipLog(path)


class _NoColumns:
    """sqlite-anslutning där PRAGMA table_info säger att inga kolumner finns."""

    def __init__(self, db):
        self.db = db
        self.row_factory = None

    def execute(self, sql, *args):
        if sql.startswith("PRAGMA table_info"):
            return []
        return self.db.execute(sql, *args)

    def __enter__(self):
        self.db.__enter__()
        return self

    def __exit__(self, *exc):
        return self.db.__exit__(*exc)


def test_broken_transcript_gives_an_empty_answer(client, settings) -> None:  # noqa: F811
    log = ClipLog(settings.data_dir / "klipp.db")
    clip_id = log.add(vod_id="123", profile="skiimasktimmy", file="/x/a.mp4", title="a", caption="c",
                      start_s=10.0, end_s=40.0)
    transcript = settings.work_dir / "123" / "transcript.json"
    transcript.parent.mkdir(parents=True)
    for broken in ("{not json", '{"segments": [{"start": 12}]}', "[]"):
        transcript.write_text(broken, encoding="utf-8")
        response = client.get(f"/campaign/clips/{clip_id}/transcript")
        assert response.status_code == 200, broken
        assert response.json() == {"text": "", "segments": []}


def test_same_min_and_max_length_is_allowed(client, started) -> None:  # noqa: F811
    response = client.post("/campaign/runs", json={"profile": "skiimasktimmy", "vod_id": VOD,
                                                   "min_s": 30, "max_s": 30})
    assert response.status_code == 201
    _, kwargs = started[0]
    assert (kwargs["min_s"], kwargs["max_s"]) == (30, 30)


def test_old_run_gets_its_vod_facts_when_shown(client, settings, fake_twitch) -> None:  # noqa: F811
    run_id = _run_with_events(settings, ["download", "done"], "done")
    log = ClipLog(settings.data_dir / "klipp.db")
    log.add_run(run_id, VOD, "skiimasktimmy", "Bodycam")  # som en gammal körning: inga fakta

    fake_twitch.error = OSError("nere")
    assert client.get(f"/campaign/runs/{run_id}").json()["title"] is None
    assert client.get("/campaign/runs").status_code == 200

    fake_twitch.error = None
    run = client.get("/campaign/runs").json()[0]
    assert run["title"] == "3x Playtest Key GIVEAWAY"
    assert run["thumbnail_url"].endswith(".jpg")
    saved = log.run(run_id)
    assert (saved["title"], saved["channel_name"], saved["duration_s"]) == (
        "3x Playtest Key GIVEAWAY", "SkiiMaskTimmy", 13950)
    assert saved["game"] == "Bodycam"
    calls = fake_twitch.calls
    client.get(f"/campaign/runs/{run_id}")
    assert fake_twitch.calls == calls  # sparat, hämtas inte igen


def test_force_accepted_picks_have_no_score_from_claude(tmp_path) -> None:
    from campaign.workflow import load_picks

    picks = tmp_path / "picks.json"
    picks.write_text(json.dumps([{"candidate": {}, "pick": {
        "start_offset_s": 1, "end_offset_s": 20, "title_es": "Momento destacado", "score": 0,
        "reason": "Force-accepted: LLM accepted fewer than min_picks candidates."}}]), encoding="utf-8")
    (pick,) = load_picks(picks)
    assert pick["score"] is None
    assert pick["reason"] == "Claude valde inte klippet. Det togs med för att nå min_picks."


def test_created_falls_back_to_clipsmiths_time(client, settings) -> None:  # noqa: F811
    run_id = _run_with_events(settings, ["download"])
    with sqlite3.connect(settings.data_dir / "klipp.db") as db:
        db.execute("UPDATE runs SET created = NULL WHERE run_id = ?", (run_id,))
    assert client.get(f"/campaign/runs/{run_id}").json()["created"]
    assert client.get("/campaign/runs").json()[0]["created"]


# ---------- försök igen ----------


def _fail_run(run_id: int) -> None:
    from clipsmith.db.models import Run, RunStatus
    from clipsmith.db.session import get_session

    db = get_session()
    db.get(Run, run_id).status = RunStatus.failed
    db.commit()
    db.close()


def test_retry_of_a_failed_run_starts_a_new_run_with_the_same_values(client, settings, started) -> None:  # noqa: F811
    body = {"profile": "skiimasktimmy", "vod_id": VOD, "game": "Bodycam", "start_s": 600.0, "end_s": 7200.0,
            "focus": "jump scares", "min_s": 20, "max_s": 30}
    first = client.post("/campaign/runs", json=body).json()["id"]
    assert client.get(f"/campaign/runs/{first}").json()["start_s"] == 600.0
    client.app.state.active_run_id = None  # bakgrundsjobbet är låtsat och släpper inte själv
    _fail_run(first)

    response = client.post(f"/campaign/runs/{first}/retry")
    assert response.status_code == 201, response.text
    new = response.json()
    assert new["id"] != first
    assert (new["vod_id"], new["channel"], new["status"]) == (VOD, "skiimasktimmy", "pending")
    _, (args2, kwargs2) = started
    assert args2[0] == new["id"]
    assert args2[1:4] == (VOD, "skiimasktimmy", "Bodycam")
    assert {k: kwargs2[k] for k in ("start_s", "end_s", "focus", "min_s", "max_s")} == {
        "start_s": 600.0, "end_s": 7200.0, "focus": "jump scares", "min_s": 20, "max_s": 30}
    run = client.get(f"/campaign/runs/{new['id']}").json()
    assert (run["start_s"], run["end_s"], run["focus"], run["min_s"], run["max_s"], run["game"]) == (
        600.0, 7200.0, "jump scares", 20, 30, "Bodycam")


def test_retry_of_an_old_run_without_values_uses_the_defaults(client, settings, started) -> None:  # noqa: F811
    run_id = _run_with_events(settings, ["download"], "failed")
    assert (client.get(f"/campaign/runs/{run_id}").json()["start_s"],
            client.get(f"/campaign/runs/{run_id}").json()["end_s"]) == (0, 0)
    assert client.post(f"/campaign/runs/{run_id}/retry").status_code == 201
    (_, kwargs), = started
    assert {k: kwargs[k] for k in ("start_s", "end_s", "focus", "min_s", "max_s")} == {
        "start_s": 0.0, "end_s": 0.0, "focus": "", "min_s": 15, "max_s": 45}


def test_retry_only_for_failed_runs(client, settings, started) -> None:  # noqa: F811
    done = _run_with_events(settings, ["download", "done"], "done")
    response = client.post(f"/campaign/runs/{done}/retry")
    assert response.status_code == 400
    assert response.json()["detail"]
    running = _run_with_events(settings, ["download"], "running")
    assert client.post(f"/campaign/runs/{running}/retry").status_code == 400
    assert client.post("/campaign/runs/999/retry").status_code == 404
    assert started == []


def test_retry_needs_the_key_and_waits_for_a_running_run(client, settings, started, monkeypatch) -> None:  # noqa: F811
    failed = _run_with_events(settings, ["download"], "failed")
    monkeypatch.setenv("CLIPSMITH_API_KEY", "nyckel")
    assert client.post(f"/campaign/runs/{failed}/retry").status_code == 401
    client.app.state.active_run_id = 77
    response = client.post(f"/campaign/runs/{failed}/retry", headers={"X-Api-Key": "nyckel"})
    assert response.status_code == 429
    assert started == []
    assert len(ClipLog(settings.data_dir / "klipp.db").runs()) == 1


def test_the_cost_is_split_into_berget_and_claude_and_berget_follows_the_price_list(client, settings,  # noqa: F811
                                                                                    fake_twitch, monkeypatch) -> None:
    """13 950 s är 232,5 minuter. Berget kostar 0,0022 dollar per minut i priser.yaml, alltså 0,51.
    Finns transkriberingen redan, kostar Berget inget i nästa körning."""
    monkeypatch.setattr(estimate, "disk_gb", lambda path: (500.0, 900.0))
    cost = client.get(f"/campaign/vod/{VOD}").json()["estimate"]["cost"]
    assert cost == {"berget_usd": 0.51, "claude_usd": 0.30, "usd": 0.81}

    prices = settings.prices_path.read_text().replace("Systran/faster-whisper-large-v3: 0.0022",
                                                       "Systran/faster-whisper-large-v3: 0.0044")
    settings.prices_path.write_text(prices)
    assert client.get(f"/campaign/vod/{VOD}").json()["estimate"]["cost"]["berget_usd"] == 1.02

    transcript = settings.work_dir / VOD / "transcript.json"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}")
    cost = client.get(f"/campaign/vod/{VOD}").json()["estimate"]["cost"]
    assert (cost["berget_usd"], cost["usd"]) == (0.0, 0.30)
