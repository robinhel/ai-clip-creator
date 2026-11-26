"""Smaken i appen och i terminalen: virala klipp, egen länk, analys och skills.
campaign/viral.py och campaign/taste.py är låtsasmoduler här, så inget går mot nätet eller Claude."""

from __future__ import annotations

import shutil
import sys
import types
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import campaign
from campaign import api, cli, profiles_api, taste_api
from campaign.budget import BudgetExceeded

from .conftest import PAKET

CLIP = {"id": "v1", "source": "link", "url": "https://www.tiktok.com/@a/video/1", "video_url": None,
        "title": "Insane clutch", "creator": "a", "views": 90000, "posted_at": "2026-11-20",
        "views_per_day": 15000.0, "outlier": 6.2, "duration": 21.0,
        "location": {"vod_id": "123", "start_s": 300.0, "end_s": 321.0, "method": "words", "confidence": 0.9},
        "measured": None}
TASTE = {"updated": "2026-11-26T12:00:00Z", "profile": "skiimasktimmy", "clip_ids": ["v1"],
         "numbers": {"length_s": 21.0, "first_cut_s": 0.8, "cuts_per_min": 9.0, "caption_words": 6,
                     "hook_text_share": 0.8, "payoff_s": 2.1},
         "rules": ["Open on the payoff"], "summary_en": "Viral clips open on the payoff."}


@pytest.fixture
def fake(monkeypatch) -> SimpleNamespace:
    """Låtsas-viral och låtsas-taste. `calls` visar vad som anropades; `error` kastas av nästa anrop."""
    state = SimpleNamespace(calls=[], error=None, clips=[CLIP], taste=TASTE)

    def call(name, *args, result=None, **kwargs):
        state.calls.append((name, args, kwargs))
        if state.error:
            raise state.error
        return result

    viral = types.ModuleType("campaign.viral")
    viral.collect = lambda profile: call("collect", profile.name, result=state.clips)
    viral.add_link = lambda profile, url, views=None: call("add_link", profile.name, url, views=views, result=CLIP)
    viral.load = lambda profile: state.clips
    taste = types.ModuleType("campaign.taste")
    taste.analyse = lambda profile, top_n=5, brain=None: call("analyse", profile.name, top_n=top_n,
                                                             brain=brain is not None, result=state.taste)
    taste.load_taste = lambda profile: state.taste
    taste.taste_text = lambda profile, data_dir: "Viral clips taste: open on the payoff."
    taste.write_skills = lambda profile: call("write_skills", profile.name,
                                              result=[PAKET / "skills" / "hook.md"])
    for name, module in (("viral", viral), ("taste", taste)):
        monkeypatch.setitem(sys.modules, f"campaign.{name}", module)
        monkeypatch.setattr(campaign, name, module, raising=False)
    return state


