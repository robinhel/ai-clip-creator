"""Liveklipp: chatten läses medan streamen pågår. När chatten exploderar blir stunden före toppen en körning
på en kort del av VOD:en, som Twitch spelar in under tiden.

Här finns kärnan utan nätverk: toppar i chatten, var toppen ligger i VOD:en, och delen som ska hämtas.
"""

from __future__ import annotations

import datetime as dt
import importlib
import json
import logging
import os
import queue
import socket
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from twitchdl import twitch

from clipsmith.candidates.math import compute_density_scores
from clipsmith.db.models import Run, RunStatus
from clipsmith.db.session import get_session
from clipsmith.models.chat import HYPE_EMOTES, ChatLog, ChatMessage

from .api import _describe_profile, _log, _profile, _start_lock
from .budget import BudgetExceeded, budget_from_settings
from .chat import lock as chat_lock
from .config import Settings, load_settings
from .editor import lock as editor_lock
from .pipeline import clipsmith_config, run_campaign_vod
from .scout import notify_mac
from .streams import LOGIN

log = logging.getLogger(__name__)

WINDOW_S = 15.0  # samma fönster som toppsökningen i clipsmith (density_window_s)
BASELINE_S = 600.0  # det normala läget är chatten de senaste tio minuterna
WARMUP_S = 120.0  # före det finns inget normalt läge att jämföra med
GAP_S = 60.0  # toppar närmare än en minut blir samma klipp
PRE_S = 90.0  # delen börjar så här långt före toppen
POST_S = 30.0  # och slutar så här långt efter
# En avsändare väger högst så här mycket per fönster: en spammare är inte hela chatten.
AUTHOR_CAP = 3.0
SURGE_GAIN = 0.5  # ökningen mot förra fönstret räknas extra: en topp är en plötslig ökning
CHECK_EVERY_S = 1.0  # chatten räknas om högst en gång per sekund

# Skickas som focus till Claudes val, så att src/clipsmith inte behöver ändras.
CHAT_LAG_FOCUS = (
    "Liveklipp från en topp i chatten. Chatten reagerar 5-15 sekunder efter det som händer, "
    "så händelsen ligger före toppen: börja klippet precis före händelsen, inte vid toppen."
)


def message(t: float, text: str, author: str) -> ChatMessage:
    """Ett chattmeddelande, räknat som clipsmith räknar en VOD:s chatt."""
    text = text.strip()
    return ChatMessage(
        time_in_seconds=float(t),
        message=text,
        author=author,
        is_clip_command=text.lower().startswith("!clip"),
        hype_emote_count=len(set(text.split()) & HYPE_EMOTES),
    )


class PeakFinder:
    """Hittar toppar i chatten ett meddelande i taget, med samma regel som körningarna på hela VOD:en
    (compute_density_scores). En topp är ett fönster på 15 sekunder där chatten väger minst `multiplier`
    gånger det normala de senaste tio minuterna, eller ett !clip från en tittare. Varje avsändare har ett tak
    per fönster, och en ökning mot förra fönstret räknas extra."""

    def __init__(
        self,
        multiplier: float = 3.0,
        author_cap: float | None = AUTHOR_CAP,
        surge_gain: float = SURGE_GAIN,
    ) -> None:
        self.multiplier = multiplier
        self.author_cap = author_cap
        self.surge_gain = surge_gain
        self._recent: deque[ChatMessage] = deque()
        self._first: float | None = None
        self._last_peak: float | None = None
        self._last_check: float | None = None

    def add(self, msg: ChatMessage) -> float | None:
        """Lägg till ett meddelande. Returnerar toppens tid i sekunder när en ny topp börjar, annars None."""
        t = msg.time_in_seconds
        if self._first is None:
            self._first = t
        self._recent.append(msg)
        while self._recent and self._recent[0].time_in_seconds < t - BASELINE_S:
            self._recent.popleft()
        if self._last_peak is not None and t - self._last_peak < GAP_S:
            return None
        if msg.is_clip_command or self._is_burst(t):
            self._last_peak = t
            return t
        return None

    def _is_burst(self, t: float) -> bool:
        if t - self._first < WARMUP_S:
            return False
        # Högst en räkning per sekund: en stor chatt skriver många meddelanden i sekunden.
        if self._last_check is not None and t - self._last_check < CHECK_EVERY_S:
            return False
        self._last_check = t
        scores = compute_density_scores(
            list(self._recent),
            window_s=WINDOW_S,
            peak_multiplier=self.multiplier,
            author_cap=self.author_cap,
            surge_gain=self.surge_gain,
        )
        # Bara fönster som innehåller det nya meddelandet: en gammal topp har redan räknats.
        return any(center - WINDOW_S / 2 > t - WINDOW_S for center, _ in scores)


