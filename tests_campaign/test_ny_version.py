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



def test_mirror_and_filter_go_before_the_subtitles_and_speed_goes_last() -> None:
    profile = Profile(name="x", streamer="y", hook_text=False)
    graph = finish.build_filter(profile, True, True, None, subtitles=Path("/tmp/a.ass"),
                                look=",hflip,eq=brightness=0.04", speed=1.1)
    assert "[0:v]setsar=1,hflip,eq=brightness=0.04,subtitles=" in graph  # texten speglas inte
    assert "format=yuv420p,setpts=PTS/1.1[vout]" in graph
    assert "LRA=11,atempo=1.1[aout]" in graph
    assert finish.build_filter(profile, True, True, None) == finish.build_filter(profile, True, True, None,
                                                                                look="", speed=1.0)


def test_the_look_is_built_from_mirror_filter_brightness_and_saturation() -> None:
    from campaign import versions

    assert versions.look() == ""
    assert versions.look(mirror=True, filter="fresh") == (
        ",hflip,eq=brightness=0.04:saturation=1.08:contrast=1.03")
    assert versions.look(brightness=0.1, saturation=-0.2) == ",eq=brightness=0.100:saturation=0.800"

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


def test_a_sticker_at_the_top_goes_below_the_text_up_there() -> None:
    from campaign import versions

    assert versions.sticker_box("top_right", 320, 200, _profile(), text_end=214) == (720, 234)  # 20 px under
    assert versions.sticker_box("bottom_left", 320, 200, _profile(), text_end=214) == (40, 1336)  # nere: som förut
    with pytest.raises(versions.VersionError, match="täcker"):
        versions.sticker_box("center", 320, 1152, _profile(), text_end=500)  # börjar vid 384, texten slutar 500


def test_the_text_at_the_top_ends_below_the_hook_and_the_campaigns_text(tmp_path) -> None:
    # 0,07 × 1920 = 134. En rad hook är 64 × 1,25 = 80 px. Kampanjens text uppe tar 46 × 1,6 = 73 px.
    hook = _profile(hook_text=True)
    assert finish.top_text_end(hook, tmp_path, "Galet skott") == 134 + 80
    assert finish.top_text_end(hook, tmp_path, "Ett galet skott från andra sidan banan") == 134 + 160  # två rader
    text = _profile(hook_text=True, text="twitch.tv/x")
    assert finish.top_text_end(text, tmp_path, "Galet skott") == 134 + 73 + 80
    assert finish.top_text_end(text, tmp_path, "") == 134 + 73
    overlay = make_overlay(tmp_path / "ov.png")
    assert finish.top_text_end(text, tmp_path, "Galet skott", overlay) == 134 + 80  # overlayen har kanalens text
    assert finish.top_text_end(_profile(hook_text=False), tmp_path, "Galet skott") == 0
    assert "y=207:" in finish.build_filter(text, False, True, None, title="Galet skott")  # där hooken ritas


def test_in_zoom_a_sticker_at_the_top_goes_below_the_lower_title(tmp_path) -> None:
    from campaign import versions
    from campaign.config import Settings

    # zoom: rubriken står ovanför bilden, 416 - 24 - 64 = 328, och slutar en rad (80 px) senare.
    hook = _profile(hook_text=True)
    assert "y=328:" in finish.build_filter(hook, False, True, None, title="Galet skott", frame="zoom")
    assert finish.top_text_end(hook, tmp_path, "Galet skott", frame="zoom") == 328 + 80
    assert finish.top_text_end(hook, tmp_path, "Galet skott") == 134 + 80  # utan zoom: som förut
    sticker = tmp_path / "stickers" / "fire.png"
    sticker.parent.mkdir()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=400x400,format=rgba",
                    "-frames:v", "1", str(sticker)], check=True)
    placed = versions._sticker(Settings(root=tmp_path), hook, {"file": "fire.png", "position": "top_left",
                                                               "start_s": 0, "duration_s": 2}, 5.0, "Galet skott",
                               frame="zoom")
    assert (placed["x"], placed["y"]) == (40, 328 + 80 + 20)


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


# ---------- 3. POST versions, GET options och klistermärken ----------

import json  # noqa: E402
import subprocess  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from campaign import api, editor_api, subtitles  # noqa: E402
from campaign.workflow import finish_vod  # noqa: E402
from clipsmith.settings import CaptionConfig, ReframeConfig  # noqa: E402

