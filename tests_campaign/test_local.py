"""python -m campaign local: en egen videofil blir ett projekt i appen, som en Twitch-VOD.
Påhittade Claude och Berget. Inga riktiga anrop."""

from __future__ import annotations

import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api, pipeline
from campaign.cli import main
from campaign.cliplog import ClipLog
from campaign.profile import load_profile
from clipsmith.models.transcript import Segment, Transcript

from .conftest import PAKET, make_clip, needs_ffmpeg

STEM = "WOW_Camelot_trlr_NoVO"


@pytest.fixture
def blizzard(settings, monkeypatch, tmp_path):
    shutil.copy(PAKET / "profiles" / "blizzard_nordics.yaml", settings.profiles_dir)
    shutil.copy(PAKET / "config.blizzard_nordics.yaml", settings.root)
    monkeypatch.setenv("KLIPP_ROOT", str(settings.root))
    monkeypatch.chdir(settings.root)  # clipsmiths config har work_dir: ./work, som appen när den körs från repot
    return settings


def _runs(settings, path: str = "/campaign/runs") -> list[dict]:
    """Projekten som webben ser. cmd_local har redan pekat clipsmiths databas på work/clipsmith.db."""
    app = FastAPI()
    app.state.active_run_id = None
    app.include_router(api.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    return TestClient(app).get(path).json()


def _file(tmp_path):
    return make_clip(tmp_path / "filer" / f"{STEM}.mp4", size="1920x1080", seconds=3)


@needs_ffmpeg
def test_a_file_that_is_already_run_becomes_a_done_project_with_its_clips(blizzard, tmp_path, monkeypatch) -> None:
    src = _file(tmp_path)
    (blizzard.work_dir / STEM).mkdir(parents=True)
    shutil.copy(src, blizzard.work_dir / STEM / f"{STEM}.mp4")
    ClipLog(blizzard.data_dir / "klipp.db").add(
        vod_id=STEM, profile="blizzard_nordics", file=str(blizzard.out_dir / STEM / "klara" / "clip_01_a.mp4"),
        title="New zones", caption="New zones @warcraft\n#ad #blizzardpartner", status="renderad",
        start_s=1.0, end_s=20.0, clip_format="world_reveal", problems=[])
    monkeypatch.setattr(pipeline, "make_clips", lambda *a, **k: pytest.fail("inget ska köras om"))

    assert main(["local", str(src), "--profile", "blizzard_nordics", "--game", "World of Warcraft: Forever",
                 "--only-register"]) == 0

    [run] = _runs(blizzard)
    assert (run["vod_id"], run["profile"], run["status"]) == (STEM, "blizzard_nordics", "done")
    assert run["title"] == "WOW Camelot trlr NoVO"
    assert run["channel_name"] == "Blizzard Entertainment" and run["thumbnail_url"] is None
    assert run["duration_s"] == 3 and run["game"] == "World of Warcraft: Forever"
    assert run["clip_counts"] == {"renderad": 1}
    # Filen hamnar i kampanjens enda projekt för egna filer.
    [project] = _runs(blizzard, "/campaign/projects")
    assert run["project_id"] == project["id"] and run["origin"] is None
    assert project["title"] == "Own files for Blizzard Entertainment"
    assert [(f["run_id"], f["vod_id"]) for f in project["files"]] == [(run["run_id"], STEM)]


@needs_ffmpeg
@pytest.mark.parametrize("fails", [False, True])
def test_a_new_file_is_copied_and_run_by_the_apps_own_pipeline(blizzard, tmp_path, monkeypatch, fails) -> None:
    src = _file(tmp_path)
    seen: dict = {}

    def make_clips(vod_id, profile, settings, **kwargs):
        seen.update(kwargs, vod_id=vod_id)
        if fails:
            raise ValueError("Claude svarade inte.")
        return pipeline.clipsmith_config(profile, settings), None

    monkeypatch.setattr(pipeline, "make_clips", make_clips)
    assert main(["local", str(src), "--profile", "blizzard_nordics"]) == (1 if fails else 0)

    assert (blizzard.work_dir / STEM / f"{STEM}.mp4").read_bytes() == src.read_bytes()
    assert '"messages": []' in (blizzard.work_dir / STEM / "chat.json").read_text(encoding="utf-8")
    assert seen["vod_id"] == STEM and seen["local"] is True and seen["run_id"] == 1
    [run] = _runs(blizzard)
    assert run["status"] == ("failed" if fails else "done")
    assert (run["error"] == "Claude svarade inte.") if fails else run["error"] is None


def test_make_clips_for_a_local_file_does_not_download_or_ask_twitch(blizzard, monkeypatch) -> None:
    mp4 = blizzard.work_dir / STEM / f"{STEM}.mp4"
    mp4.parent.mkdir(parents=True)
    mp4.touch()
    (mp4.parent / "transcript.json").write_text(Transcript(video_id=STEM, language="en", segments=[
        Segment(start=1.0, end=2.0, text="Your journey awaits.", words=[])]).to_json(), encoding="utf-8")
    monkeypatch.setattr(pipeline, "download_vod", lambda *a, **k: pytest.fail("ingen nedladdning"))
    monkeypatch.setattr(pipeline, "_twitch_ids", lambda *a: pytest.fail("ingen Twitch"))
    seen: dict = {}

    def process_vod(video, cfg, secrets, **kwargs):
        seen["video"] = video
        (mp4.parent / "candidates.json").write_text("[]", encoding="utf-8")

    monkeypatch.setattr(pipeline, "process_vod", process_vod)
    profile = load_profile("blizzard_nordics", blizzard.profiles_dir)
    _, why = pipeline.make_clips(STEM, profile, blizzard, game=None, emit=lambda *e: None, local=True)
    assert why == "No candidates were found."
    assert seen["video"].url == "" and seen["video"].user_id == ""
