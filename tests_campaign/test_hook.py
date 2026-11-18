"""Hook-text: klippets titel överst hela klippet, när kampanjen inte kräver egen text."""

from __future__ import annotations

import pytest

from campaign import editor, finish
from campaign.finish import build_filter, finish_clip
from campaign.media import probe
from campaign.profile import load_profile

from .conftest import PAKET, make_clip, needs_ffmpeg
from .test_editor import setup  # noqa: F401  (fixture)


@pytest.fixture
def blizzard(settings):
    import shutil

    shutil.copy(PAKET / "profiles" / "blizzard_nordics.yaml", settings.profiles_dir)
    return load_profile("blizzard_nordics", settings.profiles_dir)


def test_hook_is_on_when_no_text_is_required_and_off_otherwise(settings, blizzard) -> None:
    assert blizzard.hook_text is True
    assert load_profile("skiimasktimmy", settings.profiles_dir).hook_text is False


def test_the_profile_can_turn_the_hook_off(settings, blizzard) -> None:
    path = settings.profiles_dir / "blizzard_nordics.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "on_screen_text:\n", "on_screen_text:\n  hook_text: false\n"
        ),
        encoding="utf-8",
    )
    assert load_profile("blizzard_nordics", settings.profiles_dir).hook_text is False


def test_hook_is_drawn_the_whole_clip(blizzard) -> None:
    graph = build_filter(
        blizzard, has_overlay=False, has_audio=True, font=None, title="Huge clutch"
    )
    assert "drawtext" in graph and "enable=" not in graph
    assert "Huge clutch" in graph


def test_no_title_or_no_hook_gives_no_drawtext(settings, blizzard) -> None:
    assert "drawtext" not in build_filter(
        blizzard, has_overlay=False, has_audio=True, font=None, title=""
    )
    timmy = load_profile("skiimasktimmy", settings.profiles_dir)
    with_title = build_filter(
        timmy, has_overlay=True, has_audio=True, font=None, title="Huge clutch"
    )
    assert with_title == build_filter(timmy, has_overlay=True, has_audio=True, font=None)


def test_a_long_title_is_split_into_lines(blizzard) -> None:
    graph = build_filter(
        blizzard,
        has_overlay=False,
        has_audio=False,
        font=None,
        title="This boss fight went completely sideways for everyone",
    )
    assert graph.count("drawtext") >= 2


@needs_ffmpeg
def test_special_characters_render(tmp_path, blizzard, monkeypatch) -> None:
    monkeypatch.setitem(finish.ENCODER_ARGS, "libx264", ["-c:v", "libx264", "-preset", "ultrafast"])
    src = make_clip(tmp_path / "clip_01.mp4", seconds=4)
    out = tmp_path / "klara" / "clip_01.mp4"
    manifest = finish_clip(src, out, blizzard, tmp_path, title="He's back: 100% [live], wow; \\o/")
    assert manifest["hook_text"] == "He's back: 100% [live], wow; \\o/"
    assert probe(out)["duration"] > 3


@needs_ffmpeg
def test_new_title_renders_the_hook_again(setup, settings, monkeypatch) -> None:  # noqa: F811
    client, _log, clip_id = setup
    path = settings.profiles_dir / "skiimasktimmy.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "on_screen_text:\n", "on_screen_text:\n  hook_text: true\n"
        ),
        encoding="utf-8",
    )
    titles = []
    real = editor.finish_clip

    def spy(*args, **kwargs):
        titles.append(kwargs.get("title"))
        return real(*args, **kwargs)

    monkeypatch.setattr(editor, "finish_clip", spy)
    response = client.post(f"/campaign/clips/{clip_id}/edit", json={"title": "Huge clutch"})
    assert response.status_code == 200, response.text
    assert titles == ["Huge clutch"]


@needs_ffmpeg
def test_finish_vod_sends_the_pick_title(setup, settings, monkeypatch) -> None:  # noqa: F811
    from campaign import workflow

    _client, log, clip_id = setup
    titles = []
    real = workflow.finish_clip

    def spy(*args, **kwargs):
        titles.append(kwargs.get("title"))
        return real(*args, **kwargs)

    monkeypatch.setattr(workflow, "finish_clip", spy)
    log.update(clip_id, status="underkand")
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    workflow.finish_vod("123", profile, settings, log, game="Bodycam", review=False)
    assert titles == ["No way"]


def test_control_characters_in_the_title_become_spaces(blizzard) -> None:
    graph = build_filter(
        blizzard, has_overlay=False, has_audio=False, font=None, title="Huge\x00clutch\nnow"
    )
    assert "\x00" not in graph and "\n" not in graph
    assert "Huge clutch now" in graph


@needs_ffmpeg
def test_no_hook_from_the_file_name_when_there_is_no_title(setup, settings, monkeypatch) -> None:  # noqa: F811
    from campaign import workflow

    _client, log, clip_id = setup
    titles = []
    real = workflow.finish_clip

    def spy(*args, **kwargs):
        titles.append(kwargs.get("title"))
        return real(*args, **kwargs)

    monkeypatch.setattr(workflow, "finish_clip", spy)
    monkeypatch.setattr(workflow, "load_picks", lambda path: [{}])
    log.update(clip_id, status="underkand")
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    workflow.finish_vod("123", profile, settings, log, game="Bodycam", review=False)
    assert titles == [""]


@needs_ffmpeg
def test_no_hook_from_the_signals_placeholder_title(setup, settings, monkeypatch) -> None:  # noqa: F811
    from campaign import workflow

    _client, log, clip_id = setup
    titles = []
    real = workflow.finish_clip
    monkeypatch.setattr(workflow, "finish_clip", lambda *a, **k: titles.append(k.get("title")) or real(*a, **k))
    monkeypatch.setattr(workflow, "load_picks", lambda path: [{"title": "Moment 0:01:40"}])
    log.update(clip_id, status="underkand")
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    workflow.finish_vod("123", profile, settings, log, game="Bodycam", review=False)
    assert titles == [""]


def test_a_title_of_only_control_characters_gives_no_hook(blizzard) -> None:
    graph = build_filter(blizzard, has_overlay=False, has_audio=False, font=None, title="\x00\n")
    assert "withhook" not in graph and "drawtext" not in graph


@needs_ffmpeg
def test_no_hook_from_the_file_name_when_the_clip_is_edited_in_the_app(
    setup,  # noqa: F811
    settings,
    monkeypatch,
) -> None:
    from campaign import workflow

    _client, log, clip_id = setup
    titles = []
    real = workflow.finish_clip

    def spy(*args, **kwargs):
        titles.append(kwargs.get("title"))
        return real(*args, **kwargs)

    monkeypatch.setattr(workflow, "finish_clip", spy)
    monkeypatch.setattr(workflow, "load_picks", lambda path: [{}])
    monkeypatch.setattr(workflow, "edit_meta", lambda raw: {"edited": True})
    log.update(
        clip_id, status="underkand", title="No way"
    )  # filnamnsreserven för clip_01_no_way.mp4
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    workflow.finish_vod("123", profile, settings, log, game="Bodycam", review=False)
    assert titles == [""]
