"""Liveklipp från en fri Twitch-kanal utan kampanj: egen inspelning av strömmen, och körningen med _ingen.
Samma låtsas-Twitch, låtsas-klocka och låtsas-körning som i test_live.py."""

from __future__ import annotations

import json
import shutil
from types import SimpleNamespace

import pytest

from campaign import live
from campaign.cliplog import ClipLog

from .conftest import PAKET
from .test_live import CREATED, PROFILE, STARTED, exploding_chat, world  # noqa: F401


LOGIN = "ishowspeed"
KEY = f"kanal:{LOGIN}"


class FakeRecord:
    """Låtsas-campaign.live_record: Recorder och cut, utan ffmpeg och utan Twitch. Som bufferten städar cut
    bort delar äldre än keep_s, med världens klocka."""

    def __init__(self, now) -> None:
        self.recorders: list = []
        self.cuts: list[tuple] = []
        self.cut_at: list[float] = []  # klockan när cut anropades
        self.now = now
        self.keep_s = 600.0
        self.hole: tuple[float, float] | None = None  # väggklocka: reklam eller avbrott i delen
        self.not_ready = 0
        self.failed: str | None = None
        recorders = self.recorders

        class Recorder:
            def __init__(rec, login: str, folder, keep_s: float = 600) -> None:
                rec.login, rec.folder, rec.stopped = login, folder, False
                recorders.append(rec)

            def run(rec) -> None:
                pass

            def stop(rec) -> None:
                rec.stopped = True

        self.Recorder = Recorder

    def cut(self, folder, start_wall: float, end_wall: float, out) -> list[tuple[float, float]]:
        self.cuts.append((folder, start_wall, end_wall, out))
        self.cut_at.append(self.now())
        if self.not_ready:
            self.not_ready -= 1
            raise live.live_part.PartNotReady("inte inspelat än")
        if self.failed:
            raise live.live_part.PartFailed(self.failed)
        if start_wall < self.now() - self.keep_s:
            raise live.live_part.PartFailed("Början av delen är städad.")
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{out.name}.mp4").write_bytes(b"mp4")
        first = start_wall - 4.0  # delen börjar vid en bildruta lite före
        if self.hole is None:
            return [(first, end_wall)]
        return [(first, self.hole[0]), (self.hole[1], end_wall)]


@pytest.fixture
def free(world, settings):  # noqa: F811 (fixturen från test_live.py)
    """Samma värld, men bevakningen gäller kanalen ishowspeed utan kampanj."""
    shutil.copy(PAKET / "profiles" / "_ingen.yaml", settings.profiles_dir / "_ingen.yaml")
    shutil.copy(PAKET / "config.live.yaml", settings.root / "config.live.yaml")
    watcher = world.watcher
    world.key = KEY
    world.stream = {
        "id": "555",
        "createdAt": CREATED,
        "title": "SPEED LIVE",
        "game": {"name": "IRL"},
    }
    world.asked: list[str] = []

    def stream(login: str) -> dict | None:
        world.asked.append(login)
        return world.stream

    world.recording: list = []

    def spawn(target, *args) -> None:
        if target == watcher._recording:  # inspelningen går i en egen tråd tills den stoppas
            world.recording.append(args)
        else:
            target(*args)

    world.record = FakeRecord(world.now)
    watcher.stream, watcher.record, watcher.spawn = stream, world.record, spawn
    watcher.latest = lambda channel: pytest.fail("en fri kanal frågar inte efter VOD:en")
    watcher.part = SimpleNamespace(
        fetch_part=lambda *args: pytest.fail("en fri kanal hämtar inte ur VOD:en")
    )
    state = live.LiveState(settings)
    state.set_watching(PROFILE, False)
    state.set_watching(KEY, True)
    return world


def _free_peak(found) -> live.Job:
    found.chats = [exploding_chat()]
    found.watcher.check()
    assert found.watcher.jobs.qsize() == 1
    return found.watcher.jobs.get_nowait()


