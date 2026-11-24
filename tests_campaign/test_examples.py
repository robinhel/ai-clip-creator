"""Exempelklipp: kampanjens bästa klipp och klippare från Content Rewards publika kampanjsida.
Låtsas-sida via httpx.MockTransport, inga riktiga anrop."""

from __future__ import annotations

import json
import shutil

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api, examples, profiles_api

from .conftest import PAKET

PAGE_URL = "https://contentrewards.com/c/campaigns/c21c887c-5a8a-4891-a5b9-c094f7f1a689"
CDN = "https://cdn.contentrewards.com/downloaded-videos/abc"


def _page(clips: list[dict], earners: list[dict]) -> str:
    """En sida som Next.js skickar den: datan ligger som JSON-text i self.__next_f.push([1,"..."])."""
    return _flight_page("4:" + json.dumps({"campaign": {"creatorCount": 26, "topEarners": earners, "topClips": clips}}))


def _flight_page(flight: str) -> str:
    half = len(flight) // 2  # datan kan vara delad på flera push-anrop
    pushes = "".join(
        f"<script>self.__next_f.push([1,{json.dumps(part)}])</script>" for part in (flight[:half], flight[half:])
    )
    return f"<html><body>{pushes}</body></html>"


EARNERS = [
    {"rank": 1, "userId": "u1", "whopUsername": "cree3", "username": None, "earnedCents": 5473,
     "totalViews": 21876, "approvedSubmissionCount": 2},
    {"rank": 2, "userId": "u2", "username": "snorre haanæs", "earnedCents": 369, "totalViews": 13568,
     "approvedSubmissionCount": 9},
]
CLIPS = [
    {"rank": 1, "userId": "u1", "username": None, "platform": "tiktok", "totalViews": 17800,
     "postUrl": "https://www.tiktok.com/@ch1psu/video/1", "videoUrl": f"{CDN}/tiktok_1.mp4",
     "thumbnailUrl": f"{CDN}/tiktok_1_thumb.webp", "title": "The cinematic trailers always hit different"},
    {"rank": 2, "userId": "u9", "username": "Marius", "platform": "youtube", "totalViews": 5946,
     "postUrl": "javascript:alert(1)", "videoUrl": "https://evil.example/x.mp4",
     "thumbnailUrl": "https://evil.example/x.webp", "title": "x" * 500},
]


def test_parse_joins_clips_with_their_clipper_and_keeps_only_safe_links() -> None:
    out = examples.parse(_page(CLIPS, EARNERS))
    assert out["earners"] == [
        {"rank": 1, "name": "cree3", "earned_usd": 54.73, "views": 21876, "approved": 2},
        {"rank": 2, "name": "snorre haanæs", "earned_usd": 3.69, "views": 13568, "approved": 9},
    ]
    first, second = out["clips"]
    assert first == {"rank": 1, "platform": "tiktok", "views": 17800, "name": "cree3", "earner_rank": 1,
                     "post_url": "https://www.tiktok.com/@ch1psu/video/1", "video_url": f"{CDN}/tiktok_1.mp4",
                     "thumbnail_url": f"{CDN}/tiktok_1_thumb.webp",
                     "title": "The cinematic trailers always hit different"}
    # En länk som inte är https, och video från en annan värd än Content Rewards CDN, visas inte.
    assert (second["post_url"], second["video_url"], second["thumbnail_url"]) == (None, None, None)
    assert (second["name"], second["earner_rank"]) == ("Marius", None)
    assert len(second["title"]) <= 300


def test_parse_without_data_raises_a_plain_error() -> None:
    with pytest.raises(ValueError, match="No clips found"):
        examples.parse("<html>Inloggning krävs</html>")


# ---------- priset per land ----------

# Utdrag ur två riktiga sidor (.amp/in/orb-backup/clippingalpha-views/cr-*.html), som texten i flight-datan.
# Call of Duty (a1cefc3b-…): restOfWorld har bara tiktok, och instagram har ett fast pris i payoutModel.
COD = ('"budget":3000000,"geoRates":{"restOfWorld":[{"platform":"tiktok","rate":200}],"tiers":[{"countries":'
       '["BD","IN","PK"],"label":"Pak, Ind, Ban","platform":"tiktok","rate":0}]},"payoutModel":[{"maxPayout":120000,'
       '"minPayout":600,"platform":"tiktok","rate":0},{"maxPayout":120000,"minPayout":450,"platform":"instagram",'
       '"rate":150}]},"type":"cpm","description":"Post to TikTok and Instagram Reels.\\n","geoPriced":true,'
       '"id":"a1cefc3b-4533-438f-8e70-a7299550f1cf"')
