"""Hem, Projekt och Egna videor: avbryt en körning, projektens fakta och snabbläget.
Påhittade Claude, Twitch och ffmpeg. Inga riktiga anrop."""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from campaign import download, pipeline, streams
from campaign.cliplog import ClipLog
from campaign.profile import load_profile
from campaign.workflow import RunCancelled, finish_vod
from clipsmith.candidates.builder import save_candidates

from .conftest import PAKET, make_clip, needs_ffmpeg
from .test_api import client  # noqa: F401  (fixture)
from .test_flow import _run_with_events
from .test_natt_korning import FakePicker, _make, _no, _yes, vod  # noqa: F401  (vod är en fixture)
from .test_natt_lage import _run

VOD = "2925991945"


def _signals(settings) -> None:
    """Kampanjens clipsmith-config väljer med signalerna, utan Claude (snabbläget)."""
    path = settings.root / "config.timmy.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("\nclip:\n", "\nclip:\n  select: signals\n", 1),
                    encoding="utf-8")


# ---------- Avbryt körningen ----------


def test_cancel_only_works_on_a_run_in_progress(client, settings) -> None:  # noqa: F811
    assert client.post("/campaign/runs/999/cancel").status_code == 404
    done = _run("done")
    ClipLog(settings.data_dir / "klipp.db").add_run(done, VOD, "skiimasktimmy", None)
    response = client.post(f"/campaign/runs/{done}/cancel")
    assert response.status_code == 409
    assert client.get(f"/campaign/runs/{done}").json()["cancellable"] is False


def test_cancel_stops_the_run_before_claude_and_frees_the_app(client, settings, vod, monkeypatch) -> None:  # noqa: F811
    vod.picker = FakePicker([_yes("clutch"), _no()])
    run_id = _run("pending")
    ClipLog(settings.data_dir / "klipp.db").add_run(run_id, "123", "skiimasktimmy", None, title="T", duration_s=13950)
    client.app.state.active_run_id = run_id
    seen: dict = {}

    def process_vod(video, cfg, secrets, **kwargs):
        seen["cancellable"] = client.get(f"/campaign/runs/{run_id}").json()["cancellable"]
        seen["cancel"] = client.post(f"/campaign/runs/{run_id}/cancel").status_code
        save_candidates(vod.candidates, vod.mp4.parent / "candidates.json")

    monkeypatch.setattr(pipeline, "process_vod", process_vod)
    monkeypatch.setattr(pipeline, "_harvest_clips", lambda *a: None)
    pipeline.run_campaign_vod(run_id, "123", "skiimasktimmy", None, settings, app=client.app)

    assert seen == {"cancellable": True, "cancel": 202}
    run = client.get(f"/campaign/runs/{run_id}").json()
    assert (run["status"], run["cancelled"], run["cancellable"]) == ("failed", True, False)
    assert vod.picker.contexts == []  # Claude frågades aldrig
    assert client.app.state.active_run_id is None
    assert client.post(f"/campaign/runs/{run_id}/cancel").status_code == 409


def test_cancel_during_selection_asks_claude_no_more(settings, vod) -> None:  # noqa: F811
    class CancellingPicker(FakePicker):
        def pick(self, *args):
            pipeline.cancel(7)  # som knappen Avbryt mitt i valet
            return super().pick(*args)

    vod.picker = CancellingPicker([_yes("clutch"), _no()])
    with pipeline.cancellable(7), pytest.raises(RunCancelled):
        _make(settings, vod)
    assert len(vod.picker.contexts) == 1
    assert vod.cut is None and vod.finish is None  # inget klipps och inget görs klart


def test_cancel_during_download_stops_twitch_dl_and_leaves_no_parts(client, settings, monkeypatch) -> None:  # noqa: F811
    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    monkeypatch.chdir(settings.root)
    run_id = _run("pending")
    ClipLog(settings.data_dir / "klipp.db").add_run(run_id, "123", "skiimasktimmy", None, title="T", duration_s=13950)
    parts = settings.work_dir / "123" / "delar"

    def download_vod(vod_id, work_dir, on_progress):
        parts.mkdir(parents=True)
        (parts / "0001.ts").write_bytes(b"x")
        on_progress(1, 10)
        pipeline.cancel(run_id)
        on_progress(2, 10)
        pytest.fail("hämtningen ska stoppa")

    monkeypatch.setattr(pipeline, "download_vod", download_vod)
    pipeline.run_campaign_vod(run_id, "123", "skiimasktimmy", None, settings)

    assert client.get(f"/campaign/runs/{run_id}").json()["cancelled"] is True
    assert not parts.exists()
    assert not (settings.work_dir / "123" / "123.mp4").exists()


