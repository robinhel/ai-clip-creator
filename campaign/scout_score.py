"""Kampanjbetyget 0–100 och orsaken om en kampanj sorteras bort, för kampanjbevakningen (campaign/scout.py).

Betyget följer rapporten "Vad är en bra kampanj" (Jarvis, Klippverktyget, 13 nov 2026), plus delen Visningar
(40 av 100): hur många visningar klipp av kampanjens innehåll brukar få (campaign/content_views.py), och delen
Max payout per klipp (10 av 100): hur mycket ett klipp högst kan betala.

Priset per land (Robins beslut 18 nov 2026): många kampanjer betalar olika per land (geo på Content Rewards
kampanjsida, campaign/examples.py). Betalning räknar då med bästa priset i Robins land (Inställningar, Sverige som
standard), och en kampanj som inte betalar något där sorteras bort.
"""

from __future__ import annotations

import math
import re
import statistics
from datetime import UTC, datetime

from .content_views import TOP_CLIPS
from .preferences import COUNTRIES, DEFAULT_COUNTRY

MAX_MIN_VIEWS = 10_000

# Delen Visningar (Robins beslut 18 nov 2026): medianen av de 10 bästa klippens visningar, på log-skala.
VIEWS_MAX = 40
# Okänt ger 10 av 40, inte halva: en kampanj utan klipp att räkna på ska få färre poäng än en med bevisat bra klipp
# (Robins ja, 18 nov 2026). 10 poäng motsvarar en median på cirka 6 300 för de bästa klippen med skalan 2 000–200 000.
VIEWS_UNKNOWN = 10
MIN_CLIPS = 5  # färre klipp, på Twitch eller Content Rewards, visar inget mönster (Robins beslut 18 nov 2026)
CAMPAIGN_VIEWS = (2_000, 200_000)  # kampanjens klipp: 0 poäng vid 2 000 eller lägre, 40 vid 200 000 eller högre
TWITCH_VIEWS = (500, 200_000)  # kanalens Twitch-klipp, 30 dagar: 0 vid 500 eller lägre, 40 vid 200 000 eller högre
MAX_PAYOUT, PAYOUT_MAX = (100, 1_000), 10  # dollar per klipp: 0 poäng vid 100 eller lägre, 10 vid 1 000 eller högre
# Robins vikter (18 nov 2026). Delarna räknar med maxvärdena från ombyggnad-fas1 och _part skalar till de här.
PART_MAX = {"fresh": 12, "minimum": 10, "source": 8, "budget": 8, "pay": 5, "proof": 5, "hassle": 2}

FACE = re.compile(r"\bface\b|facecam of you|your voice|film yourself|on camera|\bugc\b", re.IGNORECASE)
US = re.compile(r"\bUSA?\b")  # versaler, så att "us" och "status" inte räknas
RICH = re.compile(r"united states|tier ?1", re.IGNORECASE)
LANGUAGES = ("spanish", "german", "french", "portuguese", "italian", "arabic", "hindi", "turkish", "polish",
             "dutch", "russian", "japanese", "korean", "chinese", "mandarin", "indonesian", "vietnamese", "thai",
             "swedish", "norwegian", "danish", "finnish", "romanian", "czech", "hungarian", "greek", "hebrew",
             "tagalog", "filipino", "urdu", "bengali", "malay")
PLATFORM_NAMES = {"tiktok": "TikTok", "youtube": "YouTube", "instagram": "Instagram"}
NOT_PAID = "Betalar inte för visningar från"  # början på orsaken, landet följer
OTHER_LANGUAGE = re.compile(rf"\b(?:in|language:?)\s+(?:{'|'.join(LANGUAGES)})\b", re.IGNORECASE)


def _time(value) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _first(*values):
    return next((value for value in values if value is not None), None)


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


def pay_number(pay: dict | None, key: str) -> float | None:
    """Ett tal ur pay, dollar eller visningar. Bara int och float räknas, inte bool; annat är som om fältet saknas."""
    value = (pay or {}).get(key)
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