from .conftest import make_clip, make_overlay, needs_ffmpeg, needs_subtitles, set_profile  # noqa: E402

RAW = "clip_01_no_way.mp4"
WORDS = [{"start": 0.5, "end": 0.9, "word": "Bro,"}, {"start": 1.0, "end": 1.5, "word": "no"},
         {"start": 2.0, "end": 2.6, "word": "wei"}, {"start": 6.0, "end": 6.5, "word": "wow"}]
NO_WORDS = "Ny version kräver ett klipp som appen har skurit med ordfil."


def _png(path: Path, size: str = "400x400") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=red:s={size},format=rgba",
                    "-frames:v", "1", str(path)], check=True)
    return path


@pytest.fixture
def vclip(settings, monkeypatch):
    """Timmy med två TikTok-konton och ett godkänt klipp på 10 s med ordfil, kampanjklart."""
    from campaign import versions

    monkeypatch.setitem(finish.ENCODER_ARGS, "libx264", ["-c:v", "libx264", "-preset", "ultrafast"])
    fonts = settings.root / "fonts"
    fonts.mkdir()
    (fonts / "Impact.ttf").write_bytes(b"ttf")
    monkeypatch.setattr(versions, "FONT_DIRS", (fonts,))
    set_profile(settings, clip={"min_seconds": 3, "max_seconds": 60}, accounts={"tiktok": ["openA", "openB"]},
                active=True)  # kopian i testmappen; Robins active: false döljer annars klippen i listan
    make_overlay(settings.root / "overlays" / "skiimasktimmy.png")
    _png(settings.root / "stickers" / "fire.png")
    raw = make_clip(settings.out_dir / "123" / RAW, seconds=10)
    subtitles.save_words(raw, WORDS, CaptionConfig(enabled=True, style="box"), ReframeConfig(mode="center"), raw)
    log = ClipLog(settings.data_dir / "klipp.db")
    log.add_run(1, "123", "skiimasktimmy", "Bodycam")
    (result,) = finish_vod("123", load_profile("skiimasktimmy", settings.profiles_dir), settings, log,
                           game="Bodycam", review=False)
    log.update(result["id"], status="godkand", format="funny", problems=[])
    monkeypatch.delenv("CLIPSMITH_API_KEY", raising=False)
    app = FastAPI()
    app.state.active_run_id = None
    app.include_router(api.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    return TestClient(app), log, result["id"]


def _options(client, clip_id, account="openB"):
    return client.get(f"/campaign/clips/{clip_id}/versions/options",
                      params={"platform": "tiktok", "account": account}).json()


def _make(client, clip_id, **body):
    return client.post(f"/campaign/clips/{clip_id}/versions",
                       json={"platform": "tiktok", "account": "openB", "font": None, "start_cut_s": 0,
                             "overlay": None, "sticker": None, **body})


def _spy(monkeypatch) -> list:
    from campaign import versions

    seen: list = []
    real = versions.finish_clip

    def spy(*args, **kwargs):
        ass = kwargs.get("subtitles")
        seen.append({**kwargs, "ass": ass and ass.read_text(encoding="utf-8-sig")})
        return real(*args, **kwargs)

    monkeypatch.setattr(versions, "finish_clip", spy)
    return seen


def _trimmed(client, log, clip_id, span: tuple[float, float] = (4.0, 10.0)) -> None:
    """Trimma originalet till span (4–10 s) i redigeraren, så att originalfilen är längre än klippet. Godkänt
    igen."""
    client.app.include_router(editor_api.router)
    answer = client.post(f"/campaign/clips/{clip_id}/edit", json={"in_s": span[0], "out_s": span[1]})
    assert answer.status_code == 200, answer.text
    log.update(clip_id, status="godkand", problems=[])


def _length(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(path)], capture_output=True, text=True, check=True).stdout)


def _hook_on(settings) -> None:
    set_profile(settings, on_screen_text={"text": "twitch.tv/skiimasktimmy", "required": True, "hook_text": True})