def test_an_exception_in_the_progress_callback_kills_twitch_dl(monkeypatch) -> None:
    started: list[subprocess.Popen] = []
    real = subprocess.Popen

    def spy(*args, **kwargs):
        started.append(real(*args, **kwargs))
        return started[-1]

    def stop(done, total):
        raise RunCancelled

    monkeypatch.setattr(download.subprocess, "Popen", spy)
    script = "import sys, time; sys.stdout.write('Downloaded 1/9 VODs'); sys.stdout.flush(); time.sleep(30)"
    begun = time.monotonic()
    with pytest.raises(RunCancelled):
        download._run_twitchdl([sys.executable, "-c", script], stop)
    assert started[0].poll() is not None  # twitch-dl körs inte vidare i bakgrunden
    assert time.monotonic() - begun < 10


@needs_ffmpeg
def test_cancel_in_the_finish_step_keeps_whole_clips_and_starts_no_new_one(settings) -> None:
    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    for name in ("clip_01_a.mp4", "clip_02_b.mp4"):
        make_clip(settings.out_dir / "123" / name, seconds=8)
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    log = ClipLog(settings.data_dir / "klipp.db")
    calls: list[int] = []

    def stop() -> None:
        calls.append(1)
        if len(calls) > 1:
            raise RunCancelled

    with pytest.raises(RunCancelled):
        finish_vod("123", profile, settings, log, review=False, stop=stop)
    assert [c["file"].rsplit("/", 1)[-1] for c in log.list()] == ["clip_01_a.mp4"]
    assert not (settings.out_dir / "123" / "klara" / "clip_02_b.mp4").exists()


def test_cancel_when_clips_are_made_again_keeps_the_project_done(client, settings, monkeypatch) -> None:  # noqa: F811
    run_id = _run("done")
    ClipLog(settings.data_dir / "klipp.db").add_run(run_id, VOD, "skiimasktimmy", "Bodycam")

    def finish(vod_id, profile, settings, game, run_id, stop):
        pipeline.cancel(run_id)
        stop()

    monkeypatch.setattr(pipeline, "finish", finish)
    pipeline.finish_run(run_id, VOD, "skiimasktimmy", "Bodycam", settings)
    run = client.get(f"/campaign/runs/{run_id}").json()
    assert (run["status"], run["cancelled"]) == ("done", False)
    assert run["message"] == "You cancelled. The clips that were redone in time are saved."


# ---------- Projektsidans steg ----------


def test_a_local_file_without_transcript_fails_on_the_transcribe_step(client, settings, monkeypatch) -> None:  # noqa: F811
    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    monkeypatch.chdir(settings.root)
    settings.berget_api_key = ""
    (settings.work_dir / "egen_fil").mkdir(parents=True)
    (settings.work_dir / "egen_fil" / "egen_fil.mp4").touch()
    run_id = _run("pending", vod_id="egen_fil")
    ClipLog(settings.data_dir / "klipp.db").add_run(run_id, "egen_fil", "skiimasktimmy", None, title="egen fil",
                                                     duration_s=30)
    pipeline.run_campaign_vod(run_id, "egen_fil", "skiimasktimmy", None, settings, local=True)

    run = client.get(f"/campaign/runs/{run_id}").json()
    assert [s["status"] for s in run["steps"][:3]] == ["done", "failed", "waiting"]
    # Samma ord som Kampanjer: nyckeln läggs in under Inställningar, inte i .env.
    assert run["error"] == ("Berget key (BERGET_API_KEY) is missing. Add it under Settings. "
                            "It is needed for transcription.")


