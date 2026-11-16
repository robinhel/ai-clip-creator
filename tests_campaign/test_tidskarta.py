"""Tidskartan i finish: tystnad och utfyllnadsord tas bort, och hooken visas först. Orden följer med."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from campaign import finish, timemap
from campaign.media import probe
from campaign.profile import Profile, load_profile

from .conftest import needs_ffmpeg


def _w(start: float, end: float, word: str) -> dict:
    return {"start": start, "end": end, "word": word}


def _plan(duration=10.0, fps=100.0, words=(), silences=(), hook=None, min_s=3.0, max_s=60.0, cut_fillers=True):
    return timemap.plan(duration, fps, list(words), list(silences), hook, min_s, max_s, cut_fillers=cut_fillers)


# ---------- 6: tystnad och utfyllnadsord ----------


def test_a_silence_in_the_middle_moves_only_the_words_after_it() -> None:
    words = [_w(1.0, 1.5, "before"), _w(7.0, 7.5, "after")]
    plan = _plan(words=words, silences=[(4.0, 6.0)])
    assert plan["pieces"] == [(0.0, 4.15), (5.85, 10.0)]  # 0,15 s kvar på varje sida
    assert plan["silence_cut_s"] == pytest.approx(1.7)
    moved = timemap.move_words(words, plan)
    assert moved == [_w(1.0, 1.5, "before"), _w(5.3, 5.8, "after")]


def test_a_filler_word_leaves_the_time_and_the_subtitles_but_her_and_error_stay() -> None:
    words = [_w(1.0, 1.5, "so"), _w(2.0, 2.4, "Uhm,"), _w(3.0, 3.3, "her"), _w(4.0, 4.4, "error"),
             _w(5.0, 6.5, "um")]  # ett "um" på 1,5 s är inget utfyllnadsord
    plan = _plan(words=words)
    assert plan["pieces"] == [(0.0, 2.0), (2.4, 10.0)]
    assert plan["fillers_cut"] == 1
    assert timemap.move_words(words, plan) == [
        _w(1.0, 1.5, "so"), _w(2.6, 2.9, "her"), _w(3.6, 4.0, "error"), _w(4.6, 6.1, "um")]


def test_fillers_stay_when_the_campaign_turns_it_off() -> None:
    assert _plan(words=[_w(2.0, 2.4, "uh")], cut_fillers=False) is None


def test_the_cut_points_are_whole_frames() -> None:
    plan = _plan(fps=30.0, silences=[(4.0, 6.0)])
    for start, end in plan["pieces"]:
        for t in (start, end):
            assert abs(t * 30 - round(t * 30)) < 1e-4, t
    assert plan["pieces"] != [(0.0, 4.15), (5.85, 10.0)]


def test_short_pieces_go_and_close_pieces_are_joined() -> None:
    words = [_w(2.0, 2.4, "uh"), _w(2.45, 2.8, "um"), _w(5.0, 5.05, "eh")]
    plan = _plan(words=words)
    # 2,4–2,45 är för kort och tas bort; luckan 5,0–5,05 är för liten, så "eh" blir kvar
    assert plan["pieces"] == [(0.0, 2.0), (2.8, 10.0)]
    assert plan["fillers_cut"] == 2
    assert timemap.move_words(words, plan) == [_w(4.2, 4.25, "eh")]


def test_nothing_is_cut_when_the_clip_would_be_shorter_than_the_campaign_allows() -> None:
    assert _plan(silences=[(2.0, 6.0)], min_s=9.0) is None
    assert _plan(silences=[(2.0, 6.0)], min_s=6.0) is not None


def test_a_clip_without_silence_fillers_or_hook_has_no_plan() -> None:
    assert _plan(words=[_w(1.0, 1.5, "hello")]) is None


def test_a_trimmed_clip_moves_the_words_by_in_s_and_the_silence(tmp_path, monkeypatch) -> None:
    """Orden har originalets tider. Källan börjar vid in_s = 2."""
    seen = {}
    monkeypatch.setattr(timemap, "probe", lambda path: {"duration": 10.0, "fps": 100.0, "has_audio": True})
    monkeypatch.setattr(timemap, "silences", lambda path: [(4.0, 6.0)])
    monkeypatch.setattr(timemap, "write_ass", lambda data, a, b, out: seen.update(words=data["words"], span=(a, b)))
    data = {"words": [_w(3.0, 3.5, "before"), _w(9.0, 9.5, "after")], "caption": {}, "reframe": {}}
    plan = timemap.prepare(tmp_path / "x.mp4", data, 2.0, 12.0, Profile(name="x", streamer="y"), tmp_path / "a.ass")
    assert plan["pieces"] == [(0.0, 4.15), (5.85, 10.0)]
    assert seen["words"] == [_w(1.0, 1.5, "before"), _w(5.3, 5.8, "after")]
    assert seen["span"] == (0.0, pytest.approx(8.3))


def test_without_a_plan_the_subtitles_are_written_as_today(tmp_path, monkeypatch) -> None:
    seen = {}
    monkeypatch.setattr(timemap, "probe", lambda path: {"duration": 6.0, "fps": 30.0, "has_audio": True})
    monkeypatch.setattr(timemap, "silences", lambda path: [])
    monkeypatch.setattr(timemap, "write_ass", lambda data, a, b, out: seen.update(words=data["words"], span=(a, b)))
    data = {"words": [_w(3.0, 3.5, "hello")], "caption": {}, "reframe": {}}
    assert timemap.prepare(tmp_path / "x.mp4", data, 2.0, 8.0, Profile(name="x", streamer="y"), tmp_path / "a") \
        is None
    assert seen == {"words": data["words"], "span": (2.0, 8.0)}


def test_the_campaign_can_turn_off_the_silence_cut(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(timemap, "probe", lambda path: {"duration": 10.0, "fps": 30.0, "has_audio": True})
    monkeypatch.setattr(timemap, "silences", lambda path: pytest.fail("ingen tystnadssökning"))
    monkeypatch.setattr(timemap, "write_ass", lambda *a: None)
    profile = Profile(name="x", streamer="y", cut_silence=False)
    data = {"words": [], "caption": {}, "reframe": {}}
    assert timemap.prepare(tmp_path / "x.mp4", data, 0.0, 10.0, profile, tmp_path / "a") is None


# ---------- filtret ----------


def test_the_filter_joins_the_pieces_before_subtitles_overlay_and_sound() -> None:
    profile = Profile(name="x", streamer="y", hook_text=False)
    graph = finish.build_filter(profile, True, True, None, subtitles=Path("/tmp/a.ass"),
                                pieces=[(0.0, 2.0), (2.4, 5.0)])
    assert graph.startswith(
        "[0:v]trim=start=0.000000:end=2.000000,setpts=PTS-STARTPTS[v0];"
        "[0:a]atrim=start=0.000000:end=2.000000,asetpts=PTS-STARTPTS[a0];"
        "[0:v]trim=start=2.400000:end=5.000000,setpts=PTS-STARTPTS[v1];"
        "[0:a]atrim=start=2.400000:end=5.000000,asetpts=PTS-STARTPTS[a1];"
        "[v0][a0][v1][a1]concat=n=2:v=1:a=1[cv][ca];[cv]setsar=1,subtitles=filename='/tmp/a.ass'[base];")
    assert "[1:v]scale=" in graph and "[ca]loudnorm=" in graph and "[0:a]loudnorm" not in graph


def test_without_sound_only_the_picture_is_joined() -> None:
    graph = finish.build_filter(Profile(name="x", streamer="y"), False, False, None, pieces=[(0.0, 2.0), (3.0, 5.0)])
    assert "[v0][v1]concat=n=2:v=1:a=0[cv]" in graph and "atrim" not in graph


def test_without_pieces_the_filter_is_as_before() -> None:
    profile = Profile(name="x", streamer="y")
    assert finish.build_filter(profile, True, True, None) == finish.build_filter(profile, True, True, None, pieces=None)
    assert finish.build_filter(profile, True, True, None).startswith("[0:v]setsar=1[base];[1:v]scale=")


# ---------- profilen ----------


def _profile(tmp_path: Path, edit) -> Profile:
    path = tmp_path / "p.yaml"
    data = {"name": "P", "streamer": "s"}
    if edit is not None:
        data["edit"] = edit
    path.write_text(yaml.safe_dump(data))
    return load_profile(path, tmp_path)


def test_the_profile_has_all_three_on_by_default_and_each_can_be_turned_off(tmp_path) -> None:
    p = _profile(tmp_path, None)
    assert (p.cut_silence, p.cut_fillers, p.cold_open) == (True, True, True)
    p = _profile(tmp_path, {"cold_open": False})
    assert (p.cut_silence, p.cut_fillers, p.cold_open) == (True, True, False)


@pytest.mark.parametrize("edit", [{"cut_silence": "ja"}, {"cold_open": 1}, ["cut_silence"]])
def test_wrong_types_in_edit_stop_with_a_clear_error(tmp_path, edit) -> None:
    with pytest.raises(SystemExit, match="edit"):
        _profile(tmp_path, edit)


# ---------- riktig rendering ----------


def _tone_silence_tone(path: Path) -> Path:
    """3 s ton, 2 s tystnad, 3 s ton, 30 bildrutor per sekund."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30",
         "-f", "lavfi", "-i", "aevalsrc='if(between(t,3,5),0,0.5*sin(2*PI*440*t))':s=48000:d=8",
         "-t", "8", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True)
    return path


