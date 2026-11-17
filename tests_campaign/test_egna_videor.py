"""Egna videor: en uppladdad video görs om till 9:16 med spegling, filter och hastighet och blir ett klipp
som Robin godkänner innan det hamnar i Att posta."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api, finish
from campaign.cliplog import ClipLog
from campaign.media import probe

from .conftest import make_clip, make_overlay, needs_ffmpeg, set_profile


@pytest.fixture
def client(settings, monkeypatch):
    monkeypatch.setitem(finish.ENCODER_ARGS, "libx264", ["-c:v", "libx264", "-preset", "ultrafast"])
    monkeypatch.delenv("CLIPSMITH_API_KEY", raising=False)
    set_profile(settings, clip={"min_seconds": 3, "max_seconds": 60}, active=True)
    make_overlay(settings.root / "overlays" / "skiimasktimmy.png")
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    return TestClient(app)


def _upload(client, path, **form):
    with path.open("rb") as file:
        return client.post("/campaign/uploads", files={"file": (path.name, file, "video/mp4")},
                           data={"profile": "skiimasktimmy", **form})


@needs_ffmpeg
def test_a_landscape_video_becomes_a_vertical_clip_to_review(client, settings, tmp_path) -> None:
    source = make_clip(tmp_path / "Min video.mov", size="1920x1080", seconds=6)
    answer = _upload(client, source, title="Galet skott", mirror="true", speed="1.2", filter="warm")
    assert answer.status_code == 201, answer.text
    clip = answer.json()
    assert (clip["vod_id"], clip["title"], clip["status"]) == ("upload", "Galet skott", "renderad")
    assert "Galet skott" in clip["caption"]  # kampanjens mall
    stored = ClipLog(settings.data_dir / "klipp.db").get(clip["id"])
    info = probe(stored["file"])
    assert (info["width"], info["height"]) == (1080, 1920)
    assert info["duration"] == pytest.approx(6 / 1.2, abs=0.15)
    assert stored["file"].startswith(str(settings.out_dir / "upload" / "klara"))
    assert client.get(f"/campaign/clips/{clip['id']}/file").status_code == 200


@needs_ffmpeg
def test_two_uploads_with_the_same_name_get_their_own_files(client, tmp_path) -> None:
    source = make_clip(tmp_path / "a.mp4", seconds=4)
    first, second = _upload(client, source).json(), _upload(client, source).json()
    assert first["filename"] != second["filename"]
    assert first["title"] == "a"  # utan rubrik: filens namn


@needs_ffmpeg
@pytest.mark.parametrize(("form", "text"), [
    ({"profile": "finns-inte"}, "Kampanjen"),
    ({"speed": "1.33"}, "Hastigheten"),
    ({"filter": "nope"}, "Filtret"),
])
def test_bad_choices_are_refused_and_nothing_is_saved(client, settings, tmp_path, form, text) -> None:
    answer = _upload(client, make_clip(tmp_path / "a.mp4", seconds=4), **form)
    assert answer.status_code == 400 and text in answer.json()["detail"], answer.text
    assert ClipLog(settings.data_dir / "klipp.db").list() == []
    assert not list((settings.out_dir / "upload").rglob("*.*"))


@needs_ffmpeg
def test_a_file_that_is_not_a_video_is_refused(client, settings, tmp_path) -> None:
    bad = tmp_path / "x.mp4"
    bad.write_bytes(b"inte en video")
    answer = _upload(client, bad)
    assert answer.status_code == 400 and "video" in answer.json()["detail"]
    assert ClipLog(settings.data_dir / "klipp.db").list() == []
    assert not list((settings.out_dir / "upload").rglob("*.*"))


@needs_ffmpeg
@pytest.mark.parametrize("name", ["x.exe", "x.txt", "../x.mp4.sh"])
def test_only_video_file_types_are_taken(client, tmp_path, name) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"x")
    answer = client.post("/campaign/uploads", files={"file": (name, path.read_bytes(), "video/mp4")},
                         data={"profile": "skiimasktimmy"})
    assert answer.status_code == 400 and "Filtypen" in answer.json()["detail"]
