"""Klipplogg: följer varje klipp från renderat till betalt."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

STATUSES = [
    "renderad",  # klart från verktyget, väntar på ditt ja eller nej
    "underkand",  # regelkontrollen hittade problem
    "godkand",  # du sa ja
    "nekad",  # du sa nej
    "postad",  # uppe på TikTok, Shorts eller Reels
    "inskickad",  # länken är inskickad till Content Rewards
    "analytics_inskickad",  # skärminspelningen av statistiken är inskickad
    "betald",
]

COLUMNS = (
    "id, vod_id, profile, start_s, end_s, title, format, signals, file, caption, status, "
    "problems, warnings, review, platform, url, views, likes, comments, shares, note, "
    "created, updated"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        clip = dict(row)
        for key in ("signals", "problems", "warnings", "review"):
            clip[key] = json.loads(clip[key]) if clip[key] else None
        return clip

    def add(self, *, vod_id: str, profile: str, file: str, title: str, caption: str,
            status: str = "renderad", start_s: float | None = None, end_s: float | None = None,
            clip_format: str | None = None, signals: list | None = None,
            problems: list | None = None, warnings: list | None = None,
            review: dict | None = None) -> int:
        if status not in STATUSES:
            raise ValueError(f"Okänd status: {status}")
        with self._db() as db:
            cursor = db.execute(
                """INSERT INTO clips (vod_id, profile, start_s, end_s, title, format, signals,
                   file, caption, status, problems, warnings, review, created, updated)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (vod_id, profile, start_s, end_s, title, clip_format,
                 json.dumps(signals or []), file, caption, status,
                 json.dumps(problems or [], ensure_ascii=False),
                 json.dumps(warnings or [], ensure_ascii=False),
                 json.dumps(review, ensure_ascii=False) if review else None,
                 _now(), _now()),
            )
            return int(cursor.lastrowid)

    def get(self, clip_id: int) -> dict:
        with self._db() as db:
            row = db.execute(f"SELECT {COLUMNS} FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if row is None:
            raise SystemExit(f"Hittar inget klipp med id {clip_id}.")
        return self._row(row)

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
                   "note", "format", "title", "caption"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Fält som inte får ändras: {sorted(unknown)}")
        self.get(clip_id)
        sets = ", ".join(f"{key} = ?" for key in fields)
        with self._db() as db:
            db.execute(
                f"UPDATE clips SET {sets}, updated = ? WHERE id = ?",
                (*fields.values(), _now(), clip_id),
            )

    def overlaps(self, vod_id: str, start_s: float, end_s: float, min_ratio: float = 0.25) -> bool:
        """True om samma del av samma VOD redan finns i loggen (nekade räknas inte)."""
        length = max(end_s - start_s, 0.001)
        for clip in self.list():
            if clip["vod_id"] != vod_id or clip["status"] == "nekad":
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

    def todo(self, min_views: int = 2000) -> list[str]:
        """Det du behöver göra just nu för att få betalt."""
        items: list[str] = []
        for clip in self.list():
            label = f"#{clip['id']} {clip['title']!r}"
            if clip["status"] == "godkand":
                items.append(f"{label}: posta klippet och kör `posted {clip['id']} <länk>`.")
            elif clip["status"] == "postad":
                items.append(f"{label}: skicka in länken i Content Rewards och kör `mark {clip['id']} inskickad`.")
            elif clip["status"] == "inskickad" and (clip["views"] or 0) >= min_views:
                items.append(
                    f"{label}: har {clip['views']} visningar. Skicka skärminspelningen av "
                    f"klippets statistik och kör `mark {clip['id']} analytics_inskickad`."
                )
        return items
