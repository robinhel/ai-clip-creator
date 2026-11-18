"""Visningarna för kampanjens innehåll. Låtsas-ClippingAlpha och låtsas-Twitch via httpx.MockTransport,
inga riktiga anrop.

Utdragen kommer från riktiga svar (.amp/in/orb-backup/clippingalpha-views/): get_assets för
irubyana-twitch/clipping och johndoe-twitch-stream/streaming, och /go/11045701.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from campaign import content_views

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
CR = "https://contentrewards.com/c/campaigns/a1cefc3b-4533-438f-8e70-a7299550f1cf"

# get_assets för irubyana-twitch/clipping: videorna och sist kanalen, vars channel är null
IRUBYANA = {"videos": [
    {"kind": "video", "platform": "twitch", "channel": "irubyana",
     "url": "https://www.twitch.tv/videos/2893935494"},
    {"kind": "channel", "platform": "twitch", "channel": None,
     "url": "https://www.twitch.tv/irubyana/videos?filter=archives&sort=time"}]}
# get_assets för johndoe-twitch-stream/streaming: bara videor, och channel är null i alla
JOHNDOE = {"videos": [{"kind": "video", "platform": "twitch", "channel": None,
                       "url": "https://www.twitch.tv/videos/2919796761"}]}


# ---------- kanalen ur get_assets ----------


def test_the_channel_entry_gives_the_login() -> None:
    assets = {"videos": [IRUBYANA["videos"][1] | {"url": "https://www.twitch.tv/JohnDoe_100x/videos?filter=archives"}]}
    assert content_views.channel_from_assets(assets) == "johndoe_100x"


def test_the_channel_entry_goes_before_the_videos() -> None:
    assets = {"videos": [{**IRUBYANA["videos"][0], "channel": "someone_else"}, IRUBYANA["videos"][1]]}
    assert content_views.channel_from_assets(assets) == "irubyana"


def test_the_channel_of_a_video_is_the_fallback() -> None:
    assets = {"videos": [IRUBYANA["videos"][0], {**IRUBYANA["videos"][1], "url": "https://evil.example/x"}]}
    assert content_views.channel_from_assets(assets) == "irubyana"


@pytest.mark.parametrize("assets", [
    JOHNDOE,
    {"videos": [{**IRUBYANA["videos"][1], "url": "https://www.twitch.tv/a!b/videos"}]},
    {"videos": [{**IRUBYANA["videos"][1], "url": "https://evil.example/irubyana/videos"}]},
    {"videos": [{**IRUBYANA["videos"][1], "url": None}]},
    {"videos": [{**IRUBYANA["videos"][0], "channel": "ab"}]},  # för kort
    {"videos": [{**IRUBYANA["videos"][0], "platform": "youtube"}]},
    {},
])
def test_no_valid_twitch_channel_gives_none(assets) -> None:
    assert content_views.channel_from_assets(assets) is None


def test_the_first_twitch_vod_gives_the_video_id() -> None:
    assets = {"videos": [{**JOHNDOE["videos"][0], "platform": "youtube", "url": "https://www.twitch.tv/videos/1"},
                         *JOHNDOE["videos"], {**JOHNDOE["videos"][0], "url": "https://www.twitch.tv/videos/2"}]}
    assert content_views.vod_from_assets(assets) == "2919796761"


@pytest.mark.parametrize("url", [
    "https://evil.example/videos/2919796761", "http://www.twitch.tv/videos/2919796761",
    "https://www.twitch.tv/videos/abc", "https://www.twitch.tv/irubyana/videos?filter=archives", None])
def test_no_valid_twitch_vod_gives_none(url) -> None:
    assert content_views.vod_from_assets({"videos": [{**JOHNDOE["videos"][0], "url": url}]}) is None
    assert content_views.vod_from_assets({}) is None


# ---------- /go/-länken ----------


def _http(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_redirect_to_content_rewards_gives_the_address_without_query() -> None:
    seen: list[httpx.Request] = []

    def go(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"Location": f"{CR}?utm_source=clippingalpha&utm_medium=referral"})

    with _http(go) as http:
        assert content_views.resolve_join(http, "https://clippingalpha.com/go/11045701?via=mcp") == CR
    (request,) = seen
    assert (request.method, str(request.url)) == ("GET", "https://clippingalpha.com/go/11045701")


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"Location": "https://evil.example/c/campaigns/a1cefc3b-4533-438f-8e70-a7299550f1cf"}),
    httpx.Response(302, headers={"Location": "https://contentrewards.com/c/other/x"}),
    httpx.Response(200, text="<html></html>"),
    httpx.Response(404, text="Not found"),
    httpx.Response(410, text="Gone"),
])
def test_other_answers_give_none(response) -> None:
    with _http(lambda request: response) as http:
        assert content_views.resolve_join(http, "https://clippingalpha.com/go/11045701?via=mcp") is None


@pytest.mark.parametrize("status", [429, 500, 503])
def test_a_temporary_error_is_raised_so_that_it_is_tried_again(status) -> None:
    with _http(lambda request: httpx.Response(status, text="fel")) as http, pytest.raises(httpx.HTTPStatusError):
        content_views.resolve_join(http, "https://clippingalpha.com/go/11045701?via=mcp")


@pytest.mark.parametrize("url", [
    "https://evil.example/go/1", "https://clippingalpha.com/campaigns/a/b", "http://clippingalpha.com/go/1",
    "https://clippingalpha.com/go/abc", None])
def test_other_links_are_never_fetched(url) -> None:
    def never(request: httpx.Request) -> httpx.Response:
        raise AssertionError("inget anrop")

    with _http(never) as http:
        assert content_views.resolve_join(http, url) is None


# ---------- kampanjens klipp ----------


def test_the_campaign_clip_views_come_from_the_examples(monkeypatch) -> None:
    urls: list[str] = []

    def fetch(url: str) -> dict:
        urls.append(url)
        return {"clips": [{"views": 23000, "rank": 1}, {"views": 900, "rank": 2}], "earners": []}

    monkeypatch.setattr(content_views.examples, "fetch", fetch)
    assert content_views.campaign_clip_views(CR) == [23000, 900]
    assert urls == [CR]


def test_a_page_that_cannot_be_read_gives_none(monkeypatch) -> None:
    def fetch(url: str) -> dict:
        raise RuntimeError("Content Rewards svarar inte.")

    monkeypatch.setattr(content_views.examples, "fetch", fetch)
    assert content_views.campaign_clip_views(CR) is None


# ---------- Twitch ----------


class FakeHelix:
    """Svarar som Twitch på token, /users, /videos och /clips."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.users = {"irubyana": "42"}
        self.clips = [{"view_count": 1200}, {"view_count": 300}]
        self.videos = {"2919796761": "johndoe_100x"}  # video-id och kanalens user_login
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            return httpx.Response(500)
        if request.url.host == "id.twitch.tv":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if request.url.path == "/helix/users":
            login = request.url.params["login"]
            return httpx.Response(200, json={"data": [{"id": self.users[login]}] if login in self.users else []})
        if request.url.path == "/helix/videos":
            video = request.url.params["id"]
            return httpx.Response(200, json={"data": [{"id": video, "user_login": self.videos[video]}]
                                             if video in self.videos else []})
        assert request.url.path == "/helix/clips"
        return httpx.Response(200, json={"data": self.clips, "pagination": {"cursor": "next"}})

    def paths(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == path]