@needs_ffmpeg
def test_the_options_list_fonts_stickers_overlays_and_the_accounts_style(vclip, settings) -> None:
    client, _, clip_id = vclip
    options = _options(client, clip_id)
    assert (options["allowed"], options["reason"]) == (True, None)
    assert options["fonts"] == [{"name": "Impact", "label": "Impact"}]
    assert options["stickers"] == [{"file": "fire.png", "url": "/campaign/stickers/fire.png"}]
    assert options["overlays"] == [{"file": "overlays/skiimasktimmy.png",
                                    "url": "/campaign/overlays/skiimasktimmy.png"}]
    assert options["overlay_locked"] is True  # Timmys overlay krävs
    assert options["max_start_cut_s"] == 7.0  # 10 s - 3 s, ingen spärr på 5 s
    assert options["defaults"] == {"font": "Arial", "overlay": None, "sticker": None, "start_cut_s": 0.0}
    set_profile(settings, accounts_style={"openB": {"font": "Impact", "start_cut_s": 2}})
    assert _options(client, clip_id)["defaults"]["start_cut_s"] == 2.0
    assert _options(client, clip_id)["defaults"]["font"] == "Impact"
    set_profile(settings, clip={"min_seconds": 7, "max_seconds": 60})
    assert _options(client, clip_id)["max_start_cut_s"] == 3.0  # 10 s - 7 s


@needs_ffmpeg
def test_a_campaign_with_one_account_needs_no_version(vclip, settings) -> None:
    client, _, clip_id = vclip
    set_profile(settings, accounts={"tiktok": ["openA"]})  # som Blizzard
    reason = "Kampanjen har bara ett konto på TikTok. Ny version behövs inte."
    assert (_options(client, clip_id)["allowed"], _options(client, clip_id)["reason"]) == (False, reason)
    answer = _make(client, clip_id)
    assert (answer.status_code, answer.json()["detail"]) == (400, reason)


@needs_ffmpeg
def test_an_account_that_already_has_the_clip_gets_no_version(vclip) -> None:
    client, log, clip_id = vclip
    log.add_post(clip_id, "tiktok", "https://www.tiktok.com/@a/video/1", account="openA")
    assert _options(client, clip_id, "openA")["allowed"] is False
    assert _make(client, clip_id, account="openA").status_code == 409
    assert _make(client, clip_id, account="okänt").status_code == 400  # inte kampanjens konto


@needs_subtitles
def test_a_version_cuts_the_start_shifts_the_words_and_changes_only_its_own_font(vclip, settings,
                                                                                    monkeypatch) -> None:
    client, log, clip_id = vclip
    raw, done = settings.out_dir / "123" / RAW, settings.out_dir / "123" / "klara" / RAW
    before = (done.stat().st_mtime_ns, raw.stat().st_mtime_ns, subtitles.words_path(raw).read_bytes())
    seen = _spy(monkeypatch)
    answer = _make(client, clip_id, font="Impact", start_cut_s=1.2,
                   sticker={"file": "fire.png", "position": "top_right", "start_s": 0, "duration_s": 3})
    assert answer.status_code == 201, answer.text
    version = answer.json()
    assert (version["version_of"], version["filename"], version["title"]) == (clip_id, "clip_01_no_way.v1.mp4",
                                                                            log.get(clip_id)["title"])
    assert version["status"] in ("renderad", "underkand")
    (call,) = seen
    assert "Style: Box,Impact," in call["ass"]
    assert "BRO" not in call["ass"] and "Dialogue: 0,0:00:00.80," in call["ass"]  # wei vid 2,0 s blir 0,8 s
    assert call["font_file"] == str(settings.root / "fonts" / "Impact.ttf")
    assert (call["sticker"]["x"], call["sticker"]["y"], call["sticker"]["end_s"]) == (720, 135, 3.0)
    out = settings.out_dir / "123" / "klara" / "clip_01_no_way.v1.mp4"
    length = float(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                                   "stream=duration", "-of", "csv=p=0", str(out)],
                                  capture_output=True, text=True, check=True).stdout)
    assert length == pytest.approx(10.0 - 1.2, abs=1 / 30 + 0.001)
    assert (done.stat().st_mtime_ns, raw.stat().st_mtime_ns, subtitles.words_path(raw).read_bytes()) == before
    assert json.loads(subtitles.words_path(raw).read_text())["caption"]["font"] == "Arial"
    assert not [p for p in out.parent.parent.rglob(".*")]  # inga tillfälliga filer kvar
    assert _make(client, clip_id).json()["filename"] == "clip_01_no_way.v2.mp4"
    listed = {c["id"]: c["version_of"] for c in client.get("/campaign/clips").json()}
    assert listed[clip_id] is None and listed[version["id"]] == clip_id


