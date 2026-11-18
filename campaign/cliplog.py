"""Klipplogg: följer varje klipp från renderat till betalt."""

from __future__ import annotations

import builtins
import json
import re
import sqlite3
from collections.abc import Callable, Hashable
from datetime import datetime, timezone
from pathlib import Path

STATUSES = [
    "renderad",  # klart från verktyget, väntar på ditt ja eller nej
    "underkand",  # regelkontrollen hittade problem
    "godkand",  # du sa ja
    "nekad",  # du sa nej
    "postad",  # uppe på TikTok, Shorts eller Reels
    "inskickad",  # länken är inskickad till Content Rewards
    "analytics_inskickad",  # Analytics inskickad: skärminspelningen av inläggets analytics är inskickad
    "betald",
]

# Ett inlägg är klippet på en plattform. Varje inlägg skickas in, följs och betalas för sig.
POST_STATUSES = ["postad", "inskickad", "analytics_inskickad", "betald"]
POST_COLUMNS = ("id, clip_id, platform, account, url, status, views, likes, comments, shares, paid_usd, created, "
                "updated, prev_status")
# Ett inlägg per klipp, plattform och konto. Ett inlägg loggat för hand har inget konto (NULL), och räknas som
# ett eget konto: två utan konto på samma plattform går inte.
POSTS_TABLE = """CREATE TABLE IF NOT EXISTS {name} (
    id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id INTEGER, platform TEXT, account TEXT, url TEXT,
    status TEXT, views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER,
    paid_usd REAL, created TEXT, updated TEXT)"""
POSTS_UNIQUE = ("CREATE UNIQUE INDEX IF NOT EXISTS posts_account ON posts (clip_id, platform, COALESCE(account, ''))")
PLATFORM_NAMES = {"tiktok": "TikTok", "instagram_reels": "Reels", "youtube_shorts": "Shorts"}
STAT_KEYS = ("views", "likes", "comments", "shares")

COLUMNS = (
    "id, vod_id, profile, start_s, end_s, title, format, signals, file, caption, status, "
    "problems, warnings, review, platform, url, views, likes, comments, shares, note, "
    "created, updated, score, reason, subscores, version_of"
)

# Kolumner som kom till efter första versionen. Saknas de läggs de till när loggen öppnas.
NEW_COLUMNS = {
    "clips": {"score": "INTEGER", "reason": "TEXT", "subscores": "TEXT",
              "version_of": "INTEGER"},  # en ny version för ett annat konto: originalets id
    "runs": {"focus": "TEXT", "min_s": "INTEGER", "max_s": "INTEGER", "title": "TEXT",
             "channel_name": "TEXT", "thumbnail_url": "TEXT", "duration_s": "INTEGER", "created": "TEXT",
             "start_s": "REAL", "end_s": "REAL"},
    "posts": {"prev_status": "TEXT"},  # statusen före den senaste, så att Ångra på Betald går tillbaka dit
}
# Kampanjens enda projekt för egna filer: filerna från python -m campaign local och uppladdningarna.
# Tabellen och körningarnas kolumner kommer i _add_projects, efter kopian av klipploggen.
PROJECT_COLUMNS = {"project_id": "INTEGER",  # kampanjens projekt för egna filer, NULL för Twitch
                   "origin": "TEXT"}  # "chat" när kampanjens chatt gjorde körningen, annars NULL
PROJECTS_TABLE = """CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT, profile TEXT NOT NULL, kind TEXT NOT NULL, title TEXT, created TEXT)"""
PROJECTS_UNIQUE = "CREATE UNIQUE INDEX IF NOT EXISTS projects_kind ON projects (profile, kind)"
OWN_FILES = "egna"  # projects.kind: egna filer (python -m campaign local) och uppladdningar (Egna videor)
UPLOADS = "upload"  # vod_id för klipp från Egna videor, samma som uploads_api.UPLOAD
LIVE_PART = re.compile(r"\d+_live_\d+")  # vod_id för en del från live-klipparen, samma som live.part_id
BACKUP_SUFFIX = ".fore-projekt-"  # kopian före migreringen: klipp.db.fore-projekt-<tidsstämpel>


def own_file(vod_id: str) -> bool:
    """En egen fil har filens namn som vod_id. En Twitch-VOD har bara siffror, och en del från
    live-klipparen har <VOD>_live_<n> (live.part_id): den hör till streamen, inte till egna filer."""
    return not vod_id.isdigit() and not LIVE_PART.fullmatch(vod_id)


