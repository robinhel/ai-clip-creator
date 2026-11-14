"""Instruktion per kampanj och bildrutor till Claude, plus kandidater från bildbyten.
Påhittade Claude. Inga riktiga anrop."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from clipsmith.candidates.builder import build_candidates
from clipsmith.config.loaders import load_config
from clipsmith.llm import get_provider
from clipsmith.models.candidates import CandidateMoment
from clipsmith.models.chat import ChatLog
from clipsmith.settings import CandidatesConfig

from .conftest import PAKET, make_clip, needs_ffmpeg

# sha256 av SYSTEM_PROMPT före kampanjinstruktionerna (13 nov 2026). Timmys körningar ska få exakt den.
TODAYS_PROMPT_SHA = "789a6eae86b39a977caf34939c30ff73935af8b6e2ebe6f2bb4a69fb2547a97b"
KEY = SimpleNamespace(anthropic_api_key="test")


def _fake_claude(provider) -> list[dict]:
    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        answer = {"include": True, "start_offset_s": 20.0, "end_offset_s": 40.0, "title_es": "Stormwind at dawn",
                  "reason": "Strong opening shot.", "score": 60, "format": "world_reveal"}
        usage = SimpleNamespace(input_tokens=1500, output_tokens=60, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(answer))], usage=usage)

    provider._client = SimpleNamespace(messages=SimpleNamespace(create=create))
    return calls


def _candidate(t: float) -> CandidateMoment:
    return CandidateMoment(t_center=t, score=30.0, sources=["scene_change"], reasons=["cut"])


def test_without_own_instructions_claude_gets_exactly_todays_prompt_and_no_frames(settings, monkeypatch) -> None:
    monkeypatch.setattr("campaign.config.load_settings", lambda: settings)
    provider = get_provider(load_config(PAKET / "config.timmy.yaml"), KEY, video=Path("finns-inte.mp4"))
    calls = _fake_claude(provider)
    provider.pick("[+0.0s] no way", _candidate(100.0), "ctx")
    system = calls[0]["system"][0]["text"]
    assert hashlib.sha256(system.encode()).hexdigest() == TODAYS_PROMPT_SHA
    assert [block["type"] for block in calls[0]["messages"][0]["content"]] == ["text", "text"]


@needs_ffmpeg
def test_blizzard_claude_gets_the_campaigns_instructions_and_four_small_frames(settings, monkeypatch,
                                                                             tmp_path) -> None:
    import cv2
    import numpy as np

    mp4 = make_clip(tmp_path / "trailer.mp4", size="1920x1080", seconds=60)
    monkeypatch.setattr("campaign.config.load_settings", lambda: settings)
    provider = get_provider(load_config(PAKET / "config.blizzard_nordics.yaml"), KEY, video=mp4)
    calls = _fake_claude(provider)
    pick = provider.pick("(no transcript available for this window)", _candidate(30.0), "ctx")

    assert pick is not None and pick.format == "world_reveal"
    system = calls[0]["system"][0]["text"]
    assert "World of Warcraft: Forever" in system
    assert "Twitch" not in system
    assert '"include"' in system  # svarets JSON-schema finns kvar
    content = calls[0]["messages"][0]["content"]
    images = [block for block in content if block["type"] == "image"]
    assert len(images) == 4
    for block in images:
        data = base64.b64decode(block["source"]["data"])
        assert block["source"]["media_type"] == "image/jpeg"
        assert cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE).shape[1] == 512
    assert any("t=" in block.get("text", "") and "rame" in block.get("text", "") for block in content)
    # Budgeten räknar anropet med alla tokens som Anthropic rapporterar, bilderna inräknade.
    assert provider._budget.summary()["by_purpose"]["valja"]["calls"] == 1


@needs_ffmpeg
def test_scene_changes_give_candidates_in_a_video_without_speech_or_loud_sound(tmp_path) -> None:
    mp4 = tmp_path / "cuts.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=red:s=320x240:r=25:d=4",
         "-f", "lavfi", "-i", "testsrc=s=320x240:r=25:d=4",
         "-f", "lavfi", "-i", "color=c=blue:s=320x240:r=25:d=4",
         "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0", "-pix_fmt", "yuv420p", str(mp4)],
        check=True,
    )
    chat = ChatLog(video_id="cuts", messages=[])
    on = CandidatesConfig(scene_enabled=True, scene_max=1, audio_energy_enabled=False, dedupe_window_s=1)
    found = build_candidates(chat, [], on, mp4_path=mp4)
    assert len(found) == 1  # taket gäller: två bildbyten, en kandidat
    assert found[0].sources == ["scene_change"]
    assert min(abs(found[0].t_center - 4), abs(found[0].t_center - 8)) < 0.5

    assert build_candidates(chat, [], CandidatesConfig(audio_energy_enabled=False), mp4_path=mp4) == []
    assert load_config(PAKET / "config.blizzard_nordics.yaml").candidates.scene_enabled
    assert not load_config(PAKET / "config.timmy.yaml").candidates.scene_enabled


@needs_ffmpeg
def test_a_moment_near_the_end_still_goes_to_claude_with_the_frames_that_exist(settings, monkeypatch,
                                                                              tmp_path) -> None:
    mp4 = make_clip(tmp_path / "trailer.mp4", size="1920x1080", seconds=60)
    monkeypatch.setattr("campaign.config.load_settings", lambda: settings)
    provider = get_provider(load_config(PAKET / "config.blizzard_nordics.yaml"), KEY, video=mp4)
    calls = _fake_claude(provider)
    # Fönstret är 45 s runt 58 s: två av fyra rutor ligger efter slutet.
    assert provider.pick("(no transcript available for this window)", _candidate(58.0), "ctx") is not None
    images = [block for block in calls[0]["messages"][0]["content"] if block["type"] == "image"]
    assert len(images) == 2
