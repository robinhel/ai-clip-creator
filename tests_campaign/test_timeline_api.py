"""GET /campaign/runs/{run_id}/timeline: the signals in a project, read from made-up files in a tmp folder."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api, timeline_api
from campaign.cliplog import ClipLog

VOD = "4100000001"


@pytest.fixture
def client(settings) -> TestClient:
    app = FastAPI()
    app.include_router(timeline_api.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    return TestClient(app)


def _run(settings, run_id: int = 7, duration_s: int | None = 3600) -> int:
    ClipLog(settings.data_dir / "klipp.db").add_run(run_id, VOD, "skiimasktimmy", "Game", title="T",
                                                    duration_s=duration_s)
    return run_id


def _message(t: float, clip: bool = False) -> dict:
    return {"time_in_seconds": t, "message": "!clip" if clip else "wow", "author": "viewer",
            "is_clip_command": clip, "hype_emote_count": 0}


def _files(settings, run_id: int = 7, audio: bool = True) -> None:
    work = settings.work_dir / VOD
    work.mkdir(parents=True)
    # 1 message early, 4 messages in the busiest minute near the end.
    messages = [_message(30.0)] + [_message(3500.0 + i) for i in range(4)]
    (work / "chat.json").write_text(json.dumps({"video_id": VOD, "messages": messages}), encoding="utf-8")
    candidates = [
        {"t_center": 3500.0, "score": 100.0, "sources": ["chat_density", "existing_clip"],
         "reasons": ["chat density peak (score=4.0) at t=3500.0s", "Twitch clip 'x' (10 views): 'y'"]},
        {"t_center": 900.0, "score": 40.0, "sources": ["transcript_hype"],
         "reasons": ["hype in transcript at t=899.0s: 'no way!'"]},
        {"t_center": 10.0, "score": 1.0, "sources": ["clip_command"], "reasons": ["!clip by viewer at t=10.0s"]},
    ]
    (work / "candidates.json").write_text(json.dumps(candidates), encoding="utf-8")
    if audio:
        (work / "audio_rms_v2.json").write_text(json.dumps([[1.0, -40.0], [3.0, -20.0], [5.0, -30.0]]),
                                                encoding="utf-8")
    picks = [{"candidate": candidates[0], "pick": {"include": True, "start_offset_s": 3480.0,
                                                    "end_offset_s": 3530.5, "title": "Last-second clutch"}}]
    run_dir = settings.out_dir / VOD / f"run_{run_id}"
    run_dir.mkdir(parents=True)
    (run_dir / "picks.json").write_text(json.dumps(picks), encoding="utf-8")
    # An older run's choice in work/ is not this project's.
    (work / "picks.json").write_text(json.dumps([]), encoding="utf-8")


def test_timeline_normalises_chat_density_and_gives_the_picks(client, settings) -> None:
    run_id = _run(settings)
    _files(settings, run_id)

    response = client.get(f"/campaign/runs/{run_id}/timeline")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["duration_s"] == 3600
    density = data["chat_density"]
    assert max(v for _, v in density) == 1.0
    assert min(v for _, v in density) == 0.0
    assert all(0.0 <= t <= 3600 for t, _ in density)
    busiest = max(density, key=lambda p: p[1])[0]
    assert 3400 <= busiest <= 3600  # the busy minute is near the end, not at the start
    assert data["picks"] == [{"start_s": 3480.0, "end_s": 3530.5, "title": "Last-second clutch"}]
    assert data["hype"] == [900.0]
    assert data["viewer_clips"] == [10.0, 3500.0]
    first = data["candidates"][0]
    assert first["score"] == 100.0
    assert first["sources"] == ["chat_density", "existing_clip"]
    assert first["breakdown"] == {"chat_density": 1, "existing_clip": 1}
    assert first["start_s"] < 3500.0 < first["end_s"] <= 3600
    assert [t for t, _ in data["audio_energy"]] == [1.0, 3.0, 5.0]
    assert [v for _, v in data["audio_energy"]] == [0.0, 1.0, 0.5]


def test_timeline_without_audio_gives_an_empty_list(client, settings) -> None:
    run_id = _run(settings)
    _files(settings, run_id, audio=False)
    assert client.get(f"/campaign/runs/{run_id}/timeline").json()["audio_energy"] == []


def test_timeline_uses_the_last_signal_when_the_length_is_unknown(client, settings) -> None:
    run_id = _run(settings, duration_s=None)
    _files(settings, run_id)
    assert client.get(f"/campaign/runs/{run_id}/timeline").json()["duration_s"] == 3530.5


def test_timeline_404_when_the_project_does_not_exist(client) -> None:
    response = client.get("/campaign/runs/999/timeline")
    assert response.status_code == 404
    assert response.json()["detail"] == "The project does not exist."


def test_timeline_404_with_a_clear_text_when_the_files_are_missing(client, settings) -> None:
    run_id = _run(settings)
    response = client.get(f"/campaign/runs/{run_id}/timeline")
    assert response.status_code == 404
    assert "candidates.json" in response.json()["detail"]