# WA-CHAA! (eb28f557-…): tiktok och youtube i restOfWorld.
WACHAA = ('"budget":1050000,"geoRates":{"restOfWorld":[{"platform":"tiktok","rate":200},{"platform":"youtube",'
          '"rate":200}],"tiers":[{"countries":["IN","PK","BD"],"label":"Pak, Ind, Ban","platform":"tiktok","rate":0},'
          '{"countries":["IN","PK","BD"],"label":"Pak, Ind, Ban","platform":"youtube","rate":0}]},"payoutModel":'
          '[{"maxPayout":60000,"minPayout":200,"platform":"tiktok","rate":0},{"maxPayout":60000,"minPayout":150,'
          '"platform":"instagram","rate":150},{"maxPayout":60000,"minPayout":200,"platform":"youtube","rate":0}]},'
          '"type":"cpm","description":"Use only footage from the official content folder.\\n","geoPriced":true,'
          '"id":"eb28f557-df7b-4245-8603-01380c4d84cc"')
TOP = '"topEarners":[],"topClips":[{"rank":1,"userId":"u1","platform":"tiktok","totalViews":900}]'


def _campaign_page(excerpt: str, top: str = TOP) -> str:
    """Utdraget börjar inne i kampanjens betalningsdel, som på den riktiga sidan."""
    return _flight_page('5:{"campaign":{"pay":{"requirement":{"preApproval":false},' + excerpt + "," + top + "}}")


@pytest.mark.parametrize(("excerpt", "rates", "model"), [
    (COD, {"restOfWorld": [{"platform": "tiktok", "rate": 200}],
           "tiers": [{"countries": ["BD", "IN", "PK"], "label": "Pak, Ind, Ban", "platform": "tiktok", "rate": 0}]},
     [{"maxPayout": 120000, "minPayout": 600, "platform": "tiktok", "rate": 0},
      {"maxPayout": 120000, "minPayout": 450, "platform": "instagram", "rate": 150}]),
    (WACHAA, {"restOfWorld": [{"platform": "tiktok", "rate": 200}, {"platform": "youtube", "rate": 200}],
              "tiers": [{"countries": ["IN", "PK", "BD"], "label": "Pak, Ind, Ban", "platform": "tiktok", "rate": 0},
                        {"countries": ["IN", "PK", "BD"], "label": "Pak, Ind, Ban", "platform": "youtube", "rate": 0}]},
     [{"maxPayout": 60000, "minPayout": 200, "platform": "tiktok", "rate": 0},
      {"maxPayout": 60000, "minPayout": 150, "platform": "instagram", "rate": 150},
      {"maxPayout": 60000, "minPayout": 200, "platform": "youtube", "rate": 0}])])
def test_parse_gives_the_price_per_country_from_real_pages(excerpt, rates, model) -> None:
    out = examples.parse(_campaign_page(excerpt))
    assert out["geo"] == {"priced": True, "rates": rates, "payout_model": model}
    assert [c["views"] for c in out["clips"]] == [900]


def test_a_page_with_prices_but_no_clips_gives_empty_lists_and_the_prices() -> None:
    out = examples.parse(_campaign_page(COD, top='"creatorCount":0'))
    assert (out["clips"], out["earners"], out["geo"]["priced"]) == ([], [], True)


def test_a_new_campaign_with_empty_top_clips_is_not_an_error() -> None:
    out = examples.parse(_page([], []))
    assert out == {"clips": [], "earners": [], "geo": {"priced": False, "rates": None, "payout_model": []}}