def min_views(f: dict) -> int | None:
    pay = f["pay"] or {}
    views = pay_number(pay, "minViews")
    if views:
        return int(views)
    minimum = pay_number(pay, "minPayoutUsd")
    if minimum and f["rate"]:
        return math.ceil(1000 * minimum / f["rate"])
    return None


def _days(moment: datetime | None, now: datetime) -> float | None:
    return None if moment is None else (moment - now).total_seconds() / 86400


def _part(key: str, label: str, points: float, most: int, fact: str) -> dict:
    """En del av betyget. points är räknat mot most och skalas här till delens max i PART_MAX, om den finns där."""
    new = PART_MAX.get(key, most)
    return {"key": key, "label": label, "points": round(points * new / most, 1), "max": new, "fact": fact}


def _unknown(key: str, label: str, most: int, why: str = "") -> dict:
    return _part(key, label, most / 2, most, f"Okänt: {why}" if why else "Okänt")


def _number(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


def _money(value: float) -> str:
    """Två decimaler med decimalkomma: 1,80."""
    return f"{value:.2f}".replace(".", ",")


def _cents(value: object) -> float:
    return value if isinstance(value, int | float) and not isinstance(value, bool) else 0


def _platform_rate(geo: dict, country: str, platform: str) -> float:
    """Dollar per 1 000 för landet på plattformen: först en tier med landet, sedan restOfWorld, och när plattformen
    inte finns i geoRates alls payoutModel. Annars 0."""
    def rows(where: list[dict]) -> list[dict]:
        return [row for row in where if row.get("platform") == platform]

    tiers, rest = rows(geo["rates"].get("tiers", [])), rows(geo["rates"].get("restOfWorld", []))
    mine = [tier for tier in tiers if country in (tier.get("countries") or [])]
    found = mine or rest or ([] if tiers else rows(geo.get("payout_model", [])))
    return max((_cents(row.get("rate")) for row in found), default=0) / 100  # landet i flera tiers: det högsta


def _geo_priced(geo: object) -> bool:
    """Kampanjen har pris per land. geo kommer från examples.parse: rates är None eller har tiers och restOfWorld
    som listor med dicts."""
    return isinstance(geo, dict) and geo.get("priced") is True and geo.get("rates") is not None


def _readable(rates: dict) -> bool:
    """geoRates har rader i restOfWorld eller tiers, och varje rate där är ett tal."""
    found = rates.get("restOfWorld", []) + rates.get("tiers", [])
    return bool(found) and all(pay_number(row, "rate") is not None for row in found)


def _country_unknown(geo: dict | None) -> bool:
    """Ingen kampanjsida, sidan gick inte att läsa, eller priserna per land på den gick inte att läsa."""
    return geo is None or (_geo_priced(geo) and not _readable(geo["rates"]))


def _platforms(geo: dict) -> list[str]:
    """Kampanjens plattformar: de i payoutModel, i ordning och en gång var."""
    names = (str(row.get("platform") or "") for row in geo.get("payout_model", []))
    return list(dict.fromkeys(name for name in names if name))


def country_rates(geo: dict | None, country: str) -> dict[str, float] | None:
    """Priset i dollar per 1 000 för landet på varje plattform i payoutModel. None när kampanjen inte har pris per
    land, när sidan inte är läst, eller när priserna per land inte går att läsa (då räknas det vanliga priset)."""
    if not _geo_priced(geo) or not _readable(geo["rates"]):
        return None
    return {platform: _platform_rate(geo, country, platform) for platform in _platforms(geo)} or None


def _country_name(country: str) -> str:
    return COUNTRIES.get(country, country)


def _unpaid(rates: dict[str, float] | None) -> str | None:
    """Plattformarna som inte betalar, som text, när några men inte alla betalar. Annars None."""
    rates = rates or {}
    unpaid = [PLATFORM_NAMES.get(p, p.capitalize()) for p, rate in rates.items() if rate <= 0]
    if not 0 < len(unpaid) < len(rates):
        return None
    return " och ".join([", ".join(unpaid[:-1]), unpaid[-1]] if len(unpaid) > 1 else unpaid)


def _pays_nothing(rates: dict[str, float] | None) -> bool:
    return rates is not None and not any(rate > 0 for rate in rates.values())


def excluded_for_country(excluded: str | None) -> bool:
    """Orsaken är att kampanjen inte betalar för landet. Priserna kan ändras, så sidan läses om ändå."""
    return (excluded or "").startswith(NOT_PAID)


def country_note(data: dict, country: str) -> str | None:
    """För webben: vilka plattformar som inte betalar för landet, när några andra gör det. Annars None."""
    unpaid = _unpaid(country_rates(data.get("geo"), country))
    return unpaid and f"{unpaid} betalar inte för visningar från {_country_name(country)}."


def _pay_part(f: dict, data: dict, cpm_median: float, country: str) -> dict:
    """Betalning efter avgift mot marknadens median. Med pris per land: bästa priset i landet."""
    rates = country_rates(data.get("geo"), country)
    net = _first(f["net"], f["rate"] * (1 - f["fee"]) if f["rate"] is not None else None)
    where = ""
    if rates:
        net, where = max(rates.values()) * (1 - f["fee"]), f" i {_country_name(country)}"
    if net is None:
        return _unknown("pay", "Betalning", 10)
    fact = f"{_money(net)} dollar per 1 000 efter avgift{where}, marknaden {_money(cpm_median)}"
    if _country_unknown(data.get("geo")):
        fact += ", Land okänt"
    elif unpaid := _unpaid(rates):
        fact += f". {unpaid} betalar inte för {_country_name(country)}."
    return _part("pay", "Betalning", min(10.0, 5 * min(net / cpm_median, 3)), 10, fact)


def _top_median(views: list[int]) -> tuple[float, int]:
    """Medianen av de högst 10 bästa, och hur många de var."""
    top = sorted(views, reverse=True)[:TOP_CLIPS]
    return statistics.median(top), len(top)


def _log_points(median: float, low: int, high: int, most: int = VIEWS_MAX) -> float:
    if median <= low:
        return 0.0
    return min(most, most * math.log(median / low) / math.log(high / low))


def _views_part(data: dict) -> dict:
    """Hur många visningar kampanjens innehåll brukar få: kanalens Twitch-klipp om de är minst 5,
    annars kampanjens klipp på Content Rewards om de är minst 5."""
    twitch, clips = data.get("twitch_views") or [], data.get("clip_views") or []
    if len(twitch) >= MIN_CLIPS:
        median, _ = _top_median(twitch)
        return _part("views", "Visningar", _log_points(median, *TWITCH_VIEWS), VIEWS_MAX,
                     f"Twitch: median {_number(median)} visningar för kanalens bästa klipp, 30 dagar")
    if len(clips) >= MIN_CLIPS:
        median, count = _top_median(clips)
        return _part("views", "Visningar", _log_points(median, *CAMPAIGN_VIEWS), VIEWS_MAX,
                     f"Median {_number(median)} visningar för kampanjens {count} bästa klipp")
    have = f"{len(twitch)} Twitch-klipp och {len(clips)} kampanjklipp, minst {MIN_CLIPS} behövs"
    why = have if twitch or clips else "inga klipp att räkna på ännu"
    return _part("views", "Visningar", VIEWS_UNKNOWN, VIEWS_MAX, f"Okänt: {why}")


def _max_payout_part(pay: dict | None) -> dict:
    """Hur mycket ett klipp högst kan ge (maxPayoutUsd), på log-skala. Utan tak: full poäng."""
    if pay is None:  # analytics är inte hämtad
        return _unknown("max_payout", "Max payout per klipp", PAYOUT_MAX)
    cap = pay_number(pay, "maxPayoutUsd")
    points = PAYOUT_MAX if cap is None else _log_points(cap, *MAX_PAYOUT, PAYOUT_MAX)
    fact = "Inget tak per klipp" if cap is None else f"Högst {_number(cap)} dollar per klipp"
    return _part("max_payout", "Max payout per klipp", points, PAYOUT_MAX, fact)


def _fresh_part(f: dict, drain_days: float | None, now: datetime) -> dict:
    """Färskhet och konkurrens. Antal klippare finns inte i ClippingAlpha."""
    if f["launched"] is None:
        return _unknown("fresh", "Färskhet och konkurrens", 25)
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
    return _part("fresh", "Färskhet och konkurrens", max(0.0, points - minus), 25, fact)


def _minimum_unknown(f: dict, views: int | None) -> bool:
    """analytics saknas, eller det finns en minsta utbetalning i dollar men inget pris att räkna visningar med."""
    if f["pay"] is None:
        return True
    return views is None and bool(pay_number(f["pay"], "minPayoutUsd")) and not f["rate"]


def _minimum_part(f: dict, views: int | None, median: float) -> dict:
    """Chans att nå minimum: Robins median mot visningarna som krävs för betalning."""
    label = "Chans att nå minimum"
    if _minimum_unknown(f, views):
        return _unknown("minimum", label, 20)
    if views is None:
        return _part("minimum", label, 20, 20, "Ingen minsta utbetalning")
    ratio = views / max(median, 1)
    points = 20 if ratio <= 1 else 0 if ratio >= 5 else 20 * (1 - math.log(ratio) / math.log(5))
    return _part("minimum", label, points, 20,
                 f"{_number(views)} visningar för betalning, din median är {_number(median)}")


def _budget_part(f: dict, drain_days: float | None) -> dict:
    """Budgeten räcker: prognosen, annars hur mycket som är kvar."""
    label = "Budgeten räcker"
    if drain_days is not None:
        return _part("budget", label, 15 * min(max(drain_days, 0), 7) / 7, 15,
                     f"Räcker cirka {max(drain_days, 0):.0f} dagar till")
    if f["left"] is not None:
        return _part("budget", label, 10 * min(f["left"], 1000) / 1000, 15,
                     f"Ingen prognos, {_number(f['left'])} dollar kvar")
    return _unknown("budget", label, 15)


def evaluate(data: dict, median: float, cpm_median: float, now: datetime,
             country: str = DEFAULT_COUNTRY) -> tuple[int, list[dict], str | None]:
    """Betyg 0–100, delarna med svensk förklaring, och orsaken om kampanjen sorteras bort. country är Robins land."""
    f = facts(data)
    texts = " ".join([f["about"], *(text for _, text in f["rules"])])
    drain_days = _days(f["drain"], now)
    views = min_views(f)
    parts = [_views_part(data), _fresh_part(f, drain_days, now)]  # 1. Visningar, 40 av 100, och 2. Färskhet

    parts.append(_minimum_part(f, views, median))  # 3. Chans att nå minimum

    # 4. Källan.
    source = f"{f['title']} {texts}"
    twitch, stream = "twitch" in source.lower(), bool(re.search(r"\bstream", source, re.IGNORECASE))
    other = bool(OTHER_LANGUAGE.search(texts))
    points = 8 * twitch + 4 * stream + 3 * (not other)
    if f["niche"] in ("streams", "gaming"):
        points = max(points, 4)
    said = [word for word, yes in (("Twitch", twitch), ("stream", stream), ("engelska", not other)) if yes]
    parts.append(_part("source", "Källan", points, 15, ", ".join(said) or "Varken Twitch eller stream"))

    parts.append(_budget_part(f, drain_days))  # 5. Budgeten räcker

    # 6. Betalning efter avgift mot marknadens median.
    parts.append(_pay_part(f, data, cpm_median, country))

    # 7. Max payout per klipp, direkt efter betalningen.
    parts.append(_max_payout_part(f["pay"]))

    # 8. Bevis på utbetalning.
    if f["verified"] is None and f["adjustments"] is None:
        parts.append(_unknown("proof", "Bevis på utbetalning", 10))
    else:
        paid = any(x.get("key") == "payout" and re.search(r"24 hours|7 days", str(x.get("label")))
                   for x in f["adjustments"] or [])
        points = 4 * bool(f["verified"]) + 4 * paid + 2 * (not f["stalled"])
        said = [text for text, yes in (("verifierad organisatör", f["verified"]), ("betalat ut nyligen", paid))
                if yes]
        parts.append(_part("proof", "Bevis på utbetalning", points, 10, ", ".join(said) or "Ingen utbetalning syns"))

    # 9. Krångel.
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
        (_pays_nothing(country_rates(data.get("geo"), country)), f"{NOT_PAID} {_country_name(country)}"),
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