@pytest.fixture
def helix(monkeypatch) -> FakeHelix:
    fake = FakeHelix()

    class Client(content_views.TwitchClient):
        def __init__(self, client_id: str, client_secret: str):
            super().__init__(client_id, client_secret)
            self._http.close()
            self._http = httpx.Client(transport=httpx.MockTransport(fake))

    monkeypatch.setattr(content_views, "TwitchClient", Client)
    monkeypatch.setattr(content_views, "load_secrets",
                        lambda: SimpleNamespace(twitch_client_id="id", twitch_client_secret="secret"))
    return fake


def test_one_page_of_the_ten_best_clips_from_the_last_30_days(helix) -> None:
    assert content_views.twitch_clip_views("irubyana", NOW) == [1200, 300]
    (clips,) = helix.paths("/helix/clips")  # en sida, fast Twitch har fler
    params = clips.url.params
    assert (params["broadcaster_id"], params["first"]) == ("42", "10")
    started, ended = (datetime.fromisoformat(params[k].replace("Z", "+00:00")) for k in ("started_at", "ended_at"))
    assert (started, ended) == (NOW - timedelta(days=30), NOW)
    assert helix.paths("/helix/users")[0].url.params["login"] == "irubyana"


def test_a_channel_without_clips_gives_an_empty_list(helix) -> None:
    helix.clips = []
    assert content_views.twitch_clip_views("irubyana", NOW) == []


def test_a_channel_that_does_not_exist_gives_none(helix) -> None:
    assert content_views.twitch_clip_views("nobody_here", NOW) is None
    assert helix.paths("/helix/clips") == []


def test_twitch_down_gives_none(helix) -> None:
    helix.down = True
    assert content_views.twitch_clip_views("irubyana", NOW) is None


def test_without_keys_no_call_and_none(helix, monkeypatch) -> None:
    monkeypatch.setattr(content_views, "load_secrets",
                        lambda: SimpleNamespace(twitch_client_id="", twitch_client_secret=""))
    assert content_views.twitch_clip_views("irubyana", NOW) is None
    assert content_views.twitch_login_from_vod("2919796761") is None
    assert helix.requests == []


# ---------- kanalen via VOD-länken ----------


def test_the_vod_gives_the_channel_with_one_call_to_videos(helix) -> None:
    helix.videos["2919796761"] = "JohnDoe_100x"
    assert content_views.twitch_login_from_vod("2919796761") == "johndoe_100x"
    (videos,) = helix.paths("/helix/videos")
    assert videos.url.params["id"] == "2919796761"
    assert [r.url.path for r in helix.requests if r.url.host == "api.twitch.tv"] == ["/helix/videos"]


@pytest.mark.parametrize("login", ["a!b", "ab", "", None, 42])
def test_an_invalid_user_login_gives_no_login(helix, login) -> None:
    helix.videos["2919796761"] = login
    assert content_views.twitch_login_from_vod("2919796761") is None


def test_a_vod_that_does_not_exist_or_twitch_down_gives_none(helix) -> None:
    assert content_views.twitch_login_from_vod("1") is None
    helix.down = True
    assert content_views.twitch_login_from_vod("2919796761") is None