def card_runs(runs: list[dict], key: Callable[[dict], Hashable]) -> dict:
    """Körningen som visas per nyckel, till exempel (vod_id, kampanj): den senaste, men inte en från chatten om
    det finns en annan."""
    # Chattens körningar först och de andra sist, var för sig i ordning efter run_id: den sista vinner.
    return {key(meta): meta for meta in sorted(runs, key=lambda m: (m["origin"] != "chat", m["run_id"]))}


def _project_id(db: sqlite3.Connection, profile: str, streamer: str) -> int:
    """Kampanjens projekt för egna filer. Skapas första gången, med streamerns namn i titeln."""
    db.execute("INSERT OR IGNORE INTO projects (profile, kind, title, created) VALUES (?, ?, ?, ?)",
               (profile, OWN_FILES, f"Egna filer för {streamer}", _now()))
    return db.execute("SELECT id FROM projects WHERE profile = ? AND kind = ?", (profile, OWN_FILES)).fetchone()[0]


def _backup(path: Path) -> Path:
    """En kopia av hela klipploggen bredvid filen, med sqlite3-backup. Ett nytt namn varje gång. Läses med en
    egen anslutning: den som migrerar har skrivlåset (BEGIN IMMEDIATE), och då kan backup på samma anslutning
    vänta för evigt. En läsare får läsa medan skrivlåset hålls, så länge inget är skrivet."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}{BACKUP_SUFFIX}{stamp}")
    n = 1
    while target.exists():
        n += 1
        target = path.with_name(f"{path.name}{BACKUP_SUFFIX}{stamp}-{n}")
    source, copy = sqlite3.connect(path), sqlite3.connect(target)
    try:
        source.backup(copy)
    finally:
        copy.close()
        source.close()
    return target


def _add_project_columns(db: sqlite3.Connection) -> None:
    have = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
    for name, kind in PROJECT_COLUMNS.items():
        if name not in have:
            db.execute(f"ALTER TABLE runs ADD COLUMN {name} {kind}")
    db.execute(PROJECTS_TABLE)
    db.execute(PROJECTS_UNIQUE)


def _own_runs(db: sqlite3.Connection) -> list[tuple[int, str]]:
    """Egna filers körningar: run_id och kampanj."""
    rows = db.execute("SELECT run_id, vod_id, profile FROM runs ORDER BY run_id")
    return [(run_id, profile) for run_id, vod_id, profile in rows if own_file(vod_id or "")]


def _move_own_files(db: sqlite3.Connection, path: Path) -> None:
    """Ett projekt per kampanj som har egna filer eller uppladdningar, och project_id på egna filers körningar
    (own_file: vod_id som inte bara är siffror och inte är en live-del). Först en kopia av klipploggen bredvid filen,
    klipp.db.fore-projekt-<tidsstämpel>, om det finns något att flytta. Inga rader tas bort, och samma resultat
    om den körs två gånger. Ångra: UPDATE runs SET project_id = NULL
    (eller lägg tillbaka kopian medan servern är stoppad)."""
    runs = _own_runs(db)
    uploads = [row[0] for row in db.execute("SELECT DISTINCT profile FROM clips WHERE vod_id = ?", (UPLOADS,))]
    profiles = list(dict.fromkeys([*(profile for _, profile in runs), *uploads]))
    if profiles:
        _backup(path)
    _add_project_columns(db)
    # Projektets titel har streamerns namn från kampanjens körningar, också Twitch-körningarna.
    streamers = dict(db.execute("SELECT profile, channel_name FROM runs WHERE channel_name IS NOT NULL "
                                "ORDER BY run_id"))
    for profile in profiles:
        _project_id(db, profile, streamers.get(profile, profile))
    db.executemany("UPDATE runs SET project_id = (SELECT id FROM projects WHERE profile = ? AND kind = ?) "
                   "WHERE run_id = ? AND project_id IS NULL", [(profile, OWN_FILES, run_id) for run_id, profile in runs])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class UploadRunning(Exception):
    """Ett godkänt klipp laddas upp just nu. Det får inte lämna godkänt förrän uppladdningen är klar. Ger 409."""


def _cancel_uploads(db: sqlite3.Connection, clip_id: int) -> None:
    """Klippet lämnar godkänt, så ett ja gäller inte längre: väntande schemalagda uppladdningar avbryts.
    Tabellen hör till schedule.py och finns först när något har schemalagts."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'scheduled_posts'").fetchone():
        return
    running = db.execute("SELECT platform FROM scheduled_posts WHERE clip_id = ? AND status = 'running'",
                         (clip_id,)).fetchone()
    if running:
        name = "YouTube" if running[0] == "youtube" else "TikTok"
        raise UploadRunning(f"Klippet laddas upp nu på {name}. Vänta tills uppladdningen är klar.")
    db.execute("UPDATE scheduled_posts SET status = 'cancelled', updated = ? WHERE clip_id = ? AND status = 'waiting'",
               (_now(), clip_id))


