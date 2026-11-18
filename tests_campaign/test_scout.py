"""Kampanjbevakning. Låtsas-ClippingAlpha via httpx.MockTransport, inga riktiga anrop.

Formen på en sökträff är tagen från market.similar i get_campaign_analytics, och detaljerna från
get_campaign och get_campaign_analytics (~/.amp/in/kampanj/raw/). Ett riktigt search_campaigns-svar
med träffar gick inte att hämta: gränsen på 500 anrop per dag var nådd (-32001).
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api, content_views, preferences, scout, scout_api, scout_score
from campaign.cliplog import ClipLog

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
CR = "https://contentrewards.com/c/campaigns/a1cefc3b-4533-438f-8e70-a7299550f1cf"
LIMIT_BODY = {"jsonrpc": "2.0", "error": {"code": -32001, "message": "Sign in to ClippingAlpha to go on: signed out, "
                                                                     "one network gets 500 calls a day."}, "id": None}


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _campaign(ref: str = "timmy/streams", age_h: float = 24, **over) -> dict:
    """get_campaign: structuredContent.campaign."""
    brand, series = ref.split("/")
    return {
        "campaignId": "1", "title": f"Clip {series} Twitch Streams", "brand": brand.title(), "brandSlug": brand,
        "seriesSlug": series, "round": 1, "state": "live", "payModel": "cpm", "platforms": ["tiktok", "youtube"],
        "niche": "streams", "about": "Clip the best moments from the Twitch streams.", "membersOnly": False,
        "election": False, "audience": None, "audienceLabel": None, "audienceNote": None, "region": None,
        "alpha": {"score": 80, "netPer1kUsd": 1.8},
        "drain": {"p50": _iso(NOW + timedelta(days=10)), "stalled": False}, "stalled": None,
        "rules": {"checklist": [{"kind": "account", "text": "Post in English"}]},
        "organizerVerified": True,
        "whereToJoin": [{"board": "Content Rewards", "boardSlug": "content-rewards", "ratePer1kUsd": 2,
                         "budgetLeftUsd": 5000, "budgetTotalUsd": 6000, "boardFee": 0.1, "openJoin": True,
                         "gateType": "none", "joinUrl": "https://clippingalpha.com/go/1?via=mcp"}],
        "launchedAt": _iso(NOW - timedelta(hours=age_h)),
        "pageUrl": f"https://clippingalpha.com/campaigns/{ref}?via=mcp", **over}


def _analytics(ref: str = "timmy/streams", **over) -> dict:
    """get_campaign_analytics: structuredContent."""
    return {
        "campaign": {"ref": ref, "type": "clipping", "state": "live"},
        "alpha": {"verdicts": [{"key": "fill", "label": "Fill", "verdict": "Slow", "tone": "bad"}],
                  "adjustments": [{"key": "payout", "label": "$100 paid to clippers in 24 hours", "points": 3}]},
        "pay": {"model": "cpm", "ratePer1kUsd": 2, "boardFeePct": 0.1, "minViews": None, "minPayoutUsd": None,
                "maxPayoutUsd": 500},
        "runway": {"budgetLeftUsd": 5000, "budgetTotalUsd": 6000, "stalled": False}, **over}


def _item(ref: str = "timmy/streams", age_h: float = 24, **over) -> dict:
    """En träff i search_campaigns, som market.similar."""
    brand, series = ref.split("/")
    return {
        "campaignId": "1", "title": f"Clip {series} Twitch Streams", "brand": brand.title(), "brandSlug": brand,
        "seriesSlug": series, "board": "Content Rewards", "boardSlug": "content-rewards", "state": "live",
        "campaignType": "clipping", "payModel": "cpm", "platforms": ["tiktok", "youtube"], "ratePer1kUsd": 2,
        "netPer1kUsd": 1.8, "budgetLeftUsd": 5000, "budgetTotalUsd": 6000, "alphaScore": 80,
        "drainP50": _iso(NOW + timedelta(days=10)), "openJoin": True, "niche": "streams", "membersOnly": False,
        "election": False, "audience": None, "region": None, "launchedAt": _iso(NOW - timedelta(hours=age_h)),
        "pageUrl": f"https://clippingalpha.com/campaigns/{ref}?via=mcp",
        "joinUrl": "https://clippingalpha.com/go/1?via=mcp", **over}


def _data(**campaign) -> dict:
    return {"search": _item(), "campaign": _campaign(**campaign), "analytics": _analytics()}


def _parts(data: dict, median: float = 1260, cpm_median: float = 1.05) -> tuple[int, dict, str | None]:
    total, parts, excluded = scout.evaluate(data, median, cpm_median, NOW)
    return total, {part["key"]: part for part in parts}, excluded


# ---------- betyget ----------


# Robins vikter: varje del räknar med sina ursprungliga maxvärden (ombyggnad-fas1) och skalas till det nya.
SCALE = {"fresh": 12 / 25, "minimum": 10 / 20, "source": 8 / 15, "budget": 8 / 15, "pay": 5 / 10, "proof": 5 / 10,
         "hassle": 2 / 5}


def test_a_fresh_twitch_campaign_with_everything_scores_high() -> None:
    total, parts, excluded = _parts(_data())
    assert excluded is None
    assert list(parts) == ["views", "fresh", "minimum", "source", "budget", "pay", "max_payout", "proof", "hassle"]
    assert [parts[key]["max"] for key in parts] == [40, 12, 10, 8, 8, 5, 10, 5, 2]
    assert sum(parts[key]["max"] for key in parts) == 100
    pay = 5 * 1.8 / 1.05 * SCALE["pay"]  # 4,3: betalningen är inte full
    cap = 10 * math.log(500 / 100) / math.log(1000 / 100)  # 7,0: taket är 500 dollar
    assert {key: parts[key]["points"] for key in parts} == {
        "views": 20, "fresh": 12, "minimum": 10, "source": 8, "budget": 8, "pay": pytest.approx(pay, abs=0.05),
        "max_payout": pytest.approx(cap, abs=0.05), "proof": 5, "hassle": 2}
    assert total == round(20 + 12 + 10 + 8 + 8 + pay + cap + 5 + 2) == 76  # förut 79
    assert all(part["label"] and part["fact"] for part in parts.values())


def test_a_campaign_with_every_part_full_still_gets_100() -> None:
    data = _data(alpha={"netPer1kUsd": 4.0})
    data["search"]["netPer1kUsd"] = 4.0
    data["analytics"]["pay"]["maxPayoutUsd"] = None
    total, parts, _ = _parts({**data, "clip_views": [1_000_000] * 10})
    assert all(part["points"] == part["max"] for part in parts.values())
    assert total == 100 == sum(part["max"] for part in parts.values() if part["key"] != "max_payout") + 10


@pytest.mark.parametrize(("cap", "points"), [(1_200, 10), (1_000, 10), (100, 0), (50, 0),
                                            (600, pytest.approx(7.8, abs=0.05))])
def test_max_payout_per_clip_is_logarithmic_from_100_to_1000_dollars(cap, points) -> None:
    data = _data()
    data["analytics"]["pay"]["maxPayoutUsd"] = cap
    part = _parts(data)[1]["max_payout"]
    assert (part["points"], part["max"], part["label"]) == (points, 10, "Max payout per klipp")
    assert part["fact"] == f"Högst {cap:,} dollar per klipp".replace(",", " ")


def test_max_payout_without_a_cap_is_full_and_unknown_without_analytics() -> None:
    data = _data()
    data["analytics"]["pay"]["maxPayoutUsd"] = None
    part = _parts(data)[1]["max_payout"]
    assert (part["points"], part["fact"]) == (10, "Inget tak per klipp")
    del data["analytics"]["pay"]["maxPayoutUsd"]
    assert _parts(data)[1]["max_payout"]["points"] == 10
    part = _parts({"search": _item(), "campaign": _campaign()})[1]["max_payout"]
    assert (part["points"], part["max"], part["fact"]) == (5, 10, "Okänt")


@pytest.mark.parametrize(("cap", "minimum", "views"), [("1200", "x", "abc"), (True, True, True)])
def test_payout_fields_that_are_not_numbers_count_as_missing(cap, minimum, views) -> None:
    missing = _data()
    del missing["analytics"]["pay"]["maxPayoutUsd"]
    del missing["analytics"]["pay"]["minPayoutUsd"]
    del missing["analytics"]["pay"]["minViews"]
    data = _data()
    data["analytics"]["pay"].update(maxPayoutUsd=cap, minPayoutUsd=minimum, minViews=views)
    assert scout.min_views(scout.facts(data)) is None
    assert _parts(data) == _parts(missing)


@pytest.mark.parametrize(("age_h", "points"), [
    (47, 25), (24 * 10, 25 - 20 * (10 - 2) / (21 - 2)), (24 * 25, 5 * (30 - 25) / (30 - 21)), (24 * 31, 0)])
def test_freshness_falls_with_age(age_h, points) -> None:
    part = _parts(_data(age_h=age_h))[1]["fresh"]
    assert part["points"] == pytest.approx(points * SCALE["fresh"], abs=0.05)


@pytest.mark.parametrize(("verdict", "drain_days", "minus"), [("Fast", 10, 10), ("Fair", 10, 5), ("Slow", 2, 10),
                                                               ("Slow", 5, 5), ("Too early", 10, 0)])
def test_freshness_loses_points_when_the_budget_goes_fast(verdict, drain_days, minus) -> None:
    data = _data(drain={"p50": _iso(NOW + timedelta(days=drain_days)), "stalled": False})
    data["analytics"]["alpha"]["verdicts"][0]["verdict"] = verdict
    assert _parts(data)[1]["fresh"]["points"] == pytest.approx((25 - minus) * SCALE["fresh"], abs=0.05)


def test_unknown_age_gives_half_and_okant() -> None:
    data = _data(launchedAt=None)
    data["search"]["launchedAt"] = None
    fresh = _parts(data)[1]["fresh"]
    assert (fresh["points"], fresh["fact"]) == (6, "Okänt")  # halva det nya maxvärdet


def test_the_chance_to_reach_the_minimum_is_logarithmic() -> None:
    data = _data()
    data["analytics"]["pay"]["minPayoutUsd"] = 5  # 5 dollar vid 2 dollar per 1 000: 2 500 visningar
    part = _parts(data)[1]["minimum"]
    points = 20 * (1 - math.log(2500 / 1260) / math.log(5)) * SCALE["minimum"]
    assert part["points"] == pytest.approx(points, abs=0.05)
    assert "2" in part["fact"] and "500" in part["fact"]
    assert _parts(data, median=2500)[1]["minimum"]["points"] == 10
    assert _parts(data, median=500)[1]["minimum"]["points"] == 0


def test_no_details_gives_half_points_and_okant() -> None:
    data = {"search": _item()}  # bara sökträffen: ingen pay, inga regler, ingen organisatör
    _, parts, excluded = _parts(data)
    assert excluded is None
    assert (parts["minimum"]["points"], parts["minimum"]["fact"]) == (5, "Okänt")
    assert (parts["proof"]["points"], parts["proof"]["fact"]) == (2.5, "Okänt")
    assert (parts["max_payout"]["points"], parts["max_payout"]["fact"]) == (5, "Okänt")
    assert (parts["views"]["points"], parts["views"]["fact"]) == (20, "Okänt: inga klipp att räkna på ännu")
    assert parts["fresh"]["points"] == 12  # åldern finns i sökträffen


@pytest.mark.parametrize(("title", "about", "niche", "rules", "points"), [
    ("Clip Twitch streams", "", "film-and-tv", ["Post in English"], 15),
    ("Clip the streamer", "", "film-and-tv", [], 7),  # stream 4 + inget annat språk 3
    ("Movie trailer", "Cut the trailer.", "gaming", ["Post in English"], 4),  # spel ger minst 4
    ("Movie trailer", "Cut the trailer.", "film-and-tv", [], 3)])
def test_the_source_points(title, about, niche, rules, points) -> None:
    data = _data(title=title, about=about, niche=niche,
                 rules={"checklist": [{"kind": "account", "text": text} for text in rules]})
    data["search"].update(title=title, niche=niche)
    assert _parts(data)[1]["source"]["points"] == pytest.approx(points * SCALE["source"], abs=0.05)


@pytest.mark.parametrize(("drain", "left", "points"), [(3.5, 5000, 7.5 * 8 / 15), (8, 5000, 8), (None, 2000, 10 * 8 / 15)])
def test_the_budget_lasts(drain, left, points) -> None:
    p50 = _iso(NOW + timedelta(days=drain)) if drain else None
    data = _data(drain={"p50": p50, "stalled": False})
    data["campaign"]["whereToJoin"][0]["budgetLeftUsd"] = left
    data["search"]["drainP50"] = p50
    assert _parts(data)[1]["budget"]["points"] == pytest.approx(points, abs=0.05)


@pytest.mark.parametrize(("net", "points"), [(1.575, 7.5 / 2), (None, 5 * 1.8 / 1.2 / 2), (4.0, 5)])
def test_the_pay_against_the_market(net, points) -> None:
    data = _data(alpha={"netPer1kUsd": net})
    data["search"]["netPer1kUsd"] = net
    assert _parts(data, cpm_median=1.05 if net else 1.2)[1]["pay"]["points"] == pytest.approx(points, abs=0.05)


def test_proof_of_payout() -> None:
    data = _data(organizerVerified=False)
    data["analytics"]["alpha"]["adjustments"] = [{"key": "payout", "label": "$50 paid to clippers in 7 days"}]
    assert _parts(data)[1]["proof"]["points"] == (4 + 2) * SCALE["proof"]  # betalat ut nyligen, står inte still
    data["analytics"]["alpha"]["adjustments"] = []
    assert _parts(data)[1]["proof"]["points"] == 2 * SCALE["proof"]


def test_hassle_for_applications_and_many_caption_rules() -> None:
    rules = [{"kind": "caption", "text": f"Tag @x{i}"} for i in range(4)] + [{"kind": "disclosure", "text": "#ad"}]
    data = _data(rules={"checklist": rules})
    assert _parts(data)[1]["hassle"]["points"] == pytest.approx((5 - 2) * SCALE["hassle"])  # 5 krav, 2 för många
    data["campaign"]["whereToJoin"][0].update(openJoin=False, gateType="application_full")
    assert _parts(data)[1]["hassle"]["points"] == 0


# ---------- bortsorterade ----------


def _with(**changes) -> dict:
    data = _data()
    for path, value in changes.items():
        where, key = path.split("__")
        data[where][key] = value
    return data


@pytest.mark.parametrize("data", [
    _with(search__state="paused"),
    _with(campaign__drain={"p50": None, "stalled": True}),
    _with(search__budgetLeftUsd=250),  # 96 % använd
    _with(search__campaignType="ugc", analytics__campaign={"type": "ugc"}),
    _with(campaign__platforms=["instagram"]),
    _with(campaign__about="Show your face while you react."),
    _with(campaign__rules={"checklist": [{"kind": "do", "text": "Film yourself playing"}]}),
    _with(campaign__about="Real UGC only."),
    _with(campaign__membersOnly=True),
    _with(campaign__election=True),
    _with(campaign__rules={"checklist": [{"kind": "account", "text": "Audience in US, UK, CA and AU"}]}),
    _with(campaign__audienceLabel="Tier 1 viewers"),
    _with(campaign__rules={"checklist": [{"kind": "account", "text": "Post in Spanish"}]}),
    _with(analytics__pay={**_analytics()["pay"], "minPayoutUsd": 25})])  # 12 500 visningar
def test_campaigns_that_do_not_fit_are_excluded_with_a_reason(data) -> None:
    excluded = _parts(data)[2]
    assert isinstance(excluded, str) and excluded


def test_the_streamers_facecam_or_a_word_like_status_is_not_excluded() -> None:
    data = _with(campaign__about="Keep the streamer's facecam in the clip. Status updates bonus.",
                 campaign__rules={"checklist": [{"kind": "do", "text": "Use the focus moments"}]})
    assert _parts(data)[2] is None


# ---------- låtsas-ClippingAlpha ----------


class FakeAlpha:
    """Svarar som ClippingAlphas MCP på initialize, notifications/initialized och tools/call."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.newest: list[dict] = []
        self.alpha: list[dict] = []
        self.details: dict[str, tuple[dict, dict]] = {}
        self.broken: set[str] = set()  # get_campaign svarar isError för de här
        self.broken_assets: set[str] = set()  # get_assets svarar isError för de här
        self.limit_at: str | None = None  # verktyget där ClippingAlphas dygnsgräns slår till
        self.limit_reached = False
        self.lose_session_once = False
        self.down = False
        self.sse = False
        self.assets: dict[str, dict] = {}  # get_assets per kampanj
        self.go: list[httpx.Request] = []  # GET /go/<id>
        self.go_status = 302  # svaret från /go/; 302 leder till kampanjsidan och sätter cookies som den riktiga
        self.clip_views: list[int] | None = None  # kampanjens klipp på Content Rewards
        self.geo: dict | None = None  # priset per land på samma sida
        self.twitch_views: list[int] | None = None
        self.clip_calls: list[str] = []
        self.twitch_calls: list[tuple[str, datetime]] = []
        self.vod_logins: dict[str, str] = {}  # Helix /videos: video-id och kanalen
        self.vod_calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            self.go.append(request)
            if self.go_status != 302:
                return httpx.Response(self.go_status, text="fel")
            return httpx.Response(302, headers=[("Location", f"{CR}?utm_source=clippingalpha&utm_content=direct"),
                                                ("Set-Cookie", "ca_vid=1; Path=/"), ("Set-Cookie", "_fbp=fb.1; Path=/")])
        self.requests.append(request)
        if self.down:
            return httpx.Response(500, text="nere")
        if self.limit_reached:
            return httpx.Response(401, json=LIMIT_BODY)
        msg = json.loads(request.content)
        if msg["method"] == "initialize":
            return httpx.Response(200, headers={"Mcp-Session-Id": "s1"},
                                  json={"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2025-06-18"}})
        if msg["method"] == "notifications/initialized":
            return httpx.Response(202)
        assert request.headers["mcp-session-id"] == "s1"
        if self.lose_session_once:
            self.lose_session_once = False
            return httpx.Response(404)
        name, args = msg["params"]["name"], msg["params"]["arguments"]
        if name == self.limit_at:
            return httpx.Response(401, json=LIMIT_BODY)
        if name == "search_campaigns":
            items = self.newest if args["sort"] == "newest" else self.alpha
            content = {"total": len(items), "cpm": {"medianUsd": 1.2}, "campaigns": items}
        elif (name, args.get("campaign")) in {("get_campaign", ref) for ref in self.broken} | {
                ("get_assets", ref) for ref in self.broken_assets}:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {
                "isError": True, "content": [{"type": "text", "text": "Campaign not found"}]}})
        elif name == "get_campaign":
            content = {"campaign": self.details[args["campaign"]][0]}
        elif name == "get_assets":
            content = self.assets.get(args["campaign"], {"videos": []})
        else:
            content = self.details[args["campaign"]][1]
        body = {"jsonrpc": "2.0", "id": msg["id"], "result": {"content": [{"type": "text", "text": "..."}],
                                                               "structuredContent": content}}
        if self.sse:
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                                  text=f"event: message\ndata: {json.dumps(body)}\n\n")
        return httpx.Response(200, json=body)

    def calls(self, method: str, tool: str | None = None) -> list[dict]:
        out = []
        for request in self.requests:
            msg = json.loads(request.content)
            if msg["method"] == method and (tool is None or msg["params"]["name"] == tool):
                out.append(msg)
        return out

    def campaign_page(self, cr_url: str) -> dict | None:
        self.clip_calls.append(cr_url)
        return None if self.clip_views is None else {"clip_views": self.clip_views, "geo": self.geo}

    def twitch_clip_views(self, login: str, now: datetime) -> list[int] | None:
        self.twitch_calls.append((login, now))
        return self.twitch_views

    def twitch_login_from_vod(self, video_id: str) -> str | None:
        self.vod_calls.append(video_id)
        return self.vod_logins.get(video_id)

    def add(self, ref: str, age_h: float = 24, **campaign) -> None:
        self.newest.append(_item(ref, age_h=age_h))
        self.details[ref] = (_campaign(ref, age_h=age_h, **campaign), _analytics(ref))


