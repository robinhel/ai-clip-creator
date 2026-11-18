"""Liveklipp: chatten läses under streamen, och en topp blir en körning på en del av VOD:en."""

from __future__ import annotations

import datetime as dt
import json
import shutil
import socket
from types import SimpleNamespace

import pytest

from campaign import live
from campaign.cliplog import ClipLog
from campaign.live import PeakFinder, chat_for_part, message, part_id, part_window, vod_seconds
from clipsmith.models.chat import ChatLog

from .conftest import PAKET


def _calm(finder: PeakFinder, start: float, end: float, every: float = 5.0) -> list[float]:
    """Lugn chatt: ett meddelande var femte sekund. Returnerar topparna."""
    peaks = []
    t = start
    while t < end:
        peak = finder.add(message(t, "nice", "a"))
        if peak is not None:
            peaks.append(peak)
        t += every
    return peaks


def _burst(finder: PeakFinder, start: float, count: int, text: str = "KEKW") -> list[float]:
    """Chatten exploderar: count meddelanden på fem sekunder."""
    peaks = []
    for i in range(count):
        peak = finder.add(message(start + 5.0 * i / count, text, f"u{i}"))
        if peak is not None:
            peaks.append(peak)
    return peaks


def test_a_burst_after_calm_chat_is_one_peak() -> None:
    finder = PeakFinder(multiplier=3.0)
    assert _calm(finder, 0, 600) == []
    peaks = _burst(finder, 600, 30)
    assert len(peaks) == 1  # en topp, inte en per meddelande
    assert 600 <= peaks[0] <= 605


def test_peaks_closer_than_a_minute_become_one() -> None:
    finder = PeakFinder(multiplier=3.0)
    _calm(finder, 0, 600)
    assert len(_burst(finder, 600, 30)) == 1
    _calm(finder, 606, 640)
    assert _burst(finder, 640, 30) == []  # 40 s efter förra toppen
    _calm(finder, 646, 720)
    assert len(_burst(finder, 720, 30)) == 1  # två minuter efter: en ny topp


def test_no_peak_before_the_chat_has_a_baseline() -> None:
    """De första minuterna finns inget normalt läge att jämföra med."""
    finder = PeakFinder(multiplier=3.0)
    assert _burst(finder, 10, 30) == []


def test_steady_busy_chat_is_not_a_peak() -> None:
    finder = PeakFinder(multiplier=3.0)
    assert _calm(finder, 0, 900, every=0.5) == []


def test_clip_command_is_a_peak_on_its_own() -> None:
    finder = PeakFinder(multiplier=3.0)
    _calm(finder, 0, 600)
    assert finder.add(message(601, "!clip", "fan")) == 601


def test_message_counts_hype_emotes_and_clip_command() -> None:
    msg = message(12.5, "KEKW KEKW OMEGALUL nice", "a")
    assert (msg.time_in_seconds, msg.hype_emote_count, msg.is_clip_command) == (12.5, 2, False)
    assert message(1, "!Clip that", "b").is_clip_command


