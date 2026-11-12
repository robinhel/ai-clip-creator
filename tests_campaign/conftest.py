"""Gemensamma hjälpmedel för testerna. Inga riktiga AI-anrop görs."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from campaign.budget import Budget
from campaign.config import Settings

PAKET = Path(__file__).resolve().parent.parent
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg saknas")


class FakeMessages:
    def __init__(self, answer: dict):
        self.answer = answer
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=1000, output_tokens=50),
            content=[SimpleNamespace(type="tool_use", input=self.answer)],
        )


class FakeClient:
    """Låtsas-Claude som alltid svarar samma sak."""

    def __init__(self, answer: dict):
        self.messages = FakeMessages(answer)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """En tom projektmapp med riktiga priser och profiler."""
    shutil.copy(PAKET / "priser.yaml", tmp_path / "priser.yaml")
    shutil.copytree(PAKET / "profiles", tmp_path / "profiles")
    return Settings(root=tmp_path, anthropic_api_key="test-nyckel")


@pytest.fixture
def budget(settings: Settings) -> Budget:
    return Budget(settings.data_dir / "budget.db", settings.prices_path, 200.0, 10.0)


def make_clip(path: Path, size: str = "1080x1920", seconds: int = 9) -> Path:
    """Skapa ett litet testklipp med bild och ljud."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
         "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True,
    )
    return path


def make_overlay(path: Path) -> Path:
    """Skapa en halvgenomskinlig overlay-bild i full storlek."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=purple@0.4:s=1080x1920,format=rgba", "-frames:v", "1", str(path)],
        check=True,
    )
    return path


@pytest.fixture
def fake_twitch(monkeypatch):
    """Låtsas-Twitch för campaign.vodinfo. Ändra `videos` i testet. `calls` räknar anropen."""
    from campaign import vodinfo

    state = SimpleNamespace(calls=0, error=None, videos={
        "2925991945": {"title": "3x Playtest Key GIVEAWAY", "lengthSeconds": 13950,
                       "createdAt": "2026-11-11T18:01:44Z", "game": {"name": "Bodycam"},
                       "owner": {"login": "skiimasktimmy", "displayName": "SkiiMaskTimmy"}},
    })

    def get_video(vod_id):
        state.calls += 1
        if state.error:
            raise state.error
        return state.videos.get(vod_id)

    def gql_query(query):
        vod_id = query.split('"')[1]
        video = state.videos.get(vod_id)
        return {"data": {"video": video and {
            "previewThumbnailURL": f"https://static-cdn.jtvnw.net/{vod_id}-640x360.jpg",
            "owner": video["owner"]}}}

    monkeypatch.setattr(vodinfo, "_cache", {})
    monkeypatch.setattr(vodinfo.twitch, "get_video", get_video)
    monkeypatch.setattr(vodinfo.twitch, "gql_query", gql_query)
    return state