def vod_seconds(sent_ms: int, vod_created_at: str) -> float:
    """Var i VOD:en ett meddelande ligger. sent_ms är chattens tid (tmi-sent-ts), created_at VOD:ens start."""
    started = dt.datetime.fromisoformat(vod_created_at.replace("Z", "+00:00"))
    return sent_ms / 1000 - started.timestamp()


def part_window(peak_s: float) -> tuple[float, float]:
    """Delen av VOD:en som hämtas för en topp: från 90 s före till 30 s efter."""
    return max(0.0, peak_s - PRE_S), peak_s + POST_S


def part_id(vod_id: str, n: int) -> str:
    """Delens id och mapp i work/. VOD:ens egen mapp rörs inte."""
    return f"{vod_id}_live_{n}"


def chat_for_part(
    messages: list[ChatMessage], start_s: float, end_s: float, video_id: str
) -> ChatLog:
    """Chatten under delen, med tider från delens början. Sparas som chat.json, så att toppen räknas."""
    return ChatLog(
        video_id=video_id,
        messages=[
            ChatMessage(
                time_in_seconds=m.time_in_seconds - start_s,
                message=m.message,
                author=m.author,
                is_clip_command=m.is_clip_command,
                hype_emote_count=m.hype_emote_count,
            )
            for m in messages
            if start_s <= m.time_in_seconds < end_s
        ],
    )


IRC_HOST, IRC_PORT = "irc.chat.twitch.tv", 6667
READ_TIMEOUT_S = 360.0  # Twitch skickar PING var femte minut


def parse_irc(line: str) -> tuple[str, str, int] | None:
    """(avsändare, text, tid i ms) för ett chattmeddelande från Twitchs IRC, annars None."""
    tags: dict[str, str] = {}
    if line.startswith("@"):
        raw, _, line = line.partition(" ")
        tags = dict(part.partition("=")[::2] for part in raw[1:].split(";"))
    prefix, _, rest = line.partition(" ")
    command, _, rest = rest.partition(" ")
    if command != "PRIVMSG" or not prefix.startswith(":"):
        return None
    _, _, text = rest.partition(" :")
    author = prefix[1:].partition("!")[0]
    sent = tags.get("tmi-sent-ts", "")
    return (
        author,
        text,
        int(sent) if sent.isdigit() else int(dt.datetime.now(dt.UTC).timestamp() * 1000),
    )


def read_chat(channel: str, timeout_s: float = READ_TIMEOUT_S) -> Iterator[tuple[str, str, int]]:
    """Läser kanalens chatt live, utan inloggning (justinfan är Twitchs anonyma läsare).
    Ger (avsändare, text, tid i ms) tills anslutningen stängs, eller tills Twitch ber om en ny (RECONNECT).
    Tidsgränsen är längre än Twitchs PING var femte minut, så en tyst kanal bryts inte."""
    with socket.create_connection((IRC_HOST, IRC_PORT), timeout=timeout_s) as conn:
        conn.sendall(
            b"CAP REQ :twitch.tv/tags\r\nNICK justinfan31415\r\n"
            + f"JOIN #{channel.lower()}\r\n".encode()
        )
        for line in _lines(conn):
            if line.startswith("PING"):
                conn.sendall(b"PONG :tmi.twitch.tv\r\n")
            elif line.split(" ")[1:2] == ["RECONNECT"]:  # Twitch startar om servern
                return
            elif (msg := parse_irc(line)) is not None:
                yield msg


def _lines(conn: socket.socket) -> Iterator[str]:
    """Raderna från Twitch tills anslutningen stängs."""
    buffer = b""
    while data := conn.recv(4096):
        buffer += data
        *lines, buffer = buffer.split(b"\r\n")
        yield from (raw.decode("utf-8", "replace") for raw in lines)


