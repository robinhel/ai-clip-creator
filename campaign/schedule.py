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
from .profile import PLATFORMS, Profile, load_profile
from .rules import NO_CAMPAIGN, NO_CAMPAIGN_PROBLEM
from .scout import STOCKHOLM, notify_mac
from .tiktok import TITLE_MAX as TIKTOK_TITLE_MAX
from .tiktok import TikTok, TikTokError, check_cover
from .youtube import BUSY, YouTube, YouTubeError

log = logging.getLogger(__name__)

STEP_S = 30  # tråden sover högst så länge, så att en sovande Mac märks direkt när den vaknar
LATE_S = 120  # senare än så efter tiden räknas som försenad
MISSED_S = 6 * 3600
YOUTUBE_DAILY = 100
MISSED = "Missed: the Mac was off at the time."
ALREADY = "The clip is already on the channel."
ONE_PER_ACCOUNT = "The clip already has a post on this account."
MAX_ACCOUNTS = 5  # Content Rewards villkor 14.2
SAME_TEXT = ("The same text is already on another account for this clip. Write a new text or press Suggest "
             "new text.")
TIKTOK_LIVE = ("uploading", "uploaded", "processing", "published")
FINISHED = ("done", "reminded", "failed", "cancelled")
FINISHED_SHOWN_H = 24  # Att posta visar avslutade poster så här länge
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
              refused: str = "Only approved clips can be scheduled.") -> Path:
    """Klippets fil. Bara ett godkänt (eller redan postat) klipp utan problem laddas upp, av knapparna och av
    schemat, och bara till en plattform som kampanjen postar på och där klippet inte redan har ett inlägg."""
    if clip["status"] not in ("godkand", *POST_STATUSES):  # postad: inlägget kan ha skapats av hjälpen
        raise ScheduleError(refused)
    _require_campaign(clip)
    if clip["problems"]:
        raise ScheduleError("The clip has problems. Fix them before you upload.")
    key, name = NAMES[platform]
    profile = _profile(settings, clip["profile"])
    if key not in (profile.platforms if profile else PLATFORMS):  # kampanjen finns inte: som före listan
        raise ScheduleError(f"The campaign does not post on {name}.")
    multi = profile is not None and profile.multi(key)  # flera konton: reglerna per konto i check_variant
    if not multi and any(post["platform"] == key
                         for post in ClipLog(settings.data_dir / "klipp.db").posts(clip["id"])):
        raise ScheduleError(f"The clip already has a post on {name}.")
    path = Path(clip["file"] or "").resolve()
    if not path.is_relative_to(settings.out_dir.resolve()) or path.suffix != ".mp4" or not path.exists():
        raise ScheduleError("The clip file does not exist. Make the clip campaign-ready again.")
    return path


def _require_campaign(clip: dict) -> None:
    """Extra spärr för klipp utan kampanj (_ingen), även om problemet skulle ha tagits bort ur loggen."""
    if clip["profile"] == NO_CAMPAIGN:
        raise ScheduleError(NO_CAMPAIGN_PROBLEM)


def _profile(settings: Settings, name: str | None) -> Profile | None:
    try:
        return load_profile(name or "", settings.profiles_dir)
    except SystemExit:  # kampanjen finns inte längre: standardgränserna gäller
        return None


def _day(moment: datetime) -> tuple[str, str]:
    """Det svenska dygnet som tiden ligger i, som UTC-tider: början och början på nästa dygn."""
    start = moment.astimezone(STOCKHOLM).replace(hour=0, minute=0, second=0, microsecond=0)
    return _iso(start), _iso(start + timedelta(days=1))


def _norm(text: str | None) -> str:
    """Texten utan skillnad i versaler, blanksteg och hashtaggar i början och slutet."""
    words = (text or "").lower().split()
    while words and words[0].startswith("#"):
        words.pop(0)
    while words and words[-1].startswith("#"):
        words.pop()
    return "".join(words)


def _texts(fields: dict) -> list[str]:
    """Rubriken och beskrivningen (YouTube), eller TikToks beskrivning (title)."""
    return [str(fields[key]) for key in ("title", "description") if fields.get(key)]


