"""Egna videor: en uppladdad video görs om till 9:16 med spegling, filter och hastighet och blir ett klipp
som Robin godkänner innan det hamnar i Att posta."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api, finish
from campaign.cliplog import ClipLog
from campaign.media import probe

from .conftest import make_clip, make_overlay, needs_ffmpeg, set_profile


@pytest.fixture
def client(settings, monkeypatch):
    monkeypatch.setitem(finish.ENCODER_ARGS, "libx264", ["-c:v", "libx264", "-preset", "ultrafast"])
    monkeypatch.delenv("CLIPSMITH_API_KEY", raising=False)
    set_profile(settings, clip={"min_seconds": 3, "max_seconds": 60}, active=True)
    make_overlay(settings.root / "overlays" / "skiimasktimmy.png")
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    return TestClient(app)


def _upload(client, path, **form):
    with path.open("rb") as file:
        return client.post("/campaign/uploads", files={"file": (path.name, file, "video/mp4")},
                           data={"profile": "skiimasktimmy", **form})


@needs_ffmpeg
def test_a_landscape_video_becomes_a_vertical_clip_to_review(client, settings, tmp_path) -> None:
    source = make_clip(tmp_path / "Min video.mov", size="1920x1080", seconds=6)
    answer = _upload(client, source, title="Galet skott", mirror="true", speed="1.2", filter="warm")
    assert answer.status_code == 201, answer.text
    clip = answer.json()
    assert (clip["vod_id"], clip["title"], clip["status"]) == ("upload", "Galet skott", "renderad")
    assert "Galet skott" in clip["caption"]  # kampanjens mall
    stored = ClipLog(settings.data_dir / "klipp.db").get(clip["id"])
    info = probe(stored["file"])
    assert (info["width"], info["height"]) == (1080, 1920)
    assert info["duration"] == pytest.approx(6 / 1.2, abs=0.15)
    assert stored["file"].startswith(str(settings.out_dir / "upload" / "klara"))
    assert client.get(f"/campaign/clips/{clip['id']}/file").status_code == 200


@needs_ffmpeg
def test_a_draft_without_speed_and_filter_starts_at_1x_without_filter(client, settings, tmp_path) -> None:
    from campaign import drafts

    source = make_clip(tmp_path / "a.mp4", seconds=4)
    with source.open("rb") as file:
        answer = client.post("/campaign/uploads/drafts", files={"file": ("a.mp4", file, "video/mp4")})
    url, form = f"/campaign/uploads/drafts/{answer.json()['draft_id']}", {"profile": "skiimasktimmy"}
    state = client.post(f"{url}/render", data=form).json()
    assert state["length_s"] == pytest.approx(probe(source)["duration"] + drafts.EXTRA_S, abs=0.05)

    def picture(**change) -> bytes:
        return client.get(f"{url}/frame", params={**form, "t": 1, **change}).content

    assert picture() == picture(filter="none") != picture(filter="fresh")
    saved = client.post(f"{url}/save", data=form).json()
    choices = json.loads((settings.out_dir / "upload" / "val" / f"{saved['filename'][:-4]}.json").read_text())
    assert (choices["speed"], choices["filter"]) == (1.0, "none")


@needs_ffmpeg
def test_two_uploads_with_the_same_name_get_their_own_files(client, tmp_path, monkeypatch) -> None:
    from campaign import drafts

    hooks: list = []
    real = drafts.finish_clip
    monkeypatch.setattr(drafts, "finish_clip", lambda *a, **k: hooks.append(k["title"]) or real(*a, **k))
    source = make_clip(tmp_path / "a.mp4", seconds=4)
    first, second = _upload(client, source).json(), _upload(client, source, title="Wow").json()
    assert first["filename"] != second["filename"]
    assert first["title"] == "a"  # utan rubrik: filens namn i loggen
    assert hooks == ["", "Wow"]  # men bara en skriven rubrik bränns in


@needs_ffmpeg
@pytest.mark.parametrize(("form", "text"), [
    ({"profile": "finns-inte"}, "Kampanjen"),
    ({"speed": "1.33"}, "Hastigheten 1,33 går inte. Välj 1, 1,05, 1,1, 1,15, 1,2."),
    ({"filter": "nope"}, "Filtret"),
])
def test_bad_choices_are_refused_and_nothing_is_saved(client, settings, tmp_path, form, text) -> None:
    answer = _upload(client, make_clip(tmp_path / "a.mp4", seconds=4), **form)
    assert answer.status_code == 400 and text in answer.json()["detail"], answer.text
    assert ClipLog(settings.data_dir / "klipp.db").list() == []
    assert not list((settings.out_dir / "upload").rglob("*.*"))


@needs_ffmpeg
def test_a_file_that_is_not_a_video_is_refused(client, settings, tmp_path) -> None:
    bad = tmp_path / "x.mp4"
    bad.write_bytes(b"inte en video")
    answer = _upload(client, bad)
    assert answer.status_code == 400 and "video" in answer.json()["detail"]
    assert ClipLog(settings.data_dir / "klipp.db").list() == []
    assert not list((settings.out_dir / "upload").rglob("*.*"))


@needs_ffmpeg
@pytest.mark.parametrize("name", ["x.exe", "x.txt", "../x.mp4.sh"])
def test_only_video_file_types_are_taken(client, tmp_path, name) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"x")
    answer = client.post("/campaign/uploads", files={"file": (name, path.read_bytes(), "video/mp4")},
                         data={"profile": "skiimasktimmy"})
    assert answer.status_code == 400 and "Filtypen" in answer.json()["detail"]


# ---------- Hela bilden, font, klipp i början, overlay och klistermärke ----------

import subprocess  # noqa: E402


def _edges(path, size="1920x1080", seconds=6):
    """Grön video med en röd rand längst till vänster: syns randen kommer hela bilden med."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"color=c=green:s={size}:r=30,drawbox=x=0:y=0:w=200:h=ih:c=red:t=fill",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", str(seconds),
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
                   check=True)
    return path