def test_signals_mode_shows_no_claude_step_and_no_claude_cost(client, settings) -> None:  # noqa: F811
    _signals(settings)
    run = client.get(f"/campaign/runs/{_run_with_events(settings, ['download'])}").json()
    steps = {s["id"]: s for s in run["steps"]}
    assert steps["select"]["label"] == "The signals choose, without Claude"
    assert run["cost_estimate"]["claude_usd"] == 0
    assert run["cost_estimate_usd"] == run["cost_estimate"]["berget_usd"]
    assert run["fast"] is True


def test_the_vod_says_which_campaigns_choose_without_claude(client, settings) -> None:  # noqa: F811
    assert client.get(f"/campaign/vod/{VOD}").json()["fast_campaigns"] == []
    _signals(settings)
    assert client.get(f"/campaign/vod/{VOD}").json()["fast_campaigns"] == ["skiimasktimmy"]


def test_a_project_without_a_twitch_image_gets_its_first_clips_image(client, settings) -> None:  # noqa: F811
    log = ClipLog(settings.data_dir / "klipp.db")
    run_id = _run("done", vod_id="egen_fil")
    log.add_run(run_id, "egen_fil", "skiimasktimmy", None, title="egen fil", thumbnail_url=None, duration_s=30)
    first = log.add(vod_id="egen_fil", profile="skiimasktimmy", file="/x/clip_01.mp4", title="A", caption="",
                    status="renderad", problems=[])
    log.add(vod_id="egen_fil", profile="skiimasktimmy", file="/x/clip_02.mp4", title="B", caption="",
            status="renderad", problems=[])
    [project] = client.get("/campaign/runs").json()
    assert project["poster_url"].startswith(f"/campaign/clips/{first}/poster")


# ---------- texter ----------


def test_finish_summary_says_in_plain_words_why_clips_were_left() -> None:
    results = [{"id": 1}, {"id": 2}, {"file": "a", "skipped": "you rejected the clip"},
               {"file": "b", "skipped": "already in the clip log"},
               {"file": "c", "skipped": "already in the clip log"}]
    assert pipeline.finish_summary(results) == (
        "2 clips were redone. 3 clips were not touched: 2 are already reviewed or posted, 1 is rejected.")
    assert pipeline.finish_summary(results[3:4]) == (
        "No clips were redone. 1 clip was not touched: 1 is already reviewed or posted.")


def test_a_cancelled_project_is_cancelled_on_the_stream_card_too(client, settings, monkeypatch) -> None:  # noqa: F811
    """Streamkortet på Hem säger Avbruten som projektkortet, inte Misslyckades."""
    from clipsmith.db.models import Run, RunStatus
    from clipsmith.db.session import get_session

    from .test_omgang5 import FakeStreams

    fake = FakeStreams()
    monkeypatch.setattr(streams, "_cache", {})
    monkeypatch.setattr(streams.twitch, "gql_query", fake.gql)
    monkeypatch.setattr(streams, "load_secrets", lambda: SimpleNamespace(twitch_client_id="", twitch_client_secret=""))
    db = get_session()
    for vod_id, error in ((VOD, pipeline.CANCELLED), ("2925070238", "twitch-dl gav upp")):
        run = Run(vod_id=vod_id, channel="skiimasktimmy", status=RunStatus.failed, error=error)
        db.add(run)
        db.commit()
        ClipLog(settings.data_dir / "klipp.db").add_run(run.id, vod_id, "skiimasktimmy", "Bodycam")
    db.close()
    cards = {s["vod_id"]: (s["run_status"], s["run_cancelled"]) for s in client.get("/campaign/streams").json()}
    assert cards == {VOD: ("failed", True), "2925070238": ("failed", False)}


def test_the_server_says_project_not_run(client, settings) -> None:  # noqa: F811
    """Samma ord i hela appen: projekt, aldrig körning i text som Robin ser."""
    for method, path in (("get", "/campaign/runs/999"), ("post", "/campaign/runs/999/retry"),
                         ("post", "/campaign/runs/999/cancel")):
        response = getattr(client, method)(path)
        assert (response.status_code, response.json()["detail"]) == (404, "The project does not exist."), path
    done = _run("done")
    ClipLog(settings.data_dir / "klipp.db").add_run(done, VOD, "skiimasktimmy", None)
    response = client.post(f"/campaign/runs/{done}/retry")
    assert (response.status_code, response.json()["detail"]) == (400, "Only a project that failed or was cancelled can run again.")
