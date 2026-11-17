"""Ny version av ett godkänt klipp för ett annat konto: font, klipp i början, overlay och klistermärke.
Inga riktiga anrop till YouTube, TikTok eller Claude."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from campaign import editor
from campaign.cliplog import ClipLog
from campaign.profile import load_profile



def _profile_file(tmp_path: Path, style) -> Path:
    path = tmp_path / "p.yaml"
    path.write_text(yaml.safe_dump({"name": "P", "streamer": "s", "accounts_style": style}))
    return path


# ---------- 1. accounts_style och version_of ----------


def test_the_account_style_is_read_from_the_profile(tmp_path) -> None:
    style = {"@ClipVault": {"font": "Impact", "overlay": None, "start_cut_s": 2,
                            "sticker": {"file": "fire.png", "position": "top_right", "start_s": 0, "duration_s": 3}}}
    profile = load_profile(_profile_file(tmp_path, style), tmp_path)
    assert profile.accounts_style == {"@ClipVault": {"font": "Impact", "overlay": None, "start_cut_s": 2.0,
                                                     "sticker": style["@ClipVault"]["sticker"]}}
    assert load_profile(_profile_file(tmp_path, None), tmp_path).accounts_style == {}


@pytest.mark.parametrize("style", [
    ["Impact"], {"a": "Impact"}, {"a": {"font": 5}}, {"a": {"start_cut_s": "två"}}, {"a": {"sticker": "fire.png"}},
    {"a": {"overlay": 3}}])
def test_a_wrong_account_style_stops_with_a_clear_error(tmp_path, style) -> None:
    with pytest.raises(SystemExit, match="accounts_style"):
        load_profile(_profile_file(tmp_path, style), tmp_path)


def test_a_version_points_at_its_original_and_the_family_is_found_from_both(settings) -> None:
    log = ClipLog(settings.data_dir / "klipp.db")
    original = log.add(vod_id="v", profile="skiimasktimmy", file="a.mp4", title="t", caption="c")
    other = log.add(vod_id="v", profile="skiimasktimmy", file="b.mp4", title="t", caption="c")
    version = log.add(vod_id="v", profile="skiimasktimmy", file="a.v1.mp4", title="t", caption="c",
                      version_of=original)
    assert (log.get(version)["version_of"], log.get(original)["version_of"]) == (original, None)
    assert log.family(original) == log.family(version) == [original, version]
    assert log.family(other) == [other]


def test_an_old_log_gets_the_version_column(settings) -> None:
    path = settings.data_dir / "klipp.db"
    log = ClipLog(path)
    clip_id = log.add(vod_id="v", profile="skiimasktimmy", file="a.mp4", title="t", caption="c")
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE clips DROP COLUMN version_of")
    assert ClipLog(path).get(clip_id)["version_of"] is None


def test_a_version_cannot_be_edited(settings) -> None:
    clip = {"status": "renderad", "version_of": 3}
    assert editor.why_not(clip, settings.out_dir / "x.mp4") == (
        "En version går inte att redigera. Gör en ny version av originalet i stället.")


# ---------- 2. font, egen overlay och klistermärke i renderingen ----------

from campaign import finish  # noqa: E402
from campaign.profile import Profile  # noqa: E402

STICKER = {"w": 320, "h": 200, "x": 40, "y": 135, "start_s": 1.0, "end_s": 4.0}


def test_the_sticker_is_laid_over_after_the_subtitles_and_the_campaigns_overlay() -> None:
    profile = Profile(name="x", streamer="y", hook_text=False)
    graph = finish.build_filter(profile, True, True, None, subtitles=Path("/tmp/a.ass"), sticker=STICKER)
    assert "[2:v]scale=320:200[st]" in graph
    assert "[withov][st]overlay=40:135:enable='between(t,1.000,4.000)'[withsticker]" in graph
    assert graph.index("subtitles=") < graph.index("[ov]overlay") < graph.index("[st]overlay")
    assert "[withsticker]format=yuv420p[vout]" in graph
    alone = finish.build_filter(profile, False, True, None, sticker=STICKER)
    assert "[1:v]scale=320:200[st]" in alone and "[base][st]overlay=" in alone
    hooked = finish.build_filter(profile, True, True, None, pieces=[(4.0, 6.0), (0.0, 9.0)], cold_open=True,
                                 sticker=STICKER)
    assert "[3:v]scale=320:200[st]" in hooked  # klippet, hooken, overlayen, klistermärket
    assert finish.build_filter(profile, True, True, None) == finish.build_filter(profile, True, True, None,
                                                                                sticker=None)


def test_finish_takes_the_versions_font_overlay_and_sticker(tmp_path, monkeypatch) -> None:
    seen: list = []
    monkeypatch.setattr(finish, "run", seen.append)
    monkeypatch.setattr(finish, "probe", lambda path: {"has_audio": True, "duration": 9.0})
    own = tmp_path / "overlays" / "own.png"
    own.parent.mkdir()
    own.write_bytes(b"png")
    sticker = tmp_path / "stickers" / "fire.png"
    profile = Profile(name="x", streamer="y", text="twitch.tv/y", hook_text=True)  # ingen overlay i kampanjen
    manifest = finish.finish_clip(tmp_path / "src.mp4", tmp_path / "out.mp4", profile, tmp_path, title="Big play",
                                  overlay=own, font_file="/fonts/Impact.ttf", sticker={**STICKER, "path": sticker})
    (cmd,) = seen
    inputs = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-i"]
    assert inputs == [str(tmp_path / "src.mp4"), str(own), str(sticker)]
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "fontfile='/fonts/Impact.ttf'" in graph and "[2:v]scale=320:200[st]" in graph
    assert (manifest["overlay_applied"], manifest["sticker_applied"]) == (True, True)


def _profile(**changes) -> Profile:
    return Profile(name="x", streamer="y", safe_top=0.07, safe_bottom=0.20, **changes)


def test_the_sticker_box_is_placed_just_outside_the_safe_zones() -> None:
    from campaign import versions

    assert versions.sticker_box("top_right", 320, 200, _profile()) == (720, 135)  # 0,07 × 1920 = 134,4
    assert versions.sticker_box("bottom_left", 320, 200, _profile()) == (40, 1336)  # 1536 - 200
    assert versions.sticker_box("center", 320, 1152, _profile()) == (380, 384)  # slutar precis vid 1536


def test_a_sticker_that_reaches_into_a_safe_zone_is_refused() -> None:
    from campaign import versions

    with pytest.raises(versions.VersionError, match="säkra zon"):
        versions.sticker_box("center", 320, 1154, _profile())  # 383 + 1154 = 1537, en pixel in i safe_bottom
    with pytest.raises(versions.VersionError):
        versions.sticker_box("upp", 320, 200, _profile())


@pytest.mark.parametrize("name", ["../overlays/x.png", "fire.jpg", "saknas.png", "/etc/passwd"])
def test_only_png_files_in_the_sticker_folder_can_be_used(tmp_path, name) -> None:
    from campaign import versions

    (tmp_path / "stickers").mkdir()
    (tmp_path / "stickers" / "fire.png").write_bytes(b"png")
    (tmp_path / "stickers" / "fire.jpg").write_bytes(b"jpg")
    (tmp_path / "overlays").mkdir()
    (tmp_path / "overlays" / "x.png").write_bytes(b"png")
    assert versions.sticker_path(tmp_path, "fire.png") == (tmp_path / "stickers" / "fire.png").resolve()
    with pytest.raises(versions.VersionError, match="stickers/"):
        versions.sticker_path(tmp_path, name)