@needs_ffmpeg
@pytest.mark.parametrize(("body", "text"), [
    ({"start_cut_s": 7.5}, "minst 3"),  # 10 s - 7,5 s = 2,5 s
    ({"start_cut_s": -1}, "originalfilen"),  # klippet är inte trimmat: inget finns före
    ({"hook": "Två\nrader"}, "styrtecken"),
    ({"hook": "THIS HOOK IS FAR TOO LONG TO FIT IN THREE SHORT LINES AT THE TOP OF THE CLIP"},
     "Gör den kortare"),  # fyra rader i Montserrat
    ({"font": "Wingdings"}, "Fonten"),
    ({"overlay": "overlays/annan.png"}, "kräver sin egen overlay"),
    ({"sticker": {"file": "../overlays/skiimasktimmy.png", "position": "center", "start_s": 0, "duration_s": 2}},
     "stickers/"),
    ({"sticker": {"file": "fire.png", "position": "center", "start_s": 0, "duration_s": 0.2}}, "0,5–10"),
    ({"sticker": {"file": "fire.png", "position": "center", "start_s": 0, "duration_s": 11}}, "0,5–10"),
    ({"sticker": {"file": "fire.png", "position": "center", "start_s": 20, "duration_s": 2}}, "inom klippet"),
    ({"sticker": {"file": "tall.png", "position": "center", "start_s": 0, "duration_s": 2}}, "säkra zon"),
    ({"speed": 1.33}, "Hastigheten"),
    ({"filter": "nope"}, "Filtret"),
    ({"brightness": 0.5}, "Ljusstyrkan"),
    ({"saturation": -0.9}, "Mättnaden"),
])
def test_bad_choices_are_refused_and_nothing_is_made(vclip, settings, body, text) -> None:
    client, log, clip_id = vclip
    _png(settings.root / "stickers" / "tall.png", "320x1200")  # i full bredd 320 × 1200: in i safe_bottom
    count = len(log.list())
    answer = _make(client, clip_id, **body)
    assert answer.status_code == 400 and text in answer.json()["detail"], answer.text
    assert len(log.list()) == count
    assert not (settings.out_dir / "123" / "klara" / "clip_01_no_way.v1.mp4").exists()


@needs_subtitles
def test_a_versions_sticker_at_the_top_goes_below_the_burned_in_title(vclip, settings, monkeypatch) -> None:
    client, _, clip_id = vclip
    _hook_on(settings)
    seen = _spy(monkeypatch)
    answer = _make(client, clip_id, sticker={"file": "fire.png", "position": "top_right", "start_s": 0,
                                             "duration_s": 3})
    assert answer.status_code == 201, answer.text
    (call,) = seen
    assert call["title"] == "No way"  # en rad, och overlayen har kanalens text
    assert (call["sticker"]["x"], call["sticker"]["y"]) == (720, 134 + 80 + 20)


@needs_subtitles
def test_a_version_can_be_mirrored_filtered_and_sped_up(vclip, settings, monkeypatch) -> None:
    client, _, clip_id = vclip
    seen = _spy(monkeypatch)
    answer = _make(client, clip_id, mirror=True, speed=1.2, filter="warm", brightness=0.05)
    assert answer.status_code == 201, answer.text
    (call,) = seen
    assert call["look"].startswith(",hflip,eq=") and call["speed"] == 1.2
    out = settings.out_dir / "123" / "klara" / "clip_01_no_way.v1.mp4"
    assert _length(out) == pytest.approx(10.0 / 1.2, abs=0.15)
    options = _options(client, clip_id)
    assert options["speeds"] == [1.0, 1.05, 1.1, 1.15, 1.2]
    assert options["filters"] == ["none", "fresh", "warm", "cool", "punch"]


@needs_ffmpeg
def test_a_version_too_short_after_the_speed_up_is_refused(vclip, settings) -> None:
    client, _, clip_id = vclip
    set_profile(settings, clip={"min_seconds": 9, "max_seconds": 60})
    answer = _make(client, clip_id, speed=1.2)  # 10 s / 1,2 = 8,3 s
    assert answer.status_code == 400 and "minst 9" in answer.json()["detail"]


