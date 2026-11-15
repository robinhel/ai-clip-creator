"""Schemalagda uppladdningar: Robin väljer en tid för ett godkänt klipp och appen laddar upp det då.

YouTube och TikTok med direktpostning laddas upp med samma kod som knapparna. TikTok utan direktpostning blir en
påminnelse. Efter uppladdningen påminns Robin om att skicka in länken i Content Rewards inom 30 minuter.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from . import editor
from .cliplog import POST_STATUSES, ClipLog
from .config import Settings, load_settings
from .media import probe
from .preferences import submit_helper
from .profile import PLATFORMS, load_profile
from .scout import STOCKHOLM, notify_mac
from .tiktok import TITLE_MAX as TIKTOK_TITLE_MAX
from .tiktok import TikTok, TikTokError
from .youtube import BUSY, YouTube, YouTubeError

log = logging.getLogger(__name__)

STEP_S = 30  # tråden sover högst så länge, så att en sovande Mac märks direkt när den vaknar
LATE_S = 120  # senare än så efter tiden räknas som försenad
MISSED_S = 6 * 3600
YOUTUBE_DAILY = 100
MISSED = "Missad: Macen var avstängd vid tiden."
ALREADY = "Klippet finns redan på kanalen."
TIKTOK_LIVE = ("uploading", "uploaded", "processing", "published")
FINISHED = ("done", "reminded", "failed", "cancelled")
_thread: list[threading.Thread] = []


class ScheduleError(Exception):
    """Fel med svensk text och HTTP-status för webben."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


NAMES = {"youtube": ("youtube_shorts", "YouTube"), "tiktok": ("tiktok", "TikTok")}


def clip_file(settings: Settings, clip: dict, platform: str,
              refused: str = "Bara godkända klipp kan schemaläggas.") -> Path:
    """Klippets fil. Bara ett godkänt (eller redan postat) klipp utan problem laddas upp, av knapparna och av
    schemat, och bara till en plattform som kampanjen postar på och där klippet inte redan har ett inlägg."""
    if clip["status"] not in ("godkand", *POST_STATUSES):  # postad: inlägget kan ha skapats av hjälpen
        raise ScheduleError(refused)
    if clip["problems"]:
        raise ScheduleError("Klippet har problem. Rätta dem innan du laddar upp.")
    key, name = NAMES[platform]
    try:
        platforms = load_profile(clip["profile"], settings.profiles_dir).platforms
    except SystemExit:  # kampanjen finns inte längre: som före plattformslistan
        platforms = list(PLATFORMS)
    if key not in platforms:
        raise ScheduleError(f"Kampanjen postar inte på {name}.")
    if any(post["platform"] == key for post in ClipLog(settings.data_dir / "klipp.db").posts(clip["id"])):
        raise ScheduleError(f"Klippet har redan ett inlägg på {name}.")
    path = Path(clip["file"] or "").resolve()
    if not path.is_relative_to(settings.out_dir.resolve()) or path.suffix != ".mp4" or not path.exists():
        raise ScheduleError("Klippets fil finns inte. Gör klippet kampanjklart igen.")
    return path


def clip_seconds(clip: dict, path: Path) -> float:
    has_times = clip["start_s"] is not None and clip["end_s"] is not None
    return clip["end_s"] - clip["start_s"] if has_times else probe(path)["duration"]


