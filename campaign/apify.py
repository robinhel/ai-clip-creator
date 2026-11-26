"""Apify: hämta TikTok-inlägg med aktören clockworks~tiktok-scraper, med en hård spärr på gratisnivån.

Spärren har tre lager. Före varje körning läses månadens förbrukning hos Apify; går den inte att läsa körs inget.
Samma månads kostnader i samling.db räknas också, och det största av de två gäller. En körning får kosta högst
RUN_CAP_USD och aldrig mer än det som är kvar under MONTH_CAP_USD (maxTotalChargeUsd). Token skickas bara i headern.
Inga videor laddas ner.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

BASE = "https://api.apify.com/v2"
ACTOR = "clockworks~tiktok-scraper"
MONTH_CAP_USD = 4.50  # gratisnivån ger 5 US$ i månaden
RUN_CAP_USD = 0.50
MIN_LEFT_USD = 0.05
USD_PER_RESULT = 0.005  # säker uppskattning när svaret inte säger vad körningen kostade
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


def month_usage(http: httpx.Client, token: str) -> float:
    """Månadens förbrukning enligt Apify, i US$."""
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
        usage = response.json()["data"]["current"]["monthlyUsageUsd"]
    except (ValueError, KeyError, TypeError):
        usage = None
    if not isinstance(usage, (int, float)):
        raise Stopped(
            "kunde inte läsa månadens förbrukning hos Apify (svaret saknar monthlyUsageUsd)."
        )
    return float(usage)


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
    own_spent_usd: float,
    record_cost: Callable[[float], None],
) -> list[dict]:
    """Kör aktören en gång inom spärren och ge inläggen. record_cost får kostnaden, även när körningen går fel."""
    spent = max(month_usage(http, token), own_spent_usd)
    left = MONTH_CAP_USD - spent
    if left < MIN_LEFT_USD:
        raise Stopped(f"månadens tak {MONTH_CAP_USD:.2f} US$ är nått ({spent:.2f} US$ förbrukat).")
    charge = math.floor(round(min(RUN_CAP_USD, left) * 100, 6)) / 100  # hela cent, nedåt
    try:
        response = http.post(
            f"{BASE}/acts/{ACTOR}/run-sync-get-dataset-items",
            params={"maxTotalChargeUsd": f"{charge:.2f}"},
            headers=_headers(token),
            json={**run_input, **NO_DOWNLOADS},
            timeout=320,
        )
    except httpx.HTTPError as error:
        record_cost(charge)  # körningen kan ha startat; räkna med det högsta den fick kosta
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
        record_cost(charge)
        raise Stopped(f"Apify svarade {response.status_code}. Räknar med {charge:.2f} US$.")
    usage = data.get("usageTotalUsd") if isinstance(data, dict) else None
    record_cost(float(usage) if isinstance(usage, (int, float)) else len(items) * USD_PER_RESULT)
    return parse(items)
