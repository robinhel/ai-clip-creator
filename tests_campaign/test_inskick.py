"""Hjälpen för inskick till Content Rewards: inställningen och klippets fält i API:t."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api
from campaign.cliplog import ClipLog

from .conftest import set_profile

CR = "https://contentrewards.com/c/campaigns/abc"


@pytest.fixture
def client(settings) -> TestClient:
    app = FastAPI()
    app.state.active_run_id = None
    app.include_router(api.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    return TestClient(app)


def _clip(settings) -> int:
    path = settings.out_dir / "v" / "klipp.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"mp4")
    return ClipLog(settings.data_dir / "klipp.db").add(
        vod_id="v", profile="skiimasktimmy", file=str(path), title="t", caption="c", status="godkand")


def test_the_helper_is_on_when_nothing_is_saved(client):
    assert client.get("/campaign/preferences").json() == {"submit_helper": True}


def test_put_saves_the_switch_in_the_data_folder(client, settings):
    answer = client.put("/campaign/preferences", json={"submit_helper": False})
    assert answer.status_code == 200 and answer.json() == {"submit_helper": False}
    assert json.loads((settings.data_dir / "preferences.json").read_text()) == {"submit_helper": False}
    assert client.get("/campaign/preferences").json() == {"submit_helper": False}
    assert client.put("/campaign/preferences", json={"submit_helper": True}).json() == {"submit_helper": True}


def test_a_broken_file_counts_as_on(client, settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "preferences.json").write_text("{trasig")
    assert client.get("/campaign/preferences").json() == {"submit_helper": True}


@pytest.mark.parametrize("body", [{"submit_helper": True, "annat": 1}, {}, {"submit_helper": "kanske"}])
def test_unknown_or_wrong_fields_give_422(client, body):
    assert client.put("/campaign/preferences", json=body).status_code == 422


def test_put_needs_the_api_key(client, monkeypatch):
    monkeypatch.setenv("CLIPSMITH_API_KEY", "nyckel")
    assert client.put("/campaign/preferences", json={"submit_helper": False}).status_code == 401
    answer = client.put("/campaign/preferences", json={"submit_helper": False}, headers={"X-Api-Key": "nyckel"})
    assert answer.status_code == 200


@pytest.mark.parametrize(("links", "expected"), [
    ({"content_rewards": CR}, CR), ({"content_rewards": "javascript:alert(1)"}, None),
    ({"content_rewards": "http://contentrewards.com/x"}, None), ({"content_rewards": None}, None), (None, None)])
def test_the_clip_has_the_campaigns_content_rewards_link(client, settings, links, expected):
    set_profile(settings, active=True, links=links)
    clip_id = _clip(settings)
    (clip,) = [c for c in client.get("/campaign/clips").json() if c["id"] == clip_id]
    assert clip["content_rewards_url"] == expected


@pytest.mark.parametrize(("platforms", "expected"), [
    (["tiktok"], ["tiktok"]), ([], ["tiktok", "youtube_shorts", "instagram_reels"]),
    (None, ["tiktok", "youtube_shorts", "instagram_reels"])])
def test_the_clip_has_the_campaigns_platforms(client, settings, platforms, expected):
    set_profile(settings, active=True, platforms=platforms)
    clip_id = _clip(settings)
    (clip,) = [c for c in client.get("/campaign/clips").json() if c["id"] == clip_id]
    assert clip["platforms"] == expected