class Schedule:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.clips = ClipLog(settings.data_dir / "klipp.db")  # skapar klipptabellen som listan läser
        self.db_path = self.clips.db_path
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id INTEGER, platform TEXT, account TEXT, at TEXT,
                fields TEXT, status TEXT, error TEXT, result TEXT, created TEXT, updated TEXT)""")
            # Högst en väntande post per klipp och plattform, också om två anrop kommer samtidigt.
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS scheduled_waiting ON scheduled_posts (clip_id, platform) "
                       "WHERE status = 'waiting'")

    @contextlib.contextmanager
    def _db(self):
        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    def _set(self, post_id: int, **fields) -> None:
        fields["updated"] = _iso(_now())
        with self._db() as db:
            db.execute(f"UPDATE scheduled_posts SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                       (*fields.values(), post_id))

    def _view(self, row: sqlite3.Row, now: datetime) -> dict:
        post = dict(row)
        post["fields"] = json.loads(post["fields"] or "{}")
        post["result"] = json.loads(post["result"]) if post["result"] else None
        post["late"] = bool((post["result"] or {}).get("late")) or (
            post["status"] == "waiting" and (now - datetime.fromisoformat(post["at"])).total_seconds() > LATE_S)
        for key in ("created", "updated"):
            post.pop(key)
        return post

    def get(self, post_id: int) -> dict:
        with self._db() as db:
            row = db.execute("SELECT s.*, c.title AS clip_title, c.profile FROM scheduled_posts s "
                             "LEFT JOIN clips c ON c.id = s.clip_id WHERE s.id = ?", (post_id,)).fetchone()
        if row is None:
            raise ScheduleError("Den schemalagda posten finns inte.", 404)
        return self._view(row, _now())

    def list(self) -> list[dict]:
        """Väntande (och pågående) först, närmast tid först, sedan de 20 senast avslutade."""
        select = "SELECT s.*, c.title AS clip_title, c.profile FROM scheduled_posts s LEFT JOIN clips c ON c.id = s.clip_id"
        with self._db() as db:
            coming = db.execute(f"{select} WHERE s.status IN ('waiting', 'running') ORDER BY s.at").fetchall()
            finished = db.execute(f"{select} WHERE s.status IN ({', '.join('?' * len(FINISHED))}) "
                                  "ORDER BY s.updated DESC, s.id DESC LIMIT 20", FINISHED).fetchall()
        now = _now()
        return [self._view(row, now) for row in (*coming, *finished)]

    # ---------- schemalägg och avbryt ----------

    def add(self, clip_id: int, platform: str, at: datetime, account: str | None, fields: dict) -> dict:
        if at.tzinfo is None:
            raise ScheduleError("Tiden måste ha en tidszon.")
        if at <= _now():
            raise ScheduleError("Tiden har redan passerat. Välj en tid framåt.")
        try:
            clip = self.clips.get(clip_id)
        except SystemExit as error:
            raise ScheduleError("Klippet finns inte.", 404) from error
        fields = self._check(platform, account, fields, clip_seconds(clip, clip_file(self.settings, clip, platform)))
        self._not_there_yet(clip_id, platform, account)
        now = _iso(_now())
        try:
            with self._db() as db:
                post_id = db.execute(
                    "INSERT INTO scheduled_posts (clip_id, platform, account, at, fields, status, created, updated) "
                    "VALUES (?, ?, ?, ?, ?, 'waiting', ?, ?)",
                    (clip_id, platform, account, _iso(at), json.dumps(fields, ensure_ascii=False), now, now)).lastrowid
        except sqlite3.IntegrityError as error:
            name = "YouTube" if platform == "youtube" else "TikTok"
            raise ScheduleError(f"Klippet är redan schemalagt för {name}. Avbryt den posten först.", 409) from error
        return self.get(int(post_id))

    def _check(self, platform: str, account: str | None, fields: dict, duration_s: float) -> dict:
        """Fälten som sparas, kontrollerade som vid en direkt uppladdning."""
        from .tiktok_api import UploadBody
        from .youtube_api import YouTubeBody

        try:
            if platform == "youtube":
                video = YouTubeBody(**fields).model_dump(exclude={"channel_id"})
                YouTube._check(video, duration_s)
                return video
            post = UploadBody(**fields).model_dump(exclude={"open_id", "mode"})
        except ValidationError as error:
            raise ScheduleError("Fälten är ogiltiga. Kontrollera rubrik och val.") from error
        except YouTubeError as error:
            raise ScheduleError(str(error)) from error
        if len((post.get("title") or "").encode("utf-16-le")) // 2 > TIKTOK_TITLE_MAX:
            raise ScheduleError(f"Beskrivningen är för lång. TikTok tar högst {TIKTOK_TITLE_MAX} tecken.")
        if account and TikTok(self.settings).can_publish(account) and not post.get("privacy_level"):
            raise ScheduleError("Välj vem som får se klippet.")
        return post

    def _not_there_yet(self, clip_id: int, platform: str, account: str | None) -> None:
        """Knapparnas spärr gäller bara 10 minuter. Ett klipp som redan finns på kanalen laddas inte upp igen."""
        if platform == "youtube":
            last = YouTube(self.settings)._latest_row(clip_id, account)
            there = last is not None and last["status"] != "failed"
        else:
            last = TikTok(self.settings)._latest_row(clip_id, account, "direct") if account else None
            there = last is not None and last["status"] in TIKTOK_LIVE
        if there:
            raise ScheduleError(ALREADY, 409)

    def cancel(self, post_id: int) -> dict:
        post = self.get(post_id)
        with self._db() as db:
            changed = db.execute("UPDATE scheduled_posts SET status = 'cancelled', updated = ? "
                                 "WHERE id = ? AND status = 'waiting'", (_iso(_now()), post_id)).rowcount
        if not changed:
            raise ScheduleError(f"Bara väntande poster kan avbrytas. Den här är {post['status']}.", 409)
        return self.get(post_id)

    def fail_interrupted(self) -> None:
        """Servern dog mitt i en uppladdning. Posten körs inte igen, så att inget laddas upp två gånger."""
        with self._db() as db:
            db.execute("UPDATE scheduled_posts SET status = 'failed', error = ?, updated = ? WHERE status = 'running'",
                       ("Servern startades om mitt i uppladdningen. Titta på kontot innan du försöker igen.",
                        _iso(_now())))

    # ---------- vid tiden ----------

    def run_due(self, now: datetime | None = None) -> None:
        now = now or _now()
        with self._db() as db:
            due = db.execute("SELECT id FROM scheduled_posts WHERE status = 'waiting' AND at <= ? ORDER BY at",
                             (_iso(now),)).fetchall()
        for row in due:
            if editor.lock.locked():  # filen kan bytas mitt i uppladdningen: nästa varv
                return
            with self._db() as db:  # running före uppladdningen: ett annat varv tar aldrig samma post
                if db.execute("UPDATE scheduled_posts SET status = 'running', updated = ? "
                              "WHERE id = ? AND status = 'waiting'", (_iso(now), row["id"])).rowcount != 1:
                    continue
            self._run(self.get(row["id"]), now)

    def _run(self, post: dict, now: datetime) -> None:
        late_s = (now - datetime.fromisoformat(post["at"])).total_seconds()
        title = post["clip_title"] or f"Klipp {post['clip_id']}"
        queue = f"{self.settings.web_url}/queue"
        late = late_s > LATE_S
        try:
            if late_s > MISSED_S:
                raise ScheduleError(MISSED)
            status, result = self._upload(post)
        except (ScheduleError, YouTubeError, TikTokError) as error:
            if str(error) == BUSY:  # en annan uppladdning pågår: försök nästa varv
                self._set(post["id"], status="waiting")
                return
            self._set(post["id"], status="failed", error=str(error))
            notify_mac(f"{title} laddades inte upp: {error}", queue)
            return
        except Exception:  # tråden får aldrig dö; posten körs inte igen
            log.exception("Schemalagd post %s", post["id"])
            error_text = "Uppladdningen fick ett oväntat fel. Titta på kontot."
            self._set(post["id"], status="failed", error=error_text)
            notify_mac(f"{title} laddades inte upp: {error_text}", queue)
            return
        self._set(post["id"], status=status, error=None, result=json.dumps({**result, "late": late}))
        if status == "reminded":
            message, url = f"Dags att posta {title} på TikTok", None
        else:
            done = "är uppe på YouTube" if post["platform"] == "youtube" else "skickades till TikTok"
            if submit_helper(self.settings):  # webben har länken och tiden; notisen öppnar kön
                before = (_now() + timedelta(minutes=30)).astimezone(STOCKHOLM)
                message, url = f"{title} {done}. Skicka in länken i Content Rewards före kl. {before:%H:%M}.", None
            else:
                message = f"{title} {done}. Skicka in länken i Content Rewards inom 30 minuter."
                url = self._content_rewards(post["profile"])
        notify_mac(f"Försenad: {message}" if late else message, url or queue)

    def _upload(self, post: dict) -> tuple[str, dict]:
        try:
            clip = self.clips.get(post["clip_id"])
        except SystemExit as error:
            raise ScheduleError("Klippet finns inte längre.") from error
        path = clip_file(self.settings, clip, post["platform"], "Klippet är inte längre godkänt.")
        duration_s = clip_seconds(clip, path)
        self._not_there_yet(post["clip_id"], post["platform"], post["account"])
        if post["platform"] == "youtube":
            youtube = YouTube(self.settings)
            if youtube.uploads_today() >= YOUTUBE_DAILY:
                raise ScheduleError(f"Dagens gräns på {YOUTUBE_DAILY} uppladdningar till YouTube är nådd.")
            return "done", youtube.upload(post["clip_id"], path, post["account"], post["fields"], duration_s)
        tiktok = TikTok(self.settings)
        # Påminnelse om inte Robin valde konto och vem som får se det, och kontot får publicera.
        if not (post["account"] and post["fields"].get("privacy_level") and tiktok.can_publish(post["account"])):
            return "reminded", {}
        return "done", tiktok.upload(post["clip_id"], path, post["account"], post["fields"], duration_s)

    def _content_rewards(self, profile: str | None) -> str | None:
        try:
            links = load_profile(profile or "", self.settings.profiles_dir).raw.get("links") or {}
        except (SystemExit, Exception):  # kampanjen finns inte längre: notisen öppnar kön
            log.warning("Kampanjen %s går inte att läsa", profile, exc_info=True)
            return None
        url = links.get("content_rewards") if isinstance(links, dict) else None
        return url if isinstance(url, str) and url.startswith("https://") else None


# ---------- tråden ----------


def _loop(settings: Settings) -> None:
    while True:
        try:
            # Väggklockan läses varje varv. Den monotona klockan står still när Macen sover.
            Schedule(settings).run_due()
        except Exception:
            log.exception("Schemat")
        time.sleep(STEP_S)


def start(settings: Settings | None = None) -> threading.Thread | None:
    """Starta schemat med servern. KLIPP_SCHEDULE=0 stänger av det. Startar aldrig i tester."""
    if os.environ.get("KLIPP_SCHEDULE") == "0" or "PYTEST_CURRENT_TEST" in os.environ or _thread:
        return None
    settings = settings or load_settings()
    Schedule(settings).fail_interrupted()
    thread = threading.Thread(target=_loop, args=(settings,), name="schema", daemon=True)
    thread.start()
    _thread.append(thread)
    return thread

