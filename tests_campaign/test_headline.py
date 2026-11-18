"""Rubriken i bilden sparas i klipploggen: redigeraren och en ny körning av ett redigerat klipp ritar headline,
inte titeln. Captionen byggs alltid av titeln."""

from __future__ import annotations

import json
import sqlite3

import pytest

from campaign import editor, finish, subtitles, versions, workflow
from campaign.caption import build_caption
from campaign.cliplog import ClipLog
from campaign.profile import Profile, load_profile
from campaign.versions import NO_OWN
from campaign.workflow import finish_vod

from .conftest import make_clip, make_overlay, needs_ffmpeg, needs_subtitles, set_profile
from .test_editor import WORDS, _paths, _words, setup  # noqa: F401  (fixture)
from .test_mcp_server import _call, app  # noqa: F401  (fixture)
from .test_foresla_hook import _claude_hook
from .test_ny_version import _hook_on as _version_hook_on
from .test_ny_version import _make, _options, _png, _spy, vclip  # noqa: F401  (fixture)

pytestmark = needs_ffmpeg


def _hook_on(settings) -> None:
    path = settings.profiles_dir / "skiimasktimmy.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("on_screen_text:\n", "on_screen_text:\n  hook_text: true\n"),
                    encoding="utf-8")


def _titles(module, monkeypatch) -> list:
    """Texten i bilden vid varje finish_clip."""
    seen: list = []
    real = module.finish_clip

    def spy(*args, **kwargs):
        seen.append(kwargs.get("title"))
        return real(*args, **kwargs)

    monkeypatch.setattr(module, "finish_clip", spy)
    return seen


def _profile(settings):
    return load_profile("skiimasktimmy", settings.profiles_dir)


def _finish_again(settings, log, clip_id) -> None:
    log.update(clip_id, status="underkand")
    finish_vod("123", _profile(settings), settings, log, game="Bodycam", review=False)


# ---------- klipploggen ----------


def test_the_clip_log_saves_the_headline(tmp_path) -> None:
    log = ClipLog(tmp_path / "klipp.db")
    with_headline = log.add(vod_id="1", profile="p", file="a.mp4", title="WoW Forever", caption="c",
                            headline="Humans pick up the bow")
    without = log.add(vod_id="1", profile="p", file="b.mp4", title="No way", caption="c")
    assert log.get(with_headline)["headline"] == "Humans pick up the bow"
    assert not log.get(without)["headline"]
    log.update(without, headline="Big play")
    assert log.get(without)["headline"] == "Big play"


# ---------- körningen ----------


def test_finish_vod_saves_the_cleaned_headline_and_nothing_without_one(settings, monkeypatch) -> None:
    monkeypatch.setitem(finish.ENCODER_ARGS, "libx264", ["-c:v", "libx264", "-preset", "ultrafast"])
    make_overlay(settings.root / "overlays" / "skiimasktimmy.png")
    make_clip(settings.out_dir / "123" / "clip_01_no_way.mp4", seconds=10)
    make_clip(settings.out_dir / "123" / "clip_02_jump.mp4", seconds=10)
    picks = settings.work_dir / "123" / "picks.json"
    picks.parent.mkdir(parents=True)
    picks.write_text(json.dumps([
        {"candidate": {}, "pick": {"start_offset_s": 100, "end_offset_s": 110, "title": "No way",
                                   "headline": "  Big ❤️ play \n"}},
        {"candidate": {}, "pick": {"start_offset_s": 300, "end_offset_s": 310, "title": "Jump"}},
    ]))
    log = ClipLog(settings.data_dir / "klipp.db")
    first, second = finish_vod("123", _profile(settings), settings, log, game="Bodycam", review=False)
    assert log.get(first["id"])["headline"] == "Big play"
    assert log.get(second["id"])["headline"] == ""
    assert log.get(first["id"])["caption"] == build_caption("No way", _profile(settings), "Bodycam")