class Notes:
    def __init__(self):
        self.calls: list[tuple[list, dict]] = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))


@pytest.fixture
def fake(monkeypatch) -> FakeAlpha:
    fake = FakeAlpha()
    monkeypatch.setattr(scout, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(fake)))
    monkeypatch.setattr(scout, "MIN_GAP_S", 0)
    monkeypatch.setattr(scout, "CLIP_PAGE_GAP_S", 0)
    monkeypatch.setattr(scout, "_session", {"id": None})
    monkeypatch.setattr(scout, "_now", lambda: NOW)
    monkeypatch.setattr(scout, "campaign_page", fake.campaign_page)
    monkeypatch.setattr(scout, "twitch_clip_views", fake.twitch_clip_views)
    monkeypatch.setattr(scout, "twitch_login_from_vod", fake.twitch_login_from_vod)
    return fake


@pytest.fixture
def notes(monkeypatch) -> Notes:
    notes = Notes()
    monkeypatch.setattr(scout.subprocess, "run", notes)
    monkeypatch.setattr(scout.sys, "platform", "darwin")
    monkeypatch.setattr(scout.shutil, "which", lambda name: "/opt/homebrew/bin/terminal-notifier"
                        if name == "terminal-notifier" else None)
    return notes


@pytest.fixture
def client(settings, fake, notes) -> TestClient:
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(scout_api.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    return TestClient(app)


def _later(monkeypatch, **delta) -> None:
    moment = NOW + timedelta(**delta)
    monkeypatch.setattr(scout, "_now", lambda: moment)


# ---------- hämtningen ----------


def test_a_run_searches_fetches_details_scores_and_lists(client, settings, fake, monkeypatch) -> None:
    fake.add("timmy/streams")
    fake.add("brand/trailer", age_h=24 * 40, title="Movie trailer", about="Cut the trailer.", niche="film-and-tv")
    fake.newest[1].update(title="Movie trailer", niche="film-and-tv")
    fake.sse = True
    scout.Scout(settings).run_once()

    assert len(fake.calls("initialize")) == 1
    assert len(fake.calls("notifications/initialized")) == 1
    newest, alpha = fake.calls("tools/call", "search_campaigns")
    assert newest["params"]["arguments"] == {"board": "content-rewards", "type": "clipping",
                                             "platforms": ["tiktok", "youtube"], "new_within_hours": 72,
                                             "sort": "newest", "limit": 25}
    assert alpha["params"]["arguments"] == {"board": "content-rewards", "type": "clipping",
                                            "platforms": ["tiktok", "youtube"], "sort": "alpha", "limit": 25}
    assert [c["params"]["arguments"] for c in fake.calls("tools/call", "get_campaign")] == [
        {"campaign": "timmy/streams"}, {"campaign": "brand/trailer"}]
    assert len(fake.calls("tools/call", "get_campaign_analytics")) == 2
    assert len(fake.calls("tools/call", "get_assets")) == 2
    assert len(fake.go) == 2

    answer = client.get("/campaign/scout").json()
    assert (answer["enabled"], answer["error"], answer["calls_today"], answer["call_limit"]) == (True, None, 12, 300)
    assert (answer["median_views"], answer["median_source"]) == (1260, "standard")
    assert answer["updated_at"]
    assert [c["ref"] for c in answer["campaigns"]] == ["timmy/streams", "brand/trailer"]  # på betyg
    best = answer["campaigns"][0]
    assert set(best) == {"ref", "title", "brand", "score", "parts", "excluded", "age_hours", "rate_usd",
                         "net_rate_usd", "budget_left_usd", "budget_total_usd", "min_views", "max_payout_usd",
                         "drain_days", "platforms", "mentions_twitch", "open_join", "page_url", "join_url",
                         "first_seen", "is_new", "country_note"}
    assert (best["rate_usd"], best["net_rate_usd"], best["budget_left_usd"], best["budget_total_usd"]) == (
        2, 1.8, 5000, 6000)
    assert (best["age_hours"], best["is_new"], best["mentions_twitch"], best["open_join"]) == (24, True, True, True)
    assert (best["drain_days"], best["max_payout_usd"], best["min_views"]) == (10, 500, None)
    assert best["page_url"] == "https://clippingalpha.com/campaigns/timmy/streams?via=mcp"
    assert answer["campaigns"][1]["is_new"] is False
    assert answer["excluded"] == []

    # nästa körning: samma session, ingen ny alpha-sökning (en gång i timmen), inga nya detaljer
    _later(monkeypatch, minutes=10)
    scout.Scout(settings).run_once()
    assert len(fake.calls("initialize")) == 1
    assert len(fake.calls("tools/call", "search_campaigns")) == 3
    assert len(fake.calls("tools/call", "get_campaign")) == 2
    assert client.get("/campaign/scout").json()["calls_today"] == 13

    _later(monkeypatch, minutes=61)
    scout.Scout(settings).run_once()
    assert len(fake.calls("tools/call", "search_campaigns")) == 5


def test_details_older_than_six_hours_are_fetched_again_for_the_best(settings, fake, monkeypatch) -> None:
    fake.add("timmy/streams")
    scout.Scout(settings).run_once()
    _later(monkeypatch, hours=5)
    scout.Scout(settings).run_once()
    assert len(fake.calls("tools/call", "get_campaign")) == 1
    _later(monkeypatch, hours=7)
    scout.Scout(settings).run_once()
    assert len(fake.calls("tools/call", "get_campaign")) == 2


def test_the_median_comes_from_robins_posts_when_there_are_twenty(client, settings, fake) -> None:
    log = ClipLog(settings.data_dir / "klipp.db")
    for i in range(20):
        clip_id = log.add(vod_id="1", profile="skiimasktimmy", file=f"/x/{i}.mp4", title="t", caption="c",
                          status="postad")
        log.add_post(clip_id, "tiktok", f"https://www.tiktok.com/@a/video/{i}")
    with log._db() as db:
        db.execute("UPDATE posts SET views = 3000 + id")
    answer = client.get("/campaign/scout").json()
    assert (answer["median_views"], answer["median_source"]) == (pytest.approx(3010.5), "dina inlägg")


def test_a_lost_session_is_started_again_once(settings, fake) -> None:
    fake.add("timmy/streams")
    scout.Scout(settings).run_once()
    fake.lose_session_once = True
    scout.Scout(settings).run_once()
    assert len(fake.calls("initialize")) == 2
    assert scout.Scout(settings).overview()["error"] is None


def _blocked(settings) -> datetime | None:
    return scout._time(scout.Scout(settings)._get("blocked_until"))


def test_the_limit_at_clippingalpha_waits_an_hour_not_a_day(client, settings, fake, monkeypatch) -> None:
    fake.add("timmy/streams")
    fake.limit_reached = True
    scout.Scout(settings).run_once()  # dör inte
    answer = client.get("/campaign/scout").json()
    # NOW är 12:00 UTC, alltså 13:00 i Stockholm i november
    assert answer["error"] == "ClippingAlpha tar inte emot fler anrop just nu. Bevakningen försöker igen kl. 14:00."
    assert answer["calls_today"] == 1  # bara anropet som faktiskt gjordes (initialize)
    assert _blocked(settings) == NOW + timedelta(hours=1)
    assert scout.Scout(settings).wait_s() == pytest.approx(3600, abs=1)

    _later(monkeypatch, minutes=30)  # till exempel Kontrollera nu: inga anrop medan spärren gäller
    assert scout.Scout(settings).wait_s() == pytest.approx(1800, abs=1)
    scout.Scout(settings).run_once()
    assert len(fake.requests) == 1
    assert scout.Scout(settings).calls_today() == 1

    _later(monkeypatch, minutes=61)  # spärren har gått ut, men ClippingAlpha säger nej igen
    assert scout.Scout(settings).wait_s() == 0
    scout.Scout(settings).run_once()
    assert _blocked(settings) == NOW + timedelta(minutes=121)
    assert client.get("/campaign/scout").json()["error"].endswith("kl. 15:01.")
    assert scout.Scout(settings).calls_today() == 2

    fake.limit_reached = False
    _later(monkeypatch, minutes=122)
    assert scout.Scout(settings).wait_s() == 0
    scout.Scout(settings).run_once()
    answer = client.get("/campaign/scout").json()
    assert (answer["error"], answer["calls_today"]) == (None, 10)
    assert [c["ref"] for c in answer["campaigns"]] == ["timmy/streams"]


def test_our_own_limit_of_300_calls_a_day(settings, fake, monkeypatch) -> None:
    fake.add("timmy/streams")
    scout.Scout(settings)._count(299)
    scout.Scout(settings).run_once()
    assert len(fake.requests) == 1  # initialize, sedan är gränsen nådd
    overview = scout.Scout(settings).overview()
    assert overview["calls_today"] == 300 and "300" in overview["error"]
    scout.Scout(settings).run_once()
    assert len(fake.requests) == 1


def test_details_wait_when_the_rest_of_the_day_needs_the_calls(settings, fake) -> None:
    fake.add("timmy/streams")
    reserve = scout.reserve(NOW)  # en sökning var 10:e minut och en i timmen resten av dygnet
    assert reserve == 12 * 6 + 12
    scout.Scout(settings)._count(300 - reserve - 5)  # 4 för initialize och sökningarna, 1 kvar: räcker inte för 2
    scout.Scout(settings).run_once()
    assert fake.calls("tools/call", "get_campaign") == []
    assert [c["ref"] for c in scout.Scout(settings).overview()["campaigns"]] == ["timmy/streams"]


def test_a_failing_source_saves_the_error_and_the_run_does_not_raise(settings, fake) -> None:
    fake.down = True
    scout.Scout(settings).run_once()
    assert scout.Scout(settings).overview()["error"]


@pytest.mark.parametrize("step", ["_refresh_clip_views", "_rescore"])
def test_an_error_after_the_limit_does_not_replace_the_limit(settings, fake, monkeypatch, caplog, step) -> None:
    fake.limit_at = "search_campaigns"

    def broken(self, now: datetime) -> None:
        raise RuntimeError("trasig")

    monkeypatch.setattr(scout.Scout, step, broken)
    scout.Scout(settings).run_once()
    assert scout.Scout(settings).overview()["error"] == (
        "ClippingAlpha tar inte emot fler anrop just nu. Bevakningen försöker igen kl. 14:00.")
    assert "trasig" in caplog.text  # felet loggas


def test_an_error_in_the_scores_without_an_error_from_clippingalpha_is_saved(settings, fake, monkeypatch) -> None:
    fake.add("timmy/streams")

    def broken(self, now: datetime) -> None:
        raise RuntimeError("trasig")

    monkeypatch.setattr(scout.Scout, "_rescore", broken)
    scout.Scout(settings).run_once()
    assert scout.Scout(settings).overview()["error"] == (
        "Bevakningen fick ett fel (RuntimeError). Den försöker igen om 10 minuter.")


def test_a_max_payout_that_is_not_a_number_is_not_sent_to_the_web(settings, fake) -> None:
    analytics = _analytics()
    analytics["pay"]["maxPayoutUsd"] = "1200"
    _seed(settings, "timmy/streams", 50, analytics=analytics)
    (view,) = scout.Scout(settings).overview()["campaigns"]
    assert view["max_payout_usd"] is None  # som betyget: "Inget tak per klipp"


# ---------- notisen ----------


def test_a_new_good_campaign_is_notified_once(settings, fake, notes, monkeypatch) -> None:
    fake.add("timmy/streams")
    fake.add("old/streams", age_h=50)
    fake.add("bad/trailer", title="Movie trailer", about="Cut it.", niche="film-and-tv",
             organizerVerified=False, whereToJoin=[])
    fake.newest[2].update(title="Movie trailer", niche="film-and-tv", openJoin=False)
    fake.details["bad/trailer"][1]["alpha"]["adjustments"] = []
    fake.details["bad/trailer"][1]["pay"]["minPayoutUsd"] = 10  # 5 000 visningar: lågt betyg
    scout.Scout(settings).run_once()
    _later(monkeypatch, minutes=10)
    scout.Scout(settings).run_once()

    args, kwargs = notes.calls[0]
    assert len(notes.calls) == 1
    assert args[0] == "/opt/homebrew/bin/terminal-notifier"
    score = next(c["score"] for c in scout.Scout(settings).overview()["campaigns"] if c["ref"] == "timmy/streams")
    assert args[1:] == ["-title", "Klipp", "-message", f"Ny kampanj: Clip streams Twitch Streams, {score} poäng",
                        "-open", "https://clippingalpha.com/campaigns/timmy/streams?via=mcp"]
    assert not kwargs.get("shell")


def test_without_terminal_notifier_osascript_gets_the_text_as_an_argument(settings, fake, notes,
                                                                         monkeypatch) -> None:
    monkeypatch.setattr(scout.shutil, "which", lambda name: None)
    fake.add("timmy/streams", title='Clip "x" Twitch Streams')
    fake.newest[0]["title"] = 'Clip "x" Twitch Streams'
    scout.Scout(settings).run_once()
    ((args, kwargs),) = notes.calls
    assert args[0] == "osascript"
    assert args[-1].startswith('Ny kampanj: Clip "x" Twitch Streams, ')  # texten är ett argument, inte kod
    assert all('"x"' not in part for part in args[:-1])
    assert not kwargs.get("shell")


def _seed(settings, ref: str, score: int, **data) -> None:
    """En kampanj direkt i databasen, som om en tidigare körning hade sparat och betygsatt den."""
    row = {"updated": scout._iso(NOW), "dismissed": 0, "excluded": None}
    row.update({key: data.pop(key) for key in list(data) if key in row})
    with scout.Scout(settings)._db() as db:
        db.execute("INSERT INTO scout_campaigns (ref, data, score, parts, excluded, first_seen, updated, dismissed) "
                   "VALUES (?, ?, ?, '[]', ?, ?, ?, ?)",
                   (ref, json.dumps({"search": _item(ref), **data}), score, row["excluded"], scout._iso(NOW),
                    row["updated"], row["dismissed"]))


@pytest.mark.parametrize(("score", "notified"), [(70, True), (69, True), (65, True), (64, False)])
def test_a_new_campaign_from_65_points_is_notified(settings, notes, monkeypatch, score, notified) -> None:
    monkeypatch.setattr(scout, "_now", lambda: NOW)
    _seed(settings, "timmy/streams", score)  # 24 timmar gammal
    scout.Scout(settings)._notify(NOW)
    assert [args[4] for args, _ in notes.calls] == (
        [f"Ny kampanj: Clip streams Twitch Streams, {score} poäng"] if notified else [])


def test_no_notification_outside_the_mac(settings, fake, notes, monkeypatch) -> None:
    monkeypatch.setattr(scout.sys, "platform", "linux")
    fake.add("timmy/streams")
    scout.Scout(settings).run_once()
    assert notes.calls == []


# ---------- vägarna ----------


def test_refresh_needs_the_key_runs_and_waits_two_minutes(client, settings, fake, monkeypatch) -> None:
    monkeypatch.setenv("CLIPSMITH_API_KEY", "nyckel")
    assert client.post("/campaign/scout/refresh").status_code == 401
    assert client.get("/campaign/scout").status_code == 200  # läser: ingen nyckel
    fake.add("timmy/streams")
    key = {"X-Api-Key": "nyckel"}
    assert client.post("/campaign/scout/refresh", headers=key).status_code == 202
    assert [c["ref"] for c in client.get("/campaign/scout").json()["campaigns"]] == ["timmy/streams"]
    response = client.post("/campaign/scout/refresh", headers=key)
    assert (response.status_code, response.json()["detail"]) == (429, "Vänta en stund. Senaste kontrollen var nyss.")
    _later(monkeypatch, minutes=3)
    assert client.post("/campaign/scout/refresh", headers=key).status_code == 202


def test_dismiss_hides_and_undismiss_shows(client, settings, fake, monkeypatch) -> None:
    fake.add("timmy/streams")
    fake.add("other/streams", membersOnly=True)
    scout.Scout(settings).run_once()
    answer = client.get("/campaign/scout").json()
    assert [c["ref"] for c in answer["excluded"]] == ["other/streams"]
    assert answer["excluded"][0]["excluded"]

    assert client.post("/campaign/scout/okänd/kampanj/dismiss").status_code == 404
    assert client.post("/campaign/scout/timmy/streams/dismiss").status_code == 200
    assert client.post("/campaign/scout/other/streams/dismiss").status_code == 200
    answer = client.get("/campaign/scout").json()
    assert (answer["campaigns"], answer["excluded"]) == ([], [])
    assert client.post("/campaign/scout/timmy/streams/undismiss").status_code == 200
    assert [c["ref"] for c in client.get("/campaign/scout").json()["campaigns"]] == ["timmy/streams"]

    monkeypatch.setenv("CLIPSMITH_API_KEY", "nyckel")
    assert client.post("/campaign/scout/timmy/streams/dismiss").status_code == 401


def test_the_thread_does_not_start_in_tests_or_when_turned_off(settings, monkeypatch) -> None:
    assert scout.start(settings) is None  # i tester
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    monkeypatch.setenv("KLIPP_SCOUT", "0")
    assert scout.start(settings) is None
    assert scout.Scout(settings).overview()["enabled"] is False


def test_the_router_is_registered_in_the_app() -> None:
    from clipsmith.api.app import app

    assert {"/campaign/scout", "/campaign/scout/refresh"} <= set(app.openapi()["paths"])


# ---------- granskningen ----------


def test_the_search_hit_goes_before_older_details() -> None:
    data = _data(state="paused", stalled=True,
                 whereToJoin=[{**_campaign()["whereToJoin"][0], "budgetLeftUsd": 250, "budgetTotalUsd": 9000}])
    data["search"].update(state="live", stalled=False, budgetLeftUsd=4000, budgetTotalUsd=7000)
    f = scout.facts(data)
    assert (f["state"], f["stalled"], f["left"], f["total"]) == ("live", False, 4000, 7000)
    assert _parts(data)[2] is None


def test_us_in_the_about_text_does_not_exclude() -> None:
    data = _with(campaign__about="Clip the US Open streams. Paid in US dollars, United States based brand.")
    assert _parts(data)[2] is None
    data = _with(campaign__audienceNote="US viewers only")
    assert _parts(data)[2] == "Kräver publik från USA eller andra länder."


def test_a_median_of_zero_does_not_crash() -> None:
    data = _data()
    data["analytics"]["pay"]["minPayoutUsd"] = 5
    assert _parts(data, median=0)[1]["minimum"]["points"] == 0


def test_links_outside_clippingalpha_are_not_shown(settings, fake) -> None:
    fake.add("timmy/streams", pageUrl="https://evil.example/x",
             whereToJoin=[{**_campaign()["whereToJoin"][0], "joinUrl": "javascript:alert(1)"}])
    fake.newest[0].update(pageUrl="https://evil.example/x", joinUrl="javascript:alert(1)")
    scout.Scout(settings).run_once()
    (view,) = scout.Scout(settings).overview()["campaigns"]
    assert (view["page_url"], view["join_url"]) == (None, None)


def test_a_campaign_whose_details_fail_still_gets_a_score_and_a_notice(settings, fake, notes, monkeypatch) -> None:
    fake.add("timmy/streams")
    fake.add("other/streams")
    fake.broken.add("timmy/streams")
    fake.clip_views = [1_000_000] * 10  # kampanjens klipp får mycket visningar: full poäng på Visningar
    scout.Scout(settings).run_once()
    overview = scout.Scout(settings).overview()
    assert overview["error"] is None
    assert {c["ref"] for c in overview["campaigns"]} == {"timmy/streams", "other/streams"}
    timmy = next(c for c in overview["campaigns"] if c["ref"] == "timmy/streams")
    assert timmy["score"] >= scout.NOTIFY_SCORE and timmy["max_payout_usd"] is None  # från sökträffen
    assert len(fake.calls("tools/call", "get_campaign_analytics")) == 1  # den andra hämtades ändå
    assert {args[4].split(":")[1].split(",")[0].strip() for args, _ in notes.calls} == {
        "Clip streams Twitch Streams"}  # två notiser, båda med sökträffens titel
    assert len(notes.calls) == 2

    _later(monkeypatch, minutes=10)
    scout.Scout(settings).run_once()
    assert len(fake.calls("tools/call", "get_campaign")) == 2  # den trasiga provas först om 6 timmar
    _later(monkeypatch, hours=7)
    scout.Scout(settings).run_once()
    assert {c["params"]["arguments"]["campaign"] for c in fake.calls("tools/call", "get_campaign")[2:]} == {
        "timmy/streams", "other/streams"}


def test_the_limit_inside_the_details_still_stops_the_run(settings, fake) -> None:
    fake.add("timmy/streams")
    fake.add("other/streams")
    fake.limit_at = "get_campaign"
    scout.Scout(settings).run_once()
    overview = scout.Scout(settings).overview()
    assert overview["error"].startswith("ClippingAlpha tar inte emot fler anrop just nu.")
    assert overview["calls_today"] == 5  # initialize, initialized, två sökningar och get_campaign
    assert len(fake.calls("tools/call", "get_campaign")) == 1  # nästa kampanj provas inte


def test_the_count_is_never_above_the_limit(settings) -> None:
    s = scout.Scout(settings)
    s._count(299)
    with pytest.raises(scout.ScoutError):
        s._count(2)
    assert s.calls_today() == 299
    s._count()
    assert s.calls_today() == 300


@pytest.mark.parametrize(("calls", "cost", "since_s", "wait"), [
    (0, 1, 100, 500),  # 43 200 s kvar / 300 körningar = 144 s, men minst 600
    (260, 4, 1000, 43200 / (40 / 4) - 1000),  # 10 körningar kvar: var 4 320:e sekund
    (37, 3, 7000, 0),  # 87 körningar kvar: var 596:e sekund, alltså 600, och den är redan försenad
    (298, 3, 60, 43200 - 60),  # inte ens en körning ryms: vänta till midnatt
    (None, None, None, 0)])  # aldrig körd
def test_the_wait_until_the_next_run_follows_the_calls_left(settings, monkeypatch, calls, cost, since_s,
                                                            wait) -> None:
    monkeypatch.setattr(scout, "_now", lambda: NOW)
    s = scout.Scout(settings)
    if calls:
        s._count(calls)
    if cost:
        s._put(last_cost=str(cost), last_run=scout._iso(NOW - timedelta(seconds=since_s)))
    assert s.wait_s() == pytest.approx(wait, abs=1)


class _Ran(BaseException):
    pass


def _run_loop(settings, monkeypatch, start: datetime, jump: timedelta = timedelta()) -> list[float]:
    """Kör _loop med en fejkad väggklocka. Varje sömn flyttar klockan lika långt; den första också jump
    (datorn sov). Fejkad run_once kastar _Ran, så att loopen slutar. Svaret är sömnstegen före körningen."""
    clock = [start]
    monkeypatch.setattr(scout, "_now", lambda: clock[0])
    slept: list[float] = []

    def sleep(seconds):
        slept.append(seconds)
        clock[0] += timedelta(seconds=seconds) + (jump if len(slept) == 1 else timedelta())
        assert len(slept) < 200, "loopen kör aldrig"

    def run_once(self):
        raise _Ran

    monkeypatch.setattr(scout.time, "sleep", sleep)
    monkeypatch.setattr(scout.Scout, "run_once", run_once)
    with pytest.raises(_Ran):
        scout._loop(settings)
    return slept


def test_the_thread_waits_before_its_first_run_in_short_steps(settings, monkeypatch) -> None:
    monkeypatch.setattr(scout, "_now", lambda: NOW)
    scout.Scout(settings)._put(last_run=scout._iso(NOW - timedelta(seconds=240)), last_cost="1")
    assert _run_loop(settings, monkeypatch, NOW) == [60] * 6  # 360 s kvar, i steg om högst 60 s


def test_the_thread_runs_right_after_midnight_even_if_the_mac_slept(settings, monkeypatch) -> None:
    evening = datetime(2026, 11, 13, 22, 45, tzinfo=UTC)
    monkeypatch.setattr(scout, "_now", lambda: evening)
    s = scout.Scout(settings)
    s._count(300)  # dagens gräns nådd: väntan är till midnatt UTC, cirka en timme
    s._put(last_run="2026-11-13T22:29:51+00:00", last_cost="1")
    assert s.wait_s() > 3500
    # Datorn sover i två timmar under den första sömnen. Väggklockan går förbi midnatt.
    assert _run_loop(settings, monkeypatch, evening, jump=timedelta(hours=2)) == [60]


def test_a_run_saves_what_it_cost(settings, fake, monkeypatch) -> None:
    fake.add("timmy/streams")
    scout.Scout(settings).run_once()
    assert scout.Scout(settings)._get("last_cost") == "8"  # med get_assets och /go/
    _later(monkeypatch, minutes=10)
    scout.Scout(settings).run_once()
    assert scout.Scout(settings)._get("last_cost") == "1"


POT = "Pott som delas mellan klippare, inte betalning per visning"


@pytest.mark.parametrize(("join_model", "search_model", "excluded"), [
    ("pot_split", "cpm", POT),  # joinen går före sökträffen
    (None, "pot_split", POT),  # sökträffen är reserv
    ("cpm", "pot_split", None),
    ("cpm", "cpm", None)])
def test_a_pot_split_campaign_is_excluded(join_model, search_model, excluded) -> None:
    join = {**_campaign()["whereToJoin"][0], "ratePer1kUsd": None}
    if join_model:
        join["payModel"] = join_model
    data = _with(campaign__whereToJoin=[join], search__payModel=search_model, search__ratePer1kUsd=None)
    assert _parts(data)[2] == excluded


# ---------- visningarna ----------


def _views(**data) -> dict:
    return _parts({**_data(), **data})[1]["views"]


TOP_12 = [12_000, 100, 30_000, 6_000, 25_000, 9_000, 20_000, 50, 8_000, 15_000, 10_000, 7_000]


def test_the_views_count_the_median_of_the_ten_best_campaign_clips() -> None:
    part = _views(clip_views=TOP_12)  # de två sämsta (100 och 50) räknas inte: medianen är 11 000, inte 9 500
    assert (part["max"], part["label"]) == (40, "Visningar")
    assert part["points"] == pytest.approx(40 * math.log(11_000 / 2_000) / math.log(1_000_000 / 2_000), abs=0.05)
    assert part["fact"] == "Median 11 000 visningar för kampanjens 10 bästa klipp"


@pytest.mark.parametrize(("views", "points"), [
    ([1_000_000] * 10, 40), ([2_000_000] * 6, 40), ([2_000] * 10, 0), ([500] * 5, 0), ([0] * 7, 0),
    ([57_350] * 5, pytest.approx(21.6, abs=0.05)), ([23_150] * 5, pytest.approx(15.8, abs=0.05))])
def test_the_campaign_scale_runs_from_2000_to_a_million(views, points) -> None:
    assert _views(clip_views=views)["points"] == points


def test_twitch_goes_before_the_campaign_clips() -> None:
    part = _views(twitch_views=[1_200] * 5, clip_views=[2_000_000] * 10)
    assert part["points"] == pytest.approx(40 * math.log(1_200 / 500) / math.log(200_000 / 500), abs=0.05)
    assert part["fact"] == "Twitch: median 1 200 visningar för kanalens bästa klipp, 30 dagar"


@pytest.mark.parametrize(("views", "points"), [
    ([200_000] * 5, 40), ([500] * 5, 0), ([400] * 5, 0), ([300_000] * 12 + [1], 40)])
def test_the_twitch_scale_runs_from_500_to_200000(views, points) -> None:
    assert _views(twitch_views=views)["points"] == points


def test_four_twitch_clips_go_on_to_the_campaign_clips_and_five_are_enough() -> None:
    part = _views(twitch_views=[200_000] * 4, clip_views=[57_350] * 5)
    assert part["fact"] == "Median 57 350 visningar för kampanjens 5 bästa klipp"
    part = _views(twitch_views=[200_000] * 5, clip_views=[57_350] * 5)
    assert (part["points"], part["fact"]) == (40, "Twitch: median 200 000 visningar för kanalens bästa klipp, 30 dagar")


@pytest.mark.parametrize("twitch", [[], None, [200_000] * 4])
def test_no_twitch_clips_gives_the_campaign_clips(twitch) -> None:
    part = _views(twitch_views=twitch, clip_views=[1_000_000] * 5)
    assert (part["points"], part["fact"]) == (40, "Median 1 000 000 visningar för kampanjens 5 bästa klipp")


@pytest.mark.parametrize(("data", "fact"), [
    ({"twitch_views": [200_000] * 3, "clip_views": [2_000_000] * 4},
     "Okänt: 3 Twitch-klipp och 4 kampanjklipp, minst 5 behövs"),
    ({"clip_views": [2_000_000] * 4}, "Okänt: 0 Twitch-klipp och 4 kampanjklipp, minst 5 behövs"),
    ({"clip_views": None}, "Okänt: inga klipp att räkna på ännu"),
    ({"twitch_views": []}, "Okänt: inga klipp att räkna på ännu"),
    ({}, "Okänt: inga klipp att räkna på ännu")])
def test_fewer_than_five_clips_on_twitch_and_in_the_campaign_is_unknown(data, fact) -> None:
    part = _views(**data)
    assert (part["points"], part["max"], part["fact"]) == (20, 40, fact)


CHANNEL = {"videos": [{"kind": "channel", "platform": "twitch", "channel": None,
                       "url": "https://www.twitch.tv/timmy_tv/videos?filter=archives&sort=time"}]}


def _saved(settings, ref: str = "timmy/streams") -> dict:
    with scout.Scout(settings)._db() as db:
        return json.loads(db.execute("SELECT data FROM scout_campaigns WHERE ref = ?", (ref,)).fetchone()[0])


def test_the_details_fetch_the_channel_and_the_campaign_page_once(settings, fake, monkeypatch) -> None:
    fake.add("timmy/streams")
    fake.assets["timmy/streams"] = CHANNEL
    fake.twitch_views = [1_200] * 5
    fake.clip_views = [90_000] * 10
    scout.Scout(settings).run_once()

    assert [c["params"]["arguments"] for c in fake.calls("tools/call", "get_assets")] == [{"campaign": "timmy/streams"}]
    (go,) = fake.go
    assert str(go.url) == "https://clippingalpha.com/go/1"  # utan ?via=mcp
    assert scout.Scout(settings).calls_today() == 2 + 1 + 1 + 4  # /go/ räknas i scout_calls
    data = _saved(settings)
    assert (data["twitch_login"], data["cr_url"], data["clip_views"], data["twitch_views"]) == (
        "timmy_tv", CR, [90_000] * 10, [1_200] * 5)
    assert fake.twitch_calls == [("timmy_tv", NOW)] and fake.clip_calls == [CR]
    assert data.get("twitch_vod") is None and fake.vod_calls == []  # kanalposten räcker
    (view,) = scout.Scout(settings).overview()["campaigns"]
    assert next(p for p in view["parts"] if p["key"] == "views")["fact"].startswith("Twitch: median 1 200")

    _later(monkeypatch, hours=7)  # nästa detaljuppdatering: inga nya get_assets eller /go/, Twitch är färskt
    scout.Scout(settings).run_once()
    assert len(fake.calls("tools/call", "get_campaign")) == 2
    assert (len(fake.calls("tools/call", "get_assets")), len(fake.go)) == (1, 1)
    assert (fake.clip_calls, len(fake.twitch_calls)) == ([CR, CR], 1)

    _later(monkeypatch, hours=25)  # Twitch frågas igen efter ett dygn
    scout.Scout(settings).run_once()
    assert (len(fake.calls("tools/call", "get_assets")), len(fake.go)) == (1, 1)
    assert fake.twitch_calls[1:] == [("timmy_tv", NOW + timedelta(hours=25))]


@pytest.mark.parametrize("status", [429, 503])
def test_a_temporary_error_from_go_is_tried_again_at_the_next_details(settings, fake, monkeypatch, status) -> None:
    fake.add("timmy/streams")
    fake.go_status = status
    scout.Scout(settings).run_once()
    assert "join_checked_at" not in _saved(settings) and len(fake.go) == 1
    assert scout.Scout(settings).calls_today() == len(fake.requests) + 1  # /go/ räknas i scout_calls

    fake.go_status = 302
    _later(monkeypatch, hours=7)
    scout.Scout(settings).run_once()
    assert len(fake.go) == 2
    assert scout.Scout(settings).calls_today() == len(fake.requests) + 2
    data = _saved(settings)
    assert (data["cr_url"], data["join_checked_at"]) == (CR, scout._iso(NOW + timedelta(hours=7)))


@pytest.mark.parametrize("status", [404, 410, 200])
def test_an_answer_without_a_campaign_page_marks_the_campaign_as_checked(settings, fake, monkeypatch, status) -> None:
    fake.add("timmy/streams")
    fake.go_status = status
    scout.Scout(settings).run_once()
    data = _saved(settings)
    assert (data["cr_url"], data["join_checked_at"]) == (None, scout._iso(NOW))
    _later(monkeypatch, hours=7)
    scout.Scout(settings).run_once()
    assert len(fake.go) == 1  # provas inte igen


def test_go_waits_for_the_gap_after_the_last_mcp_call(settings, fake, monkeypatch) -> None:
    fake.add("timmy/streams")
    events: list[str | float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        events.append(request.method)
        return fake(request)

    monkeypatch.setattr(scout, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(scout, "MIN_GAP_S", 1.0)
    # klockan står still: varje anrop måste vänta hela MIN_GAP_S
    monkeypatch.setattr(scout, "time", SimpleNamespace(monotonic=lambda: 100.0, sleep=events.append))
    scout.Scout(settings).run_once()
    go = events.index("GET")
    assert events[go - 2:go + 1] == ["POST", 1.0, "GET"]


def test_the_cookies_from_go_do_not_follow_to_the_mcp_calls(settings, fake) -> None:
    fake.add("timmy/streams")
    fake.add("other/streams")
    scout.Scout(settings).run_once()
    assert len(fake.go) == 2
    assert all("cookie" not in request.headers for request in fake.requests + fake.go)


def test_without_a_channel_or_campaign_page_there_is_no_twitch_or_page_call(settings, fake) -> None:
    fake.add("timmy/streams", whereToJoin=[{**_campaign()["whereToJoin"][0], "joinUrl": "https://other.example/x"}])
    fake.newest[0]["joinUrl"] = "https://other.example/x"
    scout.Scout(settings).run_once()
    assert (fake.go, fake.twitch_calls, fake.clip_calls) == ([], [], [])
    data = _saved(settings)
    assert data["twitch_login"] is None and "assets_at" in data


def test_without_a_go_link_from_get_campaign_go_is_checked_and_not_counted_again(settings, fake, monkeypatch) -> None:
    fake.add("timmy/streams", whereToJoin=[{**_campaign()["whereToJoin"][0], "joinUrl": "https://other.example/x"}])
    fake.newest[0]["joinUrl"] = "https://other.example/x"
    scout.Scout(settings).run_once()
    assert fake.go == [] and _saved(settings)["join_checked_at"] == scout._iso(NOW)

    _later(monkeypatch, hours=7)  # nästa detaljuppdatering
    # 2 sökningar, sedan 2 kvar: get_campaign och analytics ryms, men inte ett /go/ till
    s = scout.Scout(settings)
    s._count(scout.CALL_LIMIT - scout.reserve(NOW + timedelta(hours=7)) - 4 - s.calls_today())
    s.run_once()
    assert len(fake.calls("tools/call", "get_campaign")) == 2 and fake.go == []


def test_without_an_answer_from_get_campaign_go_is_not_marked_as_checked(settings, fake) -> None:
    fake.add("timmy/streams")
    fake.newest[0]["joinUrl"] = "https://other.example/x"
    fake.broken.add("timmy/streams")
    scout.Scout(settings).run_once()
    assert "join_checked_at" not in _saved(settings) and fake.go == []


def test_failing_views_do_not_stop_the_run_and_keep_what_was_there(settings, fake, monkeypatch) -> None:
    fake.add("timmy/streams")
    fake.assets["timmy/streams"] = CHANNEL
    fake.twitch_views, fake.clip_views = [1_200], [9_000] * 10
    scout.Scout(settings).run_once()
    fake.twitch_views = fake.clip_views = None  # Twitch och Content Rewards svarar inte
    _later(monkeypatch, hours=25)
    scout.Scout(settings).run_once()
    assert scout.Scout(settings).overview()["error"] is None
    data = _saved(settings)
    assert (data["twitch_views"], data["clip_views"]) == ([1_200], [9_000] * 10)


def test_a_failing_get_assets_is_handled_like_the_other_details(settings, fake) -> None:
    fake.add("timmy/streams")
    fake.add("other/streams")
    fake.broken_assets.add("timmy/streams")
    scout.Scout(settings).run_once()
    assert len(fake.calls("tools/call", "get_assets")) == 2  # den andra hämtades ändå
    assert "assets_at" not in _saved(settings)  # provas igen vid nästa detaljuppdatering
    assert "assets_at" in _saved(settings, "other/streams")
    assert scout.Scout(settings).overview()["error"] is None


def test_the_limit_in_get_assets_stops_the_run(settings, fake) -> None:
    fake.add("timmy/streams")
    fake.limit_at = "get_assets"
    scout.Scout(settings).run_once()
    assert scout.Scout(settings).overview()["error"].startswith("ClippingAlpha tar inte emot fler anrop just nu.")
    assert fake.go == []


def test_go_comes_from_get_campaign_when_the_search_hit_has_no_join_url(settings, fake) -> None:
    fake.add("timmy/streams")
    del fake.newest[0]["joinUrl"]  # bara whereToJoin i get_campaign har länken
    scout.Scout(settings).run_once()
    assert len(fake.go) == 1 and _saved(settings)["cr_url"] == CR
    assert scout.Scout(settings).calls_today() == len(fake.requests) + 1  # /go/ räknas i scout_calls


@pytest.mark.parametrize("body", [{"data": [{"view_count": "x"}]}, {"data": "x"}])
def test_an_unexpected_twitch_answer_still_saves_the_campaign(settings, fake, monkeypatch, body) -> None:
    class Helix:  # svarar 200, men i en annan form
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def get_user_id(self, login: str) -> str:
            return "42"

        def _get(self, path: str, params: dict) -> dict:
            return body

    monkeypatch.setattr(scout, "twitch_clip_views", content_views.twitch_clip_views)
    monkeypatch.setattr(content_views, "TwitchClient", Helix)
    monkeypatch.setattr(content_views, "load_secrets",
                        lambda: SimpleNamespace(twitch_client_id="id", twitch_client_secret="secret"))
    fake.add("timmy/streams")
    fake.assets["timmy/streams"] = CHANNEL
    scout.Scout(settings).run_once()
    assert scout.Scout(settings).overview()["error"] is None
    data = _saved(settings)
    assert (data["twitch_login"], data["detail_at"], data["cr_url"]) == ("timmy_tv", scout._iso(NOW), CR)
    assert "twitch_views" not in data


def test_no_get_assets_or_go_when_the_rest_of_the_day_needs_the_calls(settings, fake) -> None:
    fake.add("timmy/streams")
    # 4 för initialize och sökningarna, sedan 3 kvar: get_campaign och analytics ryms, men inte get_assets och /go/
    scout.Scout(settings)._count(300 - scout.reserve(NOW) - 7)
    scout.Scout(settings).run_once()
    assert fake.calls("tools/call", "get_assets") == [] and fake.go == []
    assert fake.calls("tools/call", "get_campaign") == []
    assert scout.Scout(settings).calls_today() == 300 - scout.reserve(NOW) - 3


# ---------- kanalen via VOD-länken ----------

VODS = {"videos": [{"kind": "video", "platform": "twitch", "channel": None, "url": f"https://www.twitch.tv/videos/{i}"}
                   for i in ("2919796761", "2927881190")]}  # som johndoe-twitch-stream/streaming, utan kanalposten


def test_without_a_channel_entry_the_channel_comes_from_the_vod(settings, fake, monkeypatch) -> None:
    fake.add("timmy/streams")
    fake.assets["timmy/streams"] = VODS
    fake.vod_logins["2919796761"] = "johndoe_100x"
    fake.twitch_views = [1_200] * 5
    scout.Scout(settings).run_once()

    data = _saved(settings)
    assert (data["twitch_vod"], data["twitch_login"]) == ("2919796761", "johndoe_100x")
    assert data["twitch_views"] == [1_200] * 5
    assert fake.vod_calls == ["2919796761"] and fake.twitch_calls == [("johndoe_100x", NOW)]
    assert scout.Scout(settings).calls_today() == 2 + 1 + 1 + 4  # samma som med kanalposten: inget extra anrop
    (view,) = scout.Scout(settings).overview()["campaigns"]
    assert next(p for p in view["parts"] if p["key"] == "views")["fact"].startswith("Twitch: median 1 200")

    _later(monkeypatch, hours=25)  # kanalen är känd: ingen ny uppslagning, bara Twitch-klippen
    scout.Scout(settings).run_once()
    assert fake.vod_calls == ["2919796761"] and len(fake.twitch_calls) == 2


def test_without_twitch_keys_no_call_no_login_and_the_campaign_clips_count(settings, fake, monkeypatch) -> None:
    lookups: list[str] = []

    def lookup(video_id: str) -> str | None:
        lookups.append(video_id)
        return content_views.twitch_login_from_vod(video_id)

    def no_client(*args, **kwargs):
        raise AssertionError("inget anrop till Twitch")

    monkeypatch.setattr(scout, "twitch_login_from_vod", lookup)
    monkeypatch.setattr(content_views, "TwitchClient", no_client)
    monkeypatch.setattr(content_views, "load_secrets",
                        lambda: SimpleNamespace(twitch_client_id="", twitch_client_secret=""))
    fake.add("timmy/streams")
    fake.assets["timmy/streams"] = VODS
    fake.clip_views = [1_000_000] * 5
    scout.Scout(settings).run_once()

    data = _saved(settings)
    assert (data["twitch_vod"], data["twitch_login"], fake.twitch_calls) == ("2919796761", None, [])
    (view,) = scout.Scout(settings).overview()["campaigns"]
    part = next(p for p in view["parts"] if p["key"] == "views")
    assert (part["points"], part["fact"]) == (40, "Median 1 000 000 visningar för kampanjens 5 bästa klipp")

    _later(monkeypatch, hours=7)  # ny chans först efter ett dygn
    scout.Scout(settings).run_once()
    assert lookups == ["2919796761"]
    _later(monkeypatch, hours=25)
    scout.Scout(settings).run_once()
    assert lookups == ["2919796761"] * 2


# ---------- kampanjens klipp för alla som syns ----------


def _page(ref: str) -> str:
    return f"https://contentrewards.com/c/campaigns/{ref}"


def _seed_with_page(settings, ref: str, score: int, clip_age_h: float, detail_age_h: float = 0, **row) -> None:
    _seed(settings, ref, score, cr_url=_page(ref), clip_views=[1_000] * 5, join_checked_at=scout._iso(NOW),
          detail_at=scout._iso(NOW - timedelta(hours=detail_age_h)),
          clip_views_at=scout._iso(NOW - timedelta(hours=clip_age_h)), **row)


def test_a_shown_campaign_outside_the_best_gets_new_clip_views_without_clippingalpha(settings, fake) -> None:
    for i in range(scout.TOP):  # de 25 bästa: färska detaljer och klipp yngre än 6 timmar
        _seed_with_page(settings, f"best/c{i}", 90, clip_age_h=5)
    _seed_with_page(settings, "low/streams", 10, clip_age_h=7, detail_age_h=7)  # utanför de 25: inga nya detaljer
    fake.clip_views = [9_000] * 5
    scout.Scout(settings).run_once()

    assert fake.clip_calls == [_page("low/streams")]
    assert scout.Scout(settings).calls_today() == 4  # bara initialize och sökningarna
    assert fake.calls("tools/call", "get_campaign") == [] and fake.go == []
    data = _saved(settings, "low/streams")
    assert (data["clip_views"], data["clip_views_at"]) == ([9_000] * 5, scout._iso(NOW))


def test_hidden_excluded_and_old_campaigns_are_not_fetched(settings, fake) -> None:
    _seed_with_page(settings, "hidden/streams", 90, clip_age_h=7, dismissed=1)
    _seed_with_page(settings, "excluded/streams", 90, clip_age_h=7, excluded="Bara för medlemmar.")
    _seed_with_page(settings, "gone/streams", 90, clip_age_h=7, updated=scout._iso(NOW - timedelta(hours=73)))
    _seed(settings, "nopage/streams", 90, clip_views_at=scout._iso(NOW - timedelta(hours=7)),
          join_checked_at=scout._iso(NOW - timedelta(hours=7)), cr_url=None)  # /go/ kollad, ingen sida
    scout.Scout(settings).run_once()
    assert fake.clip_calls == []


def test_the_clip_views_are_capped_per_run_and_a_failure_keeps_the_old(settings, fake, monkeypatch) -> None:
    refs = [f"brand/c{i}" for i in range(scout.CLIP_VIEWS_PER_RUN + 5)]
    for i, ref in enumerate(refs):
        _seed_with_page(settings, ref, 90 - i, clip_age_h=7)
    fake.clip_views = None  # Content Rewards svarar inte
    scout.Scout(settings).run_once()
    assert fake.clip_calls == [_page(ref) for ref in refs[:scout.CLIP_VIEWS_PER_RUN]]  # de bästa först
    assert scout.Scout(settings).overview()["error"] is None
    assert _saved(settings, refs[0])["clip_views"] == [1_000] * 5

    _later(monkeypatch, minutes=10)  # nästa körning tar resten
    scout.Scout(settings).run_once()
    assert fake.clip_calls[scout.CLIP_VIEWS_PER_RUN:] == [_page(ref) for ref in refs[scout.CLIP_VIEWS_PER_RUN:]]


@pytest.mark.parametrize(("stop", "requests", "error"), [
    ({"limit_at": "search_campaigns"}, 3, "ClippingAlpha tar inte emot fler anrop just nu."),
    ({"down": True}, 1, "ClippingAlpha svarade 500.")])
def test_the_clip_views_are_refreshed_when_clippingalpha_stops_the_run(settings, fake, stop, requests,
                                                                       error) -> None:
    _seed_with_page(settings, "timmy/streams", 50, clip_age_h=7)
    vars(fake).update(stop)
    fake.clip_views = [1_000_000] * 5
    scout.Scout(settings).run_once()

    overview = scout.Scout(settings).overview()
    assert overview["error"].startswith(error)  # felet sparas och visas som förut
    assert (len(fake.requests), fake.go) == (requests, [])  # inga fler anrop till ClippingAlpha
    data = _saved(settings)
    assert (data["clip_views"], data["clip_views_at"]) == ([1_000_000] * 5, scout._iso(NOW))
    (view,) = overview["campaigns"]
    assert view["score"] == scout.evaluate(data, 1260, 1.05, NOW)[0] != 50
    assert next(p for p in view["parts"] if p["key"] == "views")["points"] == 40


def test_the_pause_between_the_pages_is_one_second() -> None:
    assert scout.CLIP_PAGE_GAP_S == 1.0  # utan fixturen fake, som sätter pausen till 0


def test_a_pause_between_the_pages_from_content_rewards(settings, fake, monkeypatch) -> None:
    for i in range(3):
        _seed_with_page(settings, f"brand/c{i}", 90 - i, clip_age_h=7)
    events: list[str | float] = []
    monkeypatch.setattr(scout, "CLIP_PAGE_GAP_S", 1.0)
    monkeypatch.setattr(scout, "_last_call", [100.0])  # ingen väntan före anropen till ClippingAlpha
    monkeypatch.setattr(scout, "_last_page", [0.0])  # ingen sida hämtad ännu
    monkeypatch.setattr(scout, "time", SimpleNamespace(monotonic=lambda: 100.0, sleep=events.append))
    monkeypatch.setattr(scout, "campaign_page", events.append)
    scout.Scout(settings).run_once()
    assert events == [0, _page("brand/c0"), 1.0, _page("brand/c1"), 1.0, _page("brand/c2")]


def test_the_pause_also_holds_from_the_details_to_the_shown_campaigns(settings, fake, monkeypatch) -> None:
    fake.add("timmy/streams")  # nya detaljer: kampanjsidan CR hämtas i _details
    _seed_with_page(settings, "low/streams", 10, clip_age_h=7)  # sedan den här i _refresh_clip_views
    events: list[str | float] = []
    monkeypatch.setattr(scout, "CLIP_PAGE_GAP_S", 1.0)
    monkeypatch.setattr(scout, "_last_call", [100.0])  # ingen väntan före anropen till ClippingAlpha
    monkeypatch.setattr(scout, "_last_page", [0.0])
    # klockan står still: varje sida efter den första måste vänta hela CLIP_PAGE_GAP_S
    monkeypatch.setattr(scout, "time", SimpleNamespace(monotonic=lambda: 100.0, sleep=events.append))
    monkeypatch.setattr(scout, "campaign_page", events.append)
    scout.Scout(settings).run_once()
    assert events == [0, CR, 1.0, _page("low/streams")]


# ---------- priset i Robins land ----------

# [FRANCE] Blizzard - World of Warcraft Forever: 5 dollar per 1 000 bara för Frankrike, \"Other countries: Not paid\".
FRANCE = {"priced": True, "rates": {"restOfWorld": [], "tiers": [{"countries": ["FR"], "label": "France",
                                                                    "platform": "tiktok", "rate": 500}]},
          "payout_model": [{"platform": "tiktok", "rate": 0}]}
# TikTok betalar bara för Frankrike, YouTube betalar 1,50 dollar överallt.
PARTLY = {"priced": True, "rates": {"restOfWorld": [{"platform": "youtube", "rate": 150}],
                                    "tiers": [{"countries": ["FR"], "platform": "tiktok", "rate": 500}]},
          "payout_model": [{"platform": "tiktok", "rate": 0}, {"platform": "youtube", "rate": 0}]}
# WA-CHAA! (riktig sida): Pakistan, Indien och Bangladesh får 0, resten av världen 2 dollar.
WACHAA = {"priced": True, "rates": {
    "restOfWorld": [{"platform": "tiktok", "rate": 200}, {"platform": "youtube", "rate": 200}],
    "tiers": [{"countries": ["IN", "PK", "BD"], "label": "Pak, Ind, Ban", "platform": "tiktok", "rate": 0},
              {"countries": ["IN", "PK", "BD"], "label": "Pak, Ind, Ban", "platform": "youtube", "rate": 0}]},
    "payout_model": [{"platform": "tiktok", "rate": 0}, {"platform": "instagram", "rate": 150},
                     {"platform": "youtube", "rate": 0}]}


def _geo(geo: dict | None, country: str = "SE") -> tuple[dict, str | None]:
    data = {**_data(), "cr_url": CR, "geo": geo}
    _, parts, excluded = scout.evaluate(data, 1260, 1.05, NOW, country)
    return {part["key"]: part for part in parts}["pay"], excluded


def test_the_price_comes_from_the_tier_then_rest_of_world_then_the_payout_model() -> None:
    lower = {"priced": True, "rates": {
        "restOfWorld": [{"platform": "tiktok", "rate": 200}],
        "tiers": [{"countries": ["NO", "SE"], "platform": "tiktok", "rate": 100}]},
        "payout_model": [{"platform": "tiktok", "rate": 0}, {"platform": "instagram", "rate": 150}]}
    assert scout_score.country_rates(lower, "SE") == {"tiktok": 1.0, "instagram": 1.5}  # tier med SE, lägre
    assert scout_score.country_rates(lower, "DK") == {"tiktok": 2.0, "instagram": 1.5}  # resten av världen
    assert scout_score.country_rates(FRANCE, "SE") == {"tiktok": 0.0}  # tier utan SE, ingen restOfWorld
    assert scout_score.country_rates(FRANCE, "FR") == {"tiktok": 5.0}


def test_a_campaign_that_pays_nothing_for_sweden_is_excluded() -> None:
    pay, excluded = _geo(FRANCE)
    assert excluded == "Betalar inte för visningar från Sverige"
    assert pay["fact"] == "0,00 dollar per 1 000 efter avgift i Sverige, marknaden 1,05"


def test_a_campaign_where_only_some_platforms_pay_counts_the_best_price_for_sweden() -> None:
    pay, excluded = _geo(PARTLY)
    assert excluded is None
    assert pay["fact"] == ("1,35 dollar per 1 000 efter avgift i Sverige, marknaden 1,05. "
                           "TikTok betalar inte för Sverige.")
    assert pay["points"] == pytest.approx(5 * 1.35 / 1.05 * SCALE["pay"], abs=0.05)  # inte 1,80


@pytest.mark.parametrize("geo", [
    {"priced": False, "rates": None, "payout_model": [{"platform": "tiktok", "rate": 200}]},
    {"priced": True, "rates": None, "payout_model": []},
    {**FRANCE, "priced": False}])  # geoPriced false: geoRates räknas inte, också när den är ifylld
def test_without_a_price_per_country_the_usual_price_counts(geo) -> None:
    pay, excluded = _geo(geo)
    assert (pay["fact"], excluded) == ("1,80 dollar per 1 000 efter avgift, marknaden 1,05", None)


@pytest.mark.parametrize("data", [_data(), {**_data(), "cr_url": CR}, {**_data(), "cr_url": None}])
def test_without_the_campaign_page_the_country_is_unknown(data) -> None:
    _, parts, excluded = _parts(data)
    assert (parts["pay"]["fact"], excluded) == ("1,80 dollar per 1 000 efter avgift, marknaden 1,05, Land okänt", None)


def test_a_tier_for_other_countries_does_not_touch_sweden() -> None:
    pay, excluded = _geo(WACHAA)
    assert (pay["fact"], excluded) == ("1,80 dollar per 1 000 efter avgift i Sverige, marknaden 1,05", None)


def test_from_france_the_france_campaign_counts_five_dollars() -> None:
    pay, excluded = _geo(FRANCE, "FR")
    assert excluded is None
    assert pay["fact"] == "4,50 dollar per 1 000 efter avgift i Frankrike, marknaden 1,05"


def test_the_pay_fact_uses_a_decimal_comma() -> None:
    fact = _parts(_data())[1]["pay"]["fact"]
    assert "1,80" in fact and "1,05" in fact and "1.8" not in fact


def test_the_web_gets_a_country_note_only_when_some_platforms_do_not_pay(settings, fake) -> None:
    for ref, geo in (("partly/c", PARTLY), ("wachaa/c", WACHAA), ("france/c", FRANCE), ("nogeo/c", None)):
        _seed(settings, ref, 50, **({} if geo is None else {"geo": geo}), cr_url=CR)
    scout.Scout(settings)._rescore(NOW)
    overview = scout.Scout(settings).overview()
    notes = {view["ref"]: view["country_note"] for view in overview["campaigns"] + overview["excluded"]}
    assert notes == {"partly/c": "TikTok betalar inte för visningar från Sverige.", "wachaa/c": None,
                     "france/c": None, "nogeo/c": None}
    assert [view["ref"] for view in overview["excluded"]] == ["france/c"]


def test_the_price_per_country_is_saved_with_the_clip_views_and_kept_on_failure(settings, fake, monkeypatch) -> None:
    _seed_with_page(settings, "timmy/streams", 50, clip_age_h=7)
    fake.clip_views, fake.geo = [9_000] * 5, PARTLY
    scout.Scout(settings).run_once()
    assert (_saved(settings)["clip_views"], _saved(settings)["geo"]) == ([9_000] * 5, PARTLY)
    fake.clip_views = None  # sidan går inte att läsa
    _later(monkeypatch, hours=7)
    scout.Scout(settings).run_once()
    assert (_saved(settings)["clip_views"], _saved(settings)["geo"]) == ([9_000] * 5, PARTLY)


def test_the_highest_price_counts_when_the_country_is_in_several_tiers() -> None:
    geo = {"priced": True, "rates": {"restOfWorld": [], "tiers": [
        {"countries": ["SE"], "platform": "tiktok", "rate": 100},
        {"countries": ["NO", "SE"], "platform": "tiktok", "rate": 300},
        {"countries": ["SE", "DK"], "platform": "tiktok", "rate": 200}]},
        "payout_model": [{"platform": "tiktok", "rate": 0}]}
    assert scout_score.country_rates(geo, "SE") == {"tiktok": 3.0}


def _unreadable(rates: dict) -> dict:
    return {"priced": True, "rates": rates, "payout_model": [{"platform": "tiktok", "rate": 0}]}


@pytest.mark.parametrize("geo", [
    _unreadable({"restOfWorld": [{"platform": "tiktok", "rate": "200"}], "tiers": []}),
    _unreadable({"restOfWorld": [], "tiers": [{"countries": ["SE"], "platform": "tiktok", "rate": None}]}),
    _unreadable({"restOfWorld": [{"platform": "tiktok", "rate": True}], "tiers": []}),
    _unreadable({"restOfWorld": [{"platform": "youtube", "rate": 150}, {"platform": "tiktok", "rate": "200"}]}),
    _unreadable({}),
    _unreadable({"restOfWorld": [], "tiers": []}),
    _unreadable({"rest_of_world": [{"platform": "tiktok", "rate": 200}]})])  # omdöpt
def test_a_price_that_cannot_be_read_gives_land_okant_and_is_not_excluded(geo) -> None:
    assert scout_score.country_rates(geo, "SE") is None
    pay, excluded = _geo(geo)
    assert (pay["fact"], excluded) == ("1,80 dollar per 1 000 efter avgift, marknaden 1,05, Land okänt", None)


@pytest.mark.parametrize("geo", ["trasig", ["SE"], 5])
def test_a_price_per_country_that_is_not_a_dict_gives_none(geo) -> None:
    assert scout_score.country_rates(geo, "SE") is None


# TikTok betalar 5 dollar för Frankrike, YouTube betalar 1,50 överallt utom i Frankrike.
NOT_YOUTUBE_IN_FRANCE = {"priced": True, "rates": {
    "restOfWorld": [{"platform": "youtube", "rate": 150}],
    "tiers": [{"countries": ["FR"], "platform": "tiktok", "rate": 500},
              {"countries": ["FR"], "platform": "youtube", "rate": 0}]},
    "payout_model": [{"platform": "tiktok", "rate": 0}, {"platform": "youtube", "rate": 0}]}


def test_the_card_shows_the_price_and_the_note_for_robins_country(settings, fake) -> None:
    for ref, geo in (("partly/c", PARTLY), ("france/c", FRANCE), ("notyt/c", NOT_YOUTUBE_IN_FRANCE),
                     ("nogeo/c", None)):
        _seed(settings, ref, 50, **({} if geo is None else {"geo": geo}), cr_url=CR)

    def cards() -> dict[str, tuple]:
        scout.Scout(settings)._rescore(NOW)
        overview = scout.Scout(settings).overview()
        return {view["ref"]: (view["rate_usd"], view["net_rate_usd"], view["country_note"])
                for view in overview["campaigns"] + overview["excluded"]}

    assert cards() == {  # Sverige: TikTok betalar inte för PARTLY, så 1,50 och 1,35 och inte 2,00 och 1,80
        "partly/c": (1.5, 1.35, "TikTok betalar inte för visningar från Sverige."),
        "france/c": (0.0, 0.0, None), "notyt/c": (1.5, 1.35, "TikTok betalar inte för visningar från Sverige."),
        "nogeo/c": (2, 1.8, None)}
    preferences.save_country(settings, "FR")
    assert cards() == {
        "partly/c": (5.0, 4.5, None), "france/c": (5.0, 4.5, None),
        "notyt/c": (5.0, 4.5, "YouTube betalar inte för visningar från Frankrike."), "nogeo/c": (2, 1.8, None)}


def test_a_campaign_excluded_for_the_country_gets_its_page_read_again(settings, fake) -> None:
    _seed_with_page(settings, "france/c", 50, clip_age_h=7, geo=FRANCE)
    _seed_with_page(settings, "fresh/c", 50, clip_age_h=5, geo=FRANCE)  # läst för under 6 timmar sedan
    scout.Scout(settings)._rescore(NOW)
    assert sorted(view["ref"] for view in scout.Scout(settings).overview()["excluded"]) == ["france/c", "fresh/c"]

    fake.clip_views, fake.geo = [9_000] * 5, PARTLY  # nu betalar YouTube för Sverige
    scout.Scout(settings).run_once()
    assert fake.clip_calls == [_page("france/c")]
    overview = scout.Scout(settings).overview()
    assert ([view["ref"] for view in overview["campaigns"]], [view["ref"] for view in overview["excluded"]]) == (
        ["france/c"], ["fresh/c"])


def test_a_campaign_page_read_before_the_price_per_country_gets_it_within_two_runs(settings, fake,
                                                                                   monkeypatch) -> None:
    refs = [f"old/c{i}" for i in range(scout.CLIP_VIEWS_PER_RUN + 3)]
    for i, ref in enumerate(refs):  # sidan lästes för en timme sedan, innan geo sparades
        _seed_with_page(settings, ref, 90 - i, clip_age_h=1)
    fake.clip_views, fake.geo = [9_000] * 5, PARTLY
    scout.Scout(settings).run_once()
    assert fake.clip_calls == [_page(ref) for ref in refs[:scout.CLIP_VIEWS_PER_RUN]]  # taket gäller som förut
    assert [_saved(settings, ref).get("geo") for ref in refs] == [PARTLY] * scout.CLIP_VIEWS_PER_RUN + [None] * 3
    assert scout.Scout(settings).calls_today() == 4  # bara initialize och sökningarna

    _later(monkeypatch, minutes=10)  # nästa körning tar resten
    scout.Scout(settings).run_once()
    assert fake.clip_calls == [_page(ref) for ref in refs]
    assert all(_saved(settings, ref)["geo"] == PARTLY for ref in refs)
    assert scout.Scout(settings).calls_today() == 4 + 1  # bara sökningen


def test_a_campaign_page_that_cannot_be_read_waits_six_hours_also_without_a_price(settings, fake,
                                                                                    monkeypatch) -> None:
    _seed_with_page(settings, "timmy/streams", 50, clip_age_h=1)  # utan geo
    fake.clip_views = None  # sidan går inte att läsa
    scout.Scout(settings).run_once()
    assert fake.clip_calls == [_page("timmy/streams")]
    assert ("geo" in _saved(settings), _saved(settings).get("geo")) == (True, None)  # Land okänt

    _later(monkeypatch, minutes=10)
    scout.Scout(settings).run_once()
    assert fake.clip_calls == [_page("timmy/streams")]  # inte var 10:e minut

    _later(monkeypatch, hours=7)
    scout.Scout(settings).run_once()
    assert fake.clip_calls == [_page("timmy/streams")] * 2


# ---------- landet i Inställningar ----------


COUNTRY_CHOICES = [{"code": code, "name": name} for code, name in (
    ("SE", "Sverige"), ("NO", "Norge"), ("DK", "Danmark"), ("FI", "Finland"), ("IS", "Island"), ("DE", "Tyskland"),
    ("FR", "Frankrike"), ("ES", "Spanien"), ("IT", "Italien"), ("NL", "Nederländerna"), ("PL", "Polen"),
    ("GB", "Storbritannien"), ("IE", "Irland"), ("US", "USA"), ("CA", "Kanada"), ("AU", "Australien"),
    ("BR", "Brasilien"), ("MX", "Mexiko"), ("IN", "Indien"))]


def test_get_country_gives_sweden_and_the_list(client) -> None:
    assert client.get("/campaign/scout/country").json() == {"country": "SE", "choices": COUNTRY_CHOICES}


def test_put_country_saves_and_scores_again_without_clippingalpha(client, settings, fake, monkeypatch) -> None:
    _seed(settings, "france/c", 50, geo=FRANCE, cr_url=CR)
    scout.Scout(settings)._rescore(NOW)
    assert [v["ref"] for v in client.get("/campaign/scout").json()["excluded"]] == ["france/c"]

    monkeypatch.setenv("CLIPSMITH_API_KEY", "nyckel")
    assert client.put("/campaign/scout/country", json={"country": "FR"}).status_code == 401
    answer = client.put("/campaign/scout/country", json={"country": "FR"}, headers={"X-Api-Key": "nyckel"})
    assert (answer.status_code, answer.json()) == (200, {"country": "FR", "choices": COUNTRY_CHOICES})
    assert preferences.country(settings) == "FR"
    overview = client.get("/campaign/scout").json()
    assert [v["ref"] for v in overview["campaigns"]] == ["france/c"] and overview["excluded"] == []
    assert (fake.requests, fake.go, fake.clip_calls) == ([], [], [])


@pytest.mark.parametrize("body", [{"country": "XX"}, {"country": "se"}, {}, {"country": "SE", "annat": 1}])
def test_put_country_outside_the_list_gives_422(client, settings, body) -> None:
    assert client.put("/campaign/scout/country", json=body).status_code == 422
    assert preferences.country(settings) == "SE"


# ---------- kampanjsidan för alla som syns ----------


def _seed_without_page(settings, count: int, **row) -> list[str]:
    refs = [f"seen/c{i}" for i in range(count)]
    for i, ref in enumerate(refs):  # detaljerna är färska, men /go/ kollades aldrig
        _seed(settings, ref, 90 - i, detail_at=scout._iso(NOW), **row)
    return refs


def _with_page(settings, refs: list[str]) -> list[str]:
    return [ref for ref in refs if _saved(settings, ref).get("cr_url") == CR]


def test_shown_campaigns_without_a_campaign_page_get_one_ten_per_run(settings, fake, monkeypatch) -> None:
    refs = _seed_without_page(settings, 30)
    _seed(settings, "hidden/c", 99, dismissed=1)
    _seed(settings, "excluded/c", 99, excluded="Bara för medlemmar.")
    fake.clip_views = [9_000] * 5
    scout.Scout(settings).run_once()
    assert _with_page(settings, refs) == refs[:scout.JOINS_PER_RUN] and scout.JOINS_PER_RUN == 10  # de bästa först
    assert fake.clip_calls == [CR] * 10 and _saved(settings, refs[0])["clip_views"] == [9_000] * 5
    assert scout.Scout(settings).calls_today() == 4 + 10  # initialize, sökningarna och tio /go/
    assert fake.calls("tools/call", "get_campaign") == []

    for run in (1, 2):
        _later(monkeypatch, minutes=10 * run)
        scout.Scout(settings).run_once()
    assert _with_page(settings, refs) == refs
    assert all(_saved(settings, ref)["clip_views"] == [9_000] * 5 for ref in refs)
    assert len(fake.go) == 30 and scout.Scout(settings).calls_today() == 4 + 10 + 2 * (1 + 10)
    assert "join_checked_at" not in _saved(settings, "hidden/c") | _saved(settings, "excluded/c")


def test_no_go_when_the_rest_of_the_day_needs_the_calls(settings, fake) -> None:
    _seed_without_page(settings, 5)
    scout.Scout(settings)._count(scout.CALL_LIMIT - scout.reserve(NOW) - 4)  # sökningarna tar resten
    scout.Scout(settings).run_once()
    assert fake.go == [] and scout.Scout(settings).calls_today() == scout.CALL_LIMIT - scout.reserve(NOW)


@pytest.mark.parametrize(("left", "go"), [(0, 0), (1, 1)])  # anrop kvar ovanför marginalen efter sökningarna
def test_go_leaves_room_for_the_details_of_new_campaigns_later_in_the_day(settings, fake, left, go) -> None:
    _seed_without_page(settings, 5)
    room = scout.CALL_LIMIT - scout.reserve(NOW) - 20  # 20: detaljerna till fem nya kampanjer à fyra anrop
    scout.Scout(settings)._count(room - 4 - left)  # 4 för initialize och sökningarna
    scout.Scout(settings).run_once()
    assert len(fake.go) == go and scout.Scout(settings).calls_today() == room - left + go
    assert scout.JOIN_SLACK == 20


@pytest.mark.parametrize("stop", [{"down": True}, {"limit_at": "search_campaigns"}])
def test_no_go_when_clippingalpha_stops_the_run(settings, fake, stop) -> None:
    _seed_without_page(settings, 5)
    vars(fake).update(stop)
    scout.Scout(settings).run_once()
    assert fake.go == []


def test_a_search_hit_without_a_go_link_is_not_tried(settings, fake) -> None:
    _seed_without_page(settings, 1)
    with scout.Scout(settings)._db() as db:
        data = {"search": _item("seen/c0", joinUrl=None), "detail_at": scout._iso(NOW)}
        db.execute("UPDATE scout_campaigns SET data = ?", (json.dumps(data),))
    scout.Scout(settings).run_once()
    assert fake.go == [] and "join_checked_at" not in _saved(settings, "seen/c0")


@pytest.mark.parametrize("status", [429, 503])
def test_the_first_error_from_go_stops_the_round_and_is_tried_again_next_run(settings, fake, monkeypatch,
                                                                             status) -> None:
    refs = _seed_without_page(settings, 5)
    fake.go_status = status
    scout.Scout(settings).run_once()
    assert len(fake.go) == 1 and _with_page(settings, refs) == []
    assert scout.Scout(settings).calls_today() == 4 + 1

    fake.go_status = 302
    _later(monkeypatch, minutes=10)
    scout.Scout(settings).run_once()
    assert len(fake.go) == 1 + 5 and _with_page(settings, refs) == refs


def test_go_is_not_tried_for_campaigns_that_are_no_longer_shown(settings, fake) -> None:
    _seed(settings, "gone/c", 90, detail_at=scout._iso(NOW), updated=scout._iso(NOW - timedelta(hours=73)))
    _seed(settings, "shown/c", 80, detail_at=scout._iso(NOW), updated=scout._iso(NOW - timedelta(hours=71)))
    assert scout.SHOW_S == 72 * 3600
    scout.Scout(settings).run_once()
    assert len(fake.go) == 1 and _with_page(settings, ["gone/c", "shown/c"]) == ["shown/c"]
    assert "join_checked_at" not in _saved(settings, "gone/c")
