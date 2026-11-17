"""Chatten på kampanjsidan: Robin skriver vad han vill ha klipp på, Claude letar i kampanjens transkript och
svarar med träffar, och valda träffar blir vanliga klipp att granska. Samtalet och träffarna sparas i klipp.db.

Varje anrop till Claude går via Brain, så att budgeten gäller. Kontraktet: ~/.amp/in/klipp/kontrakt-chatt.md.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import threading
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from clipsmith.api.worker import _emit, _harvest_clips
from clipsmith.db.models import RunStatus
from clipsmith.db.session import get_session
from clipsmith.llm.base import ClipPick
from clipsmith.models.candidates import CandidateMoment
from clipsmith.models.transcript import Segment, Transcript, Word
from clipsmith.pipeline import save_picks
from clipsmith.selection.selector import PickResult, _matches, _token

from . import download, pipeline
from .brain import Brain
from .budget import budget_from_settings
from .cliplog import ClipLog
from .config import Settings
from .profile import Profile, load_profile

log = logging.getLogger(__name__)

CONFIRM_USD = 0.25  # dyrare sökningar körs först efter Robins ja
CHUNK_S, OVERLAP_S = 600.0, 60.0  # transkripten läses i bitar om cirka 10 minuter
SEARCH_OUTPUT_TOKENS = 1500  # uppskattat svar per sökanrop
MAX_MESSAGE = 1000
MARGIN_S = 5  # extra sekunder när bara en del av en Twitch-VOD hämtas
QUOTE_CHARS = 1000
lock = threading.Lock()  # en chattsökning åt gången

TABLES = (
    """CREATE TABLE IF NOT EXISTS chats (
        id INTEGER PRIMARY KEY AUTOINCREMENT, profile TEXT, title TEXT, messages TEXT, created TEXT, updated TEXT)""",
    """CREATE TABLE IF NOT EXISTS chat_hits (
        id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, vod_id TEXT, start_s REAL, end_s REAL, title TEXT,
        reason TEXT, grade INTEGER, quote TEXT, status TEXT, clip_id INTEGER, problem TEXT)""",
)
HIT_FIELDS = ("vod_id", "start_s", "end_s", "title", "reason", "grade", "quote")

INTERPRET = """Du hjälper Robin att hitta klipp i en kampanjs videor. Du ser kampanjens material, träffarna som \
finns och samtalet. Välj action:
- search: Robin vill hitta nya stunder, också "fler sådana". sources: vod_id ur materialet, eller en tom lista \
när han inte sagt var (då gäller förvalet). query: på engelska, vad som ska letas efter, med det han sagt tidigare \
i samtalet om det hör till.
- refine: Robin vill ändra träffarna som finns ("kortare", "ta bort nr 3", "bara när han dör"). hits: varje träff \
som ska vara kvar, med id, start_quote och end_quote (de exakta första och sista orden ur träffens text, tre till \
sex ord), title på engelska, reason på svenska och grade 1-10. Träffar som inte är med tas bort.
- answer: en fråga tillbaka eller ett svar, när prompten är oklar eller inte gäller klipp.
reply: en kort text på svenska till Robin."""

INTERPRET_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["search", "refine", "answer"]},
        "reply": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "string"}},
        "query": {"type": "string"},
        "hits": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "integer"}, "start_quote": {"type": "string"}, "end_quote": {"type": "string"},
            "title": {"type": "string"}, "reason": {"type": "string"},
            "grade": {"type": "integer", "minimum": 1, "maximum": 10}}, "required": ["id"]}},
    },
    "required": ["action", "reply"],
}

SEARCH = """You find moments in a campaign's videos that match Robin's request, for short vertical clips \
(TikTok, Shorts). Each transcript line starts with the second it is said, under a ## header with the video's id. \
For each match give: vod_id (from the header), start_s and end_s (seconds from the lines), start_quote (the clip's \
first 3-6 words exactly as in the transcript), end_quote (the last 3-6 words exactly), title (short and catchy, \
English), reason (one sentence in Swedish: why it matches), grade (1-10: how well it matches and how good a clip it \
makes). A clip is {min_s}-{max_s} seconds and starts and ends on whole sentences. Return no hits when nothing \
matches. Answer only by calling the tool svar."""

SEARCH_SCHEMA = {
    "type": "object",
    "properties": {"hits": {"type": "array", "items": {"type": "object", "properties": {
        "vod_id": {"type": "string"}, "start_s": {"type": "number"}, "end_s": {"type": "number"},
        "start_quote": {"type": "string"}, "end_quote": {"type": "string"}, "title": {"type": "string"},
        "reason": {"type": "string"}, "grade": {"type": "integer", "minimum": 1, "maximum": 10}},
        "required": ["vod_id", "start_s", "end_s", "start_quote", "end_quote", "title", "reason", "grade"]}}},
    "required": ["hits"],
}


class ChatError(Exception):
    def __init__(self, status: int, text: str):
        super().__init__(text)
        self.status, self.text = status, text


def brain(settings: Settings) -> Brain:
    return Brain(settings, budget_from_settings(settings))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mmss(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


# ---------- lagring ----------


class Chats:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        with self._db() as db:
            for table in TABLES:
                db.execute(table)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db

    def get(self, chat_id: int) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
        return {**dict(row), "messages": json.loads(row["messages"])} if row else None

    def list(self, profile: str) -> list[dict]:
        with self._db() as db:
            rows = db.execute("""SELECT c.id, c.title, c.created, c.updated, COUNT(h.id) AS hits FROM chats c
                                 LEFT JOIN chat_hits h ON h.chat_id = c.id WHERE c.profile = ?
                                 GROUP BY c.id ORDER BY c.updated DESC, c.id DESC""", (profile,)).fetchall()
        return [dict(row) for row in rows]

    def hits(self, chat_id: int) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM chat_hits WHERE chat_id = ? ORDER BY id", (chat_id,)).fetchall()
        return [dict(row) for row in rows]

    def set_hits(self, hit_ids: list[int], **fields) -> None:
        names = ", ".join(f"{key} = ?" for key in fields)
        with self._db() as db:
            db.executemany(f"UPDATE chat_hits SET {names} WHERE id = ?",
                           [(*fields.values(), hit_id) for hit_id in hit_ids])

    def add_turn(self, chat_id: int | None, profile: str, messages: list[dict], *, hits: list[dict] | None = None,
                 refined: dict[int, dict] | None = None) -> int:
        """Spara en tur i en transaktion: meddelandena, och nya träffar (byter ut de med status ny) eller
        ändrade träffar (ny-träffar som inte finns i `refined` tas bort)."""
        with self._db() as db:
            if chat_id is None:
                cursor = db.execute("INSERT INTO chats (profile, title, messages, created, updated) "
                                    "VALUES (?, ?, '[]', ?, ?)", (profile, messages[0]["text"][:60], _now(), _now()))
                chat_id = int(cursor.lastrowid or 0)
            old = json.loads(db.execute("SELECT messages FROM chats WHERE id = ?", (chat_id,)).fetchone()[0])
            db.execute("UPDATE chats SET messages = ?, updated = ? WHERE id = ?",
                       (json.dumps(old + messages, ensure_ascii=False), _now(), chat_id))
            if hits is not None:
                db.execute("DELETE FROM chat_hits WHERE chat_id = ? AND status = 'ny'", (chat_id,))
                db.executemany(f"INSERT INTO chat_hits (chat_id, {', '.join(HIT_FIELDS)}, status) "
                               f"VALUES (?, {', '.join('?' * len(HIT_FIELDS))}, 'ny')",
                               [(chat_id, *(hit[key] for key in HIT_FIELDS)) for hit in hits])
            if refined is not None:
                for row in db.execute("SELECT id FROM chat_hits WHERE chat_id = ? AND status = 'ny'",
                                      (chat_id,)).fetchall():
                    change = refined.get(row["id"])
                    if change is None:
                        db.execute("DELETE FROM chat_hits WHERE id = ?", (row["id"],))
                    else:
                        names = ", ".join(f"{key} = ?" for key in change)
                        db.execute(f"UPDATE chat_hits SET {names}, problem = NULL WHERE id = ?",
                                   (*change.values(), row["id"]))
        return chat_id


# ---------- material ----------


def material(profile: str, settings: Settings, log: ClipLog) -> list[dict]:
    """Kampanjens källor: varje vod_id med en körning för kampanjen, nyast först. `game` är bara för klippen."""
    found: dict[str, dict] = {}
    for run in log.runs():
        if run["profile"] != profile:
            continue
        item = found.setdefault(run["vod_id"], {"vod_id": run["vod_id"], "title": None, "created": run["created"],
                                                "duration_s": None, "game": None})
        for key in ("title", "duration_s", "game"):
            item[key] = run[key] or item[key]
        item["_run"] = run["run_id"]
    clips = Counter(clip["vod_id"] for clip in log.list() if clip["profile"] == profile)
    for vod_id, item in found.items():
        folder = settings.work_dir / vod_id
        transcript = folder / "transcript.json"
        if not item["created"] and transcript.exists():
            item["created"] = datetime.fromtimestamp(transcript.stat().st_mtime, timezone.utc).isoformat(
                timespec="seconds")
        item.update(title=item["title"] or vod_id, kind="twitch" if vod_id.isdigit() else "local",
                    has_video=(folder / f"{vod_id}.mp4").is_file(), has_transcript=transcript.is_file(),
                    clips=clips[vod_id])
    items = sorted(found.values(), key=lambda m: (m["created"] or "", m["_run"]), reverse=True)
    for item in items:
        del item["_run"]
    return items


def _transcript(settings: Settings, vod_id: str) -> Transcript:
    return Transcript.from_json((settings.work_dir / vod_id / "transcript.json").read_text(encoding="utf-8"))


def _words(transcript: Transcript) -> list[dict]:
    return [{"start": w.start, "end": w.end, "word": w.word} for seg in transcript.segments for w in seg.words or []
            if _token(w.word)]


# ---------- tider ur citat ----------


def place(words: list[dict], start_quote: str, end_quote: str, near_start: float,
          near_end: float) -> tuple[float, float] | None:
    """Tiderna ur Claudes citat, med samma ordmatchning som clipsmiths _times_from_quotes: början på
    startcitatets första ord och slutet på slutcitatets sista ord. Ett citat som sägs flera gånger tar platsen
    närmast Claudes sekunder, och ett citat som inte hittas kortas till två ord. None när något saknas."""
    start_q = [t for t in (_token(w) for w in start_quote.split()) if t]
    end_q = [t for t in (_token(w) for w in end_quote.split()) if t]
    if not start_q or not end_q:
        return None
    tokens = [_token(w["word"]) for w in words]
    starts: list[int] = []
    for n in range(len(start_q), min(2, len(start_q)) - 1, -1):
        if starts := _matches(tokens, start_q[:n]):
            break
    if not starts:
        return None
    first = min(starts, key=lambda i: abs(words[i]["start"] - near_start))
    ends: list[int] = []
    for n in range(len(end_q), min(2, len(end_q)) - 1, -1):
        if ends := [i + n - 1 for i in _matches(tokens, end_q[-n:]) if i >= first]:
            break
    if not ends:
        return None
    last = min(ends, key=lambda i: abs(words[i]["end"] - near_end))
    return words[first]["start"], words[last]["end"]


def _fit(start: float, end: float, min_s: float, max_s: float, total: float | None) -> tuple[float, float]:
    """Längden inom kampanjens min och max, och inom videon."""
    end = min(max(end, start + min_s), start + max_s)
    if total and end > total:
        end, start = total, max(0.0, min(start, total - min_s))
    return round(start, 3), round(end, 3)


def _quote(words: list[dict], start: float, end: float) -> str:
    return " ".join(w["word"].strip() for w in words if start <= w["start"] and w["end"] <= end + 0.5)[:QUOTE_CHARS]


def _overlap(a: dict, b: dict) -> bool:
    """Mer än halva tiden gemensam, i samma video."""
    shared = min(a["end_s"], b["end_s"]) - max(a["start_s"], b["start_s"])
    return a["vod_id"] == b["vod_id"] and shared > 0.5 * min(a["end_s"] - a["start_s"], b["end_s"] - b["start_s"])


# ---------- sökningen ----------


def _calls(sources: list[dict], transcripts: dict[str, Transcript]) -> list[str]:
    """Transkripten i bitar om cirka 10 minuter med överlapp, packade så att varje anrop läser högst 10 minuter."""
    pieces: list[tuple[float, str]] = []
    for item in sources:
        segments = transcripts[item["vod_id"]].segments
        end = max((s.end for s in segments), default=0.0)
        at = 0.0
        while True:
            lines = [f"[{s.start:.1f}] {s.text.strip()}" for s in segments if at <= s.start < at + CHUNK_S]
            if lines:
                pieces.append((min(CHUNK_S, end - at), f"## {item['vod_id']}: {item['title']}\n" + "\n".join(lines)))
            if at + CHUNK_S >= end:
                break
            at += CHUNK_S - OVERLAP_S
    calls: list[str] = []
    used = CHUNK_S + 1
    for length, text in pieces:
        if used + length > CHUNK_S:
            calls.append(text)
            used = length
        else:
            calls[-1] += "\n\n" + text
            used += length
    return calls


def _estimate(calls: list[str], system: str, query: str, model: str, settings: Settings) -> float:
    budget = budget_from_settings(settings)
    return sum(budget.cost(model, (len(system) + len(query) + len(text)) // 4 + 50, SEARCH_OUTPUT_TOKENS)
               for text in calls)


def _search(b: Brain, query: str, sources: list[dict], cfg, kept: list[dict], settings: Settings) -> tuple[list, float]:
    """Sök i källorna. Träffarna med tider ur citaten, inom min/max, utan dubbletter. Och kostnaden."""
    transcripts = {item["vod_id"]: _transcript(settings, item["vod_id"]) for item in sources}
    words = {vod: _words(t) for vod, t in transcripts.items()}
    totals = {item["vod_id"]: item["duration_s"] for item in sources}
    system = SEARCH.format(min_s=cfg.clip.min_seconds, max_s=cfg.clip.max_seconds)
    found, cost = [], 0.0
    for text in _calls(sources, transcripts):
        answer = b.ask(purpose="chattsökning", model=cfg.llm.model_anthropic, system=system,
                       content=f"Request: {query}\n\n{text}", schema=SEARCH_SCHEMA, max_tokens=4000,
                       force_tool=False)  # kampanjens modell kan vara en som inte tar ett tvingat verktyg
        cost += b.last_usd
        for hit in answer.get("hits") or []:
            vod = str(hit.get("vod_id", ""))
            if f"## {vod}:" not in text:
                continue  # bara källorna i anropet
            times = place(words[vod], str(hit.get("start_quote", "")), str(hit.get("end_quote", "")),
                          float(hit.get("start_s") or 0), float(hit.get("end_s") or 0))
            if times is None:
                continue  # träffen går inte att placera
            start, end = _fit(*times, cfg.clip.min_seconds, cfg.clip.max_seconds, totals[vod])
            found.append({"vod_id": vod, "start_s": start, "end_s": end, "title": str(hit.get("title", ""))[:100],
                          "reason": str(hit.get("reason", ""))[:500], "grade": int(hit.get("grade") or 0),
                          "quote": _quote(words[vod], start, end)})
    hits: list[dict] = []
    for hit in sorted(found, key=lambda h: -h["grade"]):
        if not any(_overlap(hit, other) for other in hits + kept):
            hits.append(hit)
    return hits, cost


def _refine(answer: dict, hits: list[dict], cfg, settings: Settings) -> dict[int, dict]:
    """Claudes ändrade träffar. Tiderna ur citaten i träffens video; saknas de står tiderna kvar."""
    current = {hit["id"]: hit for hit in hits if hit["status"] == "ny"}
    refined: dict[int, dict] = {}
    for change in answer.get("hits") or []:
        hit = current.get(change.get("id"))
        if hit is None:
            continue
        fields = {key: change[key] for key in ("title", "reason", "grade") if change.get(key) not in (None, "")}
        words = _words(_transcript(settings, hit["vod_id"]))
        times = place(words, str(change.get("start_quote", "")), str(change.get("end_quote", "")),
                      hit["start_s"], hit["end_s"])
        if times:
            start, end = _fit(*times, cfg.clip.min_seconds, cfg.clip.max_seconds, None)
            fields.update(start_s=start, end_s=end, quote=_quote(words, start, end))
        refined[hit["id"]] = fields
    return refined


def _context(profile: Profile, sources: list[dict], hits: list[dict], history: list[dict], message: str) -> str:
    scope = "allt material med transkript" if profile.search_default_scope == "all" else \
        "det senaste materialet med transkript"
    lines = [f"Kampanj: {profile.raw.get('campaign') or profile.name}. Förval när Robin inte säger var: {scope}.",
             "", "Material (nyast först):"]
    lines += [f"- {m['vod_id']} | {m['title']} | {(m['created'] or '')[:10]} | {round((m['duration_s'] or 0) / 60)} "
              f"min | transkript: {'ja' if m['has_transcript'] else 'nej'}" for m in sources]
    open_hits = [h for h in hits if h["status"] == "ny"]
    if open_hits:
        lines += ["", "Träffar som kan ändras:"]
        lines += [f"#{h['id']} {h['vod_id']} {_mmss(h['start_s'])}-{_mmss(h['end_s'])} \"{h['title']}\" "
                  f"betyg {h['grade']}: {h['quote']}" for h in open_hits]
    lines += ["", "Samtalet:"]
    lines += [f"{'Robin' if m['role'] == 'user' else 'Claude'}: {m['text']}" for m in history]
    lines.append(f"Robin (nytt): {message}")
    return "\n".join(lines)


def _found_text(hits: list[dict], sources: list[dict], skipped: list[dict]) -> str:
    vods = len({hit["vod_id"] for hit in hits})
    text = f"Hittade {len(hits)} träffar i {vods} av {len(sources)} videor." if hits else \
        f"Hittade inga träffar i {len(sources)} videor."
    if skipped:
        text += " Hoppade över (inget transkript): " + ", ".join(m["title"] for m in skipped) + "."
    return text


def send(settings: Settings, profile_name: str, *, chat_id: int | None = None, message: str = "",
         confirm: bool = False) -> int:
    """Ett meddelande i chatten, eller Robins ja till en dyr sökning. Sparas först när svaret är klart, så att
    BudgetExceeded inte lämnar något halvt. Returnerar chattens id."""
    clip_log = ClipLog(settings.data_dir / "klipp.db")
    store = Chats(clip_log.db_path)
    profile = load_profile(profile_name, settings.profiles_dir)
    try:
        cfg = pipeline.clipsmith_config(profile, settings)
    except ValueError as error:
        raise ChatError(400, str(error)) from error
    chat = store.get(chat_id) if chat_id is not None else None
    history = chat["messages"] if chat else []
    hits = store.hits(chat_id) if chat_id is not None else []
    kept = [hit for hit in hits if hit["status"] != "ny"]
    sources = material(profile.name, settings, clip_log)
    by_id = {item["vod_id"]: item for item in sources}
    b = brain(settings)

    if confirm:
        pending = history[-1].get("confirm") if history and history[-1]["role"] == "assistant" else None
        if not pending:
            raise ChatError(400, "Det finns ingen sökning att bekräfta.")
        chosen = [by_id[vod] for vod in pending["sources"] if vod in by_id]
        found, cost = _search(b, history[-1].get("query", ""), chosen, cfg, kept, settings)
        reply = {"role": "assistant", "text": _found_text(found, chosen, []), "created": _now(),
                 "sources": [m["vod_id"] for m in chosen], "cost_usd": round(cost, 4), "confirm": None}
        return store.add_turn(chat_id, profile.name, [reply], hits=found)

    user = {"role": "user", "text": message, "created": _now()}
    decision = b.ask(purpose="chatt", model=settings.review_model, system=INTERPRET,
                     content=_context(profile, sources, hits, history, message), schema=INTERPRET_SCHEMA)
    cost = b.last_usd
    reply = {"role": "assistant", "text": str(decision.get("reply") or ""), "created": _now(), "sources": [],
             "cost_usd": 0.0, "confirm": None}
    action = decision.get("action")
    if action == "refine":
        refined = _refine(decision, hits, cfg, settings)
        reply["cost_usd"] = round(cost, 4)
        return store.add_turn(chat_id, profile.name, [user, reply], refined=refined)
    if action != "search":
        reply["cost_usd"] = round(cost, 4)
        return store.add_turn(chat_id, profile.name, [user, reply])

    asked = [by_id[str(vod)] for vod in decision.get("sources") or [] if str(vod) in by_id]  # andras ignoreras
    with_text = [m for m in sources if m["has_transcript"]]
    if asked:
        chosen, skipped = [m for m in asked if m["has_transcript"]], [m for m in asked if not m["has_transcript"]]
    elif profile.search_default_scope == "all":
        chosen, skipped = with_text, [m for m in sources if not m["has_transcript"]]
    else:
        chosen, skipped = with_text[:1], []
    query = str(decision.get("query") or message)
    reply["sources"] = [m["vod_id"] for m in chosen]
    if not chosen:
        reply.update(text=_found_text([], [], skipped) if skipped else "Det finns inget material med transkript "
                     "att leta i.", cost_usd=round(cost, 4))
        return store.add_turn(chat_id, profile.name, [user, reply])
    transcripts = {m["vod_id"]: _transcript(settings, m["vod_id"]) for m in chosen}
    estimate = _estimate(_calls(chosen, transcripts), SEARCH, query, cfg.llm.model_anthropic, settings)
    if estimate > CONFIRM_USD:
        minutes = round(sum(m["duration_s"] or 0 for m in chosen) / 60, 1)
        reply.update(text=f"Sökningen kostar ungefär {estimate:.2f} dollar: {minutes} minuter i {len(chosen)} "
                          "videor. Ska jag köra den?", cost_usd=round(cost, 4), query=query,
                     confirm={"cost_usd": round(estimate, 4), "sources": reply["sources"], "minutes": minutes})
        return store.add_turn(chat_id, profile.name, [user, reply])
    found, search_cost = _search(b, query, chosen, cfg, kept, settings)
    reply.update(text=_found_text(found, chosen, skipped), cost_usd=round(cost + search_cost, 4))
    return store.add_turn(chat_id, profile.name, [user, reply], hits=found)


def public(chat: dict, hits: list[dict], sources: list[dict], scope: str) -> dict:
    titles = {item["vod_id"]: item["title"] for item in sources}
    messages = [{key: value for key, value in m.items() if key != "query"} for m in chat["messages"]]
    return {"id": chat["id"], "profile": chat["profile"], "default_scope": scope, "messages": messages,
            "hits": [{**{key: hit[key] for key in ("id", "vod_id", "start_s", "end_s", "title", "reason", "grade",
                                                    "quote", "status", "clip_id", "problem")},
                      "source_title": titles.get(hit["vod_id"], hit["vod_id"])} for hit in hits]}


# ---------- klipp av träffar ----------


def _shift(transcript: Transcript, offset: float) -> Transcript:
    """Transkriptet med tider från en hämtad del som börjar vid `offset`."""
    return Transcript(transcript.video_id, transcript.language, [
        Segment(s.start - offset, s.end - offset, s.text,
                [Word(w.start - offset, w.end - offset, w.word, w.probability) for w in s.words or []])
        for s in transcript.segments])


def _clip_vod(db, run_id: int, vod_id: str, hits: list[dict], game: str | None, profile: Profile,
              settings: Settings, store: Chats) -> None:
    cfg = pipeline.clipsmith_config(profile, settings)
    folder = settings.work_dir / vod_id
    mp4, offset, part = folder / f"{vod_id}.mp4", 0, None
    transcript = _transcript(settings, vod_id)
    if not mp4.exists():  # bara Twitch: en egen fil utan video stoppas innan körningen startar
        _emit(db, run_id, "download", 5.0, "Hämtar delen av VOD:en")
        offset = max(0, int(min(h["start_s"] for h in hits)) - MARGIN_S)
        end = math.ceil(max(h["end_s"] for h in hits)) + MARGIN_S
        part = download.download_part(vod_id, offset, end, folder / f"del_{offset}_{end}.mp4")
    picks = [PickResult(candidate=CandidateMoment(t_center=(h["start_s"] + h["end_s"]) / 2, score=0.0,
                                                  sources=["chatt"], reasons=[]),
                        pick=ClipPick(include=True, start_offset_s=h["start_s"], end_offset_s=h["end_s"],
                                      title=h["title"], reason=h["reason"], score=(h["grade"] or 0) * 10))
             for h in hits]
    run_dir = settings.out_dir / vod_id / f"run_{run_id}"
    _emit(db, run_id, "clip", 40.0, "Skär klippen")
    try:
        cut = [replace(p, pick=replace(p.pick, start_offset_s=p.pick.start_offset_s - offset,
                                       end_offset_s=p.pick.end_offset_s - offset)) for p in picks]
        pipeline.cut_picks(part or mp4, _shift(transcript, offset) if offset else transcript, cut, run_dir, cfg)
        for pick, done in zip(picks, cut, strict=True):
            pick.webcam_rect = done.webcam_rect
    finally:
        if part:
            part.unlink(missing_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    for path in (run_dir / "picks.json", folder / "picks.json"):  # som en vanlig körning
        save_picks(picks, path)
    _emit(db, run_id, "finish", 90.0, "Lägger på overlay, text och caption och kontrollerar reglerna")
    results = pipeline.finish(vod_id, profile, settings, game, run_id)
    done = 0
    left = {h["id"] for h in hits}
    for result in results:
        index = int(re.match(r"clip_(\d+)", result["file"]).group(1))  # type: ignore[union-attr]
        if not 0 < index <= len(hits):
            continue
        hit = hits[index - 1]
        left.discard(hit["id"])
        if "id" in result:
            store.set_hits([hit["id"]], status="gjord", clip_id=result["id"], problem=None)
            done += 1
        else:
            store.set_hits([hit["id"]], status="ny", problem=f"Inget klipp: {result['skipped']}.")
    if left:
        store.set_hits(sorted(left), status="ny", problem="Klippet blev inte gjort.")
    _harvest_clips(db, run_id, vod_id, cfg)
    _emit(db, run_id, "done", 100.0, f"{done} klipp från chatten.", status=RunStatus.done)


def make_clips(chat_id: int, jobs: list[tuple[int, str, list[int]]], profile_name: str, settings: Settings,
               app=None) -> None:
    """Bakgrundsjobb för POST /campaign/chats/{id}/clips: en körning per video, i tur och ordning."""
    clip_log = ClipLog(settings.data_dir / "klipp.db")
    store = Chats(clip_log.db_path)
    db = get_session()
    try:
        for run_id, vod_id, hit_ids in jobs:
            hits = [hit for hit in store.hits(chat_id) if hit["id"] in hit_ids]
            hits.sort(key=lambda h: h["start_s"])
            try:
                profile = load_profile(profile_name, settings.profiles_dir)
                game = (clip_log.run(run_id) or {}).get("game")
                _clip_vod(db, run_id, vod_id, hits, game, profile, settings, store)
            except BaseException as error:  # SystemExit från kampanjlagret ska också synas i appen
                pipeline._fail(db, run_id, error)
                store.set_hits(hit_ids, status="ny", problem=f"Klippet kunde inte göras: {error}")
                if not isinstance(error, Exception | SystemExit):
                    raise
    finally:
        db.close()
        if app is not None:
            app.state.active_run_id = None

