"""Kampanjbevakning: hämtar nya clipping-kampanjer från ClippingAlpha var 10:e minut, ger dem betyg 0–100
och skickar en notis på Macen när en ny, bra kampanj kommer. Robin lägger själv in kampanjen under Kampanjer.

Källa: ClippingAlphas MCP (POST https://clippingalpha.com/mcp, JSON-RPC) utan inloggning. ClippingAlpha tar
500 anrop per dag och nätverk; vi tar högst 300 per dygn (UTC), räknat i databasen, och högst ett i sekunden.
Betyget följer rapporten "Vad är en bra kampanj" (Jarvis, Klippverktyget, 13 nov 2026).

Formen på en sökträff kommer från market.similar i get_campaign_analytics. Ett riktigt search_campaigns-svar
med träffar gick inte att se när detta byggdes (gränsen var nådd), så alla fält läses med reserv.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from .config import Settings, load_settings

log = logging.getLogger(__name__)

MCP_URL = "https://clippingalpha.com/mcp"
HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
           "User-Agent": "klipp-bevakning/0.1 (low rate, read only)"}
PROTOCOL = "2025-06-18"
BASE = {"board": "content-rewards", "type": "clipping", "platforms": ["tiktok", "youtube"]}
NEWEST = {**BASE, "new_within_hours": 72, "sort": "newest", "limit": 25}
BEST = {**BASE, "sort": "alpha", "limit": 25}

CALL_LIMIT = 300  # per dygn (UTC); ClippingAlpha tar 500 utan inloggning
MIN_GAP_S = 1.0  # högst ett anrop i sekunden
INTERVAL_S = 600  # var 10:e minut
STEP_S = 60  # tråden sover högst så länge innan den räknar om väntan
BEST_EVERY_S = 3600  # sökningen på Alpha-betyg en gång i timmen
DETAIL_MAX_AGE_S = 6 * 3600
DETAILS_PER_RUN = 10  # högst 20 anrop per körning för detaljer
TOP = 25
SHOW_S = 72 * 3600  # en kampanj som inte syns i någon sökning på 72 timmar visas inte längre
REFRESH_GAP_S = 120
NOTIFY_SCORE, NOTIFY_AGE_H = 70, 48
DEFAULT_MEDIAN_VIEWS = 1260  # medianklippet för ett nytt konto, enligt rapporten
POSTS_FOR_MEDIAN = 20
DEFAULT_CPM = 1.05
MAX_MIN_VIEWS = 10_000

BLOCK_S = 3600  # efter ClippingAlphas -32001: försök igen om en timme (när deras dygn börjar är okänt)
STOCKHOLM = ZoneInfo("Europe/Stockholm")
OWN_LIMIT_TEXT = f"Dagens gräns på {CALL_LIMIT} anrop till ClippingAlpha är nådd. Bevakningen fortsätter i morgon."
REF = re.compile(r"[a-z0-9][a-z0-9-]{0,99}/[a-z0-9][a-z0-9-]{0,99}")

FACE = re.compile(r"\bface\b|facecam of you|your voice|film yourself|on camera|\bugc\b", re.IGNORECASE)
US = re.compile(r"\bUSA?\b")  # versaler, så att "us" och "status" inte räknas
RICH = re.compile(r"united states|tier ?1", re.IGNORECASE)
LANGUAGES = ("spanish", "german", "french", "portuguese", "italian", "arabic", "hindi", "turkish", "polish",
             "dutch", "russian", "japanese", "korean", "chinese", "mandarin", "indonesian", "vietnamese", "thai",
             "swedish", "norwegian", "danish", "finnish", "romanian", "czech", "hungarian", "greek", "hebrew",
             "tagalog", "filipino", "urdu", "bengali", "malay")
OTHER_LANGUAGE = re.compile(rf"\b(?:in|language:?)\s+(?:{'|'.join(LANGUAGES)})\b", re.IGNORECASE)

_session: dict[str, str | None] = {"id": None}  # MCP-sessionen återanvänds, så att initialize inte kostar varje gång
_last_call = [0.0]
_run_lock = threading.Lock()  # tråden och Kontrollera nu kör inte samtidigt
_thread: list[threading.Thread] = []


class ScoutError(Exception):
    """Fel med svensk text som visas i webben."""


class LimitError(ScoutError):
    """En gräns för anrop är nådd. Stoppar hela körningen, inte bara en kampanj."""


def _limit_text(until: datetime) -> str:
    return (f"ClippingAlpha tar inte emot fler anrop just nu. Bevakningen försöker igen kl. "
            f"{until.astimezone(STOCKHOLM):%H:%M}.")


class _SessionLost(Exception):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _time(value) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def http_client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


def reserve(now: datetime) -> int:
    """Anrop som resten av dygnet behöver för sökningarna: en var 10:e minut och en i timmen."""
    left = (datetime(now.year, now.month, now.day, tzinfo=UTC) + timedelta(days=1) - now).total_seconds()
    return math.ceil(left / INTERVAL_S) + math.ceil(left / BEST_EVERY_S)


def _first(*values):
    return next((value for value in values if value is not None), None)


def _safe(url) -> str | None:
    """Bara länkar till ClippingAlpha öppnas, i webben och från notisen."""
    return url if isinstance(url, str) and url.startswith("https://clippingalpha.com/") else None


# ---------- fakta och betyg ----------


def facts(data: dict) -> dict:
    """Det betyget och webben behöver, ur sökträffen och (om de finns) get_campaign och analytics."""
    s, c, a = data.get("search") or {}, data.get("campaign") or {}, data.get("analytics")
    a_campaign = (a or {}).get("campaign") or {}
    joins = c.get("whereToJoin") or []
    join = next((w for w in joins if w.get("boardSlug") == "content-rewards"), joins[0] if joins else None) or {}
    pay, runway = (a or {}).get("pay"), (a or {}).get("runway") or {}
    drain = c.get("drain") or {}
    rules = ((c.get("rules") or {}).get("checklist")) or []
    # Sökträffen är högst 10 minuter gammal, detaljerna upp till 6 timmar: sökträffen går före.
    left = _first(s.get("budgetLeftUsd"), join.get("budgetLeftUsd"), runway.get("budgetLeftUsd"))
    total = _first(s.get("budgetTotalUsd"), join.get("budgetTotalUsd"), runway.get("budgetTotalUsd"))
    rate = _first(join.get("ratePer1kUsd"), (pay or {}).get("ratePer1kUsd"), s.get("ratePer1kUsd"))
    fee = _first(join.get("boardFee"), (pay or {}).get("boardFeePct"), 0.1)
    return {
        "title": _first(c.get("title"), s.get("title"), a_campaign.get("title")) or "",
        "brand": _first(c.get("brand"), s.get("brand"), a_campaign.get("brand")) or "",
        "state": _first(s.get("state"), c.get("state"), a_campaign.get("state")),
        "type": _first(a_campaign.get("type"), s.get("campaignType"), s.get("type")),
        "platforms": _first(c.get("platforms"), s.get("platforms")) or [],
        "niche": _first(c.get("niche"), s.get("niche")),
        "about": c.get("about") or s.get("about") or "",
        "rules": [(str(r.get("kind") or ""), str(r.get("text") or "")) for r in rules if isinstance(r, dict)],
        "launched": _time(_first(c.get("launchedAt"), s.get("launchedAt"), s.get("newAt"))),
        "rate": rate, "fee": fee,
        "net": _first((c.get("alpha") or {}).get("netPer1kUsd"), s.get("netPer1kUsd")),
        "left": left, "total": total,
        "drain": _time(_first(drain.get("p50"), (runway.get("forecast") or {}).get("p50"), s.get("drainP50"))),
        "stalled": _first(s.get("stalled"), drain.get("stalled"), c.get("stalled"), runway.get("stalled")) is True,
        "open_join": _first(join.get("openJoin"), s.get("openJoin")), "gate": join.get("gateType"),
        "verified": c.get("organizerVerified"),
        "adjustments": None if a is None else ((a.get("alpha") or {}).get("adjustments") or []),
        "fill": next((v.get("verdict") for v in ((a or {}).get("alpha") or {}).get("verdicts") or []
                      if v.get("key") == "fill"), None),
        "pay": pay,
        "pay_model": _first(join.get("payModel"), s.get("payModel")),
        "members_only": _first(c.get("membersOnly"), a_campaign.get("membersOnly"), s.get("membersOnly")),
        "election": _first(c.get("election"), a_campaign.get("election"), s.get("election")),
        "audience": " ".join(str(v) for v in (
            *(c.get(k) for k in ("audience", "audienceLabel", "audienceNote", "region")),
            *(a_campaign.get(k) for k in ("audienceLabel", "audienceNote", "region")),
            *(s.get(k) for k in ("audience", "audienceLabel", "region"))) if v),
        "page_url": _first(c.get("pageUrl"), a_campaign.get("pageUrl"), s.get("pageUrl")),
        "join_url": _first(join.get("joinUrl"), a_campaign.get("joinUrl"), s.get("joinUrl")),
    }


def min_views(f: dict) -> int | None:
    pay = f["pay"] or {}
    if pay.get("minViews"):
        return int(pay["minViews"])
    if pay.get("minPayoutUsd") and f["rate"]:
        return math.ceil(1000 * pay["minPayoutUsd"] / f["rate"])
    return None


def _days(moment: datetime | None, now: datetime) -> float | None:
    return None if moment is None else (moment - now).total_seconds() / 86400


def _part(key: str, label: str, points: float, most: int, fact: str) -> dict:
    return {"key": key, "label": label, "points": round(points, 1), "max": most, "fact": fact}


def _unknown(key: str, label: str, most: int) -> dict:
    return _part(key, label, most / 2, most, "Okänt")


def _number(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


def evaluate(data: dict, median: float, cpm_median: float, now: datetime) -> tuple[int, list[dict], str | None]:
    """Betyg 0–100, delarna med svensk förklaring, och orsaken om kampanjen sorteras bort."""
    f = facts(data)
    texts = " ".join([f["about"], *(text for _, text in f["rules"])])
    drain_days = _days(f["drain"], now)
    views = min_views(f)
    parts = []

    # 1. Färskhet och konkurrens. Antal klippare finns inte i ClippingAlpha.
    if f["launched"] is None:
        parts.append(_unknown("fresh", "Färskhet och konkurrens", 25))
    else:
        age = (now - f["launched"]).total_seconds() / 3600
        if age < 48:
            points = 25.0
        elif age <= 21 * 24:
            points = 25 - 20 * (age - 48) / (21 * 24 - 48)
        elif age <= 30 * 24:
            points = 5 * (30 * 24 - age) / (9 * 24)
        else:
            points = 0.0
        minus = {"Fast": 10, "Fair": 5}.get(f["fill"], 0)
        if drain_days is not None:
            minus = max(minus, 10 if drain_days < 3 else 5 if drain_days < 7 else 0)
        fact = f"{age / 24:.0f} dagar sedan start" if age >= 48 else f"{age:.0f} timmar sedan start"
        if minus:
            fact += ", budgeten går åt snabbt"
        parts.append(_part("fresh", "Färskhet och konkurrens", max(0.0, points - minus), 25, fact))

    # 2. Chans att nå minimum.
    label = "Chans att nå minimum"
    if f["pay"] is None or (views is None and (f["pay"].get("minPayoutUsd") and not f["rate"])):
        parts.append(_unknown("minimum", label, 20))
    elif views is None:
        parts.append(_part("minimum", label, 20, 20, "Ingen minsta utbetalning"))
    else:
        ratio = views / max(median, 1)
        points = 20 if ratio <= 1 else 0 if ratio >= 5 else 20 * (1 - math.log(ratio) / math.log(5))
        parts.append(_part("minimum", label, points, 20,
                           f"{_number(views)} visningar för betalning, din median är {_number(median)}"))

    # 3. Källan.
    source = f"{f['title']} {texts}"
    twitch, stream = "twitch" in source.lower(), bool(re.search(r"\bstream", source, re.IGNORECASE))
    other = bool(OTHER_LANGUAGE.search(texts))
    points = 8 * twitch + 4 * stream + 3 * (not other)
    if f["niche"] in ("streams", "gaming"):
        points = max(points, 4)
    said = [word for word, yes in (("Twitch", twitch), ("stream", stream), ("engelska", not other)) if yes]
    parts.append(_part("source", "Källan", points, 15, ", ".join(said) or "Varken Twitch eller stream"))

    # 4. Budgeten räcker.
    label = "Budgeten räcker"
    if drain_days is not None:
        parts.append(_part("budget", label, 15 * min(max(drain_days, 0), 7) / 7, 15,
                           f"Räcker cirka {max(drain_days, 0):.0f} dagar till"))
    elif f["left"] is not None:
        parts.append(_part("budget", label, 10 * min(f["left"], 1000) / 1000, 15,
                           f"Ingen prognos, {_number(f['left'])} dollar kvar"))
    else:
        parts.append(_unknown("budget", label, 15))

    # 5. Betalning efter avgift mot marknadens median.
    net = _first(f["net"], f["rate"] * (1 - f["fee"]) if f["rate"] is not None else None)
    if net is None:
        parts.append(_unknown("pay", "Betalning", 10))
    else:
        ratio = min(net / cpm_median, 3)
        parts.append(_part("pay", "Betalning", min(10.0, 5 * ratio), 10,
                           f"{net:.2f} dollar per 1 000 efter avgift, marknaden {cpm_median:.2f}"))

    # 6. Bevis på utbetalning.
    if f["verified"] is None and f["adjustments"] is None:
        parts.append(_unknown("proof", "Bevis på utbetalning", 10))
    else:
        paid = any(x.get("key") == "payout" and re.search(r"24 hours|7 days", str(x.get("label")))
                   for x in f["adjustments"] or [])
        points = 4 * bool(f["verified"]) + 4 * paid + 2 * (not f["stalled"])
        said = [text for text, yes in (("verifierad organisatör", f["verified"]), ("betalat ut nyligen", paid))
                if yes]
        parts.append(_part("proof", "Bevis på utbetalning", points, 10, ", ".join(said) or "Ingen utbetalning syns"))

    # 7. Krångel.
    if f["open_join"] is None:
        parts.append(_unknown("hassle", "Krångel", 5))
    else:
        captions = sum(kind in ("caption", "disclosure") for kind, _ in f["rules"])
        is_open = bool(f["open_join"]) and f["gate"] in (None, "none")
        points = max(0, (5 if is_open else 0) - max(0, captions - 3))
        parts.append(_part("hassle", "Krångel", points, 5,
                           f"{'Fri anslutning' if is_open else 'Ansökan krävs'}, {captions} krav på caption"))

    total = round(sum(part["points"] for part in parts))
    where = " ".join([f["audience"], *(text for _, text in f["rules"])])  # inte about: "US Open" är inget krav
    used = 1 - f["left"] / f["total"] if f["left"] is not None and f["total"] else None
    platforms = {str(p).lower() for p in f["platforms"]}
    reasons = [
        (f["state"] not in (None, "live"), f"Kampanjen är inte igång ({f['state']})."),
        (f["pay_model"] == "pot_split", "Pott som delas mellan klippare, inte betalning per visning"),
        (f["stalled"], "Kampanjen står still på plattformen."),
        (used is not None and used >= 0.95, "Minst 95 % av budgeten är använd."),
        (f["type"] not in (None, "clipping"), f"Inte en clipping-kampanj ({f['type']})."),
        (bool(platforms) and not platforms & {"tiktok", "youtube"}, "Varken TikTok eller YouTube."),
        (bool(FACE.search(texts)), "Kräver ansikte, röst eller egen filmning."),
        (f["members_only"] is True, "Bara för medlemmar."),
        (f["election"] is True, "Valkampanj."),
        (bool(US.search(where) or RICH.search(where)),
         "Kräver publik från USA eller andra länder."),
        (other, "Kräver ett annat språk än engelska."),
        (views is not None and views > MAX_MIN_VIEWS,
         f"Kräver {_number(views or 0)} visningar för betalning (gränsen är {_number(MAX_MIN_VIEWS)})."),
    ]
    return total, parts, next((reason for hit, reason in reasons if hit), None)


# ---------- notisen ----------


def notify(title: str, score: int, page_url: str | None) -> None:
    notify_mac(f"Ny kampanj: {title}, {score} poäng", _safe(page_url))


def notify_mac(message: str, url: str | None = None) -> None:
    """Notis på Macen som öppnar url vid klick. Argument som lista, inget skal. Används också av schemat."""
    if sys.platform != "darwin":
        return
    tool = shutil.which("terminal-notifier")
    if tool:
        args = [tool, "-title", "Klipp", "-message", message]
        parts = urlparse(url or "")
        if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname == "localhost"):  # aldrig file://
            args += ["-open", url]
    else:  # texten är ett argument till skriptet, så citattecken i titeln blir aldrig AppleScript-kod
        args = ["osascript", "-e", "on run argv", "-e", 'display notification (item 1 of argv) with title "Klipp"',
                "-e", "end run", message]
    try:
        subprocess.run(args, check=False, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        log.warning("Notisen gick inte att visa: %s", error)


# ---------- bevakningen ----------


class Scout:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db_path = settings.data_dir / "klipp.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS scout_campaigns (
                ref TEXT PRIMARY KEY, data TEXT, score INTEGER, parts TEXT, excluded TEXT, first_seen TEXT,
                updated TEXT, notified_at TEXT, dismissed INTEGER DEFAULT 0)""")
            db.execute("CREATE TABLE IF NOT EXISTS scout_calls (day TEXT PRIMARY KEY, calls INTEGER)")
            db.execute("CREATE TABLE IF NOT EXISTS scout_state (key TEXT PRIMARY KEY, value TEXT)")

    @contextlib.contextmanager
    def _db(self):
        """En anslutning som sparar när blocket är klart och sedan stängs."""
        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    # ---------- läge och dygnsgräns ----------

    def _get(self, key: str) -> str | None:
        with self._db() as db:
            row = db.execute("SELECT value FROM scout_state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _put(self, **values: str | None) -> None:
        with self._db() as db:
            db.executemany("INSERT OR REPLACE INTO scout_state (key, value) VALUES (?, ?)", values.items())

    def calls_today(self) -> int:
        with self._db() as db:
            row = db.execute("SELECT calls FROM scout_calls WHERE day = ?", (_now().date().isoformat(),)).fetchone()
        return row[0] if row else 0

    def _count(self, n: int = 1) -> None:
        """Räkna ett anrop innan det görs. Över gränsen blir det inget anrop."""
        with self._db() as db:  # en sats, så att två trådar aldrig tillsammans går över gränsen
            counted = n <= CALL_LIMIT and db.execute(
                "INSERT INTO scout_calls (day, calls) VALUES (?, ?) ON CONFLICT(day) DO UPDATE "
                "SET calls = calls + excluded.calls WHERE calls + excluded.calls <= ?",
                (_now().date().isoformat(), n, CALL_LIMIT)).rowcount == 1
        if not counted:
            raise LimitError(OWN_LIMIT_TEXT)

    def _blocked_until(self) -> datetime | None:
        until = _time(self._get("blocked_until"))
        return until if until and until > _now() else None

    def median(self) -> tuple[float, str]:
        """Robins medianvisningar per inlägg, eller rapportens 1 260 tills det finns 20 inlägg med visningar."""
        try:
            with self._db() as db:
                views = [row[0] for row in db.execute("SELECT views FROM posts WHERE views IS NOT NULL")]
        except sqlite3.OperationalError:  # inga inlägg än
            views = []
        if len(views) >= POSTS_FOR_MEDIAN:
            return statistics.median(views), "dina inlägg"
        return DEFAULT_MEDIAN_VIEWS, "standard"

    # ---------- MCP ----------

    def _post(self, http: httpx.Client, message: dict) -> dict | None:
        until = self._blocked_until()
        if until:  # ClippingAlpha har sagt nej nyss: inget anrop, inte heller från Kontrollera nu
            raise LimitError(_limit_text(until))
        self._count()
        wait = MIN_GAP_S - (time.monotonic() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        headers = dict(HEADERS)
        if _session["id"]:
            headers.update({"Mcp-Session-Id": _session["id"], "MCP-Protocol-Version": PROTOCOL})
        try:
            response = http.post(MCP_URL, json=message, headers=headers)
        except httpx.HTTPError as error:
            raise ScoutError("ClippingAlpha svarade inte. Bevakningen försöker igen om 10 minuter.") from error
        finally:
            _last_call[0] = time.monotonic()
        body = _body(response, message.get("id"))
        error = (body or {}).get("error")
        if isinstance(error, dict) and error.get("code") == -32001:
            until = _now() + timedelta(seconds=BLOCK_S)  # ett nytt nej skjuter fram spärren en timme till
            self._put(blocked_until=_iso(until))
            raise LimitError(_limit_text(until))
        if response.status_code == 404 and _session["id"]:
            raise _SessionLost
        if response.status_code >= 400:
            raise ScoutError(f"ClippingAlpha svarade {response.status_code}. Bevakningen försöker igen om 10 minuter.")
        if isinstance(error, dict):
            raise ScoutError(f"ClippingAlpha gav ett fel: {str(error.get('message'))[:120]}")
        if "mcp-session-id" in response.headers:
            _session["id"] = response.headers["mcp-session-id"]
        return body

    def _init(self, http: httpx.Client) -> None:
        _session["id"] = None
        self._post(http, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "klipp-bevakning", "version": "0.1"}}})
        _session["id"] = _session["id"] or ""  # "" betyder klar men utan sessions-id
        self._post(http, {"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _tool(self, http: httpx.Client, name: str, arguments: dict) -> dict:
        for attempt in (1, 2):
            if _session["id"] is None:
                self._init(http)
            try:
                body = self._post(http, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                         "params": {"name": name, "arguments": arguments}})
            except _SessionLost:
                _session["id"] = None  # sessionen gick ut: starta en ny en gång
                if attempt == 2:
                    raise ScoutError("ClippingAlpha tappade sessionen. Bevakningen försöker igen om 10 minuter.") \
                        from None
                continue
            result = (body or {}).get("result") or {}
            if result.get("isError"):
                raise ScoutError(f"ClippingAlpha kunde inte svara på {name}.")
            return result.get("structuredContent") or {}
        raise AssertionError("nås inte")

    # ---------- en körning ----------

    def run_once(self) -> None:
        """En kontroll. Fel sparas som senaste fel; inget kastas vidare, så att tråden aldrig dör."""
        if not _run_lock.acquire(blocking=False):
            return
        try:
            self._put(last_run=_iso(_now()))
            before = self.calls_today()
            self._run()
            # Bara en lyckad körning: en avbruten körning säger inget om vad en hel körning kostar.
            self._put(error=None, updated_at=_iso(_now()), last_cost=str(max(self.calls_today() - before, 1)))
        except ScoutError as error:
            log.warning("Kampanjbevakningen: %s", error)
            self._put(error=str(error))
        except Exception as error:
            log.exception("Kampanjbevakningen fick ett fel")
            self._put(error=f"Bevakningen fick ett fel ({type(error).__name__}). Den försöker igen om 10 minuter.")
        finally:
            _run_lock.release()

    def _run(self) -> None:
        now = _now()
        with http_client() as http:
            items = self._search(http, NEWEST)
            last_best = _time(self._get("last_best"))
            if last_best is None or (now - last_best).total_seconds() >= BEST_EVERY_S:
                items += self._search(http, BEST)
                self._put(last_best=_iso(now))
            self._save_items(items, now)
            self._details(http, items, now)
        self._rescore(now)
        self._notify(now)

    def _search(self, http: httpx.Client, arguments: dict) -> list[dict]:
        content = self._tool(http, "search_campaigns", arguments)
        cpm = (content.get("cpm") or {}).get("medianUsd")
        if isinstance(cpm, int | float) and cpm > 0:
            self._put(cpm_median=str(cpm))
        return [item for item in content.get("campaigns") or [] if isinstance(item, dict) and _ref(item)]

    def _save_items(self, items: list[dict], now: datetime) -> None:
        with self._db() as db:
            for item in items:
                ref = _ref(item)
                row = db.execute("SELECT data FROM scout_campaigns WHERE ref = ?", (ref,)).fetchone()
                data = {**(json.loads(row[0]) if row else {}), "search": item}
                if row:
                    db.execute("UPDATE scout_campaigns SET data = ?, updated = ? WHERE ref = ?",
                               (json.dumps(data), _iso(now), ref))
                else:
                    db.execute("INSERT INTO scout_campaigns (ref, data, first_seen, updated) VALUES (?, ?, ?, ?)",
                               (ref, json.dumps(data), _iso(now), _iso(now)))

    def _details(self, http: httpx.Client, items: list[dict], now: datetime) -> None:
        """get_campaign och analytics för nya kampanjer, och för de bästa när detaljerna är äldre än 6 timmar.
        Bara så länge resten av dygnets sökningar fortfarande ryms."""
        with self._db() as db:
            rows = {row["ref"]: row for row in db.execute("SELECT * FROM scout_campaigns")}
        cpm = float(self._get("cpm_median") or DEFAULT_CPM)
        median = self.median()[0]
        new = [ref for ref in dict.fromkeys(_ref(item) for item in items)
               if "detail_at" not in json.loads(rows[ref]["data"])
               and evaluate(json.loads(rows[ref]["data"]), median, cpm, now)[2] is None]
        best = [row["ref"] for row in sorted(rows.values(), key=lambda r: -(r["score"] or 0))
                if row["excluded"] is None and row["score"] is not None][:TOP]
        old = [ref for ref in best if ref not in new and (now - (_time(json.loads(rows[ref]["data"]).get(
            "detail_at")) or now)).total_seconds() > DETAIL_MAX_AGE_S]
        for ref in (new + old)[:DETAILS_PER_RUN]:
            if self.calls_today() + 2 > CALL_LIMIT - reserve(now):
                return
            try:
                fresh = {"campaign": self._tool(http, "get_campaign", {"campaign": ref}).get("campaign") or {},
                         "analytics": self._tool(http, "get_campaign_analytics", {"campaign": ref})}
            except ScoutError as error:
                if isinstance(error, LimitError):
                    raise
                # En trasig kampanj får inte stoppa de andra. Den får betyg från sökträffen och provas om 6 timmar.
                log.warning("Kampanjbevakningen: detaljerna för %s: %s", ref, error)
                fresh = {}
            with self._db() as db:
                data = json.loads(db.execute("SELECT data FROM scout_campaigns WHERE ref = ?", (ref,)).fetchone()[0])
                data.update(fresh, detail_at=_iso(now))
                db.execute("UPDATE scout_campaigns SET data = ? WHERE ref = ?", (json.dumps(data), ref))

    def _rescore(self, now: datetime) -> None:
        median, cpm = self.median()[0], float(self._get("cpm_median") or DEFAULT_CPM)
        with self._db() as db:
            for row in db.execute("SELECT ref, data FROM scout_campaigns").fetchall():
                score, parts, excluded = evaluate(json.loads(row["data"]), median, cpm, now)
                db.execute("UPDATE scout_campaigns SET score = ?, parts = ?, excluded = ? WHERE ref = ?",
                           (score, json.dumps(parts), excluded, row["ref"]))

    def _notify(self, now: datetime) -> None:
        if sys.platform != "darwin":
            return
        with self._db() as db:
            rows = db.execute("SELECT * FROM scout_campaigns WHERE score >= ? AND excluded IS NULL "
                              "AND notified_at IS NULL AND dismissed = 0", (NOTIFY_SCORE,)).fetchall()
        for row in rows:
            f = facts(json.loads(row["data"]))
            if f["launched"] is None or (now - f["launched"]).total_seconds() >= NOTIFY_AGE_H * 3600:
                continue
            with self._db() as db:  # först sparat, så att ett fel i notisen aldrig ger samma notis igen
                db.execute("UPDATE scout_campaigns SET notified_at = ? WHERE ref = ?", (_iso(now), row["ref"]))
            notify(f["title"], row["score"], f["page_url"])

    # ---------- för webben ----------

    def overview(self) -> dict:
        now = _now()
        median, source = self.median()
        with self._db() as db:
            rows = db.execute("SELECT * FROM scout_campaigns WHERE dismissed = 0 AND score IS NOT NULL "
                              "AND updated >= ? ORDER BY score DESC, first_seen DESC",
                              (_iso(now - timedelta(seconds=SHOW_S)),)).fetchall()
        views = [_view(row, now) for row in rows]
        return {
            "enabled": os.environ.get("KLIPP_SCOUT") != "0",
            "updated_at": self._get("updated_at"), "error": self._get("error"),
            "calls_today": self.calls_today(), "call_limit": CALL_LIMIT,
            "median_views": median, "median_source": source,
            "campaigns": [view for view in views if view["excluded"] is None],
            "excluded": [view for view in views if view["excluded"] is not None][:50],
        }

    def wait_s(self) -> float:
        """Sekunder till nästa körning: minst 10 minuter, och längre när anropen annars tar slut före midnatt UTC
        (till exempel när sessionen har gått ut och varje körning kostar fyra anrop)."""
        now = _now()
        left_s = (datetime(now.year, now.month, now.day, tzinfo=UTC) + timedelta(days=1) - now).total_seconds()
        runs = (CALL_LIMIT - self.calls_today()) / float(self._get("last_cost") or 1)
        interval = left_s if runs < 1 else max(INTERVAL_S, left_s / runs)
        last = _time(self._get("last_run"))
        until = self._blocked_until()
        return max(0.0, interval - ((now - last).total_seconds() if last else interval),
                   (until - now).total_seconds() if until else 0.0)

    def ran_recently(self) -> bool:
        last = _time(self._get("last_run"))
        return last is not None and (_now() - last).total_seconds() < REFRESH_GAP_S

    def dismiss(self, ref: str, dismissed: bool) -> bool:
        with self._db() as db:
            return db.execute("UPDATE scout_campaigns SET dismissed = ? WHERE ref = ?",
                              (int(dismissed), ref)).rowcount > 0


def _ref(item: dict) -> str | None:
    ref = item.get("ref") or f"{item.get('brandSlug')}/{item.get('seriesSlug')}"
    return ref if REF.fullmatch(str(ref)) else None


def _body(response: httpx.Response, wanted_id) -> dict | None:
    """JSON eller text/event-stream med ett data-meddelande per rad."""
    text = response.text
    if not text.strip():
        return None
    try:
        if "text/event-stream" in response.headers.get("content-type", ""):
            found = None
            for line in text.splitlines():
                if line.startswith("data:"):
                    message = json.loads(line[5:].strip())
                    if message.get("id") == wanted_id or "error" in message:
                        found = message
            return found
        body = json.loads(text)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _view(row: sqlite3.Row, now: datetime) -> dict:
    f = facts(json.loads(row["data"]))
    age = None if f["launched"] is None else round((now - f["launched"]).total_seconds() / 3600, 1)
    drain = _days(f["drain"], now)
    source = f"{f['title']} {f['about']} {' '.join(text for _, text in f['rules'])}".lower()
    return {
        "ref": row["ref"], "title": f["title"], "brand": f["brand"], "score": row["score"],
        "parts": json.loads(row["parts"]), "excluded": row["excluded"], "age_hours": age,
        "rate_usd": f["rate"],
        "net_rate_usd": _first(f["net"], round(f["rate"] * (1 - f["fee"]), 2) if f["rate"] is not None else None),
        "budget_left_usd": f["left"], "budget_total_usd": f["total"], "min_views": min_views(f),
        "max_payout_usd": (f["pay"] or {}).get("maxPayoutUsd"),
        "drain_days": None if drain is None else round(max(drain, 0), 1),
        "platforms": f["platforms"], "mentions_twitch": "twitch" in source, "open_join": f["open_join"],
        "page_url": _safe(f["page_url"]), "join_url": _safe(f["join_url"]), "first_seen": row["first_seen"],
        "is_new": age is not None and age < NOTIFY_AGE_H,
    }


# ---------- tråden ----------


def _loop(settings: Settings) -> None:
    while True:
        try:
            # Väntan räknas om mot väggklockan efter varje kort steg, också vid start. Den monotona klockan står
            # still när Macen sover, och efter midnatt UTC finns nya anrop.
            wait = Scout(settings).wait_s()
            if wait > 0:
                time.sleep(min(wait, STEP_S))
                continue
            Scout(settings).run_once()
        except Exception:  # run_once fångar redan allt; det här är bara så att tråden aldrig dör
            log.exception("Kampanjbevakningen")
            time.sleep(INTERVAL_S)


def start(settings: Settings | None = None) -> threading.Thread | None:
    """Starta bevakningen med servern. KLIPP_SCOUT=0 stänger av den. Startar aldrig i tester."""
    if os.environ.get("KLIPP_SCOUT") == "0" or "PYTEST_CURRENT_TEST" in os.environ or _thread:
        return None
    thread = threading.Thread(target=_loop, args=(settings or load_settings(),), name="kampanjbevakning", daemon=True)
    thread.start()
    _thread.append(thread)
    return thread
