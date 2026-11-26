"""Samlingen: virala klipp från Content Rewards, Robins egna länkar och Apify, mätta två gånger.

Andras klipp mäts bara (visningar och datum). Inget laddas ner, postas eller renderas härifrån. Inlägg som växer mycket
snabbare än jämförbara inlägg skrivs till utvalda.txt, som går direkt in i python -m campaign viral.

Fart = nya visningar per timme mellan senaste mätningen och den senaste mätningen minst MIN_GAP_H timmar före.
Kvot = farten delad med medianfarten för kontots andra inlägg i ungefär samma ålder (hälften till dubbla åldern),
eller hela samlingens när kontot har färre än MIN_PEERS sådana.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx
import yaml

from . import apify, examples
from .viral import _clean, _Silent

MIN_GAP_H = 2.0
MIN_PEERS = 3
WAIT_DAYS = 2  # Apify-inlägg som inte gått att mäta igen på så här länge väntar inte längre

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (url TEXT PRIMARY KEY, platform TEXT, account TEXT, created TEXT, title TEXT,
                                  source TEXT);
CREATE TABLE IF NOT EXISTS measurements (url TEXT NOT NULL, at TEXT NOT NULL, views INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS apify_runs (at TEXT NOT NULL, cost_usd REAL NOT NULL, done INTEGER NOT NULL DEFAULT 1);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, isolation_level=None)  # varje rad sparas direkt
    con.executescript(SCHEMA)
    return con


def normalize(url: str) -> str:
    """https, utan www., query och fragment och utan / på slutet, så att samma inlägg inte sparas två gånger.
    YouTube behåller v=, som är klippets id."""
    parts = urlparse(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    query = ""
    if host.endswith("youtube.com") and path == "/watch":
        video = parse_qs(parts.query).get("v")
        query = urlencode({"v": video[0]}) if video else ""
    return urlunparse(("https", host, path, "", query, ""))


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).isoformat()


def _time(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _stamp(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def tiktok_created(url: str) -> datetime | None:
    """TikTok-id:ets första 32 bitar är sekunder sedan 1970."""
    match = re.search(r"/video/(\d{15,})", url)
    return datetime.fromtimestamp(int(match.group(1)) >> 32, UTC) if match else None


def _account(url: str, fallback: object) -> str:
    match = re.search(r"/@([^/?#]+)", url)
    return match.group(1) if match else str(fallback or "")


def record(con: sqlite3.Connection, post: dict, now: datetime) -> None:
    """Spara inlägget (nya uppgifter fyller i tomma) och en mätning om views finns."""
    url = normalize(post["url"])
    created = post.get("created") or tiktok_created(url)
    con.execute(
        """INSERT INTO posts (url, platform, account, created, title, source) VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(url) DO UPDATE SET platform = COALESCE(NULLIF(excluded.platform, ''), platform),
             account = COALESCE(NULLIF(excluded.account, ''), account), created = COALESCE(excluded.created, created),
             title = COALESCE(NULLIF(excluded.title, ''), title)""",
        (
            url,
            post.get("platform") or "",
            post.get("account") or "",
            created and _iso(created),
            _clean(post.get("title"))[:300],
            post.get("source") or "",
        ),
    )
    if post.get("views") is not None:
        con.execute(
            "INSERT INTO measurements (url, at, views) VALUES (?, ?, ?)",
            (url, _iso(now), int(post["views"])),
        )


def add_links(con: sqlite3.Connection, links: list[str], now: datetime) -> list[str]:
    lines, added = [], set()
    for link in links:
        if not link.startswith(("http://", "https://")):
            lines.append(f"Hoppar över {_clean(link)}: det är ingen länk.")
            continue
        record(con, {"url": link, "account": _account(link, ""), "source": "egen"}, now)
        added.add(normalize(link))
    lines.append(
        f"{len(added)} inlägg i samlingen. De mäts nästa gång du kör: python -m campaign samla mat"
    )
    return lines


def load_config(root: Path) -> dict:
    path = root / "samling.yaml"
    if not path.exists():
        raise SystemExit(
            f"{path} saknas. Kopiera samling.exempel.yaml till samling.yaml och fyll i källorna."
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit(
            "samling.yaml ska ha fälten content_rewards och apify, se samling.exempel.yaml."
        )
    cfg = {"content_rewards": data.get("content_rewards") or [], "apify": data.get("apify") or {}}
    if not isinstance(cfg["apify"], dict):
        raise SystemExit(
            "I samling.yaml ska apify ha fälten hashtags, search, profiles och per_query på egna rader, "
            "se samling.exempel.yaml."
        )
    lists = [
        cfg["content_rewards"],
        *(cfg["apify"].get(k) or [] for k in ("hashtags", "search", "profiles")),
    ]
    if not all(isinstance(x, list) for x in lists):
        raise SystemExit(
            "I samling.yaml ska content_rewards, hashtags, search och profiles vara listor."
        )
    return cfg


def _short(error: object) -> str:
    return _clean(error).replace("ERROR: ", "").split(" (caused by")[0][:150]


def _info(url: str) -> dict:
    """Uppgifter om ett inlägg via yt-dlp, utan att ladda ner videon."""
    import yt_dlp

    options = {
        "quiet": True,
        "noprogress": True,
        "noplaylist": True,
        "skip_download": True,
        "logger": _Silent(),
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        if ydl.extract_info(url, download=False, process=False).get("_type") == "playlist":
            raise ValueError(
                "länken är en spellista eller kanal. Ange länken till ett enda inlägg."
            )
        return ydl.extract_info(url, download=False)


def _content_rewards(con, campaigns: list, now: datetime, cr_fetch: Callable) -> list[str]:
    lines = []
    for campaign in campaigns:
        if problem := examples.link_problem(str(campaign)):
            lines.append(f"Content Rewards {_clean(campaign)}: {problem}")
            continue
        try:
            clips = [c for c in cr_fetch(campaign)["clips"] if c.get("post_url")]
        except Exception as error:  # en källa som faller stoppar inte de andra
            lines.append(f"Content Rewards {_clean(campaign)}: fel: {_short(error)}")
            continue
        for clip in clips:
            record(
                con,
                {
                    "url": clip["post_url"],
                    "views": clip.get("views"),
                    "platform": clip.get("platform"),
                    "account": _account(clip["post_url"], clip.get("name")),
                    "title": clip.get("title"),
                    "source": "contentrewards",
                },
                now,
            )
        lines.append(f"Content Rewards {_clean(campaign)}: {len(clips)} inlägg")
    return lines


def _own_links(con, now: datetime, info: Callable) -> list[str]:
    urls = [u for (u,) in con.execute("SELECT url FROM posts WHERE source = 'egen' ORDER BY url")]
    if not urls:
        return []
    lines, measured = [], 0
    for url in urls:
        try:
            data = info(url)
        except ImportError:
            return [
                'Egna länkar: fel: yt-dlp saknas. Installera det med: pip install -e ".[viral]"'
            ]
        except Exception as error:
            lines.append(f"  {_clean(url)}: fel: {_short(error)}")
            continue
        if not isinstance(data.get("view_count"), int):
            lines.append(f"  {_clean(url)}: fel: visningarna syns inte.")
            continue
        timestamp = data.get("timestamp")
        record(
            con,
            {
                "url": url,
                "views": data["view_count"],
                "platform": data.get("extractor_key") or "",
                "account": data.get("uploader") or data.get("channel") or "",
                "title": data.get("title"),
                "created": datetime.fromtimestamp(timestamp, UTC) if timestamp else None,
            },
            now,
        )
        measured += 1
    return [f"Egna länkar: {measured} inlägg", *lines]


def _apify_spent(con, now: datetime, start: datetime | None) -> tuple[float, float]:
    """Allt sparat i perioden (kostnader och reservationer) och det som fortfarande är reserverat. Utan start från
    Apify gäller kalendermånaden i UTC."""
    start = start or now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return con.execute(
        "SELECT COALESCE(SUM(cost_usd), 0), COALESCE(SUM(CASE WHEN done = 0 THEN cost_usd END), 0) "
        "FROM apify_runs WHERE at >= ?",
        (_iso(start),),
    ).fetchone()


def reserve(con, apify_usage: float, start: datetime | None, now: datetime) -> tuple[int, float]:
    """Spara hela körningens tak som kostnad innan anropet, i en låst transaktion. Ger radens id och taket.
    apify.Stopped när för lite är kvar. Reservationer som inte är klara läggs på Apifys siffra, som inte vet om dem."""
    con.execute("BEGIN IMMEDIATE")
    try:
        total, pending = _apify_spent(con, now, start)
        charge = apify.charge_for(max(apify_usage + pending, total))
        row = con.execute(
            "INSERT INTO apify_runs (at, cost_usd, done) VALUES (?, ?, 0)", (_iso(now), charge)
        ).lastrowid
    except BaseException:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")
    return row, charge


def _apify(con, settings: dict, token: str, now: datetime, http: httpx.Client) -> list[str]:
    if not token:
        return ["Apify hoppas över: APIFY_TOKEN saknas i .env."]
    first = con.execute(
        """SELECT p.url, MIN(m.at) FROM posts p JOIN measurements m ON m.url = p.url WHERE p.source = 'apify'
           GROUP BY p.url HAVING COUNT(*) = 1 AND MIN(m.at) >= ?""",
        (_iso(now - timedelta(days=WAIT_DAYS)),),
    ).fetchall()
    ready = sorted(url for url, at in first if _time(at) <= now - timedelta(hours=MIN_GAP_H))
    per_query = int(settings.get("per_query") or 20)
    if ready:
        run_input = {"postURLs": ready, "resultsPerPage": per_query}
    elif first:
        soonest = min(_time(at) for _, at in first) + timedelta(hours=MIN_GAP_H)
        return [
            f"Apify: {len(first)} inlägg väntar på andra mätningen; mät igen om tidigast {_stamp(soonest)}."
        ]
    else:
        run_input = {
            "hashtags": [str(h).lstrip("#") for h in settings.get("hashtags") or []],
            "searchQueries": [str(s) for s in settings.get("search") or []],
            "profiles": [str(p).lstrip("@") for p in settings.get("profiles") or []],
            "resultsPerPage": per_query,
        }
        if not any(run_input[k] for k in ("hashtags", "searchQueries", "profiles")):
            return ["Apify: inga hashtags, sökningar eller konton i samling.yaml."]

    rows, cycle = [], []

    def book(usage: float, start: datetime | None) -> float:
        cycle.append(start)
        row, charge = reserve(con, usage, start, now)
        rows.append(row)
        return charge

    def finish(cost: float) -> None:
        con.execute("UPDATE apify_runs SET cost_usd = ?, done = 1 WHERE rowid = ?", (cost, rows[0]))

    try:
        posts = apify.run(http, token, run_input, book, finish)
    except apify.Stopped as error:
        return [f"Apify körs inte: {error}"]
    if ready:  # bara de inlägg som skulle mätas igen
        wanted = set(ready)
        posts = [p for p in posts if normalize(p["url"]) in wanted]
    for post in posts:
        record(con, post, now)
    what = "mätta igen" if ready else "nya eller mätta"
    return [
        f"Apify: {len(posts)} inlägg {what} (månaden: {_apify_spent(con, now, cycle[0])[0]:.2f} av {apify.MONTH_CAP_USD:.2f} US$)"
    ]


def measure(
    con: sqlite3.Connection,
    cfg: dict,
    token: str,
    now: datetime,
    *,
    cr_fetch: Callable | None = None,
    info: Callable | None = None,
    http: httpx.Client | None = None,
) -> list[str]:
    """En mätning av alla källor. En rad per källa; en källa som faller stoppar inte de andra."""
    lines = _content_rewards(con, cfg["content_rewards"], now, cr_fetch or examples.fetch)
    lines += _own_links(con, now, info or _info)
    if http is None:
        with httpx.Client() as client:
            lines += _apify(con, cfg["apify"], token, now, client)
    else:
        lines += _apify(con, cfg["apify"], token, now, http)
    return lines


def _speeds(con, now: datetime) -> tuple[list[dict], list[datetime]]:
    """Inlägg med fart, och för inlägg utan: tidigaste tid då en ny mätning ger fart."""
    measured: dict[str, list[tuple[datetime, int]]] = {}
    for url, at, views in con.execute("SELECT url, at, views FROM measurements ORDER BY at"):
        measured.setdefault(url, []).append((_time(at), views))
    posts = {row[0]: row for row in con.execute("SELECT url, account, created FROM posts")}
    found, waiting = [], []
    for url, series in measured.items():
        last_at, last_views = series[-1]
        earlier = [m for m in series[:-1] if (last_at - m[0]).total_seconds() >= MIN_GAP_H * 3600]
        if not earlier:
            waiting.append(series[0][0] + timedelta(hours=MIN_GAP_H))
            continue
        first_at, first_views = earlier[-1]
        _, account, created = posts[url]
        found.append(
            {
                "url": url,
                "account": account or "",
                "views": last_views,
                "speed": (last_views - first_views) / ((last_at - first_at).total_seconds() / 3600),
                "age": (last_at - _time(created)).total_seconds() / 3600 if created else None,
            }
        )
    return found, waiting


def _compare(post: dict, others: list[dict]) -> tuple[float | None, str]:
    if post["age"] is None:
        return None, "datum saknas"
    near = [
        o
        for o in others
        if o is not post and o["age"] is not None and post["age"] / 2 <= o["age"] <= post["age"] * 2
    ]
    same = [o["speed"] for o in near if post["account"] and o["account"] == post["account"]]
    if len(same) >= MIN_PEERS:
        return post["speed"] / max(median(same), 1.0), f"kontot ({len(same)})"
    if len(near) >= MIN_PEERS:
        return post["speed"] / max(
            median(o["speed"] for o in near), 1.0
        ), f"hela samlingen ({len(near)})"
    return None, "för få att jämföra med"


def _num(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


def lista(con: sqlite3.Connection, out: Path, now: datetime, min_ratio: float) -> list[str]:
    """Rangordnad lista och utvalda.txt med de inlägg vars kvot är minst min_ratio."""
    posts, waiting = _speeds(con, now)
    for post in posts:
        post["ratio"], post["against"] = _compare(post, posts)
    posts.sort(key=lambda p: (p["ratio"] is not None, p["ratio"] or 0, p["speed"]), reverse=True)
    lines = [
        f"{'Kvot':>6}  {'Fart/h':>9}  {'Visningar':>11}  {'Ålder':>6}  Konto  Jämfört med  Länk"
    ]
    for p in posts:
        ratio = f"{p['ratio']:.1f}×".replace(".", ",") if p["ratio"] is not None else "–"
        age = f"{p['age']:.0f} h" if p["age"] is not None else "?"
        lines.append(
            f"{ratio:>6}  {_num(p['speed']):>9}  {_num(p['views']):>11}  {age:>6}  "
            f"@{_clean(p['account']) or '?'}  {p['against']}  {p['url']}"
        )
    if not posts:
        lines = ["Inga inlägg har två mätningar minst 2 timmar isär än."]
    chosen = [p["url"] for p in posts if p["ratio"] is not None and p["ratio"] >= min_ratio]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(f"{url}\n" for url in chosen), encoding="utf-8")
    if waiting:
        soonest = min(waiting)
        when = "nu" if soonest <= now else f"om tidigast {_stamp(soonest)}"
        lines.append(
            f"{len(waiting)} inlägg väntar på en andra mätning: mät igen {when} (python -m campaign samla mat)."
        )
    lines.append(f"{len(chosen)} inlägg med kvot minst {min_ratio:g} sparade i {out}.")
    if chosen:
        lines.append(f"Hitta dem i en VOD: python -m campaign viral <VOD-id> {out}")
    return lines
