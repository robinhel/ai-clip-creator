"""Nattens rättningar B2: läge och säkerhet. Påhittad Twitch och Berget, inga riktiga anrop.

Omstart mitt i en körning, pågående streams, halva filer, Gör klippen kampanjklara igen, budget,
SSD:n, nycklarnas .env, bildgranskningens beskrivningar, loopback och GET /campaign/setup.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from campaign import api, keys, pipeline
from campaign.budget import Budget, budget_from_settings
from campaign.cliplog import ClipLog

from .conftest import make_clip, needs_ffmpeg
from .test_api import client  # noqa: F401  (fixture)
from .test_keys import CLIENT_ID, _client, env, twitch  # noqa: F401  (fixtures)

VOD = "2925991945"
LIVE = "Streamen pågår. Vänta tills den är slut."
SSD = "SSD:n är inte ansluten."
BODY = {"profile": "skiimasktimmy", "vod_id": VOD}


@pytest.fixture
def started(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(api, "run_campaign_vod", lambda *a, **k: calls.append((a, k)))
    return calls


@pytest.fixture
def vod(fake_twitch, monkeypatch):
    """Låtsas-Twitch som också svarar med videons status (RECORDING när streamen pågår)."""
    from campaign import vodinfo

    inner = vodinfo.twitch.gql_query
    fake_twitch.queries = []

    def gql(query):
        fake_twitch.queries.append(query)
        answer = inner(query)
        if answer["data"]["video"] is not None:
            answer["data"]["video"]["status"] = fake_twitch.videos[query.split('"')[1]].get("status", "RECORDED")
        return answer

    monkeypatch.setattr(vodinfo.twitch, "gql_query", gql)
    return fake_twitch


def _run(status: str, vod_id: str = VOD) -> int:
    from clipsmith.db.models import Run, RunStatus
    from clipsmith.db.session import get_session

    db = get_session()
    run = Run(vod_id=vod_id, channel="skiimasktimmy", status=RunStatus(status))
    db.add(run)
    db.commit()
    run_id = run.id
    db.close()
    return run_id


def _status(run_id: int) -> tuple[str, str | None]:
    from clipsmith.db.models import Run
    from clipsmith.db.session import get_session

    db = get_session()
    run = db.get(Run, run_id)
    db.close()
    return run.status.value, run.error


# ---------- 1. omstart mitt i en körning ----------


def test_runs_that_were_going_when_the_server_starts_become_failed(tmp_path, monkeypatch) -> None:
    from clipsmith.api import app as app_module
    from clipsmith.db.session import init_db

    init_db(tmp_path / "clipsmith.db")
    running, pending, done, failed = _run("running"), _run("pending"), _run("done"), _run("failed")
    monkeypatch.setattr(app_module, "load_config", lambda path: SimpleNamespace(work_dir=tmp_path))
    app = FastAPI()

    async def start() -> None:
        async with app_module._lifespan(app):
            pass

    asyncio.run(start())
    stopped = ("failed", "Servern startades om mitt i körningen. Tryck Kör igen.")
    assert (_status(running), _status(pending)) == (stopped, stopped)
    assert _status(done) == ("done", None)
    assert _status(failed) == ("failed", None)
    assert app.state.active_run_id is None


# ---------- 2. streams som pågår och halva filer ----------


def test_a_stream_in_progress_is_shown_as_a_hindrance_and_cannot_start(client, vod, started) -> None:  # noqa: F811
    info = client.get(f"/campaign/vod/{VOD}").json()
    assert (info["live"], info["problems"]) == (False, [])
    assert any("status" in q for q in vod.queries)

    from campaign import vodinfo

    vodinfo._cache.clear()
    vod.videos[VOD]["status"] = "RECORDING"
    info = client.get(f"/campaign/vod/{VOD}").json()
    assert (info["live"], info["problems"]) == (True, [LIVE])
    response = client.post("/campaign/runs", json=BODY)
    assert (response.status_code, response.json()["detail"]) == (400, LIVE)
    assert started == []


def _half_download(settings) -> dict:
    folder = settings.work_dir / VOD
    (folder / "berget").mkdir(parents=True)
    files = {name: folder / name for name in (f"{VOD}.mp4", "transcript.json", "chat.json", "berget/del_01.json")}
    for path in files.values():
        path.write_text("x")
    return files


def test_a_half_downloaded_vod_is_removed_before_the_run(client, settings, vod, started, monkeypatch) -> None:  # noqa: F811
    files = _half_download(settings)
    monkeypatch.setattr(api, "probe", lambda path: {"duration": 13950 - 61.0})
    info = client.get(f"/campaign/vod/{VOD}").json()
    assert info["downloaded"] is False and info["estimate"]["download_gb"] > 0

    monkeypatch.setattr(api.estimate, "disk_gb", lambda path: (9.7, 228.0))
    assert client.post("/campaign/runs", json=BODY).status_code == 400  # disken räcker inte för en ny hämtning
    assert all(path.exists() for path in files.values())  # inget tas bort när körningen inte startar

    monkeypatch.setattr(api.estimate, "disk_gb", lambda path: (100.0, 228.0))
    assert client.post("/campaign/runs", json=BODY).status_code == 201
    assert not any(path.exists() for path in files.values())
    assert not (settings.work_dir / VOD / "berget").exists()
    assert len(started) == 1


def test_a_whole_vod_is_kept(client, settings, vod, started, monkeypatch) -> None:  # noqa: F811
    files = _half_download(settings)
    monkeypatch.setattr(api, "probe", lambda path: {"duration": 13950 - 59.0})
    assert client.get(f"/campaign/vod/{VOD}").json()["downloaded"] is True
    assert client.post("/campaign/runs", json=BODY).status_code == 201
    assert all(path.exists() for path in files.values())


def test_a_vod_that_ffprobe_cannot_read_counts_as_half(client, settings, vod, started, monkeypatch) -> None:  # noqa: F811
    files = _half_download(settings)

    def broken(path):
        raise RuntimeError("ffprobe misslyckades")

    monkeypatch.setattr(api, "probe", broken)
    assert client.post("/campaign/runs", json=BODY).status_code == 201
    assert not files[f"{VOD}.mp4"].exists()


def test_a_run_that_cannot_be_saved_does_not_lock_the_app(client, settings, vod, started, monkeypatch) -> None:  # noqa: F811
    def broken(*a, **k):
        raise OSError("disken är full")

    monkeypatch.setattr(ClipLog, "add_run", broken)
    with pytest.raises(OSError):
        client.post("/campaign/runs", json=BODY)
    assert client.app.state.active_run_id is None
    from clipsmith.db.models import Run
    from clipsmith.db.session import get_session

    db = get_session()
    (run,) = db.query(Run).all()
    db.close()
    assert run.status.value == "failed"


# ---------- 3. Gör klippen kampanjklara igen ----------


def test_finish_again_is_refused_for_a_failed_run(client, settings, started) -> None:  # noqa: F811
    run_id = _run("failed")
    ClipLog(settings.data_dir / "klipp.db").add_run(run_id, VOD, "skiimasktimmy", "Bodycam")
    response = client.post(f"/campaign/runs/{run_id}/finish")
    assert (response.status_code, response.json()["detail"]) == (400, "Körningen misslyckades. Tryck Kör igen.")
    assert client.app.state.active_run_id is None


def test_finish_summary_tells_what_was_done_and_why_clips_were_skipped() -> None:
    results = [{"id": 1, "problems": []}, {"id": 2, "problems": ["x"]},
               {"file": "a", "skipped": "du har nekat klippet"},
               {"file": "b", "skipped": "finns redan i klipploggen"},
               {"file": "c", "skipped": "finns redan i klipploggen"}]
    assert pipeline.finish_summary(results) == (
        "2 klipp gjordes om. 3 hoppades över (finns redan i klipploggen: 2, du har nekat klippet: 1).")
    assert pipeline.finish_summary(results[2:3]) == (
        "Inga klipp gjordes om. 1 hoppades över (du har nekat klippet: 1).")
    assert pipeline.finish_summary([{"id": 1}]) == "1 klipp gjordes om."
    assert pipeline.finish_summary([]) == "Inga klipp gjordes om."


def test_finish_run_puts_the_summary_in_the_runs_message(client, settings, monkeypatch) -> None:  # noqa: F811
    run_id = _run("done")
    ClipLog(settings.data_dir / "klipp.db").add_run(run_id, VOD, "skiimasktimmy", "Bodycam")
    monkeypatch.setattr(pipeline, "finish", lambda *a: [{"id": 1}, {"file": "a", "skipped": "du har nekat klippet"}])
    pipeline.finish_run(run_id, VOD, "skiimasktimmy", "Bodycam", settings)
    run = client.get(f"/campaign/runs/{run_id}").json()
    assert (run["status"], run["message"]) == (
        "done", "1 klipp gjordes om. 1 hoppades över (du har nekat klippet: 1).")


# ---------- 4. budget ----------


@pytest.mark.parametrize(("month_cap", "day_cap", "words"), [(200.0, 10.0, "Dagstaket"), (15.0, 100.0, "Månadstaket")])
def test_a_run_does_not_start_when_the_budget_is_used(client, settings, vod, started,  # noqa: F811
                                                      month_cap, day_cap, words) -> None:
    settings.budget_month_usd, settings.budget_day_usd = month_cap, day_cap
    budget_from_settings(settings).record("okänd-modell", "granska", 2_000_000, 0)  # 16 dollar
    response = client.post("/campaign/runs", json=BODY)
    assert response.status_code == 400
    assert words in response.json()["detail"] and "Körningen startar inte" in response.json()["detail"]
    assert started == []


def test_bergets_minutes_are_priced_from_the_price_list(settings) -> None:
    budget = budget_from_settings(settings)
    price = budget.prices["transcription"]["Systran/faster-whisper-large-v3"]
    assert price > 0
    assert budget.record_audio("Systran/faster-whisper-large-v3", 30.0) == pytest.approx(30 * price)
    assert budget.record_audio("okänd/modell", 30.0) == 0.0
    assert budget.summary()["by_purpose"]["transkribering"]["calls"] == 2


@needs_ffmpeg
def test_transcribe_vod_logs_each_sent_part_in_the_budget(settings) -> None:
    from campaign.transcribe import transcribe_vod

    mp4 = make_clip(settings.work_dir / "123" / "123.mp4", size="320x180", seconds=5)
    budget: Budget = budget_from_settings(settings)
    model = "Systran/faster-whisper-large-v3"
    out = mp4.parent / "transcript.json"
    transcribe_vod(mp4, "123", out, post=lambda audio, fields: {"segments": []}, model=model, language="en",
                   chunk_s=3, budget=budget)
    row = budget.summary()["by_purpose"]["transkribering"]
    assert row["calls"] == 2
    assert row["usd"] == pytest.approx(5 / 60 * budget.prices["transcription"][model], rel=0.05)


def test_make_clips_gives_the_transcription_the_budget(settings, monkeypatch) -> None:
    import shutil

    from campaign.profile import load_profile

    from .conftest import PAKET

    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    settings.berget_api_key = "test"
    mp4 = settings.work_dir / "123" / "123.mp4"
    mp4.parent.mkdir(parents=True)
    mp4.touch()
    seen: dict = {}

    def fake_transcribe(mp4, vod_id, out, **kwargs):
        seen.update(kwargs)
        out.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(pipeline, "download_vod", lambda vod_id, work_dir, **k: mp4)
    monkeypatch.setattr(pipeline, "transcribe_vod", fake_transcribe)
    monkeypatch.setattr(pipeline, "process_vod",
                        lambda *a, **k: (mp4.parent / "candidates.json").write_text("[]", encoding="utf-8"))
    monkeypatch.setattr(pipeline, "finish_vod", lambda *a, **k: [])
    pipeline.make_clips("123", load_profile("skiimasktimmy", settings.profiles_dir), settings, game=None,
                        emit=lambda *e: None)
    assert isinstance(seen["budget"], Budget)
    assert seen["budget"].db_path == settings.data_dir / "budget.db"


# ---------- 5. SSD:n ----------


def _unplug(settings, tmp_path) -> None:
    for name in ("work", "out"):
        (settings.root / name).symlink_to(tmp_path / "SSD" / name)


def test_without_the_ssd_the_app_says_so_and_no_run_starts(client, settings, vod, started, tmp_path) -> None:  # noqa: F811
    connected = client.get("/campaign/summary").json()["disk"]
    assert connected == {"free_gb": 100.0, "total_gb": 228.0, "connected": True, "problem": None}

    _unplug(settings, tmp_path)
    assert client.get("/campaign/summary").json()["disk"] == {
        "free_gb": 0.0, "total_gb": 0.0, "connected": False, "problem": SSD}
    info = client.get(f"/campaign/vod/{VOD}").json()
    assert SSD in info["problems"] and info["estimate"]["fits"] is False
    response = client.post("/campaign/runs", json=BODY)
    assert (response.status_code, response.json()["detail"]) == (400, SSD)
    assert started == []


def test_the_free_space_is_where_work_points(client, settings, tmp_path, monkeypatch) -> None:  # noqa: F811
    (tmp_path / "SSD" / "work").mkdir(parents=True)
    (tmp_path / "SSD" / "out").mkdir(parents=True)
    _unplug(settings, tmp_path)  # länkarna finns, och SSD:n sitter i
    asked: list = []
    monkeypatch.setattr(api.estimate, "disk_gb", lambda path: asked.append(path.resolve()) or (50.0, 500.0))
    assert client.get("/campaign/summary").json()["disk"]["connected"] is True
    assert asked == [(tmp_path / "SSD" / "work").resolve()]


# ---------- 6 och 8. nycklarnas .env och loopback ----------


def test_env_is_read_and_written_under_one_lock(settings, env, twitch, monkeypatch) -> None:  # noqa: F811
    seen: list = []
    read, write = keys.dotenv_values, keys._write_env
    monkeypatch.setattr(keys, "dotenv_values", lambda path: seen.append(("read", keys._env_lock.locked())) or read(path))
    monkeypatch.setattr(keys, "_write_env",
                        lambda path, values: seen.append(("write", keys._env_lock.locked())) or write(path, values))
    local = _client(settings)
    assert local.put("/campaign/keys", json={"values": {"BERGET_API_KEY": CLIENT_ID}}).status_code == 200
    local.get("/campaign/keys")
    assert ("write", True) in seen and ("read", True) in seen
    assert all(locked for _, locked in seen)


@pytest.mark.parametrize("value", [f"{CLIENT_ID}$", "abc${HOME}def"])
def test_a_dollar_sign_is_refused(settings, env, twitch, value) -> None:  # noqa: F811
    before = env.read_text()
    response = _client(settings).put("/campaign/keys", json={"values": {"BERGET_API_KEY": value}})
    assert response.status_code == 400
    assert "$" in response.json()["detail"] and value not in response.text
    assert env.read_text() == before


def test_the_real_line_is_changed_and_not_the_comment(settings, env, twitch) -> None:  # noqa: F811
    env.write_text("# TWITCH_CLIENT_ID=\nA=1\nTWITCH_CLIENT_ID=gammalt\n")
    assert _client(settings).put("/campaign/keys", json={"values": {"TWITCH_CLIENT_ID": CLIENT_ID}}).status_code == 200
    assert env.read_text() == f"# TWITCH_CLIENT_ID=\nA=1\nTWITCH_CLIENT_ID={CLIENT_ID}\n"


@pytest.mark.parametrize(("host", "status"), [
    ("127.0.0.1", 200), ("127.0.0.2", 200), ("::1", 200), ("::ffff:127.0.0.1", 200),
    ("192.168.1.20", 403), ("::ffff:192.168.1.20", 403), ("0.0.0.0", 403), ("testclient", 403),
])
def test_every_loopback_address_counts_as_this_computer(settings, env, twitch, host, status) -> None:  # noqa: F811
    body = {"values": {"BERGET_API_KEY": CLIENT_ID}}
    assert _client(settings, host=host).put("/campaign/keys", json=body).status_code == status


# ---------- 7. bildgranskningens beskrivningar ----------


def test_the_reviews_notes_are_not_warnings(settings) -> None:
    from campaign.caption import build_caption
    from campaign.profile import load_profile
    from campaign.rules import check_clip

    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    info = {"width": 1080, "height": 1920, "duration": 30.0, "has_audio": True}
    manifest = {"text_applied": True, "overlay_applied": True}
    review = {"facecam_visible": True, "text_visible": True, "overlay_visible": True, "face_covered": False,
              "notes": ["The streamer is visible in the top panel."]}
    caption = build_caption("Close call", profile)
    for answer in (review, {**review, "notes": [], "problems": ["Gammal beskrivning."]}):
        problems, warnings = check_clip(info, manifest, profile, caption, "clutch", answer)
        assert problems == [] and warnings == []
    assert check_clip(info, manifest, profile, caption, "clutch", {**review, "face_covered": True})[0]


# ---------- GET /campaign/setup ----------


@pytest.fixture
def tools(monkeypatch) -> dict:
    found = {"ffmpeg": "/opt/ffmpeg", "ffprobe": "/opt/ffprobe"}
    filters = {"text": " T.. drawtext          V->V       Draw text.\n ... subtitles         V->V       Subtitles.\n"}
    monkeypatch.setattr(keys.shutil, "which", lambda tool: found.get(tool))
    monkeypatch.setattr(keys, "_ffmpeg_filters", lambda ffmpeg: filters["text"])
    return {"found": found, "filters": filters}


def _checks(settings) -> dict:
    response = _client(settings, host="192.168.1.20").get("/campaign/setup")  # bara läsning: från alla datorer
    assert response.status_code == 200
    checks = response.json()["checks"]
    assert all(set(c) - {"href"} == {"id", "label", "ok", "detail"} for c in checks)
    return {c["id"]: c for c in checks}


def test_setup_checks_tools_keys_campaigns_budget_ssd_and_space(settings, env, tools, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(api.estimate, "disk_gb", lambda path: (100.0, 228.0))
    monkeypatch.setenv("ANTHROPIC_API_KEY", CLIENT_ID)
    inactive = (settings.profiles_dir / "skiimasktimmy.yaml").read_text() + "\nactive: false\n"
    (settings.profiles_dir / "avstangd.yaml").write_text(inactive)
    checks = _checks(settings)
    assert list(checks) == ["ffmpeg", "ffprobe", "anthropic", "berget", "profile:skiimasktimmy", "budget", "ssd",
                            "disk"]
    assert [checks[i]["ok"] for i in ("ffmpeg", "ffprobe", "anthropic", "berget", "budget", "ssd", "disk")] == [
        True, True, True, False, True, True, True]
    assert "drawtext" in checks["ffmpeg"]["detail"]
    assert "100" in checks["disk"]["detail"] and "timmar" in checks["disk"]["detail"]
    assert checks["profile:skiimasktimmy"]["detail"]
    assert CLIENT_ID not in str(checks)


def test_setup_tells_what_is_missing(settings, env, tools, tmp_path, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(api.estimate, "disk_gb", lambda path: (9.7, 228.0))
    tools["filters"]["text"] = " T.. drawtext          V->V       Draw text.\n"
    del tools["found"]["ffprobe"]
    checks = _checks(settings)
    assert (checks["ffmpeg"]["ok"], checks["ffprobe"]["ok"], checks["disk"]["ok"]) == (False, False, False)
    assert checks["disk"]["detail"].startswith("9,7 GB ledigt av 228 GB")  # svenskt decimalkomma
    assert "subtitles" in checks["ffmpeg"]["detail"] and "drawtext" not in checks["ffmpeg"]["detail"]

    del tools["found"]["ffmpeg"]
    assert _checks(settings)["ffmpeg"]["ok"] is False

    _unplug(settings, tmp_path)
    checks = _checks(settings)
    assert (checks["ssd"]["ok"], checks["ssd"]["detail"]) == (False, SSD)
    assert checks["disk"]["ok"] is False

    budget_from_settings(settings).record("okänd-modell", "granska", 2_000_000, 0)
    assert _checks(settings)["budget"]["ok"] is False


def test_setup_and_the_campaigns_page_give_the_same_answer(settings, env, tools, monkeypatch) -> None:  # noqa: F811
    """Kontrollen använder samma kod som Kampanjer: samma läge, kampanjens namn och en länk dit."""
    import shutil

    from .conftest import PAKET

    monkeypatch.setattr(api.estimate, "disk_gb", lambda path: (100.0, 228.0))
    shutil.copy(PAKET / "config.timmy.yaml", settings.root)
    config = (PAKET / "config.timmy.yaml").read_text()
    (settings.root / "config.testkanal.yaml").write_text(  # kameraläget utan rutor: inte redo
        config.replace("  - skiimasktimmy\n", "  - testkanal\n").replace("gameplay_rect:", "# gameplay_rect:"))
    (settings.profiles_dir / "testkanal.yaml").write_text(
        (settings.profiles_dir / "skiimasktimmy.yaml").read_text().replace(
            "clipsmith_config: config.timmy.yaml", "clipsmith_config: config.testkanal.yaml").replace(
            "streamer: SkiiMaskTimmy", "streamer: Testkanal"))
    checks = _checks(settings)
    pages = {p["name"]: p for p in api.list_profiles(settings)}
    for name, label in (("skiimasktimmy", "SkiiMaskTimmy"), ("testkanal", "Testkanal")):
        row, page = checks[f"profile:{name}"], pages[name]
        assert row["label"] == f"Kampanjen {label}" and row["href"] == f"/campaigns/{name}"
        assert row["ok"] is (page["state"] == "Redo")
        assert row["detail"].startswith(page["state"])
        assert "nyckeln" not in row["detail"]  # nycklarna har egna rader
    assert checks["profile:testkanal"]["ok"] is False and "Facecam" in checks["profile:testkanal"]["detail"]
    assert "Inte redo" in checks["profile:testkanal"]["detail"]
    assert "Nycklar" not in checks["berget"]["detail"]
    assert "dollar" not in checks["budget"]["detail"] and "US$" in checks["budget"]["detail"]