def test_vod_seconds_counts_from_when_the_vod_started() -> None:
    # VOD:en startade 20:00:00 UTC. Meddelandet kom 21:02:03,5 UTC.
    sent_ms = 1_789_000_000_000
    started = (
        dt.datetime.fromtimestamp(sent_ms / 1000 - 3723.5, dt.UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )
    assert abs(vod_seconds(sent_ms, started) - 3723.5) < 0.01


def test_part_starts_before_the_peak_and_ends_after() -> None:
    assert part_window(3723.0) == (3633.0, 3753.0)  # 90 s före och 30 s efter
    assert part_window(40.0) == (0.0, 70.0)  # aldrig före VOD:ens början
    assert part_id("2927104410", 3) == "2927104410_live_3"


def test_chat_for_the_part_has_times_from_the_part_start() -> None:
    msgs = [message(t, "KEKW", "a") for t in (100.0, 3640.0, 3700.0, 3760.0)]
    log = chat_for_part(msgs, 3633.0, 3753.0, "2927104410_live_3")
    assert log.video_id == "2927104410_live_3"
    assert [m.time_in_seconds for m in log.messages] == [7.0, 67.0]
    assert ChatLog.from_json(log.to_json()).messages[0].hype_emote_count == 1


def test_irc_line_becomes_a_message_with_its_time() -> None:
    from campaign.live import parse_irc

    line = (
        "@badge-info=;display-name=Fan_1;tmi-sent-ts=1789000000123;user-id=42 "
        ":fan_1!fan_1@fan_1.tmi.twitch.tv PRIVMSG #skiimasktimmy :KEKW KEKW no way"
    )
    assert parse_irc(line) == ("fan_1", "KEKW KEKW no way", 1789000000123)
    # Annat än chattmeddelanden ger None: PING, JOIN och serverns svar.
    assert parse_irc("PING :tmi.twitch.tv") is None
    assert (
        parse_irc(":justinfan123!justinfan123@justinfan123.tmi.twitch.tv JOIN #skiimasktimmy")
        is None
    )
    assert parse_irc(":tmi.twitch.tv 001 justinfan123 :Welcome, GLHF!") is None


def test_one_spammer_is_not_a_peak() -> None:
    """En person som skriver 30 meddelanden på fem sekunder är inte hela chatten som exploderar."""
    finder = PeakFinder(multiplier=3.0)
    _calm(finder, 0, 600)
    peaks = [
        p
        for i in range(30)
        if (p := finder.add(message(600 + i / 6, "KEKW KEKW", "spammer"))) is not None
    ]
    assert peaks == []


def _steps(finder: PeakFinder, start: float, end: float, every: float) -> list[float]:
    """Jämn chatt med nya avsändare varje gång. Returnerar topparna."""
    peaks = []
    # Heltal: summerade flyttal kan ge två meddelanden på samma sekund.
    for n in range(round((end - start) / every)):
        if (peak := finder.add(message(start + n * every, "nice", f"{start}_{n}"))) is not None:
            peaks.append(peak)
    return peaks


def test_a_sudden_jump_is_a_peak_but_the_same_level_reached_slowly_is_not() -> None:
    # Plötsligt: från lugn chatt till 8 meddelanden per 15 sekunder på en gång.
    sudden = PeakFinder(multiplier=3.0)
    _calm(sudden, 0, 600)
    assert _steps(sudden, 600, 615, 15 / 8) != []
    # Långsamt: chatten ökar steg för steg till samma nivå. Då är det ingen topp.
    slow = PeakFinder(multiplier=3.0)
    _calm(slow, 0, 600)
    peaks = []
    for i, every in enumerate((15 / 4, 15 / 5, 15 / 6, 15 / 7, 15 / 8)):
        peaks += _steps(slow, 600 + 15 * i, 615 + 15 * i, every)
    assert peaks == []


# ---------- bevakningen: från chatt till körning, med fakes för Twitch, tid och körningen ----------

CREATED = "2026-11-18T18:00:00Z"
STARTED = dt.datetime.fromisoformat(CREATED.replace("Z", "+00:00")).timestamp()
PROFILE = "skiimasktimmy"


class NotReady(Exception):
    pass


class Failed(Exception):
    pass


class World:
    """Låtsas-Twitch, låtsas-klocka och låtsas-körning. Inget går mot nätverket och inget sover på riktigt."""

    def __init__(self, chats: list | None = None) -> None:
        self.t = STARTED + 2000.0  # före toppen, som när chatten läses live
        self.vod = {
            "id": "777",
            "createdAt": CREATED,
            "status": "RECORDING",
            "title": "Kvällens stream",
            "game": {"name": "Bodycam"},
        }
        # En lista per anslutning, eller ett fel som anslutningen ger.
        self.chats = list(chats or [])
        self.connects = 0
        self.sleeps: list[float] = []
        self.fetches: list[tuple] = []
        self.not_ready = 0  # så många gånger svarar fetch_part PartNotReady
        self.failed: str | None = None
        self.runs: list[dict] = []
        self.notes: list[tuple] = []
        self.on_sleep = None
        self.watcher: live.Watcher | None = None

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds
        if self.on_sleep:
            self.on_sleep(seconds)

    def latest(self, channel: str) -> dict:
        assert channel == PROFILE
        return self.vod

    def read_chat(self, channel: str, timeout_s: float):
        assert timeout_s > 300  # över Twitchs PING-intervall
        self.connects += 1
        if not self.chats:  # inget mer att läsa: streamen är slut
            self.watcher.reading.pop(PROFILE, None)
            return
        chat = self.chats.pop(0)
        if isinstance(chat, Exception):
            raise chat
        yield from chat

    def fetch_part(self, vod_id: str, start_s: float, end_s: float, folder) -> float:
        self.fetches.append((vod_id, start_s, end_s, folder))
        if self.not_ready:
            self.not_ready -= 1
            raise NotReady("inte inspelat än")
        if self.failed:
            raise Failed(self.failed)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{folder.name}.mp4").write_bytes(b"mp4")
        return start_s - 4.0  # delen börjar vid en del i spellistan, lite före

    def run(self, *args, **kwargs) -> None:
        """Samma argument som run_campaign_vod."""
        from clipsmith.db.models import Run, RunStatus
        from clipsmith.db.session import get_session

        run_id, vod_id, profile_name, game, settings = args
        app = kwargs["app"]
        self.runs.append(
            {
                "run_id": run_id,
                "vod_id": vod_id,
                "profile": profile_name,
                "game": game,
                "active": app.state.active_run_id,
                **kwargs,
            }
        )
        log = ClipLog(settings.data_dir / "klipp.db")
        for status in ("renderad", "renderad", "underkand"):
            log.add(
                vod_id=vod_id,
                profile=profile_name,
                file="x.mp4",
                title=f"Klipp {status}",
                caption="c",
                status=status,
            )
        db = get_session()
        db.get(Run, run_id).status = RunStatus.done
        db.commit()
        db.close()
        app.state.active_run_id = None

    def notify(self, message: str, url: str | None = None) -> None:
        self.notes.append((message, url))


def sent(t: float) -> int:
    return int((STARTED + t) * 1000)


def exploding_chat(peak_at: float = 3000.0) -> list[tuple[str, str, int]]:
    """Lugn chatt i tio minuter, sedan exploderar den vid peak_at, och sedan lugnt igen."""
    calm = [("a", "nice", sent(t)) for t in range(int(peak_at) - 600, int(peak_at), 5)]
    burst = [(f"u{i}", "KEKW", sent(peak_at + 5.0 * i / 30)) for i in range(30)]
    after = [("a", "nice", sent(t)) for t in range(int(peak_at) + 6, int(peak_at) + 60, 5)]
    return calm + burst + after


@pytest.fixture
def world(settings, tmp_path, monkeypatch) -> World:
    from clipsmith.db.session import init_db

    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    settings.berget_api_key = "test"
    monkeypatch.chdir(tmp_path)  # clipsmiths work_dir är ./work
    init_db(tmp_path / "clipsmith.db")
    found = World()
    app = SimpleNamespace(state=SimpleNamespace(active_run_id=None))
    found.watcher = watcher = live.Watcher(settings, app)
    watcher.latest, watcher.chat, watcher.run, watcher.notify = (
        found.latest,
        found.read_chat,
        found.run,
        found.notify,
    )
    watcher.now, watcher.sleep, watcher.spawn = (
        found.now,
        found.sleep,
        lambda target, *args: target(*args),
    )
    watcher.part = SimpleNamespace(
        fetch_part=found.fetch_part, PartNotReady=NotReady, PartFailed=Failed
    )
    live.LiveState(settings).set_watching(PROFILE, True)
    return found


def _peaks(settings) -> list[dict]:
    return live.LiveState(settings).channel(PROFILE)["peaks"]


def _one_peak(world: World, settings) -> live.Job:
    world.chats = [exploding_chat()]
    world.watcher.check()
    assert world.watcher.jobs.qsize() == 1
    return world.watcher.jobs.get_nowait()


def test_a_burst_in_the_live_chat_fetches_the_part_a_minute_after_the_peak(world, settings) -> None:
    job = _one_peak(world, settings)
    assert 3000 <= job.peak_s <= 3005
    assert [p["status"] for p in _peaks(settings)] == ["vantar"]
    world.watcher.handle(job)
    vod_id, start_s, end_s, folder = world.fetches[0]
    assert (vod_id, start_s, end_s) == ("777", job.peak_s - 90, job.peak_s + 30)
    assert folder.resolve() == (settings.root / "work" / "777_live_1").resolve()
    # cirka 60 s efter toppen, räknat i väggtid
    assert abs(world.t - (STARTED + job.peak_s + 60)) < 1
    assert live.LiveState(settings).channel(PROFILE)["live"] is True


def test_the_part_chat_has_times_from_the_real_start(world, settings) -> None:
    job = _one_peak(world, settings)
    world.watcher.handle(job)
    _, start_s, end_s, folder = world.fetches[0]
    chat = json.loads((folder / "chat.json").read_text())
    assert chat["video_id"] == "777_live_1"
    times = [m["time_in_seconds"] for m in chat["messages"]]
    assert times and min(times) >= 0 and max(times) < end_s - (start_s - 4.0)
    burst = [m for m in chat["messages"] if m["author"] == "u0"][0]
    # Explosionen började 3000 s in i VOD:en. Tiden räknas från den verkliga starten.
    assert burst["time_in_seconds"] == pytest.approx(3000.0 - (start_s - 4.0))


def test_the_run_is_local_with_the_chat_lag_focus_and_holds_the_lock(world, settings) -> None:
    from clipsmith.db.models import Run
    from clipsmith.db.session import get_session

    world.watcher.handle(_one_peak(world, settings))
    run = world.runs[0]
    assert (run["vod_id"], run["profile"], run["game"]) == ("777_live_1", PROFILE, "Bodycam")
    assert run["local"] is True and run["focus"] == live.CHAT_LAG_FOCUS
    assert run["active"] == run["run_id"]  # active_run_id sattes innan körningen
    db = get_session()
    assert db.get(Run, run["run_id"]).vod_id == "777_live_1"
    db.close()
    meta = ClipLog(settings.data_dir / "klipp.db").run(run["run_id"])
    assert (meta["profile"], meta["focus"]) == (PROFILE, live.CHAT_LAG_FOCUS)
    assert "(live, topp 1)" in meta["title"]
    peak = _peaks(settings)[0]
    assert (peak["status"], peak["run_id"]) == ("klar", run["run_id"])


def test_a_peak_waits_in_the_queue_while_another_run_holds_the_lock(world, settings) -> None:
    job = _one_peak(world, settings)
    world.watcher.app.state.active_run_id = 99
    seen = []

    def free_after_a_while(seconds: float) -> None:
        if seconds == live.QUEUE_S:
            seen.append(_peaks(settings)[0]["status"])
            if len(seen) == 3:
                world.watcher.app.state.active_run_id = None

    world.on_sleep = free_after_a_while
    world.watcher.handle(job)
    assert seen == ["ko", "ko", "ko"]
    assert len(world.runs) == 1
    assert _peaks(settings)[0]["status"] == "klar"


def test_no_budget_skips_the_peak_without_fetching(world, settings) -> None:
    settings.budget_day_usd = 0.0  # dagstaket är nått
    world.watcher.handle(_one_peak(world, settings))
    assert world.fetches == [] and world.runs == []
    peak = _peaks(settings)[0]
    assert (peak["status"], peak["reason"]) == ("hoppad", "Budgeten är slut.")


def test_a_part_that_is_not_ready_is_tried_again_for_three_minutes(world, settings) -> None:
    world.not_ready = 1000
    world.watcher.handle(_one_peak(world, settings))
    assert len(world.fetches) >= 12
    assert len({f[3] for f in world.fetches}) == 1  # samma mapp varje gång
    assert sum(s for s in world.sleeps if s == live.RETRY_S) == pytest.approx(180)
    assert world.runs == []
    assert _peaks(settings)[0]["status"] == "fel"


def test_a_part_that_is_ready_on_the_second_try_is_run(world, settings) -> None:
    world.not_ready = 1
    world.watcher.handle(_one_peak(world, settings))
    assert len(world.fetches) == 2 and len(world.runs) == 1


def test_a_failed_part_is_an_error_with_its_text(world, settings) -> None:
    world.failed = "Twitch gav ingen spellista."
    world.watcher.handle(_one_peak(world, settings))
    peak = _peaks(settings)[0]
    assert (peak["status"], peak["reason"]) == ("fel", "Twitch gav ingen spellista.")
    assert world.runs == []


def test_a_broken_chat_connects_again_with_a_growing_wait(world, settings) -> None:
    world.chats = [OSError("bruten"), socket.timeout("tyst"), [], exploding_chat()]
    world.watcher.check()
    assert world.connects == 5  # tre avbrott, chatten, och sedan är streamen slut
    assert world.sleeps[:3] == [1.0, 2.0, 4.0]
    assert world.sleeps[3] == 1.0  # väntan nollställs när meddelanden kommer
    assert world.watcher.jobs.qsize() == 1


def test_read_chat_stops_on_reconnect_and_answers_ping(monkeypatch) -> None:
    lines = (
        b"PING :tmi.twitch.tv\r\n"
        b"@tmi-sent-ts=1789000000123 :fan!fan@fan.tmi.twitch.tv PRIVMSG #kanal :KEKW\r\n"
        b":tmi.twitch.tv RECONNECT\r\n"
        b"@tmi-sent-ts=1789000000999 :sen!sen@sen.tmi.twitch.tv PRIVMSG #kanal :efter\r\n"
    )

    class Conn:
        def __init__(self) -> None:
            self.sent: list[bytes] = []
            self.data = [lines]

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            pass

        def sendall(self, data: bytes) -> None:
            self.sent.append(data)

        def recv(self, size: int) -> bytes:
            return self.data.pop(0) if self.data else b""

    conn = Conn()
    opened = {}

    def connect(address, timeout):
        opened["timeout"] = timeout
        return conn

    monkeypatch.setattr(live.socket, "create_connection", connect)
    # Inget efter RECONNECT: läsaren ansluter igen.
    assert list(live.read_chat("Kanal")) == [("fan", "KEKW", 1789000000123)]
    assert b"PONG :tmi.twitch.tv\r\n" in conn.sent
    assert opened["timeout"] > 300


def test_a_channel_that_is_not_live_is_not_read(world, settings) -> None:
    world.vod["status"] = "RECORDED"
    world.chats = [exploding_chat()]
    world.watcher.check()
    assert world.connects == 0
    state = live.LiveState(settings).channel(PROFILE)
    assert (state["live"], state["vod_id"]) == (False, "777")


def test_a_campaign_that_is_not_watched_is_not_asked_about(world, settings) -> None:
    live.LiveState(settings).set_watching(PROFILE, False)
    world.watcher.latest = lambda channel: pytest.fail("Twitch frågades")
    world.watcher.check()
    assert world.connects == 0


def test_a_new_vod_starts_a_new_list_of_peaks(world, settings) -> None:
    world.watcher.handle(_one_peak(world, settings))
    world.vod = {**world.vod, "id": "888"}
    world.chats = [exploding_chat()]
    world.watcher.check()
    state = live.LiveState(settings).channel(PROFILE)
    assert state["vod_id"] == "888"
    assert [p["n"] for p in state["peaks"]] == [1]
    assert world.watcher.jobs.get_nowait().part_id == "888_live_1"


def test_a_finished_run_notifies_once_per_rendered_clip(world, settings) -> None:
    world.watcher.handle(_one_peak(world, settings))
    assert len(world.notes) == 2  # två renderade, inte det underkända
    assert all(url == f"{settings.web_url}/queue?tab=granska" for _, url in world.notes)


def test_a_restart_marks_unfinished_peaks_as_errors(world, settings) -> None:
    _one_peak(world, settings)
    live.LiveState(settings).after_restart()
    state = live.LiveState(settings).channel(PROFILE)
    assert state["live"] is False
    assert state["peaks"][0]["status"] == "fel"