def test_finish_again_on_an_edited_clip_burns_its_headline(setup, settings, monkeypatch) -> None:  # noqa: F811
    client, log, clip_id = setup
    body = {"in_s": 1.0, "out_s": 7.0, "headline": "He jumped the gap"}
    assert client.post(f"/campaign/clips/{clip_id}/edit", json=body).status_code == 200
    titles = _titles(workflow, monkeypatch)
    _finish_again(settings, log, clip_id)
    assert titles == ["He jumped the gap"]  # titeln "No way" är också filnamnets, men headline är Robins egen
    clip = log.get(clip_id)
    assert (clip["title"], clip["headline"]) == ("No way", "He jumped the gap")
    assert clip["caption"] == build_caption("No way", _profile(settings), "Bodycam")


def test_finish_again_without_a_headline_keeps_the_file_name_rule(setup, settings, monkeypatch) -> None:  # noqa: F811
    client, log, clip_id = setup
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0}).status_code == 200
    titles = _titles(workflow, monkeypatch)
    _finish_again(settings, log, clip_id)
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"title": "Huge clutch"}).status_code == 200
    _finish_again(settings, log, clip_id)
    assert titles == ["", "Huge clutch"]  # "No way" är filnamnets rubrik och blir ingen text i bilden


# ---------- redigeraren ----------


def test_edit_info_has_the_saved_headline(setup) -> None:  # noqa: F811
    client, _, clip_id = setup
    assert client.get(f"/campaign/clips/{clip_id}/edit").json()["headline"] == ""
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"headline": "Big play"}).status_code == 200
    assert client.get(f"/campaign/clips/{clip_id}/edit").json()["headline"] == "Big play"


def test_edit_info_of_an_old_clip_has_an_empty_headline(setup, settings) -> None:  # noqa: F811
    _, log, clip_id = setup
    db = sqlite3.connect(settings.data_dir / "klipp.db")
    db.execute("UPDATE clips SET headline = NULL")
    db.commit()
    db.close()
    info = editor.edit_info(log.get(clip_id), _profile(settings), settings)
    assert info["headline"] == ""


def test_a_new_headline_is_burned_and_saved_and_the_caption_stays(setup, settings, monkeypatch) -> None:  # noqa: F811
    client, log, clip_id = setup
    _hook_on(settings)
    caption = log.get(clip_id)["caption"]
    titles = _titles(editor, monkeypatch)
    response = client.post(f"/campaign/clips/{clip_id}/edit", json={"headline": "  He jumped the gap "})
    assert response.status_code == 200, response.text
    assert titles == ["He jumped the gap"]
    clip = log.get(clip_id)
    assert (clip["title"], clip["headline"], clip["caption"]) == ("No way", "He jumped the gap", caption)
    assert response.json()["headline"] == "He jumped the gap"


def test_a_new_title_with_a_headline_only_builds_a_new_caption(setup, settings, monkeypatch) -> None:  # noqa: F811
    client, log, clip_id = setup
    _hook_on(settings)
    _, done, _ = _paths(settings)
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"headline": "He jumped the gap"}).status_code == 200
    mtime = done.stat().st_mtime_ns
    titles = _titles(editor, monkeypatch)
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"title": "Huge clutch"}).status_code == 200
    assert titles == []
    assert done.stat().st_mtime_ns == mtime
    clip = log.get(clip_id)
    assert (clip["title"], clip["headline"]) == ("Huge clutch", "He jumped the gap")
    assert clip["caption"] == build_caption("Huge clutch", _profile(settings), "Bodycam")


def test_an_empty_headline_puts_the_title_on_the_picture(setup, settings, monkeypatch) -> None:  # noqa: F811
    client, log, clip_id = setup
    _hook_on(settings)
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"headline": "He jumped the gap"}).status_code == 200
    titles = _titles(editor, monkeypatch)
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"headline": ""}).status_code == 200
    assert titles == ["No way"]
    assert log.get(clip_id)["headline"] == ""


