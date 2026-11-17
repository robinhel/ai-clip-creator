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
    data = json.dumps({"campaign": {"creatorCount": 26, "topEarners": earners, "topClips": clips}})
    flight = "4:" + data
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


def test_parse_without_data_raises_a_swedish_error() -> None:
    with pytest.raises(ValueError, match="hittade inga klipp"):
        examples.parse("<html>Inloggning krävs</html>")


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
    assert body["clips"] == [] and "Ställ in" in body["problem"]
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
    assert "Content Rewards svarade 503" in response.json()["detail"]


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
    assert response.json()["detail"] == "Content Rewards svarar inte. Försök igen om en stund."


def test_the_real_client_can_build_a_request() -> None:
    # Headers måste vara ASCII. Ett "ä" i User-Agent gav 500 vid första riktiga hämtningen.
    with examples.http_client() as http:
        assert http.build_request("GET", PAGE_URL).headers["user-agent"]