# ---------- läget: vilka kampanjer som bevakas, och topparna i den senaste streamen ----------

STATUSES = ("vantar", "hamtar", "ko", "kor", "klar", "fel", "hoppad")
FINAL = {"klar", "fel", "hoppad"}
RESTARTED = "Servern startades om innan klippet blev klart."
_file_lock = threading.Lock()


class LiveState:
    """Läget i klippdata/live.json, så att det överlever en omstart."""

    def __init__(self, settings: Settings) -> None:
        self.path = settings.data_dir / "live.json"

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("watching", [])
        data.setdefault("channels", {})
        return data

    @contextmanager
    def _change(self) -> Iterator[dict]:
        with _file_lock:
            data = self._read()
            yield data
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    @staticmethod
    def _channel(data: dict, profile: str) -> dict:
        return data["channels"].setdefault(
            profile, {"live": False, "vod_id": None, "error": None, "peaks": []}
        )

    def watching(self) -> list[str]:
        return sorted(self._read()["watching"])

    def channel(self, profile: str) -> dict:
        data = self._read()
        return {**self._channel(data, profile), "watching": profile in data["watching"]}

    def set_watching(self, profile: str, on: bool) -> None:
        with self._change() as data:
            names = set(data["watching"]) - {profile}
            data["watching"] = sorted(names | {profile} if on else names)

    def set_channel(self, profile: str, **fields: Any) -> None:
        """live, vod_id och error. En ny VOD ger en ny lista med toppar."""
        with self._change() as data:
            channel = self._channel(data, profile)
            if "vod_id" in fields and fields["vod_id"] != channel["vod_id"]:
                channel["peaks"] = []
            channel.update(fields)

    def add_peak(self, profile: str, vod_id: str, vod_s: float, at: str) -> int:
        """En ny topp med status vantar. Returnerar toppens nummer i VOD:en (1, 2, 3 ...)."""
        with self._change() as data:
            channel = self._channel(data, profile)
            if channel["vod_id"] != vod_id:
                channel["vod_id"], channel["peaks"] = vod_id, []
            n = len(channel["peaks"]) + 1
            channel["peaks"].append(
                {
                    "n": n,
                    "at": at,
                    "vod_s": round(vod_s, 1),
                    "status": "vantar",
                    "run_id": None,
                    "reason": None,
                }
            )
            return n

    def update_peak(self, profile: str, vod_id: str, n: int, **fields: Any) -> None:
        """Ändra en topp. En topp från en äldre VOD ändrar inte den nya listan."""
        with self._change() as data:
            channel = self._channel(data, profile)
            for peak in channel["peaks"] if channel["vod_id"] == vod_id else []:
                if peak["n"] == n:
                    peak.update(fields)

    def after_restart(self) -> None:
        """Trådarna dog med servern: ingen kanal läses, och toppar som inte blev klara är fel."""
        with self._change() as data:
            for channel in data["channels"].values():
                channel["live"] = False
                for peak in channel["peaks"]:
                    if peak["status"] not in FINAL:
                        peak.update(status="fel", reason=RESTARTED)


# ---------- bevakningen ----------

CHECK_S = 60.0  # så ofta Twitch frågas om kanalen är live
DELAY_S = 60.0  # delen hämtas så här långt efter toppen: delen slutar 30 s efter, plus marginal
RETRY_S = 15.0  # PartNotReady: försök igen efter så här lång tid
NOT_READY_S = 180.0  # och ge upp efter tre minuter
QUEUE_S = 10.0  # en annan körning pågår: försök igen efter så här lång tid
MAX_WAIT_S = 60.0  # längsta väntan innan chatten ansluts igen
NO_BUDGET = "Budgeten är slut."
NOT_READY = "Delen fanns inte hos Twitch efter tre minuter."
NOT_WATCHABLE = "Kampanjen är avstängd, inte redo eller saknar kanal."

LATEST_QUERY = (
    '{{ user(login: "{login}") {{ videos(first: 1, type: ARCHIVE, sort: TIME) {{ edges {{ node {{ '
    "id createdAt status title game {{ name }} }} }} }} }} }}"
)