def _pixel(path, x, y, at=1.0):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path), "-vf", f"format=rgb24,crop=1:1:{x}:{y}",
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    return tuple(raw[:3])


def _png(path, size="400x400"):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=red:s={size},format=rgba",
                    "-frames:v", "1", str(path)], check=True)
    return path


@pytest.fixture
def extras(settings, monkeypatch):
    """En font, två klistermärken och en egen overlay att välja."""
    from campaign import versions

    fonts = settings.root / "fonts"
    fonts.mkdir()
    (fonts / "Impact.ttf").write_bytes(b"ttf")
    monkeypatch.setattr(versions, "FONT_DIRS", (fonts,))
    _png(settings.root / "stickers" / "fire.png")
    _png(settings.root / "stickers" / "tall.png", "320x1200")  # i bredd 320 × 1200: in i safe_bottom
    make_overlay(settings.root / "overlays" / "egen.png")
    return settings


def _spy(monkeypatch) -> list:
    from campaign import drafts

    seen: list = []
    real = drafts.finish_clip

    def spy(*args, **kwargs):
        seen.append(kwargs)
        return real(*args, **{**kwargs, "font_file": None})  # testfonten är ingen riktig font

    monkeypatch.setattr(drafts, "finish_clip", spy)
    return seen


@needs_ffmpeg
@pytest.mark.parametrize(("form", "whole"), [({}, True), ({"framing": "fill"}, False), ({"framing": "fit"}, True)])
def test_the_whole_picture_is_the_default_and_fill_still_crops_it(client, settings, tmp_path, form, whole) -> None:
    answer = _upload(client, _edges(tmp_path / "bred.mp4"), **form)
    assert answer.status_code == 201, answer.text
    out = ClipLog(settings.data_dir / "klipp.db").get(answer.json()["id"])["file"]
    red, green, _ = _pixel(out, 20, 960)  # vänsterkanten mitt i bilden
    assert (red > green) is whole, (red, green)
    if whole:  # ovanför bilden: den suddiga kopian, inga svarta kanter
        assert sum(_pixel(out, 540, 150)) > 120


@needs_ffmpeg
def test_fit_keeps_a_portrait_video_inside_the_frame(client, settings, tmp_path) -> None:
    answer = _upload(client, _edges(tmp_path / "hog.mp4", size="1080x2400"), framing="fit")
    assert answer.status_code == 201, answer.text
    out = ClipLog(settings.data_dir / "klipp.db").get(answer.json()["id"])["file"]
    info = probe(out)
    assert (info["width"], info["height"]) == (1080, 1920)
    red, green, _ = _pixel(out, 100, 960)  # randen (200 av 1080) krymper till 172 px men finns kvar
    assert red > green, (red, green)


@needs_ffmpeg
def test_font_cut_own_overlay_and_sticker_are_used(client, extras, tmp_path, monkeypatch) -> None:
    set_profile(extras, clip={"min_seconds": 3, "max_seconds": 60}, active=True,
                overlay={"file": "overlays/skiimasktimmy.png", "required": False})
    seen = _spy(monkeypatch)
    answer = _upload(client, make_clip(tmp_path / "a.mp4", seconds=8), font="Impact", start_cut_s="2",
                     overlay="overlays/egen.png", sticker="fire.png", sticker_position="top_right",
                     sticker_start_s="1", sticker_duration_s="3")
    assert answer.status_code == 201, answer.text
    (call,) = seen
    assert call["font_file"] == str(extras.root / "fonts" / "Impact.ttf")
    assert call["overlay"] == (extras.root / "overlays" / "egen.png").resolve()
    assert (call["sticker"]["x"], call["sticker"]["y"], call["sticker"]["end_s"]) == (720, 135, 4.0)
    out = ClipLog(extras.data_dir / "klipp.db").get(answer.json()["id"])["file"]
    assert probe(out)["duration"] == pytest.approx(6, abs=0.15)  # 8 s minus 2 s i början


