"""Betyg och rekommendation för Senaste streams. Påhittad Twitch (GQL och Helix), inga riktiga anrop."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from campaign import streams
from campaign.profile import load_profile

from .test_api import client  # noqa: F401  (fixture)
from .test_omgang5 import _done_run, _second_campaign

THUMB = "https://static-cdn.jtvnw.net/cf_vods/x/thumb/thumb0-640x360.jpg"
SECRET = "hemligt-varde-123"


def _node(vod_id: str, day: int, views: int, game: str = "Bodycam", status: str = "RECORDED",
          length: int = 13360) -> dict:
    return {"id": vod_id, "title": f"Stream {vod_id}", "lengthSeconds": length,
            "createdAt": f"2026-11-{day:02d}T18:00:00Z", "game": {"name": game},
            "previewThumbnailURL": THUMB, "viewCount": views, "status": status}


class FakeTwitch:
    def __init__(self):
        self.nodes = {"skiimasktimmy": [_node("103", 11, 1000), _node("102", 10, 500, game="ROB RUN"),
                                        _node("101", 9, 0, game="Just Chatting")]}
        self.clips = {"103": 25, "102": 1}  # tittarklipp per VOD
        self.keys = ("id", SECRET)
        self.helix_error: Exception | None = None
        self.gql_calls = 0
        self.helix_calls: list[dict] = []

    def gql(self, query: str) -> dict:
        self.gql_calls += 1
        login = query.split('"')[1]
        nodes = self.nodes.get(login)
        return {"data": {"user": nodes is not None and {
            "login": login, "displayName": login.title(), "videos": {"edges": [{"node": n} for n in nodes]}} or None}}

    def secrets(self):
        return SimpleNamespace(twitch_client_id=self.keys[0], twitch_client_secret=self.keys[1])

    def client(self, client_id, client_secret, **kwargs):
        fake = self

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return None

            def get_user_id(self, login):
                fake.helix_calls.append({"login": login})
                if fake.helix_error:
                    raise fake.helix_error
                return "42"

            def get_clips(self, broadcaster_id, *, started_at=None, ended_at=None, first=100):
                fake.helix_calls[-1].update(broadcaster_id=broadcaster_id, started_at=started_at, ended_at=ended_at)
                clips = [SimpleNamespace(video_id=vod) for vod, n in fake.clips.items() for _ in range(n)]
                return [*clips, SimpleNamespace(video_id="")]  # klipp vars VOD är borta räknas inte

        return Client()


@pytest.fixture
def twitch(client, monkeypatch) -> FakeTwitch:  # noqa: F811
    fake = FakeTwitch()
    monkeypatch.setattr(streams, "_cache", {})
    monkeypatch.setattr(streams.twitch, "gql_query", fake.gql)
    monkeypatch.setattr(streams, "load_secrets", fake.secrets)
    monkeypatch.setattr(streams, "TwitchClient", fake.client)
    return fake


def _by_id(client) -> dict:  # noqa: F811
    response = client.get("/campaign/streams")
    assert response.status_code == 200, response.text
    return {s["vod_id"]: s for s in response.json()}


def test_profile_has_preferred_games(settings) -> None:
    assert load_profile("skiimasktimmy", settings.profiles_dir).preferred_games == ["Bodycam"]


def test_score_is_clips_views_and_game(client, twitch) -> None:  # noqa: F811
    found = _by_id(client)
    best, rob, chat = found["103"], found["102"], found["101"]
    assert (best["view_count"], best["viewer_clips"], best["live"]) == (1000, 25, False)
    assert best["score_parts"] == {"clips": 50, "views": 30, "game": 20} and best["score"] == 100
    assert best["reason"] == "25 tittarklipp, 1 000 visningar, Bodycam"
    assert rob["score_parts"] == {"clips": round(2.5), "views": 15, "game": 0}
    assert rob["reason"] == "1 tittarklipp, 500 visningar"
    assert (chat["viewer_clips"], chat["score"], chat["reason"]) == (0, 0, "Inga tydliga signaler")
    assert [s["vod_id"] for s in client.get("/campaign/streams").json()] == ["103", "102", "101"]  # nyast först
    (call,) = twitch.helix_calls
    assert (call["login"], call["broadcaster_id"]) == ("skiimasktimmy", "42")
    assert call["started_at"] == datetime(2026, 11, 9, 18, 0, tzinfo=UTC)  # den äldsta VOD:en


def test_length_does_not_change_the_score(client, twitch) -> None:  # noqa: F811
    twitch.nodes["skiimasktimmy"] = [_node("103", 11, 1000, length=600), _node("102", 10, 1000, length=40000)]
    twitch.clips = {"103": 3, "102": 3}
    found = _by_id(client)
    assert found["103"]["score"] == found["102"]["score"]


def test_the_best_stream_without_a_run_and_not_live_is_recommended(client, settings, twitch) -> None:  # noqa: F811
    twitch.nodes["skiimasktimmy"] = [_node("104", 12, 900, status="RECORDING"), _node("103", 11, 1000),
                                     _node("102", 10, 500), _node("101", 9, 0, game="Just Chatting")]
    twitch.clips = {"104": 30, "103": 25, "102": 1}
    _done_run(settings, "103")
    found = _by_id(client)
    assert found["104"]["live"] is True
    assert [v for v, s in found.items() if s["recommended"]] == ["102"]


def test_equal_scores_give_the_newest_and_zero_gives_none(client, twitch) -> None:  # noqa: F811
    twitch.nodes["skiimasktimmy"] = [_node("103", 11, 1000), _node("102", 10, 1000)]
    twitch.clips = {}
    assert [v for v, s in _by_id(client).items() if s["recommended"]] == ["103"]

    streams._cache.clear()
    twitch.nodes["skiimasktimmy"] = [_node("103", 11, 0, game="Just Chatting")]
    assert _by_id(client)["103"]["recommended"] is False


@pytest.mark.parametrize("problem", ["no_keys", "helix_down"])
def test_without_helix_the_clips_count_nothing_and_there_is_no_error(client, twitch, caplog, problem) -> None:  # noqa: F811
    if problem == "no_keys":
        twitch.keys = ("", "")
    else:
        twitch.helix_error = ConnectionError(f"nere {SECRET}")
    with caplog.at_level(logging.WARNING):
        best = _by_id(client)["103"]
    assert (best["viewer_clips"], best["score_parts"]["clips"], best["score"]) == (None, 0, 50)
    assert "tittarklipp" in caplog.text
    assert SECRET not in caplog.text


def test_preferred_games_are_the_union_of_the_campaigns_and_ignore_case(client, settings, twitch) -> None:  # noqa: F811
    path = settings.profiles_dir / "skiimasktimmy.yaml"
    text = path.read_text()
    assert "preferred_games: [Bodycam]" in text
    path.write_text(text.replace("preferred_games: [Bodycam]", "preferred_games: [bodycam]"))
    _second_campaign(settings, "timmy2", "skiimasktimmy")
    (settings.profiles_dir / "timmy2.yaml").write_text(path.read_text().replace(
        "clipsmith_config: config.timmy.yaml", "clipsmith_config: config.timmy2.yaml").replace(
        "preferred_games: [bodycam]", "preferred_games: [rob run]"))
    found = _by_id(client)
    assert (found["103"]["score_parts"]["game"], found["102"]["score_parts"]["game"]) == (20, 20)
    assert found["101"]["score_parts"]["game"] == 0

    streams._cache.clear()
    for name in ("skiimasktimmy", "timmy2"):
        p = settings.profiles_dir / f"{name}.yaml"
        p.write_text("".join(line for line in p.read_text().splitlines(keepends=True)
                             if not line.startswith("preferred_games:")))
    assert {s["score_parts"]["game"] for s in _by_id(client).values()} == {0}


def test_the_viewer_clips_are_cached_with_the_streams(client, twitch) -> None:  # noqa: F811
    _by_id(client)
    _by_id(client)
    assert (twitch.gql_calls, len(twitch.helix_calls)) == (1, 1)
