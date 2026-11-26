"""Apify: hämta TikTok-inlägg med aktören clockworks~tiktok-scraper, med en hård spärr på gratisnivån.

Spärren har tre lager. Före varje körning läses förbrukningen i Apifys period; går den inte att läsa körs inget.
Samma periods kostnader i samling.db räknas också, och det största av de två gäller. En körning får kosta högst
RUN_CAP_USD och aldrig mer än det som är kvar under MONTH_CAP_USD (maxTotalChargeUsd). Aktören godtar inget tak under
MIN_CHARGE_USD, så finns mindre än så kvar körs inget. Taket reserveras i samling.db innan anropet, så att två
körningar samtidigt eller ett avbrott inte kan gå förbi spärren. Efter körningen läses den verkliga kostnaden
(usageTotalUsd) från Apifys körningsinfo och ersätter reservationen. Går den inte att läsa sparas uppskattningen, men
aldrig mer än taket, eftersom Apify inte låter en körning kosta mer. Token skickas bara i headern. Inga videor laddas ner.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

BASE = "https://api.apify.com/v2"
ACTOR = "clockworks~tiktok-scraper"
MONTH_CAP_USD = 4.50  # gratisnivån ger 5 US$ i månaden
RUN_CAP_USD = 0.50
MIN_CHARGE_USD = 0.50  # aktörens pricingInfo.minimalMaxTotalChargeUsd
MIN_RUN_USD = 0.01  # lägsta kostnad som sparas för en körning, också utan resultat
USD_PER_RESULT = 0.005  # säker uppskattning när den verkliga kostnaden inte går att läsa
CLOCK_SLACK = timedelta(seconds=30)  # Apifys klocka och vår kan gå lite olika
NO_DOWNLOADS = {
    "shouldDownloadVideos": False,
    "shouldDownloadCovers": False,
    "shouldDownloadSubtitles": False,
    "shouldDownloadSlideshowImages": False,
    "shouldDownloadAvatars": False,
    "shouldDownloadMusicCovers": False,
}


class Stopped(Exception):
    """Apify körs inte, eller körningen gick fel. Texten är ett kort besked på svenska."""


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def limits(http: httpx.Client, token: str) -> tuple[float, datetime | None]:
    """Förbrukningen i Apifys period enligt Apify, i US$, och när perioden började (None om svaret inte säger det)."""
    try:
        response = http.get(f"{BASE}/users/me/limits", headers=_headers(token), timeout=30)
    except httpx.HTTPError as error:
        raise Stopped(
            f"kunde inte läsa månadens förbrukning hos Apify ({type(error).__name__})."
        ) from error
    if response.status_code != 200:
        raise Stopped(
            f"kunde inte läsa månadens förbrukning hos Apify (svar {response.status_code})."
        )
    try:
        data = response.json()["data"]
        usage = data["current"]["monthlyUsageUsd"]
    except (ValueError, KeyError, TypeError):
        data, usage = {}, None
    if not isinstance(usage, (int, float)):
        raise Stopped(
            "kunde inte läsa månadens förbrukning hos Apify (svaret saknar monthlyUsageUsd)."
        )
    cycle = data.get("monthlyUsageCycle")
    start = cycle.get("startAt") if isinstance(cycle, dict) else None
    try:
        start = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(UTC)
    except (AttributeError, ValueError):
        start = None
    return float(usage), start


def charge_for(spent_usd: float) -> float:
    """Det mesta en körning får kosta när spent_usd är förbrukat: hela cent, nedåt. Stopped när taket skulle bli
    mindre än MIN_CHARGE_USD, som är det lägsta aktören godtar."""
    charge = math.floor(round(min(RUN_CAP_USD, MONTH_CAP_USD - spent_usd) * 100, 6)) / 100
    if charge < MIN_CHARGE_USD:
        raise Stopped(
            f"mindre än {MIN_CHARGE_USD:.2f} US$ är kvar under månadens tak {MONTH_CAP_USD:.2f} US$ "
            f"({spent_usd:.2f} US$ förbrukat eller reserverat), och en körning kräver minst {MIN_CHARGE_USD:.2f} US$."
        )
    return charge


def _created(item: dict) -> datetime | None:
    if isinstance(item.get("createTimeISO"), str):
        try:
            return datetime.fromisoformat(item["createTimeISO"].replace("Z", "+00:00")).astimezone(
                UTC
            )
        except ValueError:
            pass
    if isinstance(item.get("createTime"), (int, float)):
        return datetime.fromtimestamp(item["createTime"], UTC)
    return None


def parse(items: list) -> list[dict]:
    """Inläggen ur aktörens svar. Rader utan länk eller visningar (till exempel fel) hoppas över."""
    posts = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("webVideoUrl"), str):
            continue
        if not isinstance(item.get("playCount"), int):
            continue
        author = item.get("authorMeta")
        name = author.get("name") if isinstance(author, dict) else item.get("authorMeta.name")
        posts.append(
            {
                "url": item["webVideoUrl"],
                "views": item["playCount"],
                "created": _created(item),
                "account": str(name or ""),
                "title": str(item.get("text") or ""),
                "platform": "tiktok",
                "source": "apify",
            }
        )
    return posts


def run(
    http: httpx.Client,
    token: str,
    run_input: dict,
    reserve: Callable[[float, datetime | None], float],
    finish: Callable[[float], None],
) -> list[dict]:
    """Kör aktören en gång inom spärren och ge inläggen.

    reserve får Apifys förbrukning och periodens start, sparar hela körningens tak som kostnad och ger taket (eller
    Stopped). finish får körningens verkliga kostnad när svaret har kommit, eller min(uppskattning, taket) när den
    inte går att läsa. Utan svar, vid fel eller avbrott står taket kvar som kostnad."""
    charge = reserve(*limits(http, token))
    started = datetime.now(UTC)
    try:
        response = http.post(
            f"{BASE}/acts/{ACTOR}/run-sync-get-dataset-items",
            params={"maxTotalChargeUsd": f"{charge:.2f}"},
            headers=_headers(token),
            json={**run_input, **NO_DOWNLOADS},
            timeout=320,
        )
    except httpx.HTTPError as error:
        raise Stopped(
            f"Apify svarade inte ({type(error).__name__}). Räknar med {charge:.2f} US$."
        ) from error
    try:
        data = response.json() if response.status_code in (200, 201) else None
    except ValueError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        items = data["items"]
    elif isinstance(data, list):
        items = data
    else:
        raise Stopped(f"Apify svarade {response.status_code}. Räknar med {charge:.2f} US$.")
    cost = _real_cost(http, token, started)
    if cost is None:
        cost = min(len(items) * USD_PER_RESULT, charge)
    finish(max(MIN_RUN_USD, cost))
    return parse(items)


def _real_cost(http: httpx.Client, token: str, started: datetime) -> float | None:
    """usageTotalUsd för aktörens nyaste körning som startade efter started (minus CLOCK_SLACK). None om den inte
    går att läsa; ett fel här får inte göra att inläggen går förlorade."""
    try:
        response = http.get(
            f"{BASE}/acts/{ACTOR}/runs",
            params={"desc": 1, "limit": 5},
            headers=_headers(token),
            timeout=30,
        )
        response.raise_for_status()
        ours = None
        for item in response.json()["data"]["items"]:
            at = datetime.fromisoformat(item["startedAt"].replace("Z", "+00:00")).astimezone(UTC)
            if at >= started - CLOCK_SLACK and (ours is None or at > ours[0]):
                ours = (at, item["id"])
        if ours is None:
            return None
        response = http.get(f"{BASE}/actor-runs/{ours[1]}", headers=_headers(token), timeout=30)
        response.raise_for_status()
        usage = response.json()["data"]["usageTotalUsd"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return float(usage) if isinstance(usage, (int, float)) else None
