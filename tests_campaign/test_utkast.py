"""Utkast för egna videor: en bild som följer valen, ett helt klipp som förhandsvisning och Spara som använder
samma fil. Valen sparas, så att Redigera behåller spegling, filter, hastighet och klistermärke."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api, drafts, editor, editor_api, finish
from campaign.cliplog import ClipLog
from campaign.media import probe

from .conftest import make_clip, needs_ffmpeg, set_profile

TITLE = "Galet skott"
CHOICES = {"profile": "skiimasktimmy", "title": TITLE, "mirror": "true", "speed": "1.1", "filter": "warm",
           "sticker": "fire.png", "sticker_position": "top_right", "sticker_start_s": "1", "sticker_duration_s": "3"}


@pytest.fixture
def client(settings, monkeypatch):
    # ultrafast för farten, låg crf så att två kodningar inte ändrar bilden mer än förhandsbilden gör
    monkeypatch.setitem(finish.ENCODER_ARGS, "libx264", ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "12"])
    monkeypatch.delenv("CLIPSMITH_API_KEY", raising=False)
    # Rubriken bränns in (hook_text), ingen overlay: rubriken och klistermärket syns i bilden.
    set_profile(settings, clip={"min_seconds": 3, "max_seconds": 60}, active=True,
                on_screen_text={"text": "", "hook_text": True}, overlay={"file": None, "required": False})
    _png(settings.root / "stickers" / "fire.png")
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(editor_api.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    return TestClient(app)


def _png(path, size="400x400"):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=red:s={size},format=rgba",
                    "-frames:v", "1", str(path)], check=True)
    return path


def _boxed(path, seconds=4):
    """Grå video med en svart ruta till vänster: spegling flyttar rutan, men grått förblir grått."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "color=c=gray:s=1920x1080:r=30,drawbox=x=100:y=400:w=300:h=300:c=black:t=fill",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", str(seconds),
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
                   check=True)
    return path


def _draft(client, path) -> str:
    with path.open("rb") as file:
        answer = client.post("/campaign/uploads/drafts", files={"file": (path.name, file, "video/mp4")})
    assert answer.status_code == 201, answer.text
    return answer.json()["draft_id"]


def _frame(client, draft_id, t, **choices):
    answer = client.get(f"/campaign/uploads/drafts/{draft_id}/frame", params={**CHOICES, **choices, "t": t})
    assert answer.status_code == 200, answer.text
    assert answer.headers["content-type"] == "image/jpeg"
    return answer.content


def _nth(video, n, dest):
    """Bildruta nummer n. Inte på tid: efter hastigheten 1,1 ligger tidsstämplarna ojämnt."""
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vf", f"select=eq(n\\,{n})",
                    "-frames:v", "1", "-fps_mode", "passthrough", str(dest)], check=True)
    return dest