@needs_ffmpeg
def test_a_clip_too_short_after_the_cut_is_refused(vclip, settings) -> None:
    client, _, clip_id = vclip
    set_profile(settings, clip={"min_seconds": 6, "max_seconds": 60})
    answer = _make(client, clip_id, start_cut_s=4.5)
    assert answer.status_code == 400 and "minst 6" in answer.json()["detail"]


@needs_ffmpeg
def test_only_an_approved_clip_with_a_word_file_gets_a_version(vclip, settings) -> None:
    client, log, clip_id = vclip
    log.update(clip_id, status="renderad")
    assert _make(client, clip_id).status_code == 400
    log.update(clip_id, status="godkand")
    subtitles.words_path(settings.out_dir / "123" / RAW).unlink()
    answer = _make(client, clip_id)
    assert (answer.status_code, answer.json()["detail"]) == (400, NO_WORDS)


@needs_subtitles
def test_an_own_overlay_is_used_when_the_campaign_does_not_require_its_own(vclip, settings, monkeypatch) -> None:
    client, _, clip_id = vclip
    set_profile(settings, overlay={"file": "overlays/skiimasktimmy.png", "required": False, "mode": "full"})
    own = make_overlay(settings.root / "overlays" / "own.png")
    seen = _spy(monkeypatch)
    assert _make(client, clip_id, overlay="overlays/own.png").status_code == 201
    assert seen[-1]["overlay"] == own.resolve()


@needs_ffmpeg
def test_a_version_waits_for_a_running_edit_or_run(vclip) -> None:
    client, _, clip_id = vclip
    assert editor.lock.acquire(blocking=False)
    try:
        assert _make(client, clip_id).status_code == 409
    finally:
        editor.lock.release()
    client.app.state.active_run_id = 7
    answer = _make(client, clip_id)
    assert (answer.status_code, answer.json()["detail"]) == (409, "Ett projekt pågår. Vänta tills det är klart.")


def test_stickers_are_served_but_nothing_outside_the_folder(settings) -> None:
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    client = TestClient(app)
    (settings.root / "stickers").mkdir()
    (settings.root / "stickers" / "fire.png").write_bytes(b"\x89PNG")
    answer = client.get("/campaign/stickers/fire.png")
    assert (answer.status_code, answer.headers["content-type"], answer.content) == (200, "image/png", b"\x89PNG")
    assert client.get("/campaign/stickers/..%2Fprofiles%2Fskiimasktimmy.yaml").status_code == 404
    assert client.get("/campaign/stickers/saknas.png").status_code == 404


# ---------- 4. spärrarna i schemat: originalet och versionerna är samma klipp ----------

from .test_flera_konton import SAME, _schedule, notes, sched  # noqa: E402, F401
from .test_schedule import _stockholm  # noqa: E402


def _family(settings, versions_count: int = 1) -> list[int]:
    """Ett godkänt original och godkända versioner med egna filer, i en kampanj med sex TikTok-konton."""
    set_profile(settings, accounts={"tiktok": [f"open{c}" for c in "ABCDEF"]})
    log = ClipLog(settings.data_dir / "klipp.db")
    ids: list[int] = []
    for i in range(versions_count + 1):
        path = settings.out_dir / "v" / "klara" / (f"clip_01_x.v{i}.mp4" if i else "clip_01_x.mp4")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"mp4")
        ids.append(log.add(vod_id="v", profile="skiimasktimmy", file=str(path), title="Timmy lands it", caption="c",
                           status="godkand", start_s=10.0, end_s=40.0, version_of=ids[0] if ids else None))
    return ids


def test_a_version_on_account_b_stops_the_original_and_another_version_on_b(sched, settings) -> None:  # noqa: F811
    original, version, other = _family(settings, 2)
    assert _schedule(sched, version, "openB", title="Second take").status_code == 201
    assert _schedule(sched, original, "openB", title="Third take", at=_stockholm(1, 18)).status_code == 409
    assert _schedule(sched, other, "openB", title="Fourth take", at=_stockholm(1, 18)).status_code == 409
    assert _schedule(sched, original, "openA", title="Fifth take", at=_stockholm(1, 18)).status_code == 201


def test_versions_count_towards_the_five_accounts(sched, settings) -> None:  # noqa: F811
    original, version = _family(settings)
    log = ClipLog(settings.data_dir / "klipp.db")
    for i, account in enumerate("ABCD"):
        log.add_post(original, "tiktok", f"https://www.tiktok.com/@a/video/{i}", account=f"open{account}")
    assert _schedule(sched, version, "openE", title="Fifth").status_code == 201
    sixth = _schedule(sched, original, "openF", title="Sixth", at=_stockholm(1, 18))
    assert sixth.status_code == 400 and "5 konton" in sixth.json()["detail"]


