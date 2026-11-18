"""Exempelklipp: kampanjens bästa klipp och klippare, hämtade från Content Rewards publika kampanjsida.

Sidan (https://contentrewards.com/c/campaigns/<id>) är en Next.js-sida. Datan ligger som JSON-text i
self.__next_f.push([1,"..."]) och har topClips (de 10 klippen med flest visningar, med mp4 på Content
Rewards CDN) och topEarners (de 3 klipparna som tjänat mest). Vi läser bara den publika sidan, aldrig /api/,
som robots.txt stänger. En hämtning per kampanj och timme.

Sidan har också priset per land: geoPriced, geoRates (tiers och restOfWorld, cent per 1 000 visningar) och
payoutModel. Det ges som geo, för kampanjbetyget (campaign/scout_score.py). En ny kampanj utan klipp är inget fel.
"""

from __future__ import annotations

import json
import re
import threading
import time
from urllib.parse import urlparse

import httpx

HOSTS = {"contentrewards.com", "www.contentrewards.com"}
CDN = "cdn.contentrewards.com"
CACHE_S = 3600
TITLE_MAX = 300
# fälten ur sidan och deras typ
FIELDS = {"topClips": list, "topEarners": list, "geoPriced": bool, "geoRates": dict, "payoutModel": list}
_PUSH = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def http_client() -> httpx.Client:
    return httpx.Client(timeout=20, follow_redirects=True,
                        headers={"User-Agent": "Mozilla/5.0 (klippverktyg; reads the campaign page at most once an hour)"})


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def link_problem(url: str | None) -> str | None:
    """Varför länken inte går att använda, annars None."""
    if not url:
        return "Länken till kampanjen i Content Rewards saknas. Lägg in den under Ställ in."
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in HOSTS:
        return "Exempelklipp kräver kampanjens länk från contentrewards.com, till exempel https://contentrewards.com/c/campaigns/…"
    return None


def _https(value: object, host: str | None = None) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or (host and parsed.hostname != host):
        return None
    return value


def _value(text: str, key: str, kind: type) -> object:
    """Det första värdet för key i flight-datan som är av typen kind, eller None. Träffar av en annan typ, till exempel
    null, och träffar som inte går att läsa som JSON hoppas över."""
    for match in re.finditer(rf'"{key}"\s*:\s*', text):
        try:
            value, _ = json.JSONDecoder().raw_decode(text, match.end())
        except json.JSONDecodeError:
            continue
        if isinstance(value, kind):
            return value
    return None


def _dicts(value: object) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _int(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _earner(e: dict) -> dict:
    return {"rank": _int(e.get("rank")), "name": str(e.get("username") or e.get("whopUsername") or "Okänd"),
            "earned_usd": round(_int(e.get("earnedCents")) / 100, 2), "views": _int(e.get("totalViews")),
            "approved": _int(e.get("approvedSubmissionCount"))}


def _clip(c: dict, earner: dict | None) -> dict:
    return {
        "rank": _int(c.get("rank")),
        "platform": str(c.get("platform") or ""),
        "views": _int(c.get("totalViews")),
        "name": c.get("username") or (earner and earner["name"]) or None,
        "earner_rank": earner and earner["rank"],
        "post_url": _https(c.get("postUrl")),
        "video_url": _https(c.get("videoUrl"), CDN),
        "thumbnail_url": _https(c.get("thumbnailUrl"), CDN),
        "title": str(c.get("title") or "")[:TITLE_MAX],
    }


def parse(html: str) -> dict:
    """Klippen, klipparna och priset per land ur sidan. Tomma listor för en kampanj utan klipp. ValueError när sidan
    saknar kampanjdata helt."""
    try:
        flight = "".join(json.loads(m.group(1)) for m in _PUSH.finditer(html))
    except json.JSONDecodeError as error:
        raise ValueError("Kampanjsidan gick inte att läsa. Content Rewards kan ha ändrat sidan.") from error
    found = {key: _value(flight, key, kind) for key, kind in FIELDS.items()}
    if all(value is None for value in found.values()):
        raise ValueError("Kampanjsidan hittade inga klipp. Content Rewards kan ha ändrat sidan.")
    earners = {e.get("userId"): _earner(e) for e in _dicts(found["topEarners"])}
    rates = found["geoRates"]
    return {
        "clips": [_clip(c, earners.get(c.get("userId"))) for c in _dicts(found["topClips"])],
        "earners": [_earner(e) for e in _dicts(found["topEarners"])],
        "geo": {"priced": found["geoPriced"] is True, "rates": rates if isinstance(rates, dict) else None,
                "payout_model": _dicts(found["payoutModel"])},
    }


def fetch(url: str) -> dict:
    """Kampanjens exempelklipp, högst en hämtning i timmen. RuntimeError när sidan inte går att läsa."""
    with _lock:
        hit = _cache.get(url)
        if hit and time.monotonic() - hit[0] < CACHE_S:
            return hit[1]
    try:
        with http_client() as http:
            response = http.get(url)
    except httpx.HTTPError as error:
        raise RuntimeError("Content Rewards svarar inte. Försök igen om en stund.") from error
    if response.status_code != 200:
        raise RuntimeError(f"Content Rewards svarade {response.status_code}. Försök igen om en stund.")
    try:
        data = parse(response.text)
    except ValueError as error:
        raise RuntimeError(str(error)) from error
    with _lock:
        _cache[url] = (time.monotonic(), data)
    return data