def _stream_seconds(path: Path, kind: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", kind, "-show_entries", "stream=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout
    return float(out.strip())


@needs_ffmpeg
def test_a_real_render_drops_the_silence_and_keeps_the_sound_in_step(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(finish.ENCODER_ARGS, "libx264", ["-c:v", "libx264", "-preset", "ultrafast"])
    src = _tone_silence_tone(tmp_path / "src.mp4")
    found = timemap.silences(src)
    assert len(found) == 1
    assert found[0][0] == pytest.approx(3.0, abs=0.05) and found[0][1] == pytest.approx(5.0, abs=0.05)
    info = probe(src)
    assert info["fps"] == pytest.approx(30.0)
    plan = timemap.plan(info["duration"], info["fps"], [], found, None, 3.0, 60.0)
    out = tmp_path / "out.mp4"
    manifest = finish.finish_clip(src, out, Profile(name="x", streamer="y", hook_text=False), tmp_path, plan=plan)
    expected = sum(end - start for start, end in plan["pieces"])
    assert expected == pytest.approx(8.0 - 1.7, abs=0.1)  # utan 3,15–4,85
    assert manifest["silence_cut_s"] == pytest.approx(1.7, abs=0.04)
    assert (manifest["fillers_cut"], manifest["cold_open_s"]) == (0, 0.0)
    video, audio = _stream_seconds(out, "v"), _stream_seconds(out, "a")
    assert video == pytest.approx(expected, abs=1 / 30 + 0.001), video
    assert audio == pytest.approx(video, abs=0.05), (audio, video)


@needs_ffmpeg
def test_a_clip_without_a_plan_gets_the_manifest_without_cuts(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(finish.ENCODER_ARGS, "libx264", ["-c:v", "libx264", "-preset", "ultrafast"])
    from .conftest import make_clip

    src = make_clip(tmp_path / "clip.mp4", seconds=3)
    manifest = finish.finish_clip(src, tmp_path / "out.mp4", Profile(name="x", streamer="y", hook_text=False),
                                  tmp_path)
    assert (manifest["silence_cut_s"], manifest["fillers_cut"], manifest["cold_open_s"]) == (0.0, 0, 0.0)