class ClipLog:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS clips (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vod_id TEXT, profile TEXT, start_s REAL, end_s REAL,
                    title TEXT, format TEXT, signals TEXT, file TEXT, caption TEXT,
                    status TEXT, problems TEXT, warnings TEXT, review TEXT,
                    platform TEXT, url TEXT,
                    views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER,
                    note TEXT, created TEXT, updated TEXT)"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS runs (
                    run_id INTEGER PRIMARY KEY, vod_id TEXT, profile TEXT, game TEXT)"""
            )
            db.execute(POSTS_TABLE.format(name="posts"))
            if "account" in {row["name"] for row in db.execute("PRAGMA table_info(posts)")}:
                db.execute(POSTS_UNIQUE)
            # Klipp från före inläggen: klippets länk blir ett inlägg. Bara en gång (user_version 0 -> 1),
            # så att ett klipp vars inlägg är borttagna inte får tillbaka dem.
            if db.execute("PRAGMA user_version").fetchone()[0] < 1:
                self._move_links_to_posts(db)
                db.execute("PRAGMA user_version = 1")
        self._add_accounts()
        with self._db() as db:
            for table, columns in NEW_COLUMNS.items():
                have = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
                for name, kind in columns.items():
                    if name not in have:
                        try:
                            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
                        except sqlite3.OperationalError as error:  # en annan process hann före
                            if "duplicate column" not in str(error):
                                raise
        self._add_projects()

    def _add_projects(self) -> None:
        """user_version 2 -> 3, i en transaktion: se _move_own_files. Ångra: UPDATE runs SET project_id = NULL"""
        db = sqlite3.connect(self.db_path, isolation_level=None)
        try:
            db.execute("BEGIN IMMEDIATE")  # en process i taget; den andra ser user_version 3 och gör inget
            if db.execute("PRAGMA user_version").fetchone()[0] < 3:
                _move_own_files(db, self.db_path)
                db.execute("PRAGMA user_version = 3")
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    @staticmethod
    def _move_links_to_posts(db: sqlite3.Connection) -> None:
        db.execute(
            f"""INSERT OR IGNORE INTO posts (clip_id, platform, url, status, views, likes, comments, shares,
                   created, updated)
               SELECT id, COALESCE(platform, 'tiktok'), url,
                      CASE WHEN status IN ({", ".join("?" * len(POST_STATUSES))}) THEN status ELSE 'postad' END,
                      views, likes, comments, shares, updated, updated
               FROM clips WHERE url IS NOT NULL AND url != ''
                 AND id NOT IN (SELECT clip_id FROM posts)""",
            POST_STATUSES,
        )

    def _add_accounts(self) -> None:
        """user_version 1 -> 2: posts får kolumnen account och unikhet per konto. SQLite kan inte ta bort
        UNIQUE (clip_id, platform), så tabellen byggs om, i en transaktion. Gamla inlägg får kontot från
        uppladdningen när adressen pekar på just den videon, annars NULL."""
        db = sqlite3.connect(self.db_path, isolation_level=None)
        try:
            db.execute("BEGIN IMMEDIATE")  # en process i taget; den andra ser user_version 2 och gör inget
            if db.execute("PRAGMA user_version").fetchone()[0] >= 2:
                db.execute("COMMIT")
                return
            if "account" not in {row[1] for row in db.execute("PRAGMA table_info(posts)")}:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

                def has(table: str, *columns: str) -> bool:
                    # Gamla uppladdningstabeller får sina kolumner först när TikTok- eller YouTube-delen öppnar
                    # dem, och det sker efter ClipLog vid start. Saknas en kolumn gissas inget konto.
                    return table in tables and set(columns) <= {
                        row[1] for row in db.execute(f"PRAGMA table_info({table})")}

                guesses = []
                if has("tiktok_uploads", "clip_id", "open_id", "mode", "post_id"):
                    guesses.append("""SELECT open_id AS a FROM tiktok_uploads u WHERE u.clip_id = p.clip_id
                        AND p.platform = 'tiktok' AND u.mode = 'direct' AND u.open_id IS NOT NULL
                        AND u.post_id != '' AND substr(p.url, -length(u.post_id) - 7) = '/video/' || u.post_id""")
                if has("youtube_uploads", "clip_id", "channel_id", "video_id"):
                    guesses.append("""SELECT channel_id AS a FROM youtube_uploads u WHERE u.clip_id = p.clip_id
                        AND p.platform = 'youtube_shorts' AND u.channel_id IS NOT NULL AND u.video_id != ''
                        AND substr(p.url, -length(u.video_id) - 1) = '/' || u.video_id""")
                # Bara ett enda konto som passar räknas som säkert.
                account = (f"(SELECT CASE WHEN COUNT(DISTINCT a) = 1 THEN MAX(a) END FROM "
                           f"({' UNION ALL '.join(guesses)}))") if guesses else "NULL"
                db.execute(POSTS_TABLE.format(name="posts_new"))
                db.execute(f"""INSERT INTO posts_new (id, clip_id, platform, account, url, status, views, likes,
                                   comments, shares, paid_usd, created, updated)
                               SELECT id, clip_id, platform, {account}, url, status, views, likes, comments,
                                   shares, paid_usd, created, updated FROM posts p""")
                db.execute("DROP TABLE posts")
                db.execute("ALTER TABLE posts_new RENAME TO posts")
            db.execute(POSTS_UNIQUE)
            db.execute("PRAGMA user_version = 2")
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        clip = dict(row)
        for key in ("signals", "problems", "warnings", "review", "subscores"):
            clip[key] = json.loads(clip[key]) if clip[key] else None
        return clip

    def add(self, *, vod_id: str, profile: str, file: str, title: str, caption: str,
            status: str = "renderad", start_s: float | None = None, end_s: float | None = None,
            clip_format: str | None = None, signals: list | None = None,
            problems: list | None = None, warnings: list | None = None,
            review: dict | None = None, score: int | None = None, reason: str = "",
            subscores: dict | None = None, version_of: int | None = None) -> int:
        if status not in STATUSES:
            raise ValueError(f"Okänd status: {status}")
        with self._db() as db:
            cursor = db.execute(
                """INSERT INTO clips (vod_id, profile, start_s, end_s, title, format, signals,
                   file, caption, status, problems, warnings, review, created, updated, score, reason,
                   subscores, version_of)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (vod_id, profile, start_s, end_s, title, clip_format,
                 json.dumps(signals or []), file, caption, status,
                 json.dumps(problems or [], ensure_ascii=False),
                 json.dumps(warnings or [], ensure_ascii=False),
                 json.dumps(review, ensure_ascii=False) if review else None,
                 _now(), _now(), score, reason, json.dumps(subscores) if subscores else None, version_of),
            )
            return int(cursor.lastrowid)

    def get(self, clip_id: int) -> dict:
        with self._db() as db:
            row = db.execute(f"SELECT {COLUMNS} FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if row is None:
            raise SystemExit(f"Hittar inget klipp med id {clip_id}.")
        return self._row(row)

    def family(self, clip_id: int) -> list[int]:
        """Originalet och dess versioner, i den ordning de lades till. Samma klipp för reglerna per konto."""
        root = self.get(clip_id)["version_of"] or clip_id
        with self._db() as db:
            rows = db.execute("SELECT id FROM clips WHERE id = ? OR version_of = ? ORDER BY id", (root, root))
            return [row[0] for row in rows]

    def list(self, status: str | None = None) -> list[dict]:
        query = f"SELECT {COLUMNS} FROM clips"
        args: tuple = ()
        if status:
            query += " WHERE status = ?"
            args = (status,)
        with self._db() as db:
            return [self._row(r) for r in db.execute(query + " ORDER BY id", args).fetchall()]

    def update(self, clip_id: int, **fields) -> None:
        """Ändra fält på ett klipp, t.ex. status, url, format eller note."""
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(f"Okänd status: {fields['status']}")
        allowed = {"status", "platform", "url", "views", "likes", "comments", "shares",
                   "note", "format", "title", "caption", "problems", "warnings", "review", "file",
                   "start_s", "end_s", "score", "reason", "subscores"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Fält som inte får ändras: {sorted(unknown)}")
        for key in ("problems", "warnings", "review", "subscores"):
            if key in fields:
                fields[key] = json.dumps(fields[key], ensure_ascii=False) if fields[key] is not None else None
        self.get(clip_id)
        sets = ", ".join(f"{key} = ?" for key in fields)
        # En transaktion med skrivlås: schemaläggaren hinner inte starta en uppladdning mellan kollen och bytet.
        db = sqlite3.connect(self.db_path, isolation_level=None)
        try:
            db.execute("BEGIN IMMEDIATE")
            if "status" in fields and fields["status"] not in ("godkand", *POST_STATUSES):
                _cancel_uploads(db, clip_id)  # här, så att ingen väg som ändrar status glömmer det
            db.execute(f"UPDATE clips SET {sets}, updated = ? WHERE id = ?", (*fields.values(), _now(), clip_id))
            db.execute("COMMIT")
        finally:
            if db.in_transaction:
                db.execute("ROLLBACK")
            db.close()

    # ---------- inlägg ----------

    def posts(self, clip_id: int | None = None) -> list[dict]:
        """Inläggen för ett klipp, eller alla inlägg, i den ordning de lades till."""
        query, args = f"SELECT {POST_COLUMNS} FROM posts", ()
        if clip_id is not None:
            query, args = query + " WHERE clip_id = ?", (clip_id,)
        with self._db() as db:
            return [dict(r) for r in db.execute(query + " ORDER BY id", args).fetchall()]

    def get_post(self, post_id: int) -> dict:
        with self._db() as db:
            row = db.execute(f"SELECT {POST_COLUMNS} FROM posts WHERE id = ?", (post_id,)).fetchone()
        if row is None:
            raise SystemExit(f"Hittar inget inlägg med id {post_id}.")
        return dict(row)

    def add_post(self, clip_id: int, platform: str, url: str, created: str | None = None,
                 account: str | None = None) -> int:
        """Ett nytt inlägg med status postad. Samma plattform och konto två gånger ger sqlite3.IntegrityError.
        created: när inlägget publicerades, om det var före nu. account: open_id eller channel_id, None för
        ett inlägg loggat för hand."""
        self.get(clip_id)
        now = _now()
        with self._db() as db:
            cursor = db.execute(
                "INSERT INTO posts (clip_id, platform, account, url, status, created, updated) "
                "VALUES (?,?,?,?,?,?,?)",
                (clip_id, platform, account, url, "postad", created or now, now),
            )
            post_id = int(cursor.lastrowid)
        self._sync(clip_id)
        return post_id

    @staticmethod
    def _check_post_fields(fields: dict) -> None:
        if "status" in fields and fields["status"] not in POST_STATUSES:
            raise ValueError(f"Okänd status för ett inlägg: {fields['status']}")
        unknown = set(fields) - {"status", "url", "platform", "paid_usd", *STAT_KEYS}
        if unknown:
            raise ValueError(f"Fält som inte får ändras: {sorted(unknown)}")

    def update_post(self, post_id: int, **fields) -> None:
        self._check_post_fields(fields)
        post = self.get_post(post_id)
        if fields.get("status", post["status"]) != post["status"]:
            fields["prev_status"] = post["status"]
        if fields:
            sets = ", ".join(f"{key} = ?" for key in fields)
            with self._db() as db:
                db.execute(f"UPDATE posts SET {sets}, updated = ? WHERE id = ?", (*fields.values(), _now(), post_id))
        self._sync(post["clip_id"])

    def delete_post(self, post_id: int) -> None:
        post = self.get_post(post_id)
        with self._db() as db:
            db.execute("DELETE FROM posts WHERE id = ?", (post_id,))
        self._sync(post["clip_id"])

    def _sync(self, clip_id: int) -> None:
        """Klippets länk, plattform, siffror och status räknas fram ur inläggen, så att sammanfattningen
        och intäkterna fortsätter fungera. Klippet är så långt som det inlägg som har kommit kortast."""
        posts = self.posts(clip_id)
        clip = self.get(clip_id)
        if not posts:
            if clip["status"] in POST_STATUSES:
                self.update(clip_id, status="godkand", url=None, platform=None,
                            **{key: None for key in STAT_KEYS})
            return
        totals = {key: (sum(p[key] or 0 for p in posts) if any(p[key] is not None for p in posts) else None)
                  for key in STAT_KEYS}
        status = min((p["status"] for p in posts), key=POST_STATUSES.index)
        self.update(clip_id, status=status, url=posts[0]["url"], platform=posts[0]["platform"], **totals)

    def overlaps(self, vod_id: str, start_s: float, end_s: float, min_ratio: float = 0.25) -> bool:
        """True om samma del av samma VOD redan finns i loggen (nekade och underkända räknas inte)."""
        length = max(end_s - start_s, 0.001)
        for clip in self.list():
            if clip["vod_id"] != vod_id or clip["status"] in ("nekad", "underkand"):
                continue
            if clip["start_s"] is None or clip["end_s"] is None:
                continue
            shared = min(end_s, clip["end_s"]) - max(start_s, clip["start_s"])
            if shared / length >= min_ratio:
                return True
        return False

    @staticmethod
    def engagement(clip: dict) -> float | None:
        """Engagemang i procent: (gilla + kommentarer + delningar) / visningar."""
        if not clip.get("views"):
            return None
        total = sum(clip.get(key) or 0 for key in ("likes", "comments", "shares"))
        return 100.0 * total / clip["views"]

    def todo(self, min_views: int = 2000, skip: set[str] | frozenset[str] = frozenset()) -> list[str]:
        """Det du behöver göra just nu för att få betalt, per klipp och per inlägg.
        `skip` är kampanjer som inte ska räknas."""
        items: list[str] = []
        posts: dict[int, list[dict]] = {}
        for post in self.posts():
            posts.setdefault(post["clip_id"], []).append(post)
        for clip in self.list():
            if clip["profile"] in skip:
                continue
            label = f"#{clip['id']} {clip['title']!r}"
            if clip["status"] == "godkand":
                items.append(f"{label}: posta klippet och spara länken.")
            # Ett klipp som postats utan inlägg (äldre sätt) räknas som ett inlägg.
            implied = [clip] if clip["status"] in POST_STATUSES else []
            for post in posts.get(clip["id"], implied):
                name = PLATFORM_NAMES.get(post["platform"] or "", post["platform"])
                where = f"{label} på {name}" if name else label
                if post["status"] == "postad":
                    items.append(f"{where}: skicka in länken i Content Rewards och markera inlägget som inskickat.")
                elif post["status"] == "inskickad" and post["views"] is None:
                    items.append(f"{where}: fyll i visningar, gilla och kommentarer.")
                elif post["status"] == "inskickad" and post["views"] >= min_views:
                    items.append(
                        f"{where}: har {post['views']} visningar. Skicka skärminspelningen av "
                        "inläggets analytics i analytics-formuläret."
                    )
        return items

    def add_run(self, run_id: int, vod_id: str, profile: str, game: str | None, *,
                focus: str | None = None, min_s: int | None = None, max_s: int | None = None,
                title: str | None = None, channel_name: str | None = None,
                thumbnail_url: str | None = None, duration_s: int | None = None,
                start_s: float = 0.0, end_s: float = 0.0, origin: str | None = None) -> None:
        """Spara vilken kampanj en körning i appen hör till, och fakta om VOD:en. En egen fil hamnar i
        kampanjens projekt för egna filer, med streamerns namn (channel_name) i titeln. origin: "chat" när
        kampanjens chatt gör körningen."""
        with self._db() as db:
            project_id = _project_id(db, profile, channel_name or profile) if own_file(vod_id) else None
            db.execute(
                """INSERT OR REPLACE INTO runs (run_id, vod_id, profile, game, focus, min_s, max_s,
                   title, channel_name, thumbnail_url, duration_s, created, start_s, end_s, project_id, origin)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, vod_id, profile, game, focus, min_s, max_s, title, channel_name,
                 thumbnail_url, duration_s, _now(), start_s, end_s, project_id, origin))

    def set_run_facts(self, run_id: int, *, title: str, channel_name: str, thumbnail_url: str | None,
                      duration_s: int) -> None:
        """Spara VOD-fakta på en körning som saknar dem, t.ex. en från före fakta fanns."""
        with self._db() as db:
            db.execute("UPDATE runs SET title = ?, channel_name = ?, thumbnail_url = ?, duration_s = ? "
                       "WHERE run_id = ?", (title, channel_name, thumbnail_url, duration_s, run_id))

    def runs(self) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM runs ORDER BY run_id").fetchall()]

    def run(self, run_id: int) -> dict | None:
        return next((r for r in self.runs() if r["run_id"] == run_id), None)

    # ---------- projekt för egna filer ----------

    def project(self, profile: str, streamer: str) -> int:
        """Kampanjens projekt för egna filer och uppladdningar. Skapas första gången."""
        with self._db() as db:
            return _project_id(db, profile, streamer)

    def projects(self) -> builtins.list[dict]:  # builtins: list är också en metod i klassen
        with self._db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM projects ORDER BY id").fetchall()]

    def get_project(self, project_id: int) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return dict(row) if row else None