def test_trim_and_new_words_keep_the_headline_on_the_picture(setup, settings, monkeypatch) -> None:  # noqa: F811
    client, log, clip_id = setup
    _words(settings)
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"headline": "He jumped the gap"}).status_code == 200
    titles = _titles(editor, monkeypatch)
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0}).status_code == 200
    fixed = [*WORDS[:2], {"start": 2.0, "end": 2.6, "word": "way!"}, WORDS[3]]
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"words": fixed}).status_code == 200
    assert titles == ["He jumped the gap", "He jumped the gap"]
    assert log.get(clip_id)["headline"] == "He jumped the gap"


def test_the_clip_json_has_the_headline(setup) -> None:  # noqa: F811
    client, _, clip_id = setup
    client.post(f"/campaign/clips/{clip_id}/edit", json={"headline": "Big play"})
    (clip,) = [c for c in client.get("/campaign/clips").json() if c["id"] == clip_id]
    assert clip["headline"] == "Big play"


def test_edit_clip_in_mcp_sends_the_headline(app) -> None:  # noqa: F811
    _call("edit_clip", {"clip_id": 7, "headline": "Big play"})
    (request,) = app.requests
    assert (request.url.path, json.loads(request.content)) == ("/campaign/clips/7/edit", {"headline": "Big play"})


# ---------- ny version ----------


STICKER_TOP = {"file": "fire.png", "position": "top_right", "start_s": 0, "duration_s": 3}
TWO_LINES = "Ett galet skott från andra sidan banan"  # två rader i bilden, titeln "No way" en


def test_the_sticker_goes_below_the_lines_the_versions_font_makes(settings) -> None:
    _png(settings.root / "stickers" / "fire.png")
    profile = Profile(name="x", streamer="y", safe_top=0.07, safe_bottom=0.20, hook_text=True, hook_style="serif")
    title = "Nobody saw that coming"  # en rad i stilens font, två med en egen font
    assert versions._sticker(settings, profile, STICKER_TOP, 10, title)["y"] == 134 + 80 + 20
    placed = versions._sticker(settings, profile, STICKER_TOP, 10, title, None, "", "/fonts/Impact.ttf")
    assert placed["y"] == 134 + 160 + 20


def test_a_version_places_its_sticker_with_its_font(vclip, settings, monkeypatch) -> None:  # noqa: F811
    client, _, clip_id = vclip
    fonts: list = []
    real = versions.top_text_end

    monkeypatch.setattr(versions, "top_text_end", lambda *a, **k: fonts.append(a[5]) or real(*a, **k))
    answer = _make(client, clip_id, font="Impact", sticker=STICKER_TOP)
    assert answer.status_code == 201, answer.text
    assert fonts == [str(settings.root / "fonts" / "Impact.ttf")]


@needs_subtitles
def test_a_version_without_a_hook_shows_and_saves_the_originals_headline(vclip, settings,  # noqa: F811
                                                                         monkeypatch) -> None:
    client, log, clip_id = vclip
    _version_hook_on(settings)
    log.update(clip_id, headline=TWO_LINES)
    root = log.get(clip_id)
    seen = _spy(monkeypatch)
    answer = _make(client, clip_id, sticker=STICKER_TOP)
    assert answer.status_code == 201, answer.text
    assert seen[-1]["title"] == TWO_LINES
    assert seen[-1]["sticker"]["y"] == 134 + 160 + 20  # under rubrikens två rader
    version = log.get(answer.json()["id"])
    assert (version["title"], version["caption"], version["headline"]) == ("No way", root["caption"], TWO_LINES)


@needs_subtitles
def test_a_version_with_its_own_hook_shows_and_saves_the_hook(vclip, settings, monkeypatch) -> None:  # noqa: F811
    client, log, clip_id = vclip
    _version_hook_on(settings)
    log.update(clip_id, headline="Big play")
    seen = _spy(monkeypatch)
    answer = _make(client, clip_id, hook="Nobody expected that ending")
    assert answer.status_code == 201, answer.text
    assert seen[-1]["title"] == "Nobody expected that ending"
    version = log.get(answer.json()["id"])
    assert (version["title"], version["headline"]) == ("Nobody expected that ending", "Nobody expected that ending")