def latest_vod(channel: str) -> dict | None:
    """Kanalens senaste VOD, utan cache. status RECORDING betyder att streamen pågår."""
    login = channel.lower()
    if not LOGIN.fullmatch(login):
        return None
    user = twitch.gql_query(LATEST_QUERY.format(login=login))["data"]["user"] or {}
    edges = (user.get("videos") or {}).get("edges") or []
    return edges[0]["node"] if edges else None


def _started(created_at: str) -> float:
    return dt.datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()


def _spawn(target: Callable[..., None], *args: Any) -> None:
    threading.Thread(target=target, args=args, name="livechatt", daemon=True).start()


@dataclass
class Job:
    """En topp som ska bli en körning. messages är chatten från 90 s före toppen till delens slut."""

    profile: str
    channel: str
    vod: dict  # id createdAt title game
    n: int
    peak_s: float
    messages: list[ChatMessage] = field(default_factory=list)

    @property
    def part_id(self) -> str:
        return part_id(str(self.vod["id"]), self.n)

    @property
    def start_s(self) -> float:
        return part_window(self.peak_s)[0]

    @property
    def end_s(self) -> float:
        return part_window(self.peak_s)[1]

    @property
    def game(self) -> str | None:
        return (self.vod.get("game") or {}).get("name")


@dataclass
class Stream:
    """En stream vars chatt läses: toppsökningen, chatten de senaste tio minuterna, och topparna vars del
    inte är slut än. Bara chattens tråd ändrar den."""

    profile: str
    channel: str
    vod: dict
    finder: PeakFinder = field(default_factory=PeakFinder)
    recent: deque[ChatMessage] = field(default_factory=deque)
    open: list[Job] = field(default_factory=list)

    @property
    def vod_id(self) -> str:
        return str(self.vod["id"])

    def add(self, msg: ChatMessage) -> float | None:
        """Lägg till ett meddelande. Returnerar toppens tid när en ny topp börjar, annars None."""
        t = msg.time_in_seconds
        self.recent.append(msg)
        while self.recent[0].time_in_seconds < t - BASELINE_S:
            self.recent.popleft()
        self.open = [job for job in self.open if t < job.end_s]
        for job in self.open:
            job.messages.append(msg)
        return self.finder.add(msg)

    def job(self, n: int, peak_s: float) -> Job:
        """Toppen n som ett jobb, med chatten från delens början. Chatten som kommer sedan läggs till."""
        start_s = part_window(peak_s)[0]
        job = Job(
            self.profile,
            self.channel,
            self.vod,
            n,
            peak_s,
            [m for m in self.recent if m.time_in_seconds >= start_s],
        )
        self.open.append(job)
        return job


def _can_watch(described: dict) -> bool:
    """Kampanjen är på, redo och har en Twitch-kanal."""
    return bool(described["active"] and described["ready"] and described["channel"])