def test_a_version_needs_its_own_text(sched, settings) -> None:  # noqa: F811
    original, version = _family(settings)
    assert _schedule(sched, original, "openA", title="Huge clutch").status_code == 201
    same = _schedule(sched, version, "openB", title="huge  CLUTCH #fyp", at=_stockholm(1, 18))
    assert (same.status_code, same.json()["detail"]) == (400, SAME)


def test_a_version_close_in_time_to_the_original_gets_the_warning(sched, settings) -> None:  # noqa: F811
    original, version = _family(settings)
    _schedule(sched, original, "openA", title="Huge clutch")
    near = _schedule(sched, version, "openB", title="Second take", at=_stockholm(1, 13)).json()
    assert near["warning"].startswith("Samma klipp ligger på openA kl. 12:00. "), near["warning"]


def test_the_original_lists_the_account_of_its_version(sched, settings) -> None:  # noqa: F811
    original, version = _family(settings)
    _schedule(sched, version, "openB", title="Second take")
    accounts = sched.get(f"/campaign/clips/{original}/accounts", params={"platform": "tiktok"}).json()["accounts"]
    assert {a["account"]: a["state"] for a in accounts}["openB"] == "scheduled"


# ---------- 5. eget bidrag: ny hook eller ny startpunkt ----------

from campaign.versions import NO_OWN  # noqa: E402

NO_ACCOUNT = ("Klippet har ett inlägg utan konto på TikTok. Det kan redan ligga på det här kontot. Kontrollera "
              "innan du postar.")


@needs_subtitles
def test_an_earlier_start_is_cut_from_the_original_file_and_the_words_follow(vclip, settings, monkeypatch) -> None:
    client, log, clip_id = vclip
    _trimmed(client, log, clip_id)  # klippet är 4–10 s i originalfilen på 10 s
    log.update(clip_id, start_s=100.0)  # klippet börjar 100 s in i VOD:en
    seen = _spy(monkeypatch)
    answer = _make(client, clip_id, start_cut_s=-4.03)  # webben avrundar: så nära filens början är början
    assert answer.status_code == 201, answer.text
    assert "Dialogue: 0,0:00:00.50," in seen[-1]["ass"]  # Bro vid 0,5 s i originalfilen kommer med
    assert "Dialogue: 0,0:00:02.00," in seen[-1]["ass"]  # wei vid 2,0 s står kvar på 2,0 s
    out = settings.out_dir / "123" / "klara" / "clip_01_no_way.v1.mp4"
    assert _length(out) == pytest.approx(10.0, abs=0.15)
    assert log.get(answer.json()["id"])["start_s"] == 96.0  # originalfilens början, inte 95,97
    answer = _make(client, clip_id, start_cut_s=-2)  # börjar vid 2 s i originalfilen
    assert answer.status_code == 201, answer.text
    assert "Dialogue: 0,0:00:00.50," not in seen[-1]["ass"]  # Bro vid 0,5 s kommer inte med
    assert "Dialogue: 0,0:00:00.00," in seen[-1]["ass"]  # wei vid 2,0 s står först
    assert _length(out.with_name("clip_01_no_way.v2.mp4")) == pytest.approx(8.0, abs=0.15)
    assert log.get(answer.json()["id"])["start_s"] == 98.0
    assert not [p for p in out.parent.parent.rglob(".*")]  # inga tillfälliga filer kvar


@needs_subtitles
def test_an_earlier_start_of_three_seconds_is_new_and_has_no_cold_open(vclip, settings, monkeypatch) -> None:
    client, log, clip_id = vclip
    _trimmed(client, log, clip_id)
    raw = settings.out_dir / "123" / RAW
    subtitles.save_words(raw, WORDS, CaptionConfig(enabled=True, style="box"), ReframeConfig(mode="center"),
                         editor.source_path(raw), hook=[6.0, 7.5])  # i originalfilen, som orden
    seen = _spy(monkeypatch)
    version = _make(client, clip_id, start_cut_s=-2).json()  # under 3 s: hooken först, och varningen
    assert seen[-1]["plan"] and seen[-1]["plan"]["cold_open"] and NO_OWN in version["warnings"]
    version = _make(client, clip_id, start_cut_s=-3).json()
    assert not (seen[-1]["plan"] or {}).get("cold_open") and NO_OWN not in version["warnings"]