@needs_subtitles
def test_the_options_and_the_used_hooks_are_the_text_on_the_picture(vclip, settings) -> None:  # noqa: F811
    client, log, clip_id = vclip
    _version_hook_on(settings)
    log.update(clip_id, headline="Big play")
    options = _options(client, clip_id)
    assert (options["hook"], options["hooks_used"]) == ("Big play", ["Big play"])
    answer = _make(client, clip_id, hook="big PLAY")
    assert answer.status_code == 201, answer.text
    assert NO_OWN in answer.json()["warnings"]  # samma text som i bilden: inget eget bidrag
    assert _options(client, clip_id)["hooks_used"] == ["Big play", "big PLAY"]


def test_a_suggested_hook_is_not_the_text_on_the_picture(vclip, monkeypatch) -> None:  # noqa: F811
    client, log, clip_id = vclip
    log.update(clip_id, headline="Big play")
    fake = _claude_hook(monkeypatch, "big PLAY", "Bodycam never felt this tense")
    answer = client.post(f"/campaign/clips/{clip_id}/versions/hook")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"hook": "Bodycam never felt this tense"} and len(fake.messages.calls) == 2
    assert "Big play" in str(fake.messages.calls[0]["messages"])  # texten i bilden skickas som använd


# ---------- hook_fits och hookens regler ----------


WORGEN = "WORLD OF WARCRAFT FOREVER LOOKS MOMMY WORGEN WARRIOR"  # exakt tre rader i sans


def test_hook_fits_takes_three_lines_and_not_four(tmp_path) -> None:
    profile = Profile(name="x", streamer="y", safe_top=0.07, safe_bottom=0.20, hook_text=True, hook_style="sans")
    assert finish.hook_fits(profile, tmp_path, WORGEN, frame="fit")
    assert not finish.hook_fits(profile, tmp_path, WORGEN + " MAMMOTH", frame="fit")  # fyra rader


def test_hook_fits_takes_fewer_lines_in_zoom(tmp_path) -> None:
    profile = Profile(name="x", streamer="y", safe_top=0.125, safe_bottom=0.20, hook_text=True, hook_style="sans")
    assert finish.hook_fits(profile, tmp_path, WORGEN, frame="fit")
    assert not finish.hook_fits(profile, tmp_path, WORGEN, frame="zoom")  # zoom: två rader ovanför bilden


def test_hook_fits_measures_an_own_font_wider(tmp_path) -> None:
    profile = Profile(name="x", streamer="y", safe_top=0.07, safe_bottom=0.20, hook_text=True, hook_style="serif")
    title = "Nobody saw that coming at all and then the whole lobby lost it in"  # tre rader, fyra med egen font
    assert finish.hook_fits(profile, tmp_path, title)
    assert not finish.hook_fits(profile, tmp_path, title, font_file="/f/I.ttf")


def test_a_hook_with_only_emoji_is_refused() -> None:
    with pytest.raises(versions.VersionError, match="bokstäver eller siffror"):
        versions._hook("🔥🔥 😂", lambda _: True)
    assert versions._hook(" Big play 🔥 ", lambda _: True) == "Big play 🔥"


def test_a_suggested_hook_must_fit_the_clips_zoom(vclip, settings, monkeypatch) -> None:  # noqa: F811
    client, log, clip_id = vclip
    set_profile(settings, safe_zone={"top": 0.125, "bottom": 0.20})  # zoom: två rader ovanför bilden
    words = subtitles.words_path(editor.raw_path(log.get(clip_id), settings))
    data = json.loads(words.read_text(encoding="utf-8"))
    words.write_text(json.dumps({**data, "reframe": {**data["reframe"], "mode": "zoom"}}), encoding="utf-8")
    fake = _claude_hook(monkeypatch, WORGEN.title(), "Bodycam never felt this tense")  # tre rader, sedan två
    answer = client.post(f"/campaign/clips/{clip_id}/versions/hook")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"hook": "Bodycam never felt this tense"} and len(fake.messages.calls) == 2