def test_a_live_free_channel_starts_the_recorder_and_the_chat(free, settings) -> None:
    job = _free_peak(free)
    assert free.asked == [LOGIN]
    [rec] = free.record.recorders
    assert (rec.login, rec.folder) == (LOGIN, settings.data_dir / "live" / LOGIN)
    assert len(free.recording) == 1 and free.connects >= 1
    assert (job.key, job.profile, job.channel) == (KEY, "_ingen", LOGIN)
    assert job.part_id == "555_live_1"
    state = live.LiveState(settings).channel(KEY)
    assert (state["live"], state["vod_id"], state["watching"]) == (True, "555", True)
    assert [p["status"] for p in state["peaks"]] == ["ko"]  # delen är klippt och väntar i kön
    free.watcher.check()  # nästa varv: samma inspelning, ingen ny
    assert len(free.record.recorders) == 1


def test_a_free_channel_that_is_not_live_starts_nothing(free, settings) -> None:
    free.stream = None
    free.chats = [exploding_chat()]
    free.watcher.check()
    assert free.record.recorders == [] and free.connects == 0
    assert live.LiveState(settings).channel(KEY)["live"] is False


def test_the_recorder_stops_and_the_buffer_is_removed_when_watching_is_turned_off(
    free, settings
) -> None:
    free.watcher.check()
    [rec] = free.record.recorders
    rec.folder.mkdir(parents=True)
    (rec.folder / "index.tsv").write_text("")
    other = settings.data_dir / "live" / "annan"  # en kanal som bevakas har kvar sin buffert
    other.mkdir()
    live.LiveState(settings).set_watching("kanal:annan", True)
    free.stream = None  # kanalen annan är inte live
    live.LiveState(settings).set_watching(KEY, False)
    free.watcher.check()
    assert rec.stopped and free.watcher.recorders == {}
    assert not rec.folder.exists() and other.exists()


def test_the_buffer_is_removed_when_watching_is_turned_off_after_the_stream_ended(
    free, settings
) -> None:
    free.watcher.check()
    free.stream = None
    free.watcher.check()  # streamen slutade: inspelningen stoppades, bufferten finns kvar
    folder = free.record.recorders[0].folder
    folder.mkdir(parents=True)
    live.LiveState(settings).set_watching(KEY, False)
    free.watcher.check()
    assert not folder.exists()


def test_the_recorder_stops_when_the_stream_ends(free, settings) -> None:
    free.watcher.check()
    free.stream = None
    free.watcher.check()
    assert free.record.recorders[0].stopped
    state = live.LiveState(settings).channel(KEY)
    assert (state["live"], state["vod_id"]) == (False, "555")  # topparna från streamen finns kvar


def test_a_recorder_that_stops_by_itself_is_started_again(free, settings) -> None:
    free.watcher.check()
    rec = free.record.recorders[0]
    free.watcher._recording(KEY, rec)  # run() kom tillbaka: inspelningen dog
    assert free.watcher.recorders == {}
    free.watcher.check()
    assert len(free.record.recorders) == 2


def test_a_free_peak_is_cut_from_the_recording_and_run_without_a_campaign(free, settings) -> None:
    from clipsmith.db.models import Run
    from clipsmith.db.session import get_session

    job = _free_peak(free)
    free.watcher.handle(job)
    folder, start_wall, end_wall, out = free.record.cuts[0]
    assert folder == settings.data_dir / "live" / LOGIN
    assert (start_wall, end_wall) == (STARTED + job.peak_s - 90, STARTED + job.peak_s + 30)
    assert out.resolve() == (settings.root / "work" / "555_live_1").resolve()
    run = free.runs[0]
    assert (run["vod_id"], run["profile"], run["game"]) == ("555_live_1", "_ingen", "IRL")
    assert run["local"] is True and run["focus"] == live.CHAT_LAG_FOCUS
    meta = ClipLog(settings.data_dir / "klipp.db").run(run["run_id"])
    assert (meta["profile"], meta["channel_name"]) == ("_ingen", LOGIN)
    assert meta["title"] == "SPEED LIVE (live, topp 1)"
    db = get_session()
    assert db.get(Run, run["run_id"]).channel == LOGIN
    db.close()
    peak = live.LiveState(settings).channel(KEY)["peaks"][0]
    assert (peak["status"], peak["run_id"]) == ("klar", run["run_id"])
    assert all(LOGIN in note for note, _ in free.notes)