@pytest.fixture
def client(settings, fake, monkeypatch) -> TestClient:
    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    monkeypatch.delenv("CLIPSMITH_API_KEY", raising=False)
    monkeypatch.setattr(taste_api, "JOBS", {}, raising=False)
    app = FastAPI()
    app.include_router(profiles_api.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    return TestClient(app)


BASE = "/campaign/profiles/skiimasktimmy"


# ---------- API ----------


TEXT = "Viral clips taste: open on the payoff."
IDLE = {"taste": TASTE, "clips": [CLIP], "text": TEXT, "running": False, "error": None}


def test_get_taste_gives_the_taste_the_clips_the_text_and_the_job(client, fake) -> None:
    assert client.get(f"{BASE}/taste").json() == IDLE


def test_without_analysis_the_taste_is_null(client, fake) -> None:
    fake.taste, fake.clips = None, []
    assert client.get(f"{BASE}/taste").json()["taste"] is None


def test_collect_fetches_the_clips_and_answers_with_the_list(client, fake) -> None:
    response = client.post(f"{BASE}/viral/collect")
    assert response.status_code == 200
    assert response.json() == IDLE
    assert fake.calls == [("collect", ("skiimasktimmy",), {})]


def test_link_runs_in_the_background_and_saves_the_url_and_the_views(client, fake) -> None:
    url = "https://www.tiktok.com/@a/video/1"
    response = client.post(f"{BASE}/viral/link", json={"url": url, "views": 90000})
    assert response.status_code == 202
    assert response.json() == {"taste": TASTE, "clips": [CLIP], "running": True}
    assert fake.calls == [("add_link", ("skiimasktimmy", url), {"views": 90000})]
    assert client.get(f"{BASE}/taste").json() == IDLE  # jobbet är klart
    client.post(f"{BASE}/viral/link", json={"url": url})
    assert fake.calls[-1][2] == {"views": None}


@pytest.mark.parametrize(("path", "body"), [("viral/link", {"url": "https://x.com/1"}), ("taste/analyse", None)])
def test_a_second_job_while_one_runs_is_409(client, fake, path, body) -> None:
    taste_api.JOBS["skiimasktimmy"] = {"running": True, "error": None}  # client lägger en ny JOBS per test
    assert client.post(f"{BASE}/{path}", json=body).status_code == 409
    assert client.get(f"{BASE}/taste").json()["running"] is True
    assert fake.calls == []


def test_link_without_a_url_or_with_negative_views_is_refused(client, fake) -> None:
    assert client.post(f"{BASE}/viral/link", json={"url": ""}).status_code == 422
    assert client.post(f"{BASE}/viral/link", json={"url": "https://x.com/1", "views": -1}).status_code == 422
    assert fake.calls == []


def test_analyse_asks_claude_through_the_brain_in_the_background(client, fake) -> None:
    response = client.post(f"{BASE}/taste/analyse")
    assert response.status_code == 202 and response.json()["running"] is True
    assert fake.calls == [("analyse", ("skiimasktimmy",), {"top_n": 5, "brain": True})]


def test_analyse_without_a_claude_key_says_so(client, fake, settings) -> None:
    settings.anthropic_api_key = ""
    response = client.post(f"{BASE}/taste/analyse")
    assert response.status_code == 400 and "Claude key is missing" in response.json()["detail"]
    assert fake.calls == []


ERRORS = [
    (ValueError("The link is not a TikTok, YouTube or Instagram video."), 400),
    (RuntimeError("TikTok did not answer."), 502),
    (BudgetExceeded("The daily cap is reached: 10.00 of 10.00 dollars."), 402),
]


@pytest.mark.parametrize(("error", "status"), ERRORS)
def test_collect_errors_come_back_as_plain_english(client, fake, error, status) -> None:
    fake.error = error
    response = client.post(f"{BASE}/viral/collect")
    assert response.status_code == status
    assert response.json()["detail"].startswith(str(error))


@pytest.mark.parametrize(("error", "status"), ERRORS)
def test_job_errors_come_back_in_get_taste(client, fake, error, status) -> None:
    fake.error = error
    for path, body in (("viral/link", {"url": "https://x.com/1"}), ("taste/analyse", None)):
        assert client.post(f"{BASE}/{path}", json=body).status_code == 202
        job = client.get(f"{BASE}/taste").json()
        assert job["running"] is False and job["error"].startswith(str(error))
    fake.error = None
    client.post(f"{BASE}/taste/analyse")
    assert client.get(f"{BASE}/taste").json()["error"] is None  # ett nytt jobb tömmer felet


def test_an_unexpected_job_error_does_not_leave_the_job_running(client, fake) -> None:
    fake.error = KeyError("x")
    client.post(f"{BASE}/taste/analyse")
    job = client.get(f"{BASE}/taste").json()
    assert job["running"] is False and job["error"]


def test_an_unknown_campaign_is_404(client, fake) -> None:
    assert client.get("/campaign/profiles/finns_inte/taste").status_code == 404
    assert client.post("/campaign/profiles/finns_inte/viral/collect").status_code == 404
    assert fake.calls == []


def test_the_writing_routes_need_the_api_key(client, fake, monkeypatch) -> None:
    monkeypatch.setenv("CLIPSMITH_API_KEY", "nyckel")
    assert client.post(f"{BASE}/viral/collect").status_code == 401
    assert client.post(f"{BASE}/viral/link", json={"url": "https://x.com/1"}).status_code == 401
    assert client.post(f"{BASE}/taste/analyse").status_code == 401
    assert client.get(f"{BASE}/taste").status_code == 200
    assert fake.calls == []


# ---------- terminalen ----------


@pytest.fixture
def run(settings, fake, monkeypatch):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    return cli.main


def test_cli_collect(run, fake, capsys) -> None:
    assert run(["viral", "collect", "skiimasktimmy"]) == 0
    assert fake.calls == [("collect", ("skiimasktimmy",), {})]
    out = capsys.readouterr().out
    assert "Insane clutch" in out and "90,000" in out and "123" in out


def test_cli_link(run, fake, capsys) -> None:
    assert run(["viral", "link", "skiimasktimmy", "https://x.com/1", "--views", "5000"]) == 0
    assert fake.calls == [("add_link", ("skiimasktimmy", "https://x.com/1"), {"views": 5000})]
    assert "Insane clutch" in capsys.readouterr().out


def test_cli_analyse(run, fake, capsys) -> None:
    assert run(["viral", "analyse", "skiimasktimmy", "--top", "3"]) == 0
    assert fake.calls == [("analyse", ("skiimasktimmy",), {"top_n": 3, "brain": True})]
    out = capsys.readouterr().out
    assert "Open on the payoff" in out and "Viral clips open on the payoff." in out


def test_cli_skills(run, fake, capsys) -> None:
    assert run(["viral", "skills", "skiimasktimmy"]) == 0
    assert fake.calls == [("write_skills", ("skiimasktimmy",), {})]
    assert "hook.md" in capsys.readouterr().out


def test_cli_errors_are_printed_and_give_1(run, fake, capsys) -> None:
    fake.error = RuntimeError("TikTok did not answer.")
    assert run(["viral", "collect", "skiimasktimmy"]) == 1
    assert "TikTok did not answer." in capsys.readouterr().out
