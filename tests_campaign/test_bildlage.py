"""Bildläget zoom: en inzoomad 16:9-bild på svart botten, rubriken ovanför och undertexten under.
Och reframe_clip, som gör om ett befintligt klipp i kampanjens bildläge utan att tappa rubrik och ord."""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from campaign import editor, finish
from campaign.cliplog import ClipLog
from campaign.finish import HOOK_FONT_SIZE, build_filter
from campaign.media import probe
from campaign.profile import load_profile
from campaign.subtitles import load_words, save_words

from clipsmith.rendering.clipper import ZOOM_TOP, _build_ffmpeg_cmd
from clipsmith.settings import CaptionConfig, ReframeConfig

from .conftest import PAKET, make_clip, needs_ffmpeg, needs_subtitles

LONG = "This boss fight went completely sideways for everyone"


@pytest.fixture
def blizzard(settings):
    shutil.copy(PAKET / "profiles" / "blizzard_nordics.yaml", settings.profiles_dir)
    config = (PAKET / "config.blizzard_nordics.yaml").read_text(encoding="utf-8")
    (settings.root / "config.blizzard_nordics.yaml").write_text(
        re.sub(r"(?m)^  mode: \w+", "  mode: zoom", config), encoding="utf-8")
    return load_profile("blizzard_nordics", settings.profiles_dir)


def _hook_rows(graph: str) -> list[int]:
    return [int(y) for y in re.findall(r"drawtext=[^\[]*?:y=(\d+):", graph)]


def test_the_title_stands_on_the_black_just_above_the_zoomed_picture(blizzard) -> None:
    rows = _hook_rows(build_filter(blizzard, has_overlay=False, has_audio=True, font=None, title=LONG, frame="zoom"))
    assert len(rows) >= 2
    assert rows[-1] + HOOK_FONT_SIZE <= ZOOM_TOP  # the last line ends above the picture
    assert ZOOM_TOP - (rows[-1] + HOOK_FONT_SIZE) <= 40  # and close to it, not up in the corner
    assert rows[0] >= int(1920 * blizzard.safe_top)  # never in the top safe zone


def test_other_picture_modes_keep_the_title_at_the_top(blizzard) -> None:
    for frame in ("", "fit"):
        rows = _hook_rows(build_filter(blizzard, has_overlay=False, has_audio=True, font=None, title=LONG, frame=frame))
        assert rows[0] == int(1920 * blizzard.safe_top)


def _black_top(path) -> float:
    """Medelvärdet av de översta 100 raderna i en bildruta vid 1 s (0 = svart)."""
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", str(path), "-frames:v", "1",
                          "-vf", "crop=1080:100:0:0,format=gray", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    return sum(out) / len(out)


@needs_ffmpeg
@needs_subtitles
def test_reframe_clip_makes_an_old_clip_again_in_the_new_mode_and_keeps_title_and_words(
        settings, blizzard, monkeypatch) -> None:
    monkeypatch.setitem(finish.ENCODER_ARGS, "libx264", ["-c:v", "libx264", "-preset", "ultrafast"])
    source = make_clip(settings.work_dir / "vid" / "vid.mp4", size="1920x1080", seconds=14)
    raw = settings.out_dir / "vid" / "run_1" / "clip_01_old.mp4"
    raw.parent.mkdir(parents=True)
    subprocess.run(_build_ffmpeg_cmd(source, 2.0, 14.0, None, ReframeConfig(mode="fit"), raw),
                   check=True, capture_output=True)
    words = [{"start": 0.5, "end": 1.0, "word": "Hello"}, {"start": 1.2, "end": 1.8, "word": "Azeroth"}]
    save_words(raw, words, CaptionConfig(enabled=True), ReframeConfig(mode="fit"), raw)
    assert _black_top(raw) > 20  # fit: a blurred copy of the picture, not black
    log = ClipLog(settings.data_dir / "klipp.db")
    clip_id = log.add(vod_id="vid", profile="blizzard_nordics", file=str(raw.parent / "klara" / raw.name),
                      title="Meet the Skyborne", caption="old", start_s=2.0, end_s=14.0, status="renderad")

    editor.reframe_clip(log.get(clip_id), blizzard, settings, log)

    info = probe(raw)
    assert (info["width"], info["height"]) == (1080, 1920)
    assert info["duration"] == pytest.approx(12.0, abs=0.1)
    assert _black_top(raw) < 5  # zoom: black above the picture
    data = load_words(raw, editor.source_path(raw))
    assert data is not None and data["words"] == words and data["reframe"]["mode"] == "zoom"
    clip = log.get(clip_id)
    assert clip["title"] == "Meet the Skyborne"
    assert (clip["start_s"], clip["end_s"]) == (2.0, 14.0)
    done = raw.parent / "klara" / raw.name
    assert done.exists() and probe(done)["height"] == 1920


@needs_ffmpeg
def test_reframe_clip_refuses_without_the_source_video(settings, blizzard) -> None:
    raw = make_clip(settings.out_dir / "gone" / "run_1" / "clip_01_x.mp4", seconds=12)
    log = ClipLog(settings.data_dir / "klipp.db")
    clip_id = log.add(vod_id="gone", profile="blizzard_nordics", file=str(raw.parent / "klara" / raw.name),
                      title="T", caption="c", start_s=0.0, end_s=12.0)
    before = raw.read_bytes()
    with pytest.raises(ValueError, match="Källvideon saknas"):
        editor.reframe_clip(log.get(clip_id), blizzard, settings, log)
    assert raw.read_bytes() == before