def test_a_key_with_the_wrong_type_before_the_real_one_is_skipped() -> None:
    clips = json.dumps(CLIPS[:1])
    rates = '{"restOfWorld":[{"platform":"tiktok","rate":200}],"tiers":[]}'
    out = examples.parse(_flight_page(
        '3:{"topClips":null,"topEarners":"x","geoRates":[],"payoutModel":{}}\n'
        f'4:{{"campaign":{{"topEarners":{json.dumps(EARNERS)},"topClips":{clips},"geoPriced":true,'
        f'"geoRates":{rates},"payoutModel":[{{"platform":"tiktok","rate":0}}]}}}}'))
    assert [(c["views"], c["name"]) for c in out["clips"]] == [(17800, "cree3")]
    assert [e["name"] for e in out["earners"]] == ["cree3", "snorre haanæs"]
    assert out["geo"] == {"priced": True, "rates": {"restOfWorld": [{"platform": "tiktok", "rate": 200}], "tiers": []},
                          "payout_model": [{"platform": "tiktok", "rate": 0}]}


def test_a_field_that_is_not_json_only_empties_that_field() -> None:
    clips = json.dumps(CLIPS[:1])
    out = examples.parse(_flight_page(f'4:{{"campaign":{{"topEarners":[oops],"topClips":{clips}}}}}'))
    assert ([c["views"] for c in out["clips"]], out["earners"]) == ([17800], [])


def test_a_page_with_price_per_country_turned_off() -> None:
    out = examples.parse(_campaign_page('"geoRates":null,"payoutModel":[{"platform":"tiktok","rate":150}]},'
                                        '"geoPriced":false'))
    assert out["geo"] == {"priced": False, "rates": None, "payout_model": [{"platform": "tiktok", "rate": 150}]}


@pytest.fixture
def client(settings, monkeypatch) -> tuple[TestClient, list[str]]:
    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    monkeypatch.delenv("CLIPSMITH_API_KEY", raising=False)
    calls: list[str] = []

    def fake(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "trasig" in str(request.url):
            return httpx.Response(503)
        return httpx.Response(200, text=_page(CLIPS, EARNERS))

    monkeypatch.setattr(examples, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(fake)))
    examples.clear_cache()
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(profiles_api.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    return TestClient(app), calls


def _set_link(settings, link: str) -> None:
    path = settings.profiles_dir / "skiimasktimmy.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("content_rewards: null", f"content_rewards: {link}"), encoding="utf-8")


def test_examples_need_a_content_rewards_link(client, settings) -> None:
    http, calls = client
    body = http.get("/campaign/profiles/skiimasktimmy/examples").json()
    assert body["clips"] == [] and "Set up" in body["problem"]
    _set_link(settings, "https://whop.com/x/")
    body = http.get("/campaign/profiles/skiimasktimmy/examples").json()
    assert body["clips"] == [] and "contentrewards.com" in body["problem"]
    assert calls == []


def test_examples_come_from_the_campaign_page_and_are_cached(client, settings) -> None:
    http, calls = client
    _set_link(settings, PAGE_URL)
    body = http.get("/campaign/profiles/skiimasktimmy/examples").json()
    assert body["problem"] is None and body["source"] == PAGE_URL
    assert [c["rank"] for c in body["clips"]] == [1, 2]
    assert body["earners"][0]["name"] == "cree3"
    http.get("/campaign/profiles/skiimasktimmy/examples")
    assert calls == [PAGE_URL]  # en hämtning per timme räcker


def test_examples_report_when_the_page_cannot_be_read(client, settings) -> None:
    http, _ = client
    _set_link(settings, "https://contentrewards.com/c/campaigns/trasig")
    response = http.get("/campaign/profiles/skiimasktimmy/examples")
    assert response.status_code == 502
    assert "Content Rewards answered 503" in response.json()["detail"]


def test_examples_say_in_plain_words_when_content_rewards_does_not_answer(settings, monkeypatch) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nere", request=request)

    monkeypatch.setattr(examples, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(down)))
    examples.clear_cache()
    app = FastAPI()
    app.include_router(profiles_api.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    _set_link(settings, PAGE_URL)
    response = TestClient(app).get("/campaign/profiles/skiimasktimmy/examples")
    assert response.status_code == 502
    assert response.json()["detail"] == "Content Rewards does not answer. Try again in a while."


def test_the_real_client_can_build_a_request() -> None:
    # Headers måste vara ASCII. Ett "ä" i User-Agent gav 500 vid första riktiga hämtningen.
    with examples.http_client() as http:
        assert http.build_request("GET", PAGE_URL).headers["user-agent"]