def test_the_free_part_chat_has_times_from_the_recorded_start(free, settings) -> None:
    job = _free_peak(free)
    free.watcher.handle(job)
    _, start_wall, _, out = free.record.cuts[0]
    real_start = start_wall - 4.0 - STARTED  # cut svarar med väggklockan för delens början
    chat = json.loads((out / "chat.json").read_text())
    assert chat["video_id"] == "555_live_1"
    burst = [m for m in chat["messages"] if m["author"] == "u0"][0]
    assert burst["time_in_seconds"] == pytest.approx(3000.0 - real_start)
    assert min(m["time_in_seconds"] for m in chat["messages"]) >= 0


def test_the_free_part_chat_leaves_out_a_hole_and_follows_the_picture_after_it(
    free, settings
) -> None:
    # Reklam 3010-3020 s in i streamen: de tio sekunderna finns inte i mp4-filen.
    hole_start, hole_end = 3010.0, 3020.0
    free.record.hole = (STARTED + hole_start, STARTED + hole_end)
    job = _free_peak(free)
    free.watcher.handle(job)
    _, start_wall, _, out = free.record.cuts[0]
    real_start = start_wall - 4.0 - STARTED
    sent_s = [ms / 1000 - STARTED for _, _, ms in exploding_chat()]
    expected = [
        t - real_start - (hole_end - hole_start if t >= hole_end else 0.0)
        for t in sent_s
        if job.start_s <= t < job.end_s and not hole_start <= t < hole_end
    ]
    chat = json.loads((out / "chat.json").read_text())
    times = [m["time_in_seconds"] for m in chat["messages"]]
    assert sorted(times) == pytest.approx(sorted(expected))
    assert len(times) < len([t for t in sent_s if job.start_s <= t < job.end_s])  # hålet är borta
    run = free.runs[0]
    meta = ClipLog(settings.data_dir / "klipp.db").run(run["run_id"])
    assert meta["duration_s"] == round(job.end_s - real_start - (hole_end - hole_start))


def test_a_free_part_is_cut_as_soon_as_it_is_recorded(free, settings) -> None:
    job = _free_peak(free)
    assert free.record.cut_at == [pytest.approx(STARTED + job.peak_s + live.DELAY_S)]
    assert (free.record.cuts[0][3] / "555_live_1.mp4").exists()  # klar innan toppen tas ur kön


def test_a_free_peak_that_waits_in_the_queue_keeps_its_part(free, settings) -> None:
    # Förra körningen tog en kvart. Bufferten har städat bort början av delen när toppen tas ur kön.
    job = _free_peak(free)
    free.t += 900.0
    free.watcher.handle(job)
    assert len(free.record.cuts) == 1  # delen klipptes en gång, när den var inspelad
    peak = live.LiveState(settings).channel(KEY)["peaks"][0]
    assert (peak["status"], peak["reason"]) == ("klar", None)
    assert free.runs[0]["vod_id"] == "555_live_1"


def _free_peak_not_queued(found) -> dict:
    """Toppen kom aldrig till kön: delen gick inte att klippa. Svarar med toppen i läget."""
    found.chats = [exploding_chat()]
    found.watcher.check()
    assert found.watcher.jobs.empty()
    return live.LiveState(found.watcher.settings).channel(KEY)["peaks"][0]


def test_a_free_part_that_is_not_ready_is_tried_again_for_three_minutes(free, settings) -> None:
    free.record.not_ready = 1000
    peak = _free_peak_not_queued(free)
    assert len(free.record.cuts) >= 12
    assert sum(s for s in free.sleeps if s == live.RETRY_S) == pytest.approx(180)
    assert free.runs == []
    assert (peak["status"], peak["reason"]) == ("fel", live.NOT_RECORDED)
    assert not free.record.cuts[0][3].exists()


