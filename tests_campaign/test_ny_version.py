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
