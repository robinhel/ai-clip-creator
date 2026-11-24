"""Kampanjbevakning: hämtar nya clipping-kampanjer från ClippingAlpha var 10:e minut, ger dem betyg 0–100
och skickar en notis på Macen när en ny, bra kampanj kommer. Robin lägger själv in kampanjen under Kampanjer.

Källa: ClippingAlphas MCP (POST https://clippingalpha.com/mcp, JSON-RPC) utan inloggning. ClippingAlpha tar
500 anrop per dag och nätverk; vi tar högst 300 per dygn (UTC), räknat i databasen, och högst ett i sekunden.
Betyget räknas i campaign/scout_score.py.

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

from . import preferences
from .config import Settings, load_settings
from .content_views import (USER_AGENT, campaign_page, channel_from_assets, go_link, resolve_join,
                            twitch_clip_views, twitch_login_from_vod, vod_from_assets)
from .scout_score import (_days, _first, _time, country_note, country_rates, evaluate, excluded_for_country, facts,
                          min_views, pay_number)

log = logging.getLogger(__name__)

MCP_URL = "https://clippingalpha.com/mcp"
HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
           "User-Agent": USER_AGENT}
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
DETAILS_PER_RUN = 10  # högst 40 anrop per körning för detaljer (get_campaign, analytics, get_assets och /go/)
JOINS_PER_RUN = 10  # /go/ för kampanjer som syns men inte fick det i detaljerna: högst 10 anrop per körning
# _find_pages (/go/) slutar 20 anrop före CALL_LIMIT - reserve(now), så att de anropen blir kvar åt _details.
# Hur många nya kampanjer de räcker till är inte lovat: _details hämtar också om de 25 bästa var 6:e timme.
JOIN_SLACK = 20
TOP = 25
SHOW_S = 72 * 3600  # en kampanj som inte syns i någon sökning på 72 timmar visas inte längre
REFRESH_GAP_S = 120
NOTIFY_SCORE, NOTIFY_AGE_H = 65, 48
DEFAULT_MEDIAN_VIEWS = 1260  # medianklippet för ett nytt konto, enligt rapporten
POSTS_FOR_MEDIAN = 20
DEFAULT_CPM = 1.05

TWITCH_MAX_AGE_S = 24 * 3600
# Kampanjens klipp på Content Rewards för kampanjer som syns: högst 25 sidor per körning, 1 sekund mellan sidorna
CLIP_VIEWS_PER_RUN, CLIP_PAGE_GAP_S = 25, 1.0

BLOCK_S = 3600  # efter ClippingAlphas -32001: försök igen om en timme (när deras dygn börjar är okänt)
STOCKHOLM = ZoneInfo("Europe/Stockholm")
OWN_LIMIT_TEXT = f"Today's limit of {CALL_LIMIT} calls to ClippingAlpha is reached. Scouting continues tomorrow."
REF = re.compile(r"[a-z0-9][a-z0-9-]{0,99}/[a-z0-9][a-z0-9-]{0,99}")

_session: dict[str, str | None] = {"id": None}  # MCP-sessionen återanvänds, så att initialize inte kostar varje gång
_last_call = [0.0]
_last_page = [0.0]  # senaste sidan från Content Rewards, i _details eller _refresh_clip_views
_run_lock = threading.Lock()  # tråden och Kontrollera nu kör inte samtidigt
_thread: list[threading.Thread] = []


class ScoutError(Exception):
    """Fel med svensk text som visas i webben."""


class LimitError(ScoutError):
    """En gräns för anrop är nådd. Stoppar hela körningen, inte bara en kampanj."""


def _limit_text(until: datetime) -> str:
    return (f"ClippingAlpha does not take more calls right now. Scouting tries again at "
            f"{until.astimezone(STOCKHOLM):%H:%M}.")


class _SessionLost(Exception):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def http_client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


def _wait_gap() -> None:
    """Högst ett anrop i sekunden till ClippingAlpha."""
    wait = MIN_GAP_S - (time.monotonic() - _last_call[0])
    if wait > 0:
        time.sleep(wait)


def reserve(now: datetime) -> int:
    """Anrop som resten av dygnet behöver för sökningarna: en var 10:e minut och en i timmen."""
    left = (datetime(now.year, now.month, now.day, tzinfo=UTC) + timedelta(days=1) - now).total_seconds()
    return math.ceil(left / INTERVAL_S) + math.ceil(left / BEST_EVERY_S)


def _safe(url) -> str | None:
    """Bara länkar till ClippingAlpha öppnas, i webben och från notisen."""
    return url if isinstance(url, str) and url.startswith("https://clippingalpha.com/") else None


# ---------- notisen ----------


def notify(title: str, score: int, page_url: str | None) -> None:
    notify_mac(f"New campaign: {title}, {score} points", _safe(page_url))


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
            return statistics.median(views), "your posts"
        return DEFAULT_MEDIAN_VIEWS, "standard"

    # ---------- MCP ----------

    def _post(self, http: httpx.Client, message: dict) -> dict | None:
        until = self._blocked_until()
        if until:  # ClippingAlpha har sagt nej nyss: inget anrop, inte heller från Kontrollera nu
            raise LimitError(_limit_text(until))
        self._count()
        _wait_gap()
        headers = dict(HEADERS)
        if _session["id"]:
            headers.update({"Mcp-Session-Id": _session["id"], "MCP-Protocol-Version": PROTOCOL})
        try:
            response = http.post(MCP_URL, json=message, headers=headers)
        except httpx.HTTPError as error:
            raise ScoutError("ClippingAlpha did not answer. Scouting tries again in 10 minutes.") from error
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
            raise ScoutError(f"ClippingAlpha answered {response.status_code}. Scouting tries again in 10 minutes.")
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
                    raise ScoutError("ClippingAlpha lost the session. Scouting tries again in 10 minutes.") \
                        from None
                continue
            result = (body or {}).get("result") or {}
            if result.get("isError"):
                raise ScoutError(f"ClippingAlpha could not answer {name}.")
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
            self._put(error=f"Scouting hit an error ({type(error).__name__}). It tries again in 10 minutes.")
        finally:
            _run_lock.release()

    def _run(self) -> None:
        now = _now()
        try:
            with http_client() as http:
                items = self._search(http, NEWEST)
                last_best = _time(self._get("last_best"))
                if last_best is None or (now - last_best).total_seconds() >= BEST_EVERY_S:
                    items += self._search(http, BEST)
                    self._put(last_best=_iso(now))
                self._save_items(items, now)
                self._details(http, items, now)
                self._find_pages(http, now)
        except BaseException:  # också när ClippingAlpha stoppar körningen: klippen kommer från Content Rewards
            self._after_alpha(now, alpha_failed=True)
            raise
        self._after_alpha(now, alpha_failed=False)
        self._notify(now)

    def _after_alpha(self, now: datetime, alpha_failed: bool) -> None:
        """Nya klipp och nya betyg. Efter ett fel från ClippingAlpha loggas deras fel och kastas inte, så att de inte
        ersätter det felet."""
        for step in (self._refresh_clip_views, self._rescore):
            try:
                step(now)
            except Exception:
                if not alpha_failed:
                    raise
                log.exception("Kampanjbevakningen: %s fick ett fel", step.__name__)

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
        median, cpm = self.median()[0], float(self._get("cpm_median") or DEFAULT_CPM)
        country = preferences.country(self.settings)
        new = [ref for ref in dict.fromkeys(_ref(item) for item in items)
               if "detail_at" not in json.loads(rows[ref]["data"])
               and evaluate(json.loads(rows[ref]["data"]), median, cpm, now, country)[2] is None]
        best = [row["ref"] for row in sorted(rows.values(), key=lambda r: -(r["score"] or 0))
                if row["excluded"] is None and row["score"] is not None][:TOP]
        old = [ref for ref in best if ref not in new and (now - (_time(json.loads(rows[ref]["data"]).get(
            "detail_at")) or now)).total_seconds() > DETAIL_MAX_AGE_S]
        for ref in (new + old)[:DETAILS_PER_RUN]:
            saved = json.loads(rows[ref]["data"])
            assets, go = "assets_at" not in saved, "join_checked_at" not in saved  # en gång per kampanj
            if self.calls_today() + 2 + assets + go > CALL_LIMIT - reserve(now):
                return
            fresh = self._fetch(http, ref, assets, now)
            fresh.update(self._join(http, {**saved, **fresh}, now, answered="campaign" in fresh))
            self._merge(ref, {**fresh, **_content_views({**saved, **fresh}, now), "detail_at": _iso(now)})

    def _merge(self, ref: str, fresh: dict) -> None:
        """Lägg till fresh i kampanjens sparade data, läst på nytt så att inget annat skrivs över."""
        with self._db() as db:
            data = json.loads(db.execute("SELECT data FROM scout_campaigns WHERE ref = ?", (ref,)).fetchone()[0])
            data.update(fresh)
            db.execute("UPDATE scout_campaigns SET data = ? WHERE ref = ?", (json.dumps(data), ref))

    def _find_pages(self, http: httpx.Client, now: datetime) -> None:
        """/go/ för kampanjer som syns under Kampanjer och inte har kollats, till exempel de som fick sina detaljer
        innan /go/ fanns, eller som ligger utanför de 25 bästa. De bästa först, högst JOINS_PER_RUN per körning, och
        bara så länge resten av dygnets sökningar fortfarande ryms. Första felet (429, 5xx eller nätverket) avbryter
        rundan, och kampanjen provas igen nästa körning."""
        due = [r for r in self._shown(now) if r["excluded"] is None and _join_due(json.loads(r["data"]))]
        for row in due[:JOINS_PER_RUN]:
            if self.calls_today() + 1 > CALL_LIMIT - reserve(now) - JOIN_SLACK:
                return
            data = json.loads(row["data"])
            found = self._join(http, data, now, answered="campaign" in data)
            if not found:  # bara vid fel här: due tar inte kollade kampanjer eller sådana utan /go/-länk
                return
            self._merge(row["ref"], found)

    def _refresh_clip_views(self, now: datetime) -> None:
        """Kampanjens klipp på Content Rewards för kampanjer som syns under Kampanjer, när de är äldre än 6 timmar.
        Också för dem som är bortsorterade för att de inte betalar för landet, så att nya priser kommer med, och för
        sidor som lästes innan priset per land (geo) sparades. En sida som inte går att läsa väntar 6 timmar.
        De bästa först och högst CLIP_VIEWS_PER_RUN per körning. Inga anrop till ClippingAlpha."""
        due = [r for r in self._shown(now) if _clip_views_wanted(r, now)]
        for row in due[:CLIP_VIEWS_PER_RUN]:
            self._merge(row["ref"], _clip_views(json.loads(row["data"]), now))

    def _fetch_tool(self, http: httpx.Client, name: str, ref: str) -> dict | None:
        """Ett verktyg för en kampanj. None vid fel: en trasig kampanj får inte stoppa de andra. Gränsen stoppar."""
        try:
            return self._tool(http, name, {"campaign": ref})
        except LimitError:
            raise
        except ScoutError as error:
            log.warning("Kampanjbevakningen: %s för %s: %s", name, ref, error)
            return None

    def _fetch(self, http: httpx.Client, ref: str, assets: bool, now: datetime) -> dict:
        """get_campaign och analytics, och get_assets om kanalen inte har letats upp än. Vid fel i get_campaign eller
        analytics får kampanjen betyg från sökträffen och provas om 6 timmar; vid fel i get_assets provas den då."""
        campaign = self._fetch_tool(http, "get_campaign", ref)
        analytics = None if campaign is None else self._fetch_tool(http, "get_campaign_analytics", ref)
        if campaign is None or analytics is None:
            return {}
        fresh = {"campaign": campaign.get("campaign") or {}, "analytics": analytics}
        found = self._fetch_tool(http, "get_assets", ref) if assets else None
        if found is not None:  # VOD:en används bara när kanalen saknas
            fresh.update(twitch_login=channel_from_assets(found), twitch_vod=vod_from_assets(found), assets_at=_iso(now))
        return fresh

    def _join(self, http: httpx.Client, data: dict, now: datetime, answered: bool) -> dict:
        """Content Rewards-sidan bakom /go/-länken. Ett anrop till ClippingAlpha, räknat innan det görs."""
        # En gång per kampanj, med joinUrl ur get_campaign när den finns.
        if "join_checked_at" in data:
            return {}
        if not (url := go_link(facts(data)["join_url"])):  # kollad utan anrop när get_campaign har svarat
            return {"join_checked_at": _iso(now)} if answered else {}
        self._count()
        _wait_gap()
        try:
            cr_url = resolve_join(http, url)
        except httpx.HTTPError as error:  # provas igen vid nästa detaljuppdatering
            log.warning("Kampanjbevakningen: %s svarade inte: %s", url, error)
            return {}
        finally:
            _last_call[0] = time.monotonic()
        return {"cr_url": cr_url, "join_checked_at": _iso(now)}

    def rescore(self) -> None:
        """Nya betyg ur det som är sparat, till exempel när Robin byter land. Inga anrop."""
        self._rescore(_now())

    def _rescore(self, now: datetime) -> None:
        median, cpm = self.median()[0], float(self._get("cpm_median") or DEFAULT_CPM)
        country = preferences.country(self.settings)
        with self._db() as db:
            for row in db.execute("SELECT ref, data FROM scout_campaigns").fetchall():
                score, parts, excluded = evaluate(json.loads(row["data"]), median, cpm, now, country)
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

    def _shown(self, now: datetime) -> list[sqlite3.Row]:
        """Kampanjerna som syns under Kampanjer, också de bortsorterade, bäst först."""
        with self._db() as db:
            return db.execute("SELECT * FROM scout_campaigns WHERE dismissed = 0 AND score IS NOT NULL "
                              "AND updated >= ? ORDER BY score DESC, first_seen DESC",
                              (_iso(now - timedelta(seconds=SHOW_S)),)).fetchall()

    def overview(self) -> dict:
        now = _now()
        median, source = self.median()
        country = preferences.country(self.settings)
        views = [_view(row, now, country) for row in self._shown(now)]
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


def _older_than(value: str | None, seconds: float, now: datetime) -> bool:
    moment = _time(value)
    return moment is None or (now - moment).total_seconds() > seconds


def _twitch_due(data: dict, now: datetime) -> bool:
    """Kampanjen har en Twitch-kanal eller en VOD, och Twitch har inte frågats det senaste dygnet."""
    has_twitch = bool(data.get("twitch_login") or data.get("twitch_vod"))
    return has_twitch and _older_than(data.get("twitch_at"), TWITCH_MAX_AGE_S, now)


def _clip_views_due(data: dict, now: datetime) -> bool:
    """Kampanjen har en Content Rewards-sida, och klippen hämtades inte eller för mer än 6 timmar sedan, eller
    sidan lästes innan priset per land (geo) sparades."""
    return bool(data.get("cr_url")) and (
        "geo" not in data or _older_than(data.get("clip_views_at"), DETAIL_MAX_AGE_S, now))


def _clip_views_wanted(row: sqlite3.Row, now: datetime) -> bool:
    """Inte bortsorterad, eller bortsorterad bara för att den inte betalar för landet, och klippen behövs."""
    wanted = row["excluded"] is None or excluded_for_country(row["excluded"])
    return wanted and _clip_views_due(json.loads(row["data"]), now)


def _join_due(data: dict) -> bool:
    """/go/ är inte kollad, och det finns en /go/-länk, eller get_campaign har svarat (då kollas den utan anrop)."""
    return "join_checked_at" not in data and ("campaign" in data or go_link(facts(data)["join_url"]) is not None)


def _clip_views(data: dict, now: datetime) -> dict:
    """Kampanjens klipp och priset per land på Content Rewards. clip_views_at sätts också när sidan inte går att
    läsa, så att sidan provas igen först efter 6 timmar. Felet loggas i content_views och de gamla clip_views och geo
    behålls. Saknas geo sätts den till None (Land okänt), så att en trasig sida inte hämtas varje körning."""
    if not data.get("cr_url"):
        return {}
    found: dict = {"clip_views_at": _iso(now)}
    # minst CLIP_PAGE_GAP_S mellan två sidor, också från den sista i _details till den första i _refresh_clip_views
    time.sleep(max(0.0, CLIP_PAGE_GAP_S - (time.monotonic() - _last_page[0])))
    try:
        page = campaign_page(data["cr_url"])
    finally:
        _last_page[0] = time.monotonic()
    if page is not None:
        found.update(page)
    else:
        found["geo"] = data.get("geo")  # nyckeln finns, så _clip_views_due väntar 6 timmar
    return found


def _twitch(data: dict, now: datetime) -> dict:
    """Kanalens Twitch-klipp högst en gång per dygn. Saknas kanalen men finns en VOD slås kanalen först upp i Helix.
    twitch_at sätts före anropen, så Twitch provas tidigast efter ett dygn, också efter ett fel eller när nycklarna
    saknas. Vid fel behålls de gamla twitch_views."""
    if not _twitch_due(data, now):
        return {}
    found: dict = {"twitch_at": _iso(now)}
    login = data.get("twitch_login") or twitch_login_from_vod(data["twitch_vod"])
    if login is None:
        return found
    found["twitch_login"] = login
    twitch_views = twitch_clip_views(login, now)
    if twitch_views is not None:
        found["twitch_views"] = twitch_views
    return found


def _content_views(data: dict, now: datetime) -> dict:
    """Kampanjens klipp vid varje detaljuppdatering, och kanalens Twitch-klipp högst en gång per dygn."""
    return {**_clip_views(data, now), **_twitch(data, now)}


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


def _prices(f: dict, data: dict, country: str) -> tuple[float | None, float | None]:
    """Priset och priset efter avgift för kortet. Med pris per land: det högsta i landet, som Betalning räknar."""
    rates = country_rates(data.get("geo"), country)
    if rates:
        rate = max(rates.values())
        return rate, round(rate * (1 - f["fee"]), 2)
    return f["rate"], _first(f["net"], round(f["rate"] * (1 - f["fee"]), 2) if f["rate"] is not None else None)


def _view(row: sqlite3.Row, now: datetime, country: str) -> dict:
    data = json.loads(row["data"])
    f = facts(data)
    age = None if f["launched"] is None else round((now - f["launched"]).total_seconds() / 3600, 1)
    drain = _days(f["drain"], now)
    source = f"{f['title']} {f['about']} {' '.join(text for _, text in f['rules'])}".lower()
    rate, net = _prices(f, data, country)
    return {
        "ref": row["ref"], "title": f["title"], "brand": f["brand"], "score": row["score"],
        "parts": json.loads(row["parts"]), "excluded": row["excluded"], "age_hours": age,
        "rate_usd": rate, "net_rate_usd": net,
        "budget_left_usd": f["left"], "budget_total_usd": f["total"], "min_views": min_views(f),
        "max_payout_usd": pay_number(f["pay"], "maxPayoutUsd"),
        "drain_days": None if drain is None else round(max(drain, 0), 1),
        "platforms": f["platforms"], "mentions_twitch": "twitch" in source, "open_join": f["open_join"],
        "page_url": _safe(f["page_url"]), "join_url": _safe(f["join_url"]), "first_seen": row["first_seen"],
        "is_new": age is not None and age < NOTIFY_AGE_H,
        "country_note": country_note(data, country),
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
