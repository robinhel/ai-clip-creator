"""Bevaka live per kampanj: GET /campaign/live och PUT /campaign/live/{profile}."""

from __future__ import annotations

import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api
from campaign.live import LiveState
from campaign.live_api import router

from .conftest import PAKET

KEY = {"X-Api-Key": "nyckel"}


@pytest.fixture
def client(settings, monkeypatch):
    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    monkeypatch.setenv("CLIPSMITH_API_KEY", "nyckel")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    return TestClient(app)


def test_watching_is_turned_on_and_off_and_saved(client, settings) -> None:
    answer = client.put("/campaign/live/skiimasktimmy", json={"watching": True}, headers=KEY)
    assert answer.status_code == 200
    assert answer.json() == {
        "profile": "skiimasktimmy",
        "channel": "skiimasktimmy",
        "watching": True,
        "live": False,
        "vod_id": None,
        "error": None,
        "peaks": [],
    }
    assert LiveState(settings).watching() == ["skiimasktimmy"]  # sparat i klippdata/live.json
    assert (settings.data_dir / "live.json").exists()
    answer = client.put("/campaign/live/skiimasktimmy", json={"watching": False}, headers=KEY)
    assert answer.json()["watching"] is False
    assert LiveState(settings).watching() == []


def test_get_shows_the_state_and_the_peaks(client, settings) -> None:
    state = LiveState(settings)
    state.set_watching("skiimasktimmy", True)
    state.set_channel("skiimasktimmy", live=True, vod_id="777")
    n = state.add_peak("skiimasktimmy", "777", 3002.5, "2026-11-18T18:50:02+00:00")
    state.update_peak("skiimasktimmy", "777", n, status="klar", run_id=12)
    answer = client.get("/campaign/live", headers=KEY)
    assert answer.status_code == 200
    assert answer.json() == [
        {
            "profile": "skiimasktimmy",
            "channel": "skiimasktimmy",
            "watching": True,
            "live": True,
            "vod_id": "777",
            "error": None,
            "peaks": [
                {
                    "n": 1,
                    "at": "2026-11-18T18:50:02+00:00",
                    "vod_s": 3002.5,
                    "status": "klar",
                    "run_id": 12,
                    "reason": None,
                }
            ],
        }
    ]


def test_without_the_key_the_answer_is_401(client) -> None:
    assert client.get("/campaign/live").status_code == 401
    assert client.put("/campaign/live/skiimasktimmy", json={"watching": True}).status_code == 401


def test_an_unknown_campaign_is_404(client) -> None:
    answer = client.put("/campaign/live/finnsinte", json={"watching": True}, headers=KEY)
    assert answer.status_code == 404
    assert answer.json()["detail"] == "Kampanjen finnsinte finns inte."
