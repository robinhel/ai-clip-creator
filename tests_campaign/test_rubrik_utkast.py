"""Egna videor: förhandsbilden och klippet får samma rubrikfont, och klistermärket uppe hamnar under rubriken
också när en vald font bryter rubriken på fler rader. Filtren fångas, inget renderas."""

from __future__ import annotations

import re
import subprocess

import pytest

from campaign import drafts, finish, versions
from campaign.profile import load_profile

from .conftest import needs_ffmpeg, set_profile

HOOK_FONT = re.compile(r"drawtext=fontfile='([^']*)':expansion=none")
HOOK_BAR = re.compile(r"drawbox=x=0:y=(\d+):w=iw:h=(\d+)")
STICKER = re.compile(r"\[st\]overlay=(\d+):(\d+):")
WIDE = "WWWW MMMM WWWW MMMM"  # serif: en rad i stilens font, två med en vald font


class Stop(Exception):
    """Stoppar efter filtret, före ffmpeg."""


@pytest.fixture
def campaign(settings, monkeypatch):
    """Rubriken bränns in utan overlay och kampanjens text, och fonten Impact finns att välja."""
    fonts = settings.root / "fonts"
    fonts.mkdir()
    (fonts / "Impact.ttf").write_bytes(b"ttf")
    monkeypatch.setattr(versions, "FONT_DIRS", (fonts,))
    set_profile(settings, clip={"min_seconds": 3, "max_seconds": 60}, active=True,
                on_screen_text={"text": "", "hook_text": True, "hook_style": "serif"},
                overlay={"file": None, "required": False})
    return load_profile("skiimasktimmy", settings.profiles_dir)


def _sticker_png(settings):
    path = settings.root / "stickers" / "fire.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=400x400,format=rgba",
                    "-frames:v", "1", str(path)], check=True)


def _graphs(monkeypatch, tmp_path, plan: drafts.Plan) -> tuple[str, str]:
    """Filtret i förhandsbilden (drafts.frame) och i klippet (finish_clip via drafts.finish)."""
    seen: dict[str, str] = {}

    def spy(name):
        def wrapped(*args, **kwargs):
            seen[name] = real(*args, **kwargs)
            raise Stop

        return wrapped

    real = finish.build_filter
    monkeypatch.setattr(drafts, "build_filter", spy("frame"))
    monkeypatch.setattr(finish, "build_filter", spy("clip"))
    monkeypatch.setattr(drafts, "meta", lambda path: {"fps": 30, "suffix": ".mp4"})
    monkeypatch.setattr(finish, "probe", lambda src: {"has_audio": False})
    monkeypatch.setattr(finish, "need", lambda tool: tool)
    with pytest.raises(Stop):
        drafts.frame(tmp_path, plan, 1.0)
    with pytest.raises(Stop):
        drafts.finish(tmp_path / "raw.mp4", tmp_path / "klara" / "raw.mp4", plan)
    return seen["frame"], seen["clip"]


def _plan(settings, campaign, **choices) -> drafts.Plan:
    return drafts.make_plan(settings, campaign, drafts.Choices(profile="skiimasktimmy", **choices), 8.0)


def _sticker_below_hook(graph: str) -> None:
    (bar,) = HOOK_BAR.findall(graph)
    sticker = STICKER.search(graph)
    assert sticker is not None, graph
    assert int(sticker.group(2)) >= int(bar[0]) + int(bar[1]), graph


@pytest.mark.parametrize("font", ["Impact", ""])
def test_the_preview_and_the_clip_get_the_same_hook_font(settings, campaign, tmp_path, monkeypatch, font) -> None:
    plan = _plan(settings, campaign, title="Galet skott", font=font)
    preview, clip = _graphs(monkeypatch, tmp_path, plan)
    expected = settings.root / "fonts" / "Impact.ttf" if font else finish.HOOK_FONTS["serif"]
    assert set(HOOK_FONT.findall(preview)) == set(HOOK_FONT.findall(clip)) == {finish._esc(str(expected))}


@needs_ffmpeg
def test_a_sticker_at_the_top_goes_below_a_hook_in_the_chosen_font(settings, campaign, tmp_path,
                                                                    monkeypatch) -> None:
    _sticker_png(settings)
    plan = _plan(settings, campaign, title=WIDE, font="Impact", sticker="fire.png", sticker_position="top_left")
    preview, clip = _graphs(monkeypatch, tmp_path, plan)
    assert len(HOOK_FONT.findall(clip)) == 2  # två rader med den valda fonten
    _sticker_below_hook(preview)
    _sticker_below_hook(clip)


@needs_ffmpeg
def test_edit_puts_the_sticker_below_a_hook_in_the_chosen_font(settings, campaign, tmp_path, monkeypatch) -> None:
    _sticker_png(settings)
    look = drafts.EditLook({"profile": "skiimasktimmy", "title": WIDE, "font": "Impact", "sticker": "fire.png",
                            "sticker_position": "top_left"}, hook=WIDE)
    own = look.args(settings, campaign, 4.0)
    seen: list[str] = []
    real = finish.build_filter

    def spy(*args, **kwargs):
        seen.append(real(*args, **kwargs))
        raise Stop

    monkeypatch.setattr(finish, "build_filter", spy)
    monkeypatch.setattr(finish, "probe", lambda src: {"has_audio": False})
    monkeypatch.setattr(finish, "need", lambda tool: tool)
    with pytest.raises(Stop):  # samma anrop som editor.edit_clip gör med look.args
        finish.finish_clip(tmp_path / "raw.mp4", tmp_path / "klara" / "raw.mp4", campaign, settings.root,
                           title=look.hook, **own)
    (graph,) = seen
    assert len(HOOK_FONT.findall(graph)) == 2
    _sticker_below_hook(graph)