@needs_subtitles
def test_a_start_moved_less_than_three_seconds_is_not_new_after_the_clamp(vclip, settings, monkeypatch) -> None:
    client, log, clip_id = vclip
    _trimmed(client, log, clip_id, (2.98, 10.0))
    raw = settings.out_dir / "123" / RAW
    subtitles.save_words(raw, WORDS, CaptionConfig(enabled=True, style="box"), ReframeConfig(mode="center"),
                         editor.source_path(raw), hook=[6.0, 7.5])
    seen = _spy(monkeypatch)
    answer = _make(client, clip_id, start_cut_s=-3.0)  # kläms till filens början: verklig flytt 2,98 s
    assert answer.status_code == 201, answer.text
    assert seen[-1]["plan"] and seen[-1]["plan"]["cold_open"] and NO_OWN in answer.json()["warnings"]


@needs_subtitles
def test_a_later_start_beyond_five_seconds_is_allowed(vclip, settings) -> None:
    client, _, clip_id = vclip
    answer = _make(client, clip_id, start_cut_s=6)
    assert answer.status_code == 201, answer.text
    assert _length(settings.out_dir / "123" / "klara" / "clip_01_no_way.v1.mp4") == pytest.approx(4.0, abs=0.15)


@needs_subtitles
def test_the_edge_from_the_options_is_accepted(vclip, settings) -> None:
    client, log, clip_id = vclip
    _trimmed(client, log, clip_id, (0.0, 8.28))  # 8,28 - 1,28 blir 6,9999… s i flyttal
    set_profile(settings, clip={"min_seconds": 7, "max_seconds": 60})
    edge = _options(client, clip_id)["max_start_cut_s"]
    assert edge == 1.28
    answer = _make(client, clip_id, start_cut_s=edge)
    assert answer.status_code == 201, answer.text


@needs_subtitles
@pytest.mark.parametrize("case", [  # trimning, kampanjens gränser, gränsen i options
    ((4.125, 9.13), {"min_seconds": 2, "max_seconds": 60}, "max_start_cut_s", 3.0),  # 5,005 - 2 = 3,005
    ((4.125, 9.12), {"min_seconds": 3, "max_seconds": 8}, "min_start_cut_s", -3.0),  # 4,995 - 8 = -3,005
])
def test_the_edge_from_the_options_is_rounded_inwards(vclip, settings, case) -> None:
    client, log, clip_id = vclip
    span, limits, key, expected = case
    _trimmed(client, log, clip_id, span)  # redigeraren avrundar inte: tre decimaler
    set_profile(settings, clip=limits)
    edge = _options(client, clip_id)[key]
    assert edge == expected
    answer = _make(client, clip_id, start_cut_s=edge)
    assert answer.status_code == 201, answer.text


@needs_ffmpeg
def test_a_start_that_makes_the_version_too_long_is_refused(vclip, settings) -> None:
    client, log, clip_id = vclip
    _trimmed(client, log, clip_id)
    set_profile(settings, clip={"min_seconds": 3, "max_seconds": 8})
    count = len(log.list())
    answer = _make(client, clip_id, start_cut_s=-4)  # 6 s + 4 s = 10 s
    assert answer.status_code == 400 and "högst 8" in answer.json()["detail"], answer.text
    answer = _make(client, clip_id, start_cut_s=-4.5)  # före originalfilens början
    assert answer.status_code == 400 and "originalfilen" in answer.json()["detail"], answer.text
    assert len(log.list()) == count


@needs_subtitles
def test_a_raw_clip_longer_than_the_max_is_left_to_the_rule_check(vclip, settings) -> None:
    client, _, clip_id = vclip
    set_profile(settings, clip={"min_seconds": 3, "max_seconds": 8})  # råklippet är 10 s, som förut
    for start_cut_s in (0, 1):
        answer = _make(client, clip_id, start_cut_s=start_cut_s)
        assert answer.status_code == 201, answer.text