def _psnr(a, b, crop: str = "") -> float:
    """PSNR i dB mellan två bilder i RGB, med samma crop på båda. Lika bilder ger inf."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(a), "-i", str(b), "-lavfi",
                          f"[0:v]{crop}format=rgb24[x];[1:v]{crop}format=rgb24[y];[x][y]psnr", "-f", "null", "-"],
                         capture_output=True, text=True, check=True).stderr
    return float(re.search(r"PSNR .*average:(\S+)", out).group(1))


def _md5(path) -> str:
    return hashlib.md5(path.read_bytes() if hasattr(path, "read_bytes") else path).hexdigest()


def _count(monkeypatch) -> list:
    calls: list = []
    real = drafts.finish_clip
    monkeypatch.setattr(drafts, "finish_clip", lambda *a, **k: calls.append(k) or real(*a, **k))
    return calls


@needs_ffmpeg
def test_a_frame_is_the_same_picture_as_that_frame_in_the_finished_clip(client, tmp_path) -> None:
    draft_id = _draft(client, make_clip(tmp_path / "a.mp4", size="1920x1080", seconds=7))
    rendered = client.post(f"/campaign/uploads/drafts/{draft_id}/render", data={**CHOICES, "start_s": "2"})
    assert rendered.status_code == 200, rendered.text
    video = tmp_path / "klipp.mp4"
    video.write_bytes(client.get(rendered.json()["video_url"]).content)
    # Bildruta 45 har rubriken (råtid 1,5 s), bildruta 93 inte (råtid 3,1 s). Klistermärket syns i båda.
    for n in (45, 93):
        t = n / 30 / 1.1  # tiden i det färdiga klippet
        picture = tmp_path / f"bild_{n}.jpg"
        picture.write_bytes(_frame(client, draft_id, t, start_s="2"))
        assert _psnr(picture, _nth(video, n, tmp_path / f"ruta_{n}.png")) >= 40
    # Kontroll: t räknat som råtid (utan hastigheten) ger fel bild, med rubriken kvar.
    wrong = tmp_path / "fel.jpg"
    wrong.write_bytes(_frame(client, draft_id, 93 / 30 / 1.1, start_s="2", speed="1.0"))
    assert _psnr(wrong, tmp_path / "ruta_93.png") < 30


@needs_ffmpeg
def test_save_with_the_same_choices_keeps_the_rendered_file(client, settings, tmp_path, monkeypatch) -> None:
    calls = _count(monkeypatch)
    draft_id = _draft(client, make_clip(tmp_path / "Min video.mp4", size="1920x1080", seconds=4))
    rendered = client.post(f"/campaign/uploads/drafts/{draft_id}/render", data=CHOICES)
    assert rendered.status_code == 200, rendered.text
    body = rendered.json()
    assert body["video_url"] == f"/campaign/uploads/drafts/{draft_id}/video?v={body['choices_key']}"
    assert body["length_s"] == pytest.approx(4 / 1.1, abs=0.15)
    assert (body["problems"], TITLE in body["caption"]) == ([], True)
    preview = client.get(body["video_url"]).content
    saved = client.post(f"/campaign/uploads/drafts/{draft_id}/save", data=CHOICES)
    assert saved.status_code == 201, saved.text
    clip = saved.json()
    assert (clip["vod_id"], clip["title"], clip["status"]) == ("upload", TITLE, "renderad")
    stored = ClipLog(settings.data_dir / "klipp.db").get(clip["id"])
    assert _md5(preview) == _md5(open(stored["file"], "rb").read())  # samma fil, inte en ny rendering
    assert len(calls) == 1
    assert not (settings.out_dir / "upload" / ".utkast" / draft_id).exists()
    raw = settings.out_dir / "upload" / clip["filename"]
    assert raw.is_file()  # råklippet, som Redigera utgår från
    choices = json.loads((settings.out_dir / "upload" / "val" / f"{raw.stem}.json").read_text())
    assert (choices["mirror"], choices["speed"], choices["sticker"]) == (True, 1.1, "fire.png")
    # Kampanjens projekt för egna filer skapas när klippet sparas, inte när projekten läses.
    assert [p["profile"] for p in ClipLog(settings.data_dir / "klipp.db").projects()] == ["skiimasktimmy"]


@needs_ffmpeg
def test_a_save_that_fails_in_the_clip_log_leaves_no_clip_and_can_be_done_again(client, settings, tmp_path,
                                                                                monkeypatch) -> None:
    calls = _count(monkeypatch)
    draft_id = _draft(client, make_clip(tmp_path / "a.mp4", size="1920x1080", seconds=4))
    assert client.post(f"/campaign/uploads/drafts/{draft_id}/render", data=CHOICES).status_code == 200
    real = drafts.store

    def broken(log, plan, done, manifest):
        drafts.write_choices(done.parent.parent, done.stem, plan.saved())  # som store: valen före raden
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(drafts, "store", broken)
    with pytest.raises(sqlite3.OperationalError):
        client.post(f"/campaign/uploads/drafts/{draft_id}/save", data=CHOICES)
    folder = settings.out_dir / "upload"
    assert list((folder / "klara").glob("*")) == [] and list(folder.glob("*.mp4")) == []
    assert list((folder / "val").glob("*")) == []
    assert (folder / ".utkast" / draft_id / "render.mp4").is_file()
    monkeypatch.setattr(drafts, "store", real)
    saved = client.post(f"/campaign/uploads/drafts/{draft_id}/save", data=CHOICES)
    assert saved.status_code == 201, saved.text
    assert len(calls) == 1  # samma render, inte en ny
    assert len(ClipLog(settings.data_dir / "klipp.db").list()) == 1


@pytest.mark.parametrize(("out_s", "choices", "ok"), [
    (4.9, {"speed": 1.0}, True),  # 4,9 + 0,1 = 5,0 s: precis på taket
    (4.95, {"speed": 1.0}, False),  # 5,05 s: över, som utkastens kontroll och regelkontrollen
    (4.95, None, True),  # ett klipp utan val-fil blir inte längre än råklippet
])
def test_edit_counts_the_extra_tenth_for_an_own_video(out_s, choices, ok) -> None:
    campaign = types.SimpleNamespace(min_seconds=3, max_seconds=5)
    try:
        editor._check_trim({"duration_s": 6.0}, (0.0, out_s), campaign, drafts.EditLook(choices))
    except ValueError as error:
        assert not ok and "Kampanjen tillåter 3–5 s." in str(error), error
    else:
        assert ok


@needs_ffmpeg
def test_save_with_other_choices_makes_the_clip_again(client, settings, tmp_path, monkeypatch) -> None:
    calls = _count(monkeypatch)
    draft_id = _draft(client, make_clip(tmp_path / "a.mp4", size="1920x1080", seconds=4))
    assert client.post(f"/campaign/uploads/drafts/{draft_id}/render", data=CHOICES).status_code == 200
    saved = client.post(f"/campaign/uploads/drafts/{draft_id}/save", data={**CHOICES, "title": "Annan rubrik"})
    assert saved.status_code == 201, saved.text
    assert [c["title"] for c in calls] == [TITLE, "Annan rubrik"]
    assert saved.json()["title"] == "Annan rubrik"
    assert probe(ClipLog(settings.data_dir / "klipp.db").get(saved.json()["id"])["file"])["duration"] == \
        pytest.approx(4 / 1.1, abs=0.15)


@needs_ffmpeg
def test_with_mirror_the_title_reads_the_right_way(client, tmp_path) -> None:
    draft_id = _draft(client, _boxed(tmp_path / "ruta.mp4"))
    plain, mirrored = tmp_path / "rak.jpg", tmp_path / "spegel.jpg"
    plain.write_bytes(_frame(client, draft_id, 1.0, mirror="false", sticker=""))
    mirrored.write_bytes(_frame(client, draft_id, 1.0, mirror="true", sticker=""))
    title = "crop=1080:110:0:120,"  # rubriken: en rad från säkra zonen uppe (134 px)
    picture = "crop=540:220:0:860,"  # vänstra halvan där videons svarta ruta står
    assert _psnr(plain, mirrored, title) >= 45
    assert _psnr(plain, mirrored, picture) < 20
    untitled = tmp_path / "utan.jpg"  # kontroll: rubriken står verkligen på raden
    untitled.write_bytes(_frame(client, draft_id, 1.0, mirror="true", sticker="", title=""))
    assert _psnr(mirrored, untitled, title) < 30


def _edit(client, clip_id, **body):
    answer = client.post(f"/campaign/clips/{clip_id}/edit", json=body)
    assert answer.status_code == 200, answer.text
    return answer.json()


@needs_ffmpeg
def test_edit_keeps_mirror_speed_filter_and_sticker(client, settings, tmp_path) -> None:
    choices = {**CHOICES, "sticker_start_s": "0", "sticker_duration_s": "10"}  # klistermärket syns hela tiden
    draft_id = _draft(client, make_clip(tmp_path / "a.mp4", size="1920x1080", seconds=6))
    clip = client.post(f"/campaign/uploads/drafts/{draft_id}/save", data=choices).json()
    path = ClipLog(settings.data_dir / "klipp.db").get(clip["id"])["file"]
    before = _nth(path, 130, tmp_path / "fore.png")  # råtid 4,3 s: efter rubriken
    length = probe(path)["duration"]
    assert client.get(f"/campaign/clips/{clip['id']}/edit").json()["speed"] == 1.1  # webben räknar längden

    _edit(client, clip["id"], title="Ny rubrik")
    assert probe(path)["duration"] == pytest.approx(length, abs=0.05)
    assert _psnr(_nth(path, 130, tmp_path / "rubrik.png"), before) >= 40

    _edit(client, clip["id"], in_s=1.0)  # råklippet börjar 1 s (30 bildrutor) senare
    assert probe(path)["duration"] == pytest.approx(length - 1 / 1.1, abs=0.1)
    trimmed = _nth(path, 100, tmp_path / "trim.png")
    assert _psnr(trimmed, before) >= 35
    flipped = tmp_path / "vand.png"  # kontroll: utan spegling hade bilden varit den här
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(before), "-vf", "hflip", str(flipped)], check=True)
    assert _psnr(trimmed, flipped) < 25


@needs_ffmpeg
@pytest.mark.parametrize(("form", "text"), [
    ({}, "Klippet blir 7,1 s. Kampanjen tillåter högst 5 s."),
    ({"start_s": "1", "end_s": "6.5", "speed": "1"}, "Klippet blir 5,6 s. Kampanjen tillåter högst 5 s."),
    ({"start_s": "5"}, "Kampanjen kräver minst 3 s."),
])
def test_a_clip_outside_the_campaigns_length_is_refused_before_rendering(client, settings, tmp_path, monkeypatch,
                                                                         form, text) -> None:
    set_profile(settings, clip={"min_seconds": 3, "max_seconds": 5}, active=True,
                on_screen_text={"text": "", "hook_text": True}, overlay={"file": None, "required": False})
    calls = _count(monkeypatch)
    draft_id = _draft(client, make_clip(tmp_path / "a.mp4", size="1920x1080", seconds=7))
    _frame(client, draft_id, 1.0, **{"speed": "1.0", **form})  # bilden nekar aldrig för längden
    for step in ("render", "save"):
        answer = client.post(f"/campaign/uploads/drafts/{draft_id}/{step}",
                             data={**CHOICES, "speed": "1.0", **form})
        assert answer.status_code == 400, answer.text
        assert text in answer.json()["detail"]
    assert calls == []
    folder = settings.out_dir / "upload" / ".utkast" / draft_id
    assert [p.name for p in folder.glob("*.mp4")] == ["source.mp4"]  # inget råklipp heller
    assert ClipLog(settings.data_dir / "klipp.db").list() == []


@needs_ffmpeg
def test_an_old_upload_without_saved_choices_is_edited_as_before(client, settings, tmp_path, monkeypatch) -> None:
    with make_clip(tmp_path / "a.mp4", size="1920x1080", seconds=4).open("rb") as file:
        clip = client.post("/campaign/uploads", files={"file": ("a.mp4", file, "video/mp4")},
                           data={**CHOICES, "sticker": ""}).json()
    raw = settings.out_dir / "upload" / clip["filename"]
    saved = settings.out_dir / "upload" / "val" / f"{raw.stem}.json"
    assert json.loads(saved.read_text())["mirror"] is True  # POST /uploads sparar också valen
    saved.unlink()  # som ett klipp från före den här ändringen
    assert client.get(f"/campaign/clips/{clip['id']}/edit").json()["speed"] == 1.0
    calls: list = []
    real = editor.finish_clip
    monkeypatch.setattr(editor, "finish_clip", lambda *a, **k: calls.append(k) or real(*a, **k))
    _edit(client, clip["id"], title="Ny rubrik")
    (call,) = calls
    assert (call.get("look", ""), call.get("speed", 1.0), call.get("sticker")) == ("", 1.0, None)
    path = ClipLog(settings.data_dir / "klipp.db").get(clip["id"])["file"]
    assert probe(path)["duration"] == pytest.approx(probe(raw)["duration"], abs=0.1)  # utan hastigheten


@needs_ffmpeg
def test_a_draft_can_be_played_deleted_and_old_ones_go_away(client, settings, tmp_path) -> None:
    source = make_clip(tmp_path / "a.mp4", size="1920x1080", seconds=4)
    with source.open("rb") as file:
        made = client.post("/campaign/uploads/drafts", files={"file": ("a.mp4", file, "video/mp4")}).json()
    assert {k: made[k] for k in ("name", "width", "height", "has_audio")} == {
        "name": "a.mp4", "width": 1920, "height": 1080, "has_audio": True}
    assert made["duration_s"] == pytest.approx(4, abs=0.1)
    folder = settings.out_dir / "upload" / ".utkast" / made["draft_id"]
    assert client.get(f"/campaign/uploads/drafts/{made['draft_id']}/video").status_code == 404  # inget renderat
    assert client.post(f"/campaign/uploads/drafts/{made['draft_id']}/render", data=CHOICES).status_code == 200
    part = client.get(f"/campaign/uploads/drafts/{made['draft_id']}/video", headers={"Range": "bytes=0-99"})
    assert (part.status_code, len(part.content)) == (206, 100)  # går att spola i
    old = time.time() - 25 * 3600
    for path in (folder, *folder.iterdir()):
        os.utime(path, (old, old))
    newer = _draft(client, source)
    assert not folder.exists()  # äldre än 24 timmar: borta när ett nytt utkast görs
    assert client.delete(f"/campaign/uploads/drafts/{newer}").status_code == 204
    assert not (settings.out_dir / "upload" / ".utkast" / newer).exists()


@pytest.mark.parametrize("draft_id", ["..", "ABC", "x" * 32, "0" * 31 + "/"])
def test_a_draft_id_is_never_a_path(client, draft_id) -> None:
    answer = client.get(f"/campaign/uploads/drafts/{draft_id}/frame", params={**CHOICES, "t": 0})
    assert answer.status_code == 404


def test_a_draft_takes_only_video_files(client, tmp_path) -> None:
    answer = client.post("/campaign/uploads/drafts", files={"file": ("x.txt", b"x", "text/plain")})
    assert answer.status_code == 400 and "Filtypen" in answer.json()["detail"]


@pytest.mark.parametrize("t", ["nan", "inf", "-inf"])
def test_a_time_that_is_not_a_number_is_400(client, t) -> None:
    answer = client.get(f"/campaign/uploads/drafts/{'0' * 32}/frame", params={**CHOICES, "t": t})
    assert answer.status_code == 400 and "Tiden" in answer.json()["detail"], answer.text


def test_writing_to_drafts_needs_the_api_key(client, monkeypatch) -> None:
    monkeypatch.setenv("CLIPSMITH_API_KEY", "hemlig")
    draft = f"/campaign/uploads/drafts/{'0' * 32}"
    calls = [("post", "/campaign/uploads/drafts", {"files": {"file": ("a.mp4", b"x", "video/mp4")}}),
             ("post", f"{draft}/render", {"data": CHOICES}), ("post", f"{draft}/save", {"data": CHOICES}),
             ("delete", draft, {})]
    for method, url, body in calls:
        assert getattr(client, method)(url, **body).status_code == 401, url
    assert client.delete(draft, headers={"X-Api-Key": "hemlig"}).status_code == 204  # kontroll: med nyckeln


@needs_ffmpeg
def test_a_sticker_after_the_new_end_moves_back_inside_the_clip(client, settings) -> None:
    choices = drafts.Choices(profile="skiimasktimmy", speed=1.1, sticker="fire.png", sticker_start_s=4,
                             sticker_duration_s=1).model_dump()
    look = drafts.EditLook(choices).titled("a", "a")
    placed = look.args(settings, api._profile("skiimasktimmy", settings), 3.0)["sticker"]  # kortat till 3 s
    assert (placed["start_s"], placed["end_s"]) == (2.5, 3.5)
    assert look.args(settings, api._profile("skiimasktimmy", settings), 6.0)["sticker"]["start_s"] == 4


@needs_ffmpeg
def test_the_sticker_error_has_a_decimal_comma(client, settings) -> None:
    with pytest.raises(drafts.VersionError, match="mellan 0 och 3,5 s"):
        drafts.versions._sticker(settings, api._profile("skiimasktimmy", settings),
                                 {"file": "fire.png", "start_s": 4, "duration_s": 1}, 3.5)


@needs_ffmpeg
def test_a_draft_in_use_is_not_cleaned_away(client, settings, tmp_path) -> None:
    source = make_clip(tmp_path / "a.mp4", size="1920x1080", seconds=4)
    draft_id = _draft(client, source)
    folder = settings.out_dir / "upload" / ".utkast" / draft_id
    old = time.time() - 25 * 3600
    for path in (folder, *folder.iterdir()):
        os.utime(path, (old, old))
    _frame(client, draft_id, 1.0)  # Robin tittar på utkastet
    _draft(client, source)
    assert folder.exists()