def _when(text: str) -> datetime:
    moment = datetime.fromisoformat(text)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def clip_seconds(clip: dict, path: Path) -> float:
    """Den färdiga filens längd: tidskartan kan ha tagit bort tystnad eller lagt hooken först, så loggens
    tider stämmer inte alltid. Loggens tider bara när filen inte går att läsa."""
    try:
        return probe(path)["duration"]
    except RuntimeError:
        if clip["start_s"] is None or clip["end_s"] is None:
            raise
        return clip["end_s"] - clip["start_s"]


class Schedule:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.clips = ClipLog(settings.data_dir / "klipp.db")  # skapar klipptabellen som listan läser
        self.db_path = self.clips.db_path
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id INTEGER, platform TEXT, account TEXT, at TEXT,
                fields TEXT, status TEXT, error TEXT, result TEXT, created TEXT, updated TEXT)""")
            # Högst en väntande post per klipp, plattform och konto, också om två anrop kommer samtidigt.
            db.execute("DROP INDEX IF EXISTS scheduled_waiting")  # före flera konton: per klipp och plattform
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS scheduled_waiting_account ON scheduled_posts "
                       "(clip_id, platform, COALESCE(account, '')) WHERE status = 'waiting'")

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
        post["warning"] = self._too_close(post) if post["status"] == "waiting" else None
        post["reminder"] = post["status"] == "reminded" or (
            post["status"] != "done" and post["platform"] == "tiktok" and self._reminds(post))
        for key in ("created", "updated"):
            post.pop(key)
        return post

    def _reminds(self, post: dict) -> bool:
        """TikTok blir en påminnelse om inte Robin valde konto och vem som får se det, och kontot får publicera.
        Samma villkor som vid tiden (_upload)."""
        account = post["account"]
        return not (account and post["fields"].get("privacy_level") and TikTok(self.settings).can_publish(account))

    def get(self, post_id: int) -> dict:
        with self._db() as db:
            row = db.execute("SELECT s.*, c.title AS clip_title, c.profile FROM scheduled_posts s "
                             "LEFT JOIN clips c ON c.id = s.clip_id WHERE s.id = ?", (post_id,)).fetchone()
        if row is None:
            raise ScheduleError("The scheduled post does not exist.", 404)
        return self._view(row, _now())

    def list(self) -> list[dict]:
        """Väntande (och pågående) först, närmast tid först, sedan högst 20 avslutade från de senaste 24 timmarna,
        senaste tid först: webben visar den planerade tiden. Äldre avslutade poster ligger kvar i databasen men
        visas inte."""
        select = "SELECT s.*, c.title AS clip_title, c.profile FROM scheduled_posts s LEFT JOIN clips c ON c.id = s.clip_id"
        now = _now()
        with self._db() as db:
            coming = db.execute(f"{select} WHERE s.status IN ('waiting', 'running') ORDER BY s.at").fetchall()
            finished = db.execute(f"{select} WHERE s.status IN ({', '.join('?' * len(FINISHED))}) AND s.updated >= ? "
                                  "ORDER BY s.at DESC, s.updated DESC, s.id DESC LIMIT 20",
                                  (*FINISHED, _iso(now - timedelta(hours=FINISHED_SHOWN_H)))).fetchall()
        return [self._view(row, now) for row in (*coming, *finished)]

    # ---------- schemalägg och avbryt ----------

    def add(self, clip_id: int, platform: str, at: datetime, account: str | None, fields: dict) -> dict:
        if at.tzinfo is None:
            raise ScheduleError("The time must have a time zone.")
        if at <= _now():
            raise ScheduleError("The time has already passed. Choose a later time.")
        try:
            clip = self.clips.get(clip_id)
        except SystemExit as error:
            raise ScheduleError("The clip does not exist.", 404) from error
        fields = self._check(platform, account, fields, clip_seconds(clip, clip_file(self.settings, clip, platform)))
        self.check_account(clip, platform, account, at)
        self._not_there_yet(clip_id, platform, account)
        self.check_variant(clip, platform, account, fields)
        name = "YouTube" if platform == "youtube" else "TikTok"
        busy = f"The clip is already scheduled for {name}. Cancel that post first."
        if not self._multi(clip, platform):  # ett konto: en väntande post per plattform, som förut
            with self._db() as db:
                if db.execute("SELECT 1 FROM scheduled_posts WHERE clip_id = ? AND platform = ? AND status = 'waiting'",
                              (clip_id, platform)).fetchone():
                    raise ScheduleError(busy, 409)
        now = _iso(_now())
        try:
            with self._db() as db:
                post_id = db.execute(
                    "INSERT INTO scheduled_posts (clip_id, platform, account, at, fields, status, created, updated) "
                    "VALUES (?, ?, ?, ?, ?, 'waiting', ?, ?)",
                    (clip_id, platform, account, _iso(at), json.dumps(fields, ensure_ascii=False), now, now)).lastrowid
        except sqlite3.IntegrityError as error:
            raise ScheduleError(busy, 409) from error
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
            raise ScheduleError("The fields are invalid. Check the title and choices.") from error
        except YouTubeError as error:
            raise ScheduleError(str(error)) from error
        if len((post.get("title") or "").encode("utf-16-le")) // 2 > TIKTOK_TITLE_MAX:
            raise ScheduleError(f"The description is too long. TikTok takes at most {TIKTOK_TITLE_MAX} characters.")
        try:
            check_cover(post, duration_s)
        except TikTokError as error:
            raise ScheduleError(str(error)) from error
        if account and TikTok(self.settings).can_publish(account) and not post.get("privacy_level"):
            raise ScheduleError("Choose who can see the clip.")
        return post

    # ---------- spärrarna för konton ----------

    def check_account(self, clip: dict, platform: str, account: str | None, at: datetime, counts: bool = True) -> None:
        """Kampanjens konton och högst antal inlägg per konto och svensk dag. counts=False: ett TikTok-utkast,
        som inte är ett inlägg än. Ett inlägg utan konto (en påminnelse) räknas mot andra utan konto."""
        profile = _profile(self.settings, clip["profile"])
        key, name = NAMES[platform]
        allowed = profile.accounts.get(key) if profile else None
        if account and allowed and not self._names(platform, account) & {a.lower() for a in allowed}:
            # Profilen kan ange kontot med sitt id. Robin läser namnet.
            names = ", ".join(self.label(platform, a) for a in allowed)
            raise ScheduleError(f"The campaign only posts from {names} on {name}. Choose that account.")
        limit = profile.max_posts_per_day if profile else 3
        if counts and len(self._post_times(platform, account, *_day(at))) >= limit:
            day = at.astimezone(STOCKHOLM)
            raise ScheduleError(f"The account already has {limit} posts on {name} on {day.day}/{day.month}. The campaign "
                                f"allows at most {limit} per account and day. Choose another day or another account.")

    def _multi(self, clip: dict, platform: str) -> bool:
        profile = _profile(self.settings, clip["profile"])
        return profile is not None and profile.multi(NAMES[platform][0])

    def _uses(self, clip_id: int, platform: str, skip: int | None = None) -> dict[str | None, dict]:
        """Kontona där klippet ligger eller är schemalagt på plattformen: konto -> state (posted eller
        scheduled), tid och texter. None är ett inlägg loggat för hand, eller en påminnelse. Originalet och
        dess versioner räknas som samma klipp."""
        uses: dict[str | None, dict] = {}
        family = self.clips.family(clip_id)
        same = f"clip_id IN ({', '.join('?' * len(family))})"

        def use(account: str | None, state: str, at: str | None, texts: list) -> None:
            found = uses.setdefault(account, {"state": state, "at": at, "texts": []})
            if state == "posted" and found["state"] != "posted":
                found.update(state=state, at=at)
            found["texts"] += [t for t in texts if t]

        with self._db() as db:
            rows = db.execute(f"SELECT account, at, fields FROM scheduled_posts WHERE {same} AND platform = ? "
                              "AND id IS NOT ? AND status IN ('waiting', 'running')",
                              (*family, platform, skip)).fetchall()
        for row in rows:
            use(row["account"], "scheduled", row["at"], _texts(json.loads(row["fields"] or "{}")))
        for post in (post for member in family for post in self.clips.posts(member)):
            if post["platform"] == NAMES[platform][0]:
                use(post["account"], "posted", post["created"], [])
        if platform == "youtube":
            with YouTube(self.settings)._db() as db:
                rows = db.execute(f"SELECT channel_id, created, title, description FROM youtube_uploads "
                                  f"WHERE {same} AND status != 'failed'", family).fetchall()
        else:
            with TikTok(self.settings)._db() as db:
                rows = db.execute(f"SELECT open_id, created, title FROM tiktok_uploads WHERE {same} AND "
                                  f"mode = 'direct' AND status IN ({', '.join('?' * len(TIKTOK_LIVE))})",
                                  (*family, *TIKTOK_LIVE)).fetchall()
        for row in rows:
            use(row[0], "posted", row[1], list(row[2:]))
        return uses

    def check_variant(self, clip: dict, platform: str, account: str | None, fields: dict,
                      skip: int | None = None) -> None:
        """Reglerna när kampanjen har flera konton på plattformen: ett inlägg per konto, högst fem konton per
        klipp, och en egen text på varje konto. Med ett eller inget konto gäller clip_file som förut."""
        if not self._multi(clip, platform):
            return
        name = NAMES[platform][1]
        uses = self._uses(clip["id"], platform, skip)
        mine = uses.pop(account, None)
        if mine:
            raise ScheduleError(ONE_PER_ACCOUNT if mine["state"] == "posted" else
                                f"The clip is already scheduled for {name} on that account. Cancel that post first.", 409)
        if len(uses) >= MAX_ACCOUNTS:
            raise ScheduleError(f"The clip is already on {MAX_ACCOUNTS} accounts on {name}. Content Rewards allows "
                                f"at most {MAX_ACCOUNTS}.")
        taken = {_norm(text) for other in uses.values() for text in other["texts"]} - {""}
        if any(_norm(text) in taken for text in _texts(fields)):
            raise ScheduleError(SAME_TEXT)

    def accounts_for(self, clip: dict, platform: str) -> dict:
        """Kampanjens konton på plattformen och var klippet ligger: posted, scheduled eller free. Ett konto som
        profilen nämner med användarnamn eller handle får sitt id om det är kopplat; bara kopplade har namn."""
        profile = _profile(self.settings, clip["profile"])
        key = NAMES[platform][0]
        connected = list((YouTube if platform == "youtube" else TikTok)(self.settings).accounts())
        uses = self._uses(clip["id"], platform)
        accounts = []
        for entry in profile.accounts.get(key, []) if profile else []:
            account = next((a for a in connected if entry.lstrip("@").lower() in self._names(platform, a)), None)
            use = uses.get(account or entry)  # ett konto som inte är kopplat: profilens id
            accounts.append({"account": account or entry, "name": self.label(platform, account) if account else None,
                             "state": use["state"] if use else "free", "at": use["at"] if use else None,
                             "text": use["texts"][0] if use and use["texts"] else None})
        return {"multi": bool(profile and profile.multi(key)), "max_accounts": MAX_ACCOUNTS, "accounts": accounts}

    def label(self, platform: str, account: str | None) -> str:
        """Kontots namn för Robin: användarnamn eller kanalnamn, annars id."""
        if account is None:
            return "an account without a name"
        if platform == "youtube":
            info = YouTube(self.settings).accounts().get(account) or {}
            return info.get("title") or info.get("handle") or account
        info = TikTok(self.settings).accounts().get(account) or {}
        return info.get("username") or info.get("display_name") or account

    def _names(self, platform: str, account: str) -> set[str]:
        """Kontots id och namn, så att profilen kan nämna det med användarnamn eller handle."""
        if platform == "youtube":
            info = YouTube(self.settings).accounts().get(account) or {}
            names = (account, info.get("handle"), info.get("title"))
        else:
            info = TikTok(self.settings).accounts().get(account) or {}
            names = (account, info.get("username"), info.get("display_name"))
        return {str(n).lstrip("@").lower() for n in names if n}

    def _post_times(self, platform: str, account: str | None, start: str, end: str,
                    skip: int | None = None) -> list[str]:
        """Inläggen på kontot mellan start och end: väntande och påminda poster i schemat, och uppladdningar
        med knapparna eller av schemat. Misslyckade och avbrutna räknas inte, och inte heller TikTok-utkast."""
        with self._db() as db:
            times = [row[0] for row in db.execute(
                "SELECT at FROM scheduled_posts WHERE platform = ? AND account IS ? AND id IS NOT ? "
                "AND status IN ('waiting', 'running', 'reminded') AND at >= ? AND at < ?",
                (platform, account, skip, start, end))]
        if not account:
            return times
        if platform == "youtube":
            with YouTube(self.settings)._db() as db:
                rows = db.execute("SELECT created FROM youtube_uploads WHERE channel_id = ? AND status != 'failed' "
                                  "AND created >= ? AND created < ?", (account, start, end)).fetchall()
        else:
            with TikTok(self.settings)._db() as db:
                rows = db.execute(f"SELECT created FROM tiktok_uploads WHERE open_id = ? AND mode = 'direct' AND "
                                  f"status IN ({', '.join('?' * len(TIKTOK_LIVE))}) AND created >= ? AND created < ?",
                                  (account, *TIKTOK_LIVE, start, end)).fetchall()
        return times + [row[0] for row in rows]

    def _too_close(self, post: dict) -> str | None:
        profile = _profile(self.settings, post.get("profile"))
        gap = profile.min_gap_minutes if profile else 120
        if not gap:
            return None
        at = datetime.fromisoformat(post["at"])
        near = self._post_times(post["platform"], post["account"], _iso(at - timedelta(minutes=gap, seconds=-1)),
                                _iso(at + timedelta(minutes=gap)), skip=post["id"])  # precis gap ifrån är okej
        if near:
            other = datetime.fromisoformat(min(near, key=lambda t: abs(datetime.fromisoformat(t) - at)))
            return (f"The account has another post at {other.astimezone(STOCKHOLM):%H:%M}. The campaign wants at least "
                    f"{gap} minutes between posts on the same account.")
        # Samma klipp på ett annat konto på samma plattform.
        same = [(abs(_when(use["at"]) - at), account, _when(use["at"]))
                for account, use in self._uses(post["clip_id"], post["platform"], skip=post["id"]).items()
                if account != post["account"] and use["at"] and abs(_when(use["at"]) - at) < timedelta(minutes=gap)]
        if not same:
            return None
        _, account, when = min(same, key=lambda item: item[0])
        return (f"The same clip is on {self.label(post['platform'], account)} at "
                f"{when.astimezone(STOCKHOLM):%H:%M}. The campaign wants at least {gap} minutes between the same clip on "
                f"different accounts.")

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
            raise ScheduleError(f"Only waiting posts can be cancelled. This one is {post['status']}.", 409)
        return self.get(post_id)

    def fail_interrupted(self) -> None:
        """Servern dog mitt i en uppladdning. Posten körs inte igen, så att inget laddas upp två gånger."""
        with self._db() as db:
            db.execute("UPDATE scheduled_posts SET status = 'failed', error = ?, updated = ? WHERE status = 'running'",
                       ("The server restarted in the middle of the upload. Check the account before you try again.",
                        _iso(_now())))

    # ---------- vid tiden ----------

    def run_due(self, now: datetime | None = None) -> None:
        now = now or _now()
        with self._db() as db:
            due = db.execute("SELECT id FROM scheduled_posts WHERE status = 'waiting' AND at <= ? ORDER BY at",
                             (_iso(now),)).fetchall()
        for row in due:
            if editor.lock.locked():  # filen kan bytas mitt i uppladdningen: nästa varv
                break
            with self._db() as db:  # running före uppladdningen: ett annat varv tar aldrig samma post
                if db.execute("UPDATE scheduled_posts SET status = 'running', updated = ? "
                              "WHERE id = ? AND status = 'waiting'", (_iso(now), row["id"])).rowcount != 1:
                    continue
            self._run(self.get(row["id"]), now)
        self._ask_tiktok()

    def _ask_tiktok(self) -> None:
        """Direktinlägg som TikTok behandlar: latest() skapar inlägget när det blir publicerat, utan att någon
        behöver titta i webben. Cachen på en minut i latest() skyddar TikTok."""
        tiktok = TikTok(self.settings)
        with tiktok._db() as db:
            clips = [row[0] for row in db.execute(
                "SELECT DISTINCT clip_id FROM tiktok_uploads WHERE mode = 'direct' AND status = 'processing'")]
        for clip_id in clips:
            try:
                tiktok.latest(clip_id)
            except Exception:
                log.exception("Status från TikTok för klipp %s", clip_id)

    def _run(self, post: dict, now: datetime) -> None:
        late_s = (now - datetime.fromisoformat(post["at"])).total_seconds()
        title = post["clip_title"] or f"Klipp {post['clip_id']}"
        # Notisen öppnar rätt flik i Klippen: klippet ligger kvar i Att posta tills det har ett inlägg.
        queue = f"{self.settings.web_url}/queue?tab=posta"
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
            notify_mac(f"{title} was not uploaded: {error}", queue)
            return
        except Exception:  # tråden får aldrig dö; posten körs inte igen
            log.exception("Schemalagd post %s", post["id"])
            error_text = "The upload hit an unexpected error. Check the account."
            self._set(post["id"], status="failed", error=error_text)
            notify_mac(f"{title} was not uploaded: {error_text}", queue)
            return
        self._set(post["id"], status=status, error=None, result=json.dumps({**result, "late": late}))
        if status == "reminded":
            message, url = f"Time to post {title} on TikTok", None
        else:
            done = "is up on YouTube" if post["platform"] == "youtube" else "was sent to TikTok"
            if submit_helper(self.settings):  # webben har länken och tiden; notisen öppnar fliken Skicka in
                # Samma start som inlägget: TikTok-inlägget räknas från uppladdningen (TikTok.latest).
                start = datetime.fromisoformat(result["created"]) if result.get("created") else _now()
                before = (start + timedelta(minutes=30)).astimezone(STOCKHOLM)
                message = f"{title} {done}. Submit the link in Content Rewards before {before:%H:%M}."
                url = f"{self.settings.web_url}/queue?tab=skicka-in"
            else:
                message = f"{title} {done}. Submit the link in Content Rewards within 30 minutes."
                url = self._content_rewards(post["profile"])
        notify_mac(f"Late: {message}" if late else message, url or queue)

    def _upload(self, post: dict) -> tuple[str, dict]:
        try:
            clip = self.clips.get(post["clip_id"])
        except SystemExit as error:
            raise ScheduleError("The clip no longer exists.") from error
        path = clip_file(self.settings, clip, post["platform"], "The clip is no longer approved.")
        duration_s = clip_seconds(clip, path)
        self._not_there_yet(post["clip_id"], post["platform"], post["account"])
        self.check_variant(clip, post["platform"], post["account"], post["fields"], skip=post["id"])
        if post["platform"] == "youtube":
            youtube = YouTube(self.settings)
            if youtube.uploads_today() >= YOUTUBE_DAILY:
                raise ScheduleError(f"Today's limit of {YOUTUBE_DAILY} uploads to YouTube is reached.")
            return "done", youtube.upload(post["clip_id"], path, post["account"], post["fields"], duration_s)
        if self._reminds(post):
            return "reminded", {}
        return "done", TikTok(self.settings).upload(post["clip_id"], path, post["account"], post["fields"], duration_s)

    def _content_rewards(self, profile: str | None) -> str | None:
        try:
            return load_profile(profile or "", self.settings.profiles_dir).content_rewards_url
        except (SystemExit, Exception):  # kampanjen finns inte längre: notisen öppnar kön
            log.warning("Kampanjen %s går inte att läsa", profile, exc_info=True)
            return None


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

