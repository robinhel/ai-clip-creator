"""Rubriken i bilden sparas i klipploggen: redigeraren och en ny körning av ett redigerat klipp ritar headline,
inte titeln. Captionen byggs alltid av titeln."""

from __future__ import annotations

import json
import sqlite3

import pytest

from campaign import editor, finish, workflow
from campaign.caption import build_caption
from campaign.cliplog import ClipLog
from campaign.profile import load_profile
from campaign.workflow import finish_vod

from .conftest import make_clip, make_overlay, needs_ffmpeg
from .test_editor import WORDS, _paths, _words, setup  # noqa: F401  (fixture)
from .test_mcp_server import _call, app  # noqa: F401  (fixture)

pytestmark = needs_ffmpeg


@pytest.fixture
def edit(setup):  # noqa: F811
    """test_editor.setup: Timmys klipp clip_01_no_way.mp4 med titeln "No way", underkänt på 10 s."""
    return setup

OLD_CLIPS = """CREATE TABLE clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vod_id TEXT, profile TEXT, start_s REAL, end_s REAL,
    title TEXT, format TEXT, signals TEXT, file TEXT, caption TEXT,
    status TEXT, problems TEXT, warnings TEXT, review TEXT,
    platform TEXT, url TEXT,
    views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER,
    note TEXT, created TEXT, updated TEXT)"""


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


def test_an_old_clip_log_gets_the_column_and_old_clips_have_no_headline(tmp_path) -> None:
    path = tmp_path / "klipp.db"
    db = sqlite3.connect(path)
    db.execute(OLD_CLIPS)
    db.execute("INSERT INTO clips (vod_id, profile, title, file, caption, status) "
               "VALUES ('1', 'p', 'Old', 'a.mp4', 'c', 'renderad')")
    db.commit()
    db.close()
    log = ClipLog(path)
    db = sqlite3.connect(path)
    assert "headline" in {row[1] for row in db.execute("PRAGMA table_info(clips)")}
    db.close()
    assert log.get(1)["headline"] is None


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


def test_finish_again_on_an_edited_clip_burns_its_headline(edit, settings, monkeypatch) -> None:
    client, log, clip_id = edit
    body = {"in_s": 1.0, "out_s": 7.0, "headline": "He jumped the gap"}
    assert client.post(f"/campaign/clips/{clip_id}/edit", json=body).status_code == 200
    titles = _titles(workflow, monkeypatch)
    _finish_again(settings, log, clip_id)
    assert titles == ["He jumped the gap"]  # titeln "No way" är också filnamnets, men headline är Robins egen
    clip = log.get(clip_id)
    assert (clip["title"], clip["headline"]) == ("No way", "He jumped the gap")
    assert clip["caption"] == build_caption("No way", _profile(settings), "Bodycam")


def test_finish_again_without_a_headline_keeps_the_file_name_rule(edit, settings, monkeypatch) -> None:
    client, log, clip_id = edit
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0}).status_code == 200
    titles = _titles(workflow, monkeypatch)
    _finish_again(settings, log, clip_id)
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"title": "Huge clutch"}).status_code == 200
    _finish_again(settings, log, clip_id)
    assert titles == ["", "Huge clutch"]  # "No way" är filnamnets rubrik och blir ingen text i bilden


# ---------- redigeraren ----------


def test_edit_info_has_the_saved_headline(edit) -> None:
    client, _, clip_id = edit
    assert client.get(f"/campaign/clips/{clip_id}/edit").json()["headline"] == ""
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"headline": "Big play"}).status_code == 200
    assert client.get(f"/campaign/clips/{clip_id}/edit").json()["headline"] == "Big play"


def test_edit_info_of_an_old_clip_has_an_empty_headline(edit, settings) -> None:
    _, log, clip_id = edit
    db = sqlite3.connect(settings.data_dir / "klipp.db")
    db.execute("UPDATE clips SET headline = NULL")
    db.commit()
    db.close()
    info = editor.edit_info(log.get(clip_id), _profile(settings), settings)
    assert info["headline"] == ""


def test_a_new_headline_is_burned_and_saved_and_the_caption_stays(edit, settings, monkeypatch) -> None:
    client, log, clip_id = edit
    _hook_on(settings)
    caption = log.get(clip_id)["caption"]
    titles = _titles(editor, monkeypatch)
    response = client.post(f"/campaign/clips/{clip_id}/edit", json={"headline": "  He jumped the gap "})
    assert response.status_code == 200, response.text
    assert titles == ["He jumped the gap"]
    clip = log.get(clip_id)
    assert (clip["title"], clip["headline"], clip["caption"]) == ("No way", "He jumped the gap", caption)
    assert response.json()["headline"] == "He jumped the gap"


def test_a_new_title_with_a_headline_only_builds_a_new_caption(edit, settings, monkeypatch) -> None:
    client, log, clip_id = edit
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


def test_an_empty_headline_puts_the_title_on_the_picture(edit, settings, monkeypatch) -> None:
    client, log, clip_id = edit
    _hook_on(settings)
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"headline": "He jumped the gap"}).status_code == 200
    titles = _titles(editor, monkeypatch)
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"headline": ""}).status_code == 200
    assert titles == ["No way"]
    assert log.get(clip_id)["headline"] == ""


def test_trim_and_new_words_keep_the_headline_on_the_picture(edit, settings, monkeypatch) -> None:
    client, log, clip_id = edit
    _words(settings)
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"headline": "He jumped the gap"}).status_code == 200
    titles = _titles(editor, monkeypatch)
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": 1.0, "out_s": 7.0}).status_code == 200
    fixed = [*WORDS[:2], {"start": 2.0, "end": 2.6, "word": "way!"}, WORDS[3]]
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"words": fixed}).status_code == 200
    assert titles == ["He jumped the gap", "He jumped the gap"]
    assert log.get(clip_id)["headline"] == "He jumped the gap"


def test_a_too_long_headline_is_refused(edit) -> None:
    client, log, clip_id = edit
    before = log.get(clip_id)
    assert client.post(f"/campaign/clips/{clip_id}/edit", json={"headline": "x" * 81}).status_code == 422
    assert log.get(clip_id) == before


def test_the_clip_json_has_the_headline(edit) -> None:
    client, _, clip_id = edit
    client.post(f"/campaign/clips/{clip_id}/edit", json={"headline": "Big play"})
    (clip,) = [c for c in client.get("/campaign/clips").json() if c["id"] == clip_id]
    assert clip["headline"] == "Big play"


def test_edit_clip_in_mcp_sends_the_headline(app) -> None:  # noqa: F811
    _call("edit_clip", {"clip_id": 7, "headline": "Big play"})
    (request,) = app.requests
    assert (request.url.path, json.loads(request.content)) == ("/campaign/clips/7/edit", {"headline": "Big play"})

