"""Choosing the webcam rect per clip when the streamer moves the camera between scenes.

The Haar detector is replaced by a fake that counts a white square as a face."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import pytest

from clipsmith.llm.base import ClipPick
from clipsmith.models.candidates import CandidateMoment
from clipsmith.rendering import clipper, detect
from clipsmith.selection.selector import PickResult
from clipsmith.settings import AppConfig, ReframeConfig

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")

LEFT = [10, 120, 60, 40]
RIGHT = [250, 120, 60, 40]


def _fake_count(crop) -> int:  # crop: a numpy array
    return int(crop.mean() > 128)


def _video(path: Path, left_until: float = 2.0, seconds: int = 4, square: bool = True) -> Path:
    """320x180, black. A white square sits in LEFT until `left_until`, then in RIGHT."""
    boxes = ""
    if square:
        boxes = (
            f",drawbox=x=15:y=125:w=50:h=30:color=white:t=fill:enable='lt(t,{left_until})'"
            f",drawbox=x=255:y=125:w=50:h=30:color=white:t=fill:enable='gte(t,{left_until})'"
        )
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=black:s=320x180:r=25:d={seconds}",
         "-vf", f"format=yuv420p{boxes}", "-c:v", "libx264", "-preset", "ultrafast", str(path)],
        check=True,
    )
    return path


@pytest.fixture
def fake_detector(monkeypatch) -> None:
    monkeypatch.setattr(detect, "_count_faces", _fake_count)


@needs_ffmpeg
def test_each_clip_gets_the_rect_where_the_faces_are(tmp_path, fake_detector) -> None:
    mp4 = _video(tmp_path / "vod.mp4")
    assert detect.choose_webcam_rect(mp4, 0.0, 2.0, [RIGHT, LEFT]) == (LEFT, [0, 5])
    assert detect.choose_webcam_rect(mp4, 2.0, 4.0, [RIGHT, LEFT]) == (RIGHT, [5, 0])


@needs_ffmpeg
def test_no_faces_anywhere_gives_the_default_rect_and_a_warning(tmp_path, fake_detector, caplog) -> None:
    mp4 = _video(tmp_path / "vod.mp4", square=False)
    with caplog.at_level(logging.WARNING):
        assert detect.choose_webcam_rect(mp4, 0.0, 4.0, [RIGHT, LEFT]) == (RIGHT, [0, 0])
    assert "no face" in caplog.text


def _pick(start: float, end: float) -> PickResult:
    return PickResult(
        candidate=CandidateMoment(t_center=start, score=1.0, sources=["chat"], reasons=[]),
        pick=ClipPick(include=True, start_offset_s=start, end_offset_s=end, title_es="x", reason="y"),
    )


@needs_ffmpeg
def test_cut_all_clips_uses_and_remembers_the_chosen_rect(tmp_path, fake_detector, monkeypatch) -> None:
    mp4 = _video(tmp_path / "vod.mp4")
    used: list[list[int] | None] = []
    monkeypatch.setattr(clipper, "_cut_one", lambda mp4, tr, pr, i, out, cfg: used.append(cfg.reframe.webcam_rect) or out)
    cfg = AppConfig(reframe=ReframeConfig(mode="stacked", webcam_rect=RIGHT, webcam_rects=[RIGHT, LEFT]))
    picks = [_pick(0.0, 2.0), _pick(2.0, 4.0)]
    clipper.cut_all_clips(mp4, None, picks, tmp_path / "out", cfg)  # type: ignore[arg-type]
    assert used == [LEFT, RIGHT]
    assert [p.webcam_rect for p in picks] == [LEFT, RIGHT]
    assert picks[0].to_dict()["pick"]["webcam_rect"] == LEFT
    assert cfg.reframe.webcam_rect == RIGHT  # the shared config is not changed


def test_one_rect_works_exactly_as_before(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(detect, "choose_webcam_rect", lambda *a, **k: pytest.fail("no choice with one rect"))
    used: list[list[int] | None] = []
    monkeypatch.setattr(clipper, "_cut_one", lambda mp4, tr, pr, i, out, cfg: used.append(cfg.reframe.webcam_rect) or out)
    for reframe in (ReframeConfig(mode="stacked", webcam_rect=RIGHT),
                    ReframeConfig(mode="stacked", webcam_rect=RIGHT, webcam_rects=[RIGHT])):
        picks = [_pick(0.0, 2.0)]
        clipper.cut_all_clips(tmp_path / "vod.mp4", None, picks, tmp_path / "out", AppConfig(reframe=reframe))  # type: ignore[arg-type]
        assert picks[0].webcam_rect is None
        assert "webcam_rect" not in picks[0].to_dict()["pick"]
    assert used == [RIGHT, RIGHT]
