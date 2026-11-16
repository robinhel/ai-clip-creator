"""Undertexten som ord i en fil bredvid råklippet (väg B): skärs utan undertext och bränns in i finish."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


from campaign import finish, subtitles
from campaign.profile import Profile
from clipsmith.models.transcript import Segment, Transcript, Word
from clipsmith.settings import CaptionConfig, ReframeConfig

from .conftest import make_clip, needs_subtitles

WORDS = [{"start": 0.5, "end": 0.8, "word": "Bro,"}, {"start": 1.0, "end": 1.4, "word": "no"},
         {"start": 1.5, "end": 2.0, "word": "way"}]


def _transcript() -> Transcript:
    return Transcript("v", "en", [
        Segment(100.0, 102.0, "Bro, no way", [Word(100.5, 100.8, "Bro,", 0.9), Word(101.0, 101.4, "no", 0.9),
                                               Word(101.5, 102.0, "way", 0.9)]),
        Segment(103.5, 104.0, "Thank you.", [Word(103.5, 103.7, "Thank", 0.2), Word(103.7, 104.0, "you.", 0.2)]),
        Segment(120.0, 121.0, "later", [Word(120.0, 121.0, "later", 0.9)]),
    ])


def _data(mode: str = "stacked", words=WORDS) -> dict:
    return {"words": words, "caption": CaptionConfig(enabled=True, style="box", font_size=72).model_dump(),
            "reframe": {"mode": mode, "split_ratio": 0.4}}


def test_pick_words_counts_from_the_clip_start_and_skips_invented_phrases() -> None:
    assert subtitles.pick_words(_transcript(), 100.0, 110.0) == WORDS


def test_saved_words_are_read_back_the_same(tmp_path) -> None:
    raw = tmp_path / "clip_01_x.mp4"
    raw.write_bytes(b"mp4")
    subtitles.save_words(raw, WORDS, CaptionConfig(enabled=True, style="box"),
                         ReframeConfig(mode="stacked", split_ratio=0.4), raw)
    assert subtitles.words_path(raw) == tmp_path / "clip_01_x.words.json"
    data = subtitles.load_words(raw, raw)
    assert data["words"] == WORDS
    assert data["reframe"] == {"mode": "stacked", "split_ratio": 0.4}
    assert data["caption"]["style"] == "box"
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")]  # ingen tmp-fil kvar


def test_a_word_file_for_another_raw_clip_is_not_used(tmp_path) -> None:
    raw = tmp_path / "clip_01_x.mp4"
    raw.write_bytes(b"mp4")
    subtitles.save_words(raw, WORDS, CaptionConfig(enabled=True), ReframeConfig(), raw)
    other = tmp_path / "other.mp4"
    other.write_bytes(b"other file")
    assert subtitles.load_words(raw, other) is None
    raw.write_bytes(b"a new raw clip from clipsmith")
    assert subtitles.load_words(raw, raw) is None
    subtitles.words_path(raw).write_text("{trasig")
    assert subtitles.load_words(raw, raw) is None
    assert subtitles.load_words(tmp_path / "saknas.mp4", raw) is None


def test_stacked_gives_the_box_on_the_seam(tmp_path) -> None:
    out = tmp_path / "a.ass"
    subtitles.write_ass(_data("stacked"), 0.0, 10.0, out)
    text = out.read_text(encoding="utf-8-sig")
    assert "Style: Box," in text
    assert ",3,12,0,2,20,20,1116,1" in text  # 1920 * 0,6 - 72 / 2: mitt på skarven
    assert "BRO," in text and "WAY" in text


def test_center_gives_the_box_above_the_bottom_safe_zone(tmp_path) -> None:
    out = tmp_path / "a.ass"
    subtitles.write_ass(_data("center"), 0.0, 10.0, out)
    assert ",3,12,0,2,20,20,384,1" in out.read_text(encoding="utf-8-sig")


def test_times_are_moved_by_in_s(tmp_path) -> None:
    out = tmp_path / "a.ass"
    subtitles.write_ass(_data(), 0.8, 10.0, out)
    text = out.read_text(encoding="utf-8-sig")
    assert "BRO" not in text  # före in_s
    assert "Dialogue: 0,0:00:00.20," in text  # \"no\" vid 1,0 s blir 0,2 s i det trimmade klippet


def test_an_invented_phrase_in_the_word_file_is_kept(tmp_path) -> None:
    """Robin har sett och rättat orden; ingen sannolikhet slänger dem."""
    out = tmp_path / "a.ass"
    subtitles.write_ass(_data(words=[{"start": 0.5, "end": 0.9, "word": "you"}]), 0.0, 5.0, out)
    assert "YOU" in out.read_text(encoding="utf-8-sig")


def test_the_filter_burns_the_subtitles_under_the_overlay() -> None:
    profile = Profile(name="x", streamer="y")
    graph = finish.build_filter(profile, True, True, None, subtitles=Path("/tmp/a b:c.ass"))
    assert graph.startswith("[0:v]setsar=1,subtitles=filename='/tmp/a b\\:c.ass'[base];")
    assert graph.index("subtitles=") < graph.index("overlay=")
    assert finish.build_filter(profile, True, True, None).startswith("[0:v]setsar=1[base];")


def _gray(path: Path, at: float) -> bytes:
    return subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path), "-frames:v", "1",
                           "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout


@needs_subtitles
def test_finish_clip_burns_the_words_into_the_picture(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(finish.ENCODER_ARGS, "libx264", ["-c:v", "libx264", "-preset", "ultrafast"])
    src = make_clip(tmp_path / "clip_01_x.mp4", seconds=3)
    ass = tmp_path / ".clip_01_x.ass"
    subtitles.write_ass(_data("center"), 0.0, 3.0, ass)
    profile = Profile(name="x", streamer="y", hook_text=False)
    with_words = finish.finish_clip(src, tmp_path / "med.mp4", profile, tmp_path, subtitles=ass)
    without = finish.finish_clip(src, tmp_path / "utan.mp4", profile, tmp_path)
    assert (with_words["subtitles_applied"], without["subtitles_applied"]) == (True, False)
    a, b = _gray(tmp_path / "med.mp4", 1.2), _gray(tmp_path / "utan.mp4", 1.2)
    width = 1080
    band = range(1920 - 384 - 120, 1920 - 384 + 20)  # raden ovanför den nedre säkra zonen
    changed = sum(abs(a[y * width + x] - b[y * width + x]) > 60 for y in band for x in range(0, width, 4))
    top = sum(abs(a[y * width + x] - b[y * width + x]) > 60 for y in range(0, 300) for x in range(0, width, 4))
    assert changed > 500 and top < 50, (changed, top)


def test_json_has_the_identity_of_the_source(tmp_path) -> None:
    raw, source = tmp_path / "clip.mp4", tmp_path / "original.mp4"
    raw.write_bytes(b"trimmed")
    source.write_bytes(b"the original")
    subtitles.save_words(raw, WORDS, CaptionConfig(enabled=True), ReframeConfig(), source)
    saved = json.loads(subtitles.words_path(raw).read_text())
    assert (saved["raw_size"], saved["raw_mtime_ns"]) == (source.stat().st_size, source.stat().st_mtime_ns)
    assert subtitles.load_words(raw, source) is not None and subtitles.load_words(raw, raw) is None
