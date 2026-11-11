"""Tester som kör ffmpeg på små låtsasklipp."""

from __future__ import annotations

import json

from campaign.brain import Brain
from campaign.cli import main
from campaign.cliplog import ClipLog
from campaign.finish import finish_clip
from campaign.media import grab_frames, probe
from campaign.profile import load_profile
from campaign.workflow import finish_vod

from .conftest import FakeClient, make_clip, make_overlay, needs_ffmpeg

GOOD_REVIEW = {"facecam_visible": True, "text_visible": True, "overlay_visible": True, "problems": []}


def _picks(path, start=100.0, end=109.0, title="Too close call"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([
        {"candidate": {"t_center": 104.0, "score": 30.0, "sources": ["audio_energy"], "reasons": []},
         "pick": {"include": True, "start_offset_s": start, "end_offset_s": end,
                  "title_es": title, "reason": "test"}}
    ]), encoding="utf-8")


@needs_ffmpeg
def test_finish_clip_adds_overlay_and_text(settings):
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    make_overlay(settings.root / "overlays" / "skiimasktimmy.png")
    src = make_clip(settings.root / "src.mp4")
    out = settings.root / "klar.mp4"

    manifest = finish_clip(src, out, profile, settings.root)

    info = probe(out)
    assert (info["width"], info["height"]) == (1080, 1920)
    assert info["has_audio"]
    assert abs(info["duration"] - 9.0) < 0.5
    assert manifest["overlay_applied"] and manifest["text_applied"]


@needs_ffmpeg
def test_finish_clip_works_without_overlay_file(settings):
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    src = make_clip(settings.root / "src.mp4")
    manifest = finish_clip(src, settings.root / "klar.mp4", profile, settings.root)
    assert manifest["overlay_applied"] is False


@needs_ffmpeg
def test_grab_frames_gives_four_images(settings):
    src = make_clip(settings.root / "src.mp4")
    frames = grab_frames(src, settings.root / "rutor")
    assert len(frames) == 4
    assert all(f.exists() and f.stat().st_size > 0 for f in frames)


@needs_ffmpeg
def test_finish_vod_makes_campaign_ready_clip(settings, budget):
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    make_overlay(settings.root / "overlays" / "skiimasktimmy.png")
    make_clip(settings.out_dir / "v1" / "clip_01_too_close_call.mp4")
    _picks(settings.work_dir / "v1" / "picks.json")
    log = ClipLog(settings.data_dir / "klipp.db")
    client = FakeClient(GOOD_REVIEW)
    brain = Brain(settings, budget, client=client)

    results = finish_vod("v1", profile, settings, log, brain=brain, game="Arc Raiders")

    assert len(results) == 1 and results[0]["problems"] == []
    clip = log.get(results[0]["id"])
    assert clip["status"] == "renderad"
    assert clip["title"] == "Too close call"
    assert (clip["start_s"], clip["end_s"]) == (100.0, 109.0)
    assert clip["signals"] == ["audio_energy"]
    assert "#arcraiders" in clip["caption"]
    out = settings.out_dir / "v1" / "klara" / "clip_01_too_close_call.mp4"
    assert out.exists() and out.with_suffix(".txt").read_text(encoding="utf-8").strip() == clip["caption"]
    # Granskningen skickade fyra bilder och loggades i budgeten.
    sent = client.messages.calls[0]["messages"][0]["content"]
    assert sum(1 for part in sent if part["type"] == "image") == 4
    assert budget.summary()["by_purpose"]["granska"]["calls"] == 1


@needs_ffmpeg
def test_finish_vod_skips_clips_already_in_log(settings, budget):
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    make_overlay(settings.root / "overlays" / "skiimasktimmy.png")
    make_clip(settings.out_dir / "v1" / "clip_01_too_close_call.mp4")
    _picks(settings.work_dir / "v1" / "picks.json")
    log = ClipLog(settings.data_dir / "klipp.db")

    finish_vod("v1", profile, settings, log, review=False)
    again = finish_vod("v1", profile, settings, log, review=False)

    assert "skipped" in again[0]
    assert len(log.list()) == 1


@needs_ffmpeg
def test_finish_vod_fails_clip_without_overlay(settings):
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    make_clip(settings.out_dir / "v1" / "clip_01_test.mp4")
    log = ClipLog(settings.data_dir / "klipp.db")

    results = finish_vod("v1", profile, settings, log, review=False)

    assert any("Overlayen saknas" in p for p in results[0]["problems"])
    assert log.get(results[0]["id"])["status"] == "underkand"


@needs_ffmpeg
def test_finish_vod_fails_clip_that_is_not_vertical(settings):
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    make_clip(settings.out_dir / "v1" / "clip_01_wide.mp4", size="1280x720")
    log = ClipLog(settings.data_dir / "klipp.db")

    results = finish_vod("v1", profile, settings, log, review=False)

    assert any("1080×1920" in p for p in results[0]["problems"])
    assert log.get(results[0]["id"])["status"] == "underkand"


@needs_ffmpeg
def test_cli_finish_status_and_facit(settings, monkeypatch, capsys):
    monkeypatch.setenv("KLIPP_ROOT", str(settings.root))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("KLIPP_PROFILE", raising=False)
    make_overlay(settings.root / "overlays" / "skiimasktimmy.png")
    make_clip(settings.out_dir / "v1" / "clip_01_too_close_call.mp4")
    _picks(settings.work_dir / "v1" / "picks.json")
    (settings.root / "facit.yaml").write_text(
        'vod: "v1"\nmoments:\n  - {time: "0:01:45", note: "träff"}\n  - {time: "0:10:00", note: "miss"}\n',
        encoding="utf-8",
    )

    assert main(["finish", "v1", "--no-review"]) == 0
    assert "KLAR #1" in capsys.readouterr().out
    assert main(["posted", "1", "https://example.com/v"]) == 0
    assert main(["stats", "1", "--views", "2500", "--likes", "30"]) == 0
    assert "klarar kravet" in capsys.readouterr().out
    assert main(["status"]) == 0
    assert "postad: 1" in capsys.readouterr().out
    assert main(["facit", str(settings.root / "facit.yaml")]) == 0
    assert "1 av 2" in capsys.readouterr().out