@needs_subtitles
def test_a_new_start_turns_off_the_cold_open_and_a_small_one_keeps_it(vclip, settings, monkeypatch) -> None:
    client, _, clip_id = vclip
    raw = settings.out_dir / "123" / RAW
    subtitles.save_words(raw, WORDS, CaptionConfig(enabled=True, style="box"), ReframeConfig(mode="center"), raw,
                         hook=[6.0, 7.5])
    seen = _spy(monkeypatch)
    assert _make(client, clip_id, start_cut_s=2).status_code == 201
    assert seen[-1]["plan"] and seen[-1]["plan"]["cold_open"]  # under 3 s: hooken först, som förut
    assert _make(client, clip_id, start_cut_s=3).status_code == 201
    assert not (seen[-1]["plan"] or {}).get("cold_open")


@needs_subtitles
@pytest.mark.parametrize("case", [  # hook_text, versionerna i tur och ordning, varning på den sista
    (True, [{"mirror": True, "speed": 1.1}], True),
    (True, [{"start_cut_s": 2.9}], True),
    (True, [{"start_cut_s": 3}], False),
    (True, [{"hook": "Nobody saw that coming"}], False),
    (True, [{"hook": "no  WAY"}], True),  # originalets hook
    (True, [{"hook": "Nobody saw that coming"}, {"hook": "nobody saw  THAT coming"}], True),  # en versions hook
    (False, [{"hook": "Nobody saw that coming"}], True),  # Timmys hook_text är falskt: kanalens text står uppe
    (False, [{"start_cut_s": 4}], False),
])
def test_the_warning_comes_without_a_new_hook_or_a_new_start(vclip, settings, case) -> None:
    client, _, clip_id = vclip
    hook_text, bodies, warned = case
    if hook_text:
        _hook_on(settings)
    for body in bodies:
        version = _make(client, clip_id, **body).json()
    assert (NO_OWN in version["warnings"]) is warned, version["warnings"]


@needs_subtitles
def test_a_post_without_an_account_gives_a_warning_but_does_not_stop(vclip) -> None:
    client, log, clip_id = vclip
    assert _options(client, clip_id)["account_warning"] is None
    assert NO_ACCOUNT not in _make(client, clip_id).json()["warnings"]
    log.add_post(clip_id, "tiktok", "https://www.tiktok.com/@a/video/1", account=None)  # loggat för hand
    options = _options(client, clip_id)
    assert (options["allowed"], options["account_warning"]) == (True, NO_ACCOUNT)
    answer = _make(client, clip_id)
    assert answer.status_code == 201, answer.text
    assert NO_ACCOUNT in answer.json()["warnings"]
    assert _options(client, clip_id, "okänt")["account_warning"] is None  # versionen går inte att göra


@needs_subtitles
def test_the_options_tell_the_start_range_and_the_hooks(vclip, settings) -> None:
    client, log, clip_id = vclip
    options = _options(client, clip_id)
    assert {k: options[k] for k in ("min_start_cut_s", "max_start_cut_s", "new_start_s", "hook", "hooks_used",
                                    "hook_text", "original_url", "in_s")} == {
        "min_start_cut_s": 0.0, "max_start_cut_s": 7.0, "new_start_s": 3.0, "hook": "No way",
        "hooks_used": ["No way"], "hook_text": False, "original_url": f"/campaign/clips/{clip_id}/original",
        "in_s": 0.0}
    _hook_on(settings)
    version = _make(client, clip_id, hook="Nobody saw that coming").json()
    for asked in (clip_id, version["id"]):  # en version visar originalets val
        options = _options(client, asked)
        assert options["hooks_used"] == ["No way", "Nobody saw that coming"] and options["hook_text"] is True
        assert (options["hook"], options["original_url"]) == ("No way", f"/campaign/clips/{clip_id}/original")
    _trimmed(client, log, clip_id)
    set_profile(settings, clip={"min_seconds": 3, "max_seconds": 8})
    options = _options(client, clip_id)
    assert (options["in_s"], options["min_start_cut_s"], options["max_start_cut_s"]) == (4.0, -2.0, 3.0)
    log.update(clip_id, title="Moment 0:01:40")  # snabblägets platshållare är ingen hook
    options = _options(client, clip_id)
    assert (options["hook"], options["hooks_used"]) == ("", ["Nobody saw that coming"])
    blocked = _options(client, clip_id, "okänt")
    assert (blocked["allowed"], blocked["hook"], blocked["hooks_used"], blocked["min_start_cut_s"]) == (
        False, "", [], 0.0)