def test_a_free_part_that_failed_is_an_error_with_its_text(free, settings) -> None:
    free.record.failed = "Inspelningen har inte den delen."
    peak = _free_peak_not_queued(free)
    assert (peak["status"], peak["reason"]) == ("fel", "Inspelningen har inte den delen.")
    assert free.runs == []


def test_a_free_part_that_breaks_is_an_error_and_not_queued(free, settings) -> None:
    def broken(*args):
        raise OSError("disken är full")

    free.record.cut = broken
    peak = _free_peak_not_queued(free)
    assert (peak["status"], peak["reason"]) == ("fel", "disken är full")


def test_no_budget_skips_a_free_peak(free, settings) -> None:
    settings.budget_day_usd = 0.0
    free.watcher.handle(_free_peak(free))
    assert free.runs == []
    assert not free.record.cuts[0][3].exists()  # den klippta delen tas bort
    peak = live.LiveState(settings).channel(KEY)["peaks"][0]
    assert (peak["status"], peak["reason"]) == ("hoppad", live.NO_BUDGET)


def test_a_clip_without_a_campaign_gets_no_image_review_from_claude(
    free, settings, monkeypatch
) -> None:
    from campaign import pipeline
    from campaign.profile import load_profile

    seen: dict = {}
    monkeypatch.setattr(pipeline, "Brain", lambda *a, **k: pytest.fail("ingen Brain utan kampanj"))
    monkeypatch.setattr(pipeline, "finish_vod", lambda *a, **k: seen.update(k) or [])
    profile = load_profile("_ingen", settings.profiles_dir)
    pipeline.finish("555_live_1", profile, settings, "IRL", 7)
    assert seen["review"] is False and seen["brain"] is None


def test_stream_info_asks_twitch_for_the_live_stream(monkeypatch) -> None:
    asked: list[str] = []
    answer = {
        "data": {
            "user": {"stream": {"id": "555", "createdAt": CREATED, "title": "t", "game": None}}
        }
    }

    def gql(query: str) -> dict:
        asked.append(query)
        return answer

    monkeypatch.setattr(live.twitch, "gql_query", gql)
    assert live.stream_info("IShowSpeed")["id"] == "555"
    assert 'user(login: "ishowspeed")' in asked[0] and "stream" in asked[0]
    answer["data"]["user"]["stream"] = None
    assert live.stream_info("ishowspeed") is None  # inte live
    answer["data"]["user"] = None
    assert live.stream_info("finnsinte") is None
    # Fel format frågar inte Twitch.
    assert live.stream_info('x" } evil') is None and len(asked) == 3


@pytest.mark.parametrize("status", ["vantar", "hamtar"])
def test_the_buffer_is_kept_while_a_peak_waits_to_be_cut(free, settings, status) -> None:
    free.watcher.check()
    folder = free.record.recorders[0].folder
    folder.mkdir(parents=True)
    state = live.LiveState(settings)
    n = state.add_peak(KEY, "555", 3000.0, "2026-11-18T12:00:00+00:00")  # delen är inte klippt än
    state.update_peak(KEY, "555", n, status=status)
    state.set_watching(KEY, False)
    free.watcher.check()
    assert folder.exists() and free.record.recorders[0].stopped
    state.update_peak(KEY, "555", n, status="klar")
    free.watcher.check()  # nästa varv: ingen topp väntar, bufferten tas bort
    assert not folder.exists()


def test_a_free_peak_is_an_error_when_it_cannot_be_queued(free, settings, monkeypatch) -> None:
    mark = free.watcher._mark

    def broken(job, status, **fields):
        if status == "ko":
            raise OSError("läget gick inte att skriva")
        mark(job, status, **fields)

    monkeypatch.setattr(free.watcher, "_mark", broken)
    peak = _free_peak_not_queued(free)
    assert (peak["status"], peak["reason"]) == ("fel", "läget gick inte att skriva")