class Watcher:
    """Frågar Twitch var 60:e sekund, läser chatten medan kanalen är live, och gör en körning per topp."""

    def __init__(self, settings: Settings, app: Any) -> None:
        self.settings, self.app = settings, app
        # Det som går mot nätverket eller tid. Testerna byter dem.
        self.latest: Callable[[str], dict | None] = latest_vod
        self.chat: Callable[..., Iterator[tuple[str, str, int]]] = read_chat
        self.run: Callable[..., None] = run_campaign_vod
        self.notify: Callable[..., None] = notify_mac
        self.now: Callable[[], float] = time.time
        self.sleep: Callable[[float], None] = time.sleep
        self.spawn: Callable[..., None] = _spawn
        self.part: Any = None  # campaign.live_part, som importeras när den behövs
        self.reading: dict[str, str] = {}  # kampanj -> VOD vars chatt läses
        # Utan tak. Topparna körs i tur och ordning.
        self.jobs: queue.Queue[Job] = queue.Queue()

    # ----- Twitch och chatten -----

    def check(self) -> None:
        """Ett varv: för varje bevakad kampanj, fråga om kanalen är live och läs chatten om den är det."""
        state = LiveState(self.settings)
        watched = state.watching()
        for profile in set(self.reading) - set(watched):
            self.reading.pop(profile, None)
        for profile in watched:
            try:
                self._check_one(state, profile)
            except (SystemExit, Exception) as error:  # en kampanj stoppar inte de andra
                log.warning("Livebevakningen av %s", profile, exc_info=True)
                state.set_channel(
                    profile, error=f"Kanalen gick inte att fråga ({type(error).__name__})."
                )

    def _check_one(self, state: LiveState, profile: str) -> None:
        described = _describe_profile(profile, self.settings)
        channel = described["channel"]
        if not _can_watch(described):
            self.reading.pop(profile, None)
            state.set_channel(profile, live=False, error=NOT_WATCHABLE)
            return
        vod = self.latest(channel)
        live = vod is not None and vod.get("status") == "RECORDING"
        vod_id = str(vod["id"]) if vod else None
        state.set_channel(profile, live=live, vod_id=vod_id, error=None)
        if not live:
            self.reading.pop(profile, None)
        elif self.reading.get(profile) != vod_id:
            self.reading[profile] = vod_id
            self.spawn(self._read, Stream(profile, channel, vod))

    def _read(self, stream: Stream) -> None:
        """Läser chatten tills kanalen inte är live längre. Bryts anslutningen ansluts den igen, efter
        1 s som dubblas upp till 60 s. Väntan börjar om på 1 s när meddelanden kommer."""
        wait = 1.0
        try:
            while self._still_reading(stream):
                wait = 1.0 if self._read_once(stream) else wait
                self.sleep(wait)
                wait = min(wait * 2, MAX_WAIT_S)
        except Exception:
            log.exception("Chatten för %s", stream.channel)
        finally:
            if self._still_reading(stream):
                self.reading.pop(stream.profile, None)

    def _still_reading(self, stream: Stream) -> bool:
        return self.reading.get(stream.profile) == stream.vod_id

    def _read_once(self, stream: Stream) -> bool:
        """En anslutning. Svarar om några meddelanden kom."""
        got = False
        try:
            for author, text, sent_ms in self.chat(stream.channel, READ_TIMEOUT_S):
                got = True
                self._feed(
                    stream, message(vod_seconds(sent_ms, stream.vod["createdAt"]), text, author)
                )
                if not self._still_reading(stream):
                    break
        except OSError as error:  # nätverket, eller tyst längre än tidsgränsen
            log.info("Chatten för %s bröts (%s)", stream.channel, type(error).__name__)
        return got

    def _feed(self, stream: Stream, msg: ChatMessage) -> None:
        peak = stream.add(msg)
        if peak is None:
            return
        at = dt.datetime.fromtimestamp(_started(stream.vod["createdAt"]) + peak, dt.UTC)
        n = LiveState(self.settings).add_peak(
            stream.profile, stream.vod_id, peak, at.isoformat(timespec="seconds")
        )
        self.jobs.put(stream.job(n, peak))

    # ----- från topp till körning -----

    def handle(self, job: Job) -> None:
        """Vänta tills delen är inspelad, hämta den och kör den. Ett fel gäller bara den här toppen."""
        try:
            self._handle(job)
        except Exception as error:
            log.exception("Liveklipp %s", job.part_id)
            self._mark(job, "fel", reason=str(error) or type(error).__name__)

    def _mark(self, job: Job, status: str, **fields: Any) -> None:
        LiveState(self.settings).update_peak(
            job.profile, str(job.vod["id"]), job.n, status=status, **fields
        )

    def _handle(self, job: Job) -> None:
        self.sleep(max(0.0, _started(job.vod["createdAt"]) + job.peak_s + DELAY_S - self.now()))
        try:
            budget_from_settings(self.settings).check()
        except BudgetExceeded:
            self._mark(job, "hoppad", reason=NO_BUDGET)
            return
        profile = _profile(job.profile, self.settings)
        folder = clipsmith_config(profile, self.settings).work_dir.expanduser() / job.part_id
        real_start = self._fetch(job, folder)
        if real_start is None:
            return
        # Före körningen: annars frågar clipsmith Twitch efter chatten med delens id.
        chat = chat_for_part(job.messages, real_start, job.end_s, job.part_id)
        (folder / "chat.json").write_text(chat.to_json(), encoding="utf-8")
        run_id = self._start_run(job, profile.streamer, round(job.end_s - real_start))
        self._run(job, run_id, profile.streamer)

    def _fetch(self, job: Job, folder: Any) -> float | None:
        """Delens verkliga start i VOD:en, eller None när den inte gick att hämta (toppen är då fel)."""
        part = self.part or importlib.import_module("campaign.live_part")
        self._mark(job, "hamtar")
        deadline = self.now() + NOT_READY_S
        while True:
            try:
                return part.fetch_part(str(job.vod["id"]), job.start_s, job.end_s, folder)
            except part.PartNotReady:
                if self.now() >= deadline:
                    self._mark(job, "fel", reason=NOT_READY)
                    return None
                self.sleep(RETRY_S)
            except part.PartFailed as error:
                self._mark(job, "fel", reason=str(error))
                return None

    def _busy(self) -> bool:
        return (
            self.app.state.active_run_id is not None or editor_lock.locked() or chat_lock.locked()
        )

    def _start_run(self, job: Job, streamer: str, duration_s: int) -> int:
        """Samma lås som vanliga körningar. Pågår något annat väntar toppen i kö."""
        db = get_session()
        try:
            while True:
                with _start_lock:
                    if not self._busy():
                        run = Run(vod_id=job.part_id, channel=job.channel, status=RunStatus.pending)
                        db.add(run)
                        db.commit()
                        db.refresh(run)
                        self.app.state.active_run_id = run.id
                        break
                self._mark(job, "ko")
                self.sleep(QUEUE_S)
            try:
                title = f"{job.vod.get('title') or job.vod['id']} (live, topp {job.n})"
                _log(self.settings).add_run(
                    run.id,
                    job.part_id,
                    job.profile,
                    job.game,
                    focus=CHAT_LAG_FOCUS,
                    title=title,
                    channel_name=streamer,
                    thumbnail_url=None,
                    duration_s=duration_s,
                )
            except Exception:  # annars står körningen som pågående tills servern startas om
                run.status, run.error = (
                    RunStatus.failed,
                    "Projektet kunde inte sparas i klipploggen.",
                )
                db.commit()
                self.app.state.active_run_id = None
                raise
            return run.id
        finally:
            db.close()

    def _run(self, job: Job, run_id: int, streamer: str) -> None:
        self._mark(job, "kor", run_id=run_id)
        # run_campaign_vod släpper active_run_id själv när den är klar.
        self.run(
            run_id,
            job.part_id,
            job.profile,
            job.game,
            self.settings,
            focus=CHAT_LAG_FOCUS,
            local=True,
            app=self.app,
        )
        db = get_session()
        try:
            run = db.get(Run, run_id)
            done, error = (
                run is not None and run.status == RunStatus.done,
                run.error if run else None,
            )
        finally:
            db.close()
        self._mark(job, "klar" if done else "fel", run_id=run_id, reason=None if done else error)
        if done:
            url = f"{self.settings.web_url}/queue?tab=granska"
            for clip in _log(self.settings).list("renderad"):
                if clip["vod_id"] == job.part_id:
                    self.notify(f"Nytt liveklipp från {streamer}: {clip['title']}", url)

    # ----- trådarna -----

    def loop(self) -> None:
        while True:
            try:
                self.check()
            except Exception:
                log.exception("Livebevakningen")
            self.sleep(CHECK_S)

    def work(self) -> None:
        while True:
            self.handle(self.jobs.get())


_thread: list[threading.Thread] = []


def _off() -> bool:
    return (
        os.environ.get("KLIPP_LIVE") == "0" or "PYTEST_CURRENT_TEST" in os.environ or bool(_thread)
    )


def start(app: Any, settings: Settings | None = None) -> threading.Thread | None:
    """Starta bevakningen med servern. KLIPP_LIVE=0 stänger av den. Startar aldrig i tester."""
    if _off():
        return None
    settings = settings or load_settings()
    LiveState(settings).after_restart()
    watcher = Watcher(settings, app)
    for target, name in ((watcher.loop, "livebevakning"), (watcher.work, "liveklipp")):
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        _thread.append(thread)
    return _thread[0]