@needs_ffmpeg
def test_without_new_choices_the_upload_is_made_as_before(client, extras, tmp_path, monkeypatch) -> None:
    seen = _spy(monkeypatch)
    assert _upload(client, make_clip(tmp_path / "a.mp4", seconds=4)).status_code == 201
    (call,) = seen
    assert (call["font_file"], call["overlay"], call["sticker"]) == (None, None, None)


@needs_ffmpeg
@pytest.mark.parametrize(("form", "text"), [
    ({"framing": "zoom"}, "Bildläget"),
    ({"font": "Wingdings"}, "Fonten"),
    ({"start_cut_s": "6"}, "Klipp i början"),
    ({"start_cut_s": "-1"}, "Klipp i början"),
    ({"start_cut_s": "1.5"}, "Kampanjen kräver minst 3 s"),  # 4 s minus 1,5 s blir 2,5 s
    ({"overlay": "overlays/egen.png"}, "kräver sin egen overlay"),
    ({"sticker": "../overlays/egen.png"}, "stickers/"),
    ({"sticker": "fire.png", "sticker_duration_s": "11"}, "0,5–10"),
    ({"sticker": "fire.png", "sticker_start_s": "5"}, "inom klippet"),
    ({"sticker": "tall.png", "sticker_position": "center"}, "säkra zon"),
    ({"sticker": "fire.png", "sticker_position": "upp"}, "Platsen"),
])
def test_new_choices_that_break_the_campaigns_rules_are_refused(client, extras, tmp_path, form, text) -> None:
    answer = _upload(client, make_clip(tmp_path / "a.mp4", seconds=4), **form)
    assert answer.status_code == 400 and text in answer.json()["detail"], answer.text
    assert ClipLog(extras.data_dir / "klipp.db").list() == []
    assert not list((extras.out_dir / "upload").rglob("*.*"))


def test_the_options_follow_the_campaign(client, extras) -> None:
    options = client.get("/campaign/uploads/options", params={"profile": "skiimasktimmy"})
    assert options.status_code == 200, options.text
    assert options.json() == {
        "fonts": [{"name": "Impact", "label": "Impact"}],
        "stickers": [{"file": "fire.png", "url": "/campaign/stickers/fire.png"},
                     {"file": "tall.png", "url": "/campaign/stickers/tall.png"}],
        "overlays": [{"file": "overlays/egen.png", "url": "/campaign/overlays/egen.png"},
                     {"file": "overlays/skiimasktimmy.png", "url": "/campaign/overlays/skiimasktimmy.png"}],
        "overlay_locked": True,  # Timmys overlay krävs
        "max_start_cut_s": 5.0,
        "min_seconds": 3.0,
        "max_seconds": 60.0,  # kampanjens tak, som tidslinjen visar
        "safe_zone": {"top": 0.07, "bottom": 0.2},  # andelar av höjden, samma som profilen
        "hook_text": False,  # Timmy kräver sin text, så rubriken bränns inte in
        "framings": ["fill", "fit"],
        # Kampanjens regel för material, så att Robin ser den under väljaren.
        "source_allowed": "Bara SkiiMaskTimmys Twitch-streams: live eller tidigare sändningar (VOD).",
        "music": [],  # ingen musikmapp än (test_ljud)
        "audio_rules": [],  # Timmys regler säger inget om ljud
    }
    assert client.get("/campaign/uploads/options", params={"profile": "finns-inte"}).status_code == 400


@needs_ffmpeg
def test_without_a_title_the_caption_gets_no_file_name_and_can_be_posted(client, settings, tmp_path) -> None:
    clip = _upload(client, make_clip(tmp_path / "IMG_4821.mov", seconds=4)).json()
    assert "IMG_4821" not in clip["caption"], clip["caption"]
    assert clip["caption"].startswith("👉 Catch him live: twitch.tv/skiimasktimmy"), clip["caption"]
    assert (clip["title"], clip["status"], clip["problems"]) == ("IMG_4821", "renderad", [])  # namnet bara i listan


@needs_ffmpeg
def test_a_sticker_at_the_top_goes_below_the_burned_in_title(client, extras, tmp_path, monkeypatch) -> None:
    set_profile(extras, clip={"min_seconds": 3, "max_seconds": 60}, active=True,
                on_screen_text={"text": "", "hook_text": True}, overlay={"file": None, "required": False})
    seen = _spy(monkeypatch)
    answer = _upload(client, make_clip(tmp_path / "a.mp4", seconds=4), title="Galet skott", sticker="fire.png",
                     sticker_position="top_left")
    assert answer.status_code == 201, answer.text
    (call,) = seen
    assert (call["sticker"]["x"], call["sticker"]["y"]) == (40, 134 + 80 + 20)  # rubriken: en rad, 80 px


@needs_ffmpeg
def test_a_too_short_video_says_what_to_do_with_a_decimal_comma(client, tmp_path) -> None:
    clip = _upload(client, make_clip(tmp_path / "kort.mp4", seconds=2), speed="1.2").json()
    assert clip["status"] == "underkand"
    [problem] = clip["problems"]
    assert problem.startswith("Klippet är 1,7 s.")  # svenskt talformat, inte 1.7
    assert problem.endswith("Ladda upp en längre video eller tryck Nej.")
