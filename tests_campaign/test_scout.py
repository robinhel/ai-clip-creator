"""Kampanjbevakning. Låtsas-ClippingAlpha via httpx.MockTransport, inga riktiga anrop.

Formen på en sökträff är tagen från market.similar i get_campaign_analytics, och detaljerna från
get_campaign och get_campaign_analytics (~/.amp/in/kampanj/raw/). Ett riktigt search_campaigns-svar
med träffar gick inte att hämta: gränsen på 500 anrop per dag var nådd (-32001).
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api, scout, scout_api
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


def test_a_fresh_twitch_campaign_with_everything_scores_high() -> None:
    total, parts, excluded = _parts(_data())
    assert excluded is None
    assert list(parts) == ["views", "fresh", "minimum", "source", "budget", "pay", "proof", "hassle"]
    assert [parts[key]["max"] for key in parts] == [40, 15, 12, 9, 9, 6, 6, 3]
    assert sum(parts[key]["max"] for key in parts) == 100
    assert {key: parts[key]["points"] for key in parts} == {
        "views": 20, "fresh": 15, "minimum": 12, "source": 9, "budget": 9, "pay": pytest.approx(5.1, abs=0.05),
        "proof": 6, "hassle": 3}
    assert total == round(20 + 15 + 12 + 9 + 9 + 3 * 1.8 / 1.05 + 6 + 3)
    assert all(part["label"] and part["fact"] for part in parts.values())


@pytest.mark.parametrize(("age_h", "points"), [
    (47, 15), (24 * 10, 15 - 12 * (10 - 2) / (21 - 2)), (24 * 25, 3 * (30 - 25) / (30 - 21)), (24 * 31, 0)])
def test_freshness_falls_with_age(age_h, points) -> None:
    assert _parts(_data(age_h=age_h))[1]["fresh"]["points"] == pytest.approx(points, abs=0.05)


@pytest.mark.parametrize(("verdict", "drain_days", "minus"), [("Fast", 10, 6), ("Fair", 10, 3), ("Slow", 2, 6),
                                                               ("Slow", 5, 3), ("Too early", 10, 0)])
def test_freshness_loses_points_when_the_budget_goes_fast(verdict, drain_days, minus) -> None:
    data = _data(drain={"p50": _iso(NOW + timedelta(days=drain_days)), "stalled": False})
    data["analytics"]["alpha"]["verdicts"][0]["verdict"] = verdict
    assert _parts(data)[1]["fresh"]["points"] == 15 - minus


def test_unknown_age_gives_half_and_okant() -> None:
    data = _data(launchedAt=None)
    data["search"]["launchedAt"] = None
    fresh = _parts(data)[1]["fresh"]
    assert (fresh["points"], fresh["fact"]) == (7.5, "Okänt")


def test_the_chance_to_reach_the_minimum_is_logarithmic() -> None:
    data = _data()
    data["analytics"]["pay"]["minPayoutUsd"] = 5  # 5 dollar vid 2 dollar per 1 000: 2 500 visningar
    part = _parts(data)[1]["minimum"]
    assert part["points"] == pytest.approx(12 * (1 - math.log(2500 / 1260) / math.log(5)), abs=0.05)
    assert "2" in part["fact"] and "500" in part["fact"]
    assert _parts(data, median=2500)[1]["minimum"]["points"] == 12
    assert _parts(data, median=500)[1]["minimum"]["points"] == 0


def test_no_details_gives_half_points_and_okant() -> None:
    data = {"search": _item()}  # bara sökträffen: ingen pay, inga regler, ingen organisatör
    _, parts, excluded = _parts(data)
    assert excluded is None
    assert (parts["minimum"]["points"], parts["minimum"]["fact"]) == (6, "Okänt")
    assert (parts["proof"]["points"], parts["proof"]["fact"]) == (3, "Okänt")
    assert (parts["views"]["points"], parts["views"]["fact"]) == (20, "Okänt")
    assert parts["fresh"]["points"] == 15  # åldern finns i sökträffen


@pytest.mark.parametrize(("title", "about", "niche", "rules", "points"), [
    ("Clip Twitch streams", "", "film-and-tv", ["Post in English"], 9),
    ("Clip the streamer", "", "film-and-tv", [], 4.2),  # stream 2,4 + inget annat språk 1,8
    ("Movie trailer", "Cut the trailer.", "gaming", ["Post in English"], 2.4),  # spel ger minst 2,4
    ("Movie trailer", "Cut the trailer.", "film-and-tv", [], 1.8)])
def test_the_source_points(title, about, niche, rules, points) -> None:
    data = _data(title=title, about=about, niche=niche,
                 rules={"checklist": [{"kind": "account", "text": text} for text in rules]})
    data["search"].update(title=title, niche=niche)
    assert _parts(data)[1]["source"]["points"] == points


@pytest.mark.parametrize(("drain", "left", "points"), [(3.5, 5000, 4.5), (8, 5000, 9), (None, 2000, 6)])
def test_the_budget_lasts(drain, left, points) -> None:
    p50 = _iso(NOW + timedelta(days=drain)) if drain else None
    data = _data(drain={"p50": p50, "stalled": False})
    data["campaign"]["whereToJoin"][0]["budgetLeftUsd"] = left
    data["search"]["drainP50"] = p50
    assert _parts(data)[1]["budget"]["points"] == pytest.approx(points, abs=0.05)


@pytest.mark.parametrize(("net", "points"), [(1.575, 4.5), (None, 3 * 1.8 / 1.2), (4.0, 6)])
def test_the_pay_against_the_market(net, points) -> None:
    data = _data(alpha={"netPer1kUsd": net})
    data["search"]["netPer1kUsd"] = net
    assert _parts(data, cpm_median=1.05 if net else 1.2)[1]["pay"]["points"] == pytest.approx(points, abs=0.05)


def test_proof_of_payout() -> None:
    data = _data(organizerVerified=False)
    data["analytics"]["alpha"]["adjustments"] = [{"key": "payout", "label": "$50 paid to clippers in 7 days"}]
    assert _parts(data)[1]["proof"]["points"] == 3.6
    data["analytics"]["alpha"]["adjustments"] = []
    assert _parts(data)[1]["proof"]["points"] == 1.2


def test_hassle_for_applications_and_many_caption_rules() -> None:
    rules = [{"kind": "caption", "text": f"Tag @x{i}"} for i in range(4)] + [{"kind": "disclosure", "text": "#ad"}]
    data = _data(rules={"checklist": rules})
    assert _parts(data)[1]["hassle"]["points"] == 1.8
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
        self.clip_views: list[int] | None = None  # kampanjens klipp på Content Rewards
        self.twitch_views: list[int] | None = None
        self.clip_calls: list[str] = []
        self.twitch_calls: list[tuple[str, datetime]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            self.go.append(request)
            return httpx.Response(302, headers={"Location": f"{CR}?utm_source=clippingalpha&utm_content=direct"})
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

    def campaign_clip_views(self, cr_url: str) -> list[int] | None:
        self.clip_calls.append(cr_url)
        return self.clip_views

    def twitch_clip_views(self, login: str, now: datetime) -> list[int] | None:
        self.twitch_calls.append((login, now))
        return self.twitch_views

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
    monkeypatch.setattr(scout, "_session", {"id": None})
    monkeypatch.setattr(scout, "_now", lambda: NOW)
    monkeypatch.setattr(scout, "campaign_clip_views", fake.campaign_clip_views)
    monkeypatch.setattr(scout, "twitch_clip_views", fake.twitch_clip_views)
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
                         "first_seen", "is_new"}
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
    fake.clip_views = [50_000] * 10
    scout.Scout(settings).run_once()
    overview = scout.Scout(settings).overview()
    assert overview["error"] is None
    assert {c["ref"] for c in overview["campaigns"]} == {"timmy/streams", "other/streams"}
    timmy = next(c for c in overview["campaigns"] if c["ref"] == "timmy/streams")
    assert timmy["score"] >= 70 and timmy["max_payout_usd"] is None  # från sökträffen
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
    assert part["points"] == pytest.approx(40 * math.log(11_000 / 2_000) / math.log(50_000 / 2_000), abs=0.05)
    assert part["fact"] == "Median 11 000 visningar för kampanjens 10 bästa klipp"


@pytest.mark.parametrize(("views", "points"), [
    ([50_000] * 10, 40), ([90_000] * 6, 40), ([2_000] * 10, 0), ([500] * 5, 0), ([0] * 7, 0)])
def test_the_campaign_scale_runs_from_2000_to_50000(views, points) -> None:
    assert _views(clip_views=views)["points"] == points


def test_twitch_goes_before_the_campaign_clips() -> None:
    part = _views(twitch_views=[1_200], clip_views=[90_000] * 10)
    assert part["points"] == pytest.approx(40 * math.log(1_200 / 100) / math.log(10_000 / 100), abs=0.05)
    assert part["fact"] == "Twitch: median 1 200 visningar för kanalens bästa klipp, 30 dagar"


@pytest.mark.parametrize(("views", "points"), [([10_000], 40), ([100, 50], 0), ([20_000] * 12 + [1], 40)])
def test_the_twitch_scale_runs_from_100_to_10000(views, points) -> None:
    assert _views(twitch_views=views)["points"] == points


@pytest.mark.parametrize("twitch", [[], None])
def test_no_twitch_clips_gives_the_campaign_clips(twitch) -> None:
    part = _views(twitch_views=twitch, clip_views=[50_000] * 5)
    assert (part["points"], part["fact"]) == (40, "Median 50 000 visningar för kampanjens 5 bästa klipp")


@pytest.mark.parametrize("data", [{"clip_views": [90_000] * 4}, {"clip_views": None}, {"twitch_views": []}, {}])
def test_fewer_than_five_campaign_clips_and_no_twitch_is_unknown(data) -> None:
    part = _views(**data)
    assert (part["points"], part["max"], part["fact"]) == (20, 40, "Okänt")


def test_the_parts_sum_to_100() -> None:
    _, parts, _ = _parts(_data())
    assert [(key, part["max"]) for key, part in parts.items()] == [
        ("views", 40), ("fresh", 15), ("minimum", 12), ("source", 9), ("budget", 9), ("pay", 6), ("proof", 6),
        ("hassle", 3)]
    assert sum(part["max"] for part in parts.values()) == 100


CHANNEL = {"videos": [{"kind": "channel", "platform": "twitch", "channel": None,
                       "url": "https://www.twitch.tv/timmy_tv/videos?filter=archives&sort=time"}]}


def _saved(settings, ref: str = "timmy/streams") -> dict:
    with scout.Scout(settings)._db() as db:
        return json.loads(db.execute("SELECT data FROM scout_campaigns WHERE ref = ?", (ref,)).fetchone()[0])


def test_the_details_fetch_the_channel_and_the_campaign_page_once(settings, fake, monkeypatch) -> None:
    fake.add("timmy/streams")
    fake.assets["timmy/streams"] = CHANNEL
    fake.twitch_views = [1_200]
    fake.clip_views = [90_000] * 10
    scout.Scout(settings).run_once()

    assert [c["params"]["arguments"] for c in fake.calls("tools/call", "get_assets")] == [{"campaign": "timmy/streams"}]
    (go,) = fake.go
    assert str(go.url) == "https://clippingalpha.com/go/1"  # utan ?via=mcp
    assert scout.Scout(settings).calls_today() == 2 + 1 + 1 + 4  # /go/ räknas i scout_calls
    data = _saved(settings)
    assert (data["twitch_login"], data["cr_url"], data["clip_views"], data["twitch_views"]) == (
        "timmy_tv", CR, [90_000] * 10, [1_200])
    assert fake.twitch_calls == [("timmy_tv", NOW)] and fake.clip_calls == [CR]
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


def test_without_a_channel_or_campaign_page_there_is_no_twitch_or_page_call(settings, fake) -> None:
    fake.add("timmy/streams", whereToJoin=[{**_campaign()["whereToJoin"][0], "joinUrl": "https://other.example/x"}])
    fake.newest[0]["joinUrl"] = "https://other.example/x"
    scout.Scout(settings).run_once()
    assert (fake.go, fake.twitch_calls, fake.clip_calls) == ([], [], [])
    data = _saved(settings)
    assert data["twitch_login"] is None and "assets_at" in data and "join_checked_at" not in data


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


def test_no_get_assets_or_go_when_the_rest_of_the_day_needs_the_calls(settings, fake) -> None:
    fake.add("timmy/streams")
    # 4 för initialize och sökningarna, sedan 3 kvar: get_campaign och analytics ryms, men inte get_assets och /go/
    scout.Scout(settings)._count(300 - scout.reserve(NOW) - 7)
    scout.Scout(settings).run_once()
    assert fake.calls("tools/call", "get_assets") == [] and fake.go == []
    assert fake.calls("tools/call", "get_campaign") == []
    assert scout.Scout(settings).calls_today() == 300 - scout.reserve(NOW) - 3
