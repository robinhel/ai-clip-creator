"""Smakfilen: profilens style väljer en fil i smak/ som går in i Claudes val via focus."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from campaign.cliplog import ClipLog
from campaign.pipeline import _with_memory
from campaign.profile import load_profile
from campaign.smak import load_taste

PAKET = Path(__file__).resolve().parent.parent


def _profile(settings, style: str | None):
    path = settings.profiles_dir / "skiimasktimmy.yaml"
    text = path.read_text(encoding="utf-8")
    if style is not None:
        path.write_text(f"{text.rstrip()}\nstyle: {style}\n", encoding="utf-8")
    return load_profile("skiimasktimmy", settings.profiles_dir)


def _remember(settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    log = ClipLog(settings.data_dir / "klipp.db")
    clip_id = log.add(
        vod_id="1",
        profile="skiimasktimmy",
        file="/x/a.mp4",
        title="Huge clutch",
        caption="c",
        status="betald",
    )
    log.update(clip_id, views=9000)


def test_style_puts_taste_last_after_focus_and_memory(settings) -> None:
    shutil.copytree(PAKET / "smak", settings.root / "smak")
    _remember(settings)
    focus = _with_memory("funny NPC moments", _profile(settings, "gta"), settings)
    taste = load_taste("gta", settings.root)
    assert taste and taste in focus
    assert focus.index("funny NPC moments") < focus.index("Huge clutch") < focus.index(taste)


def test_no_style_gives_todays_focus(settings) -> None:
    _remember(settings)
    profile = _profile(settings, None)
    assert profile.style == ""
    focus = _with_memory("funny NPC moments", profile, settings)
    from campaign.memory import selection_memory

    memory = selection_memory(
        ClipLog(settings.data_dir / "klipp.db"), "skiimasktimmy", profile.min_views or 2000
    )
    assert focus == f"funny NPC moments, and {memory}"
    assert _with_memory("", profile, settings) == memory


def test_bad_style_names_the_file(settings, tmp_path) -> None:
    with pytest.raises(SystemExit, match=r"smak/\.\./hemligt\.md"):
        _profile(settings, "../hemligt")
    with pytest.raises(ValueError, match=r"smak/\.\./hemligt\.md"):
        load_taste("../hemligt", tmp_path)
    with pytest.raises(ValueError, match=r"smak/finnsinte\.md"):
        load_taste("finnsinte", tmp_path)
    assert load_taste("", tmp_path) == ""


def test_gta_taste_file_keeps_the_patterns() -> None:
    text = (PAKET / "smak" / "gta.md").read_text(encoding="utf-8")
    assert len(text) <= 1200
    lower = text.lower()
    assert "quote" in lower and "headline" in lower and "shareable" in lower
    taste = load_taste("gta", PAKET)
    assert "#" not in taste.splitlines()[0]
