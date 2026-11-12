"""Kampanjer i appen: overlay, profilens YAML, ny kampanj, facecam- och spelruta och en bild från VOD:en.
Allt körs mot en kopia i tmp_path. Inga riktiga Twitch-anrop."""

from __future__ import annotations

import shutil
import struct
import subprocess
import zlib

import httpx
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api, profiles_api
from campaign.cliplog import ClipLog
from campaign.media import probe
from campaign.profile import load_profile
from clipsmith.config.loaders import load_config

from .conftest import PAKET, make_clip, needs_ffmpeg

VOD = "2925991945"
THUMB = "https://static-cdn.jtvnw.net/cf_vods/x/thumb/thumb0-1920x1080.jpg"


def _png(width: int, height: int) -> bytes:
    """En riktig PNG-fil, gjord utan bildbibliotek."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"".join(b"\x00" + bytes(width * 4) for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class FakeTwitch:
    def __init__(self):
        self.channels = {"nykanal": "NyKanal", "skiimasktimmy": "SkiiMaskTimmy"}
        self.videos = {VOD: THUMB}
        self.error: Exception | None = None
        self.queries: list[str] = []
        self.downloads: list[str] = []
        self.image = b""

    def gql(self, query: str) -> dict:
        self.queries.append(query)
        if self.error:
            raise self.error
        key = query.split('"')[1]
        if "user(login" in query:
            name = self.channels.get(key)
            return {"data": {"user": name and {"id": "1", "login": key, "displayName": name}}}
        url = self.videos.get(key)
        return {"data": {"video": url and {"previewThumbnailURL": url}}}

    def http(self, request: httpx.Request) -> httpx.Response:
        self.downloads.append(str(request.url))
        return httpx.Response(200, content=self.image, headers={"content-type": "image/jpeg"})


@pytest.fixture
def twitch(monkeypatch) -> FakeTwitch:
    fake = FakeTwitch()
    monkeypatch.setattr(profiles_api.twitch, "gql_query", fake.gql)
    monkeypatch.setattr(profiles_api, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(fake.http)))
    return fake


@pytest.fixture
def client(settings, twitch, monkeypatch) -> TestClient:
    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    settings.anthropic_api_key = settings.berget_api_key = "test"
    monkeypatch.delenv("CLIPSMITH_API_KEY", raising=False)
    app = FastAPI()
    app.state.active_run_id = None
    app.include_router(api.router)
    app.include_router(profiles_api.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    return TestClient(app)


def _profile_text(settings, name="skiimasktimmy") -> str:
    return (settings.profiles_dir / f"{name}.yaml").read_bytes().decode()


# ---------- kampanjen ----------


def test_campaign_detail_has_overlay_text_caption_and_payment(client) -> None:
    detail = client.get("/campaign/profiles/skiimasktimmy").json()
    assert detail["name"] == "skiimasktimmy"
    assert (detail["clip_min_s"], detail["clip_max_s"]) == (15, 45)
    assert detail["overlay"] == {"file": "overlays/skiimasktimmy.png", "required": True, "mode": "full",
                                 "exists": False, "url": "/campaign/profiles/skiimasktimmy/overlay",
                                 "width": None, "height": None}
    assert detail["on_screen_text"] == {"text": "twitch.tv/skiimasktimmy", "position": "top", "required": True}
    assert detail["safe_zone"] == {"top": 0.07, "bottom": 0.20}
    assert detail["caption"]["must_include"] == ["twitch.tv/skiimasktimmy"]
    assert "#fps" in detail["caption"]["hashtags"]
    assert "{title}" in detail["caption"]["template"]
    assert detail["payment"] == {"cpm_usd": 1.0, "min_views": 2000, "max_payout_per_clip_usd": 150}
    assert detail["clipsmith_config"] == "config.timmy.yaml"
    assert client.get("/campaign/profiles/finnsinte").status_code == 404
    assert client.get("/campaign/profiles/Fel-Id").status_code == 400


# ---------- 1. overlay ----------


def test_overlay_is_saved_and_served(client, settings) -> None:
    png = _png(1080, 1920)
    response = client.put("/campaign/profiles/skiimasktimmy/overlay", content=png,
                          headers={"Content-Type": "image/png"})
    assert response.status_code == 200, response.text
    overlay = response.json()["overlay"]
    assert (overlay["exists"], overlay["width"], overlay["height"]) == (True, 1080, 1920)
    assert (settings.root / "overlays" / "skiimasktimmy.png").read_bytes() == png
    assert not any("Overlay-bilden saknas" in p for p in response.json()["problems"])

    served = client.get("/campaign/profiles/skiimasktimmy/overlay")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.content == png


def test_bad_overlays_are_refused(client, settings) -> None:
    assert client.get("/campaign/profiles/skiimasktimmy/overlay").status_code == 404
    url = "/campaign/profiles/skiimasktimmy/overlay"
    wrong_size = client.put(url, content=_png(1080, 1080))
    assert wrong_size.status_code == 400
    assert "1080x1080" in wrong_size.json()["detail"]
    assert client.put(url, content=b"GIF89a" + bytes(100)).status_code == 400
    too_big = b"\x89PNG\r\n\x1a\n" + bytes(10 * 1024 * 1024)
    assert client.put(url, content=too_big).status_code == 400
    assert not (settings.root / "overlays" / "skiimasktimmy.png").exists()

    path = settings.profiles_dir / "skiimasktimmy.yaml"
    path.write_text(_profile_text(settings).replace("file: overlays/skiimasktimmy.png", "file: ../evil.png"))
    response = client.put(url, content=_png(1080, 1920))
    assert response.status_code == 400
    assert "overlays/" in response.json()["detail"]
    assert not (settings.root.parent / "evil.png").exists()


def test_overlay_top_needs_width_1080(client, settings) -> None:
    path = settings.profiles_dir / "skiimasktimmy.yaml"
    path.write_text(_profile_text(settings).replace("mode: full ", "mode: top  "))
    url = "/campaign/profiles/skiimasktimmy/overlay"
    assert client.put(url, content=_png(1080, 300)).status_code == 200
    assert client.put(url, content=_png(1000, 300)).status_code == 400


def test_overlay_without_a_file_in_the_profile_gets_one(client, settings) -> None:
    path = settings.profiles_dir / "skiimasktimmy.yaml"
    before = _profile_text(settings).replace(
        "  file: overlays/skiimasktimmy.png   # ladda ner \"TWITCH STREAM OVERLAY\" från kampanjsidan\n", "")
    path.write_text(before)
    response = client.put("/campaign/profiles/skiimasktimmy/overlay", content=_png(1080, 1920))
    assert response.status_code == 200, response.text
    assert response.json()["overlay"]["file"] == "overlays/skiimasktimmy.png"
    after = _profile_text(settings)
    assert after.replace("  file: overlays/skiimasktimmy.png\n", "", 1) == before
    assert load_profile("skiimasktimmy", settings.profiles_dir).overlay_file == "overlays/skiimasktimmy.png"


def test_writing_routes_need_the_api_key(client, settings, monkeypatch) -> None:
    monkeypatch.setenv("CLIPSMITH_API_KEY", "nyckel")
    before = _profile_text(settings)
    assert client.put("/campaign/profiles/skiimasktimmy/overlay", content=_png(1080, 1920)).status_code == 401
    assert client.put("/campaign/profiles/skiimasktimmy/yaml", json={"yaml": before}).status_code == 401
    assert client.post("/campaign/profiles", json={"channel": "nykanal", "streamer": "Ny"}).status_code == 401
    assert client.put("/campaign/profiles/skiimasktimmy/reframe", json={
        "webcam_rect": [0, 0, 316, 224], "gameplay_rect": [0, 0, 1012, 1080], "split_ratio": 0.4}).status_code == 401
    assert not (settings.root / "overlays" / "skiimasktimmy.png").exists()
    ok = client.put("/campaign/profiles/skiimasktimmy/overlay", content=_png(1080, 1920),
                    headers={"X-Api-Key": "nyckel"})
    assert ok.status_code == 200


# ---------- 2. YAML ----------


def test_yaml_is_shown_with_comments_and_saved_exactly(client, settings) -> None:
    before = _profile_text(settings)
    assert client.get("/campaign/profiles/skiimasktimmy/yaml").json() == {"yaml": before}

    text = before.replace('campaign: "$2,500', 'campaign: "$3,000') + "\n# Robins egen anteckning\r\n"
    response = client.put("/campaign/profiles/skiimasktimmy/yaml", json={"yaml": text})
    assert response.status_code == 200, response.text
    assert response.json()["campaign"].startswith("$3,000")
    assert (settings.profiles_dir / "skiimasktimmy.yaml").read_bytes() == text.encode()
    assert (settings.profiles_dir / ".skiimasktimmy.yaml.bak").read_bytes() == before.encode()


@pytest.mark.parametrize(("change", "words"), [
    (lambda t: t.replace("name: skiimasktimmy", "name: [skiimasktimmy"), "rad"),
    (lambda t: t.replace("streamer: SkiiMaskTimmy\n", ""), "streamer"),
    (lambda t: t.replace("clipsmith_config: config.timmy.yaml", "clipsmith_config: config.finnsinte.yaml"),
     "config.finnsinte.yaml"),
    (lambda t: t.replace('template: "{title}', 'template: "{titel}'), "template"),
    (lambda t: "- bara en lista\n", "fält"),
])
def test_broken_yaml_is_refused_and_nothing_changes(client, settings, change, words) -> None:
    before = _profile_text(settings)
    response = client.put("/campaign/profiles/skiimasktimmy/yaml", json={"yaml": change(before)})
    assert response.status_code == 400
    assert words in response.json()["detail"]
    assert _profile_text(settings) == before
    assert not (settings.profiles_dir / ".skiimasktimmy.yaml.bak").exists()


def test_yaml_error_says_which_line(client, settings) -> None:
    text = _profile_text(settings).replace("language: en", "language: [en")
    detail = client.put("/campaign/profiles/skiimasktimmy/yaml", json={"yaml": text}).json()["detail"]
    assert "rad" in detail


# ---------- 3. ny kampanj ----------


def test_new_campaign_creates_a_profile_and_a_config(client, settings) -> None:
    response = client.post("/campaign/profiles", json={"channel": "NyKanal", "streamer": "Ny Kanal"})
    assert response.status_code == 201, response.text
    detail = response.json()
    assert (detail["name"], detail["channel"], detail["streamer"]) == ("nykanal", "nykanal", "Ny Kanal")
    assert (detail["clip_min_s"], detail["clip_max_s"]) == (15, 45)
    assert detail["overlay"]["file"] == "overlays/nykanal.png"
    assert detail["on_screen_text"]["text"] == "twitch.tv/nykanal"

    text = _profile_text(settings, "nykanal")
    assert text.startswith("#")
    profile = load_profile("nykanal", settings.profiles_dir)
    assert profile.clipsmith_config == "config.nykanal.yaml"
    assert "twitch.tv/nykanal" in profile.caption_must_include
    profile.caption_template.format(title="x")

    config_text = (settings.root / "config.nykanal.yaml").read_text()
    cfg = load_config(settings.root / "config.nykanal.yaml")
    assert cfg.channels == ["nykanal"]
    assert (cfg.reframe.mode, cfg.reframe.webcam_rect, cfg.reframe.gameplay_rect) == ("stacked", None, None)
    assert "1542" not in config_text and "2925991945" not in config_text and "skiimasktimmy" not in config_text
    assert "hype_keywords" in config_text and "# Engelska" in config_text  # resten av mallen finns kvar
    assert (settings.root / "config.timmy.yaml").read_bytes() == (PAKET / "config.timmy.yaml").read_bytes()
    assert "nykanal" in [p["name"] for p in client.get("/campaign/profiles").json()]


def test_new_campaign_with_all_fields(client, settings) -> None:
    response = client.post("/campaign/profiles", json={
        "channel": "nykanal", "streamer": "NyKanal", "campaign": "$1,000 NyKanal (ClippedIn)", "language": "en",
        "clip_min_s": 10, "clip_max_s": 30, "on_screen_text": "twitch.tv/nykanal", "text_position": "bottom",
        "overlay_required": True, "overlay_mode": "top", "facecam_required": True,
        "must_include": ["twitch.tv/nykanal"], "hashtags": ["#nykanal", "#twitchclips"],
        "caption_template": "{title} 👉 twitch.tv/nykanal",
        "formats": [{"name": "Clutch plays", "description": "Big plays"}, {"name": "Clutch plays!"}],
        "cpm_usd": 1.5, "min_views": 1000, "max_payout_per_clip_usd": 100, "engagement_min_percent": 0.8})
    assert response.status_code == 201, response.text
    detail = response.json()
    assert detail["campaign"] == "$1,000 NyKanal (ClippedIn)"
    assert (detail["clip_min_s"], detail["clip_max_s"]) == (10, 30)
    assert [f["id"] for f in detail["formats"]] == ["clutch_plays", "clutch_plays_2"]
    assert detail["overlay"]["mode"] == "top"
    assert detail["payment"] == {"cpm_usd": 1.5, "min_views": 1000, "max_payout_per_clip_usd": 100}
    profile = load_profile("nykanal", settings.profiles_dir)
    assert (profile.min_seconds, profile.max_seconds, profile.text_position) == (10, 30, "bottom")
    assert profile.facecam_required is True
    assert profile.engagement_min(5000) == 0.8
    assert "👉" in _profile_text(settings, "nykanal")  # allow_unicode
    assert load_config(settings.root / "config.nykanal.yaml").clip.max_seconds == 30


def test_new_campaign_is_refused_when_it_exists_or_the_channel_is_wrong(client, settings, twitch) -> None:
    assert client.post("/campaign/profiles", json={"channel": "skiimasktimmy", "streamer": "x"}).status_code == 409
    assert client.post("/campaign/profiles", json={"channel": "nykanal", "streamer": "x"}).status_code == 201
    assert client.post("/campaign/profiles", json={"channel": "nykanal", "streamer": "x"}).status_code == 409

    unknown = client.post("/campaign/profiles", json={"channel": "finnsinte", "streamer": "x"})
    assert unknown.status_code == 400
    assert "finnsinte" in unknown.json()["detail"]
    for channel in ("Ny Kanal!", "a", "../x", "nå"):
        assert client.post("/campaign/profiles", json={"channel": channel, "streamer": "x"}).status_code == 400
    bad = {"channel": "nykanal2", "streamer": "x"}
    twitch.channels["nykanal2"] = "NyKanal2"
    assert client.post("/campaign/profiles", json={**bad, "clip_min_s": 40, "clip_max_s": 20}).status_code == 400
    assert client.post("/campaign/profiles", json={**bad, "caption_template": "{nope}"}).status_code == 400
    twitch.error = OSError("nere")
    assert client.post("/campaign/profiles", json=bad).status_code == 502
    names = sorted(p.name for p in settings.profiles_dir.iterdir())
    assert names == ["nykanal.yaml", "skiimasktimmy.yaml"]
    assert sorted(p.name for p in settings.root.glob("config.*.yaml")) == ["config.nykanal.yaml", "config.timmy.yaml"]


# ---------- 4. facecam och spelruta ----------


def test_reframe_shows_rects_and_shapes(client) -> None:
    assert client.get("/campaign/profiles/skiimasktimmy/reframe").json() == {
        "mode": "stacked", "split_ratio": 0.4, "webcam_rect": [1542, 770, 316, 224],
        "gameplay_rect": [454, 0, 1012, 1080], "source_width": 1920, "source_height": 1080,
        "webcam_aspect": 1.40625, "gameplay_aspect": 0.9375}


def test_reframe_writes_only_its_lines(client, settings) -> None:
    path = settings.root / "config.timmy.yaml"
    before = path.read_bytes().decode().splitlines(keepends=True)
    response = client.put("/campaign/profiles/skiimasktimmy/reframe", json={
        "webcam_rect": [1500, 700, 316, 224], "gameplay_rect": [400, 0, 1012, 1080], "split_ratio": 0.4})
    assert response.status_code == 200, response.text
    assert response.json()["webcam_rect"] == [1500, 700, 316, 224]
    after = path.read_bytes().decode().splitlines(keepends=True)
    assert len(after) == len(before)
    changed = [i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b]
    assert [after[i] for i in changed] == ["  webcam_rect: [1500, 700, 316, 224]   # x, y, bredd, höjd\n",
                                           "  gameplay_rect: [400, 0, 1012, 1080]  # mitten av bilden\n"]

    response = client.put("/campaign/profiles/skiimasktimmy/reframe", json={
        "webcam_rect": [0, 0, 450, 400], "gameplay_rect": [0, 0, 1080, 960], "split_ratio": 0.5})
    assert response.status_code == 200, response.text
    assert (response.json()["webcam_aspect"], response.json()["gameplay_aspect"]) == (1.125, 1.125)
    assert "  split_ratio: 0.5      # facecam får 40 % av höjden\n" in path.read_text()


@pytest.mark.parametrize(("body", "words"), [
    ({"webcam_rect": [0, 0, 400, 224]}, "form"),
    ({"gameplay_rect": [0, 0, 1080, 1080]}, "form"),
    ({"webcam_rect": [1700, 0, 316, 224]}, "utanför"),
    ({"webcam_rect": [-1, 0, 316, 224]}, "utanför"),
    ({"webcam_rect": [0, 0, 42, 30]}, "minst"),
    ({"webcam_rect": [0, 0, 316.5, 224]}, "heltal"),
    ({"webcam_rect": [0, 0, 316]}, "heltal"),
    ({"split_ratio": 0.95}, "split_ratio"),
])
def test_bad_rects_are_refused(client, settings, body, words) -> None:
    before = (settings.root / "config.timmy.yaml").read_bytes()
    full = {"webcam_rect": [1542, 770, 316, 224], "gameplay_rect": [454, 0, 1012, 1080], "split_ratio": 0.4}
    response = client.put("/campaign/profiles/skiimasktimmy/reframe", json={**full, **body})
    assert response.status_code == 400
    assert words in response.json()["detail"]
    assert (settings.root / "config.timmy.yaml").read_bytes() == before


def test_missing_reframe_lines_are_added_under_reframe(client, settings) -> None:
    assert client.post("/campaign/profiles", json={"channel": "nykanal", "streamer": "x"}).status_code == 201
    path = settings.root / "config.nykanal.yaml"
    before = path.read_text()
    body = {"webcam_rect": [1542, 770, 316, 224], "gameplay_rect": [454, 0, 1012, 1080], "split_ratio": 0.4}
    assert client.put("/campaign/profiles/nykanal/reframe", json=body).status_code == 200
    after = path.read_text()
    added = ["  webcam_rect: [1542, 770, 316, 224]\n", "  gameplay_rect: [454, 0, 1012, 1080]\n"]
    assert after.split("reframe:\n")[1].startswith("".join(added))
    assert after.replace("".join(added), "") == before
    cfg = load_config(path)
    assert (cfg.reframe.webcam_rect, cfg.reframe.gameplay_rect) == (body["webcam_rect"], body["gameplay_rect"])

    path.write_text(before.split("reframe:")[0].rstrip() + "\n")  # ingen reframe-del alls
    assert client.put("/campaign/profiles/nykanal/reframe", json=body).status_code == 200
    reframe = yaml.safe_load(path.read_text())["reframe"]
    assert reframe == {"mode": "stacked", "webcam_rect": body["webcam_rect"], "gameplay_rect": body["gameplay_rect"],
                       "split_ratio": 0.4}


# ---------- 5. bild från VOD:en ----------


def _jpeg(path) -> bytes:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360",
                    "-frames:v", "1", str(path)], check=True)
    return path.read_bytes()


@needs_ffmpeg
def test_frame_comes_from_the_local_vod_and_is_cached(client, settings, twitch) -> None:
    mp4 = make_clip(settings.work_dir / VOD / f"{VOD}.mp4", size="640x360", seconds=4)
    response = client.get("/campaign/profiles/skiimasktimmy/frame", params={"vod_id": VOD})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/jpeg"
    cached = settings.work_dir / "frames" / f"{VOD}.jpg"
    assert cached.read_bytes() == response.content
    info = probe(cached)
    assert (info["width"], info["height"]) == (1920, 1080)
    assert twitch.queries == []

    mp4.unlink()
    again = client.get("/campaign/profiles/skiimasktimmy/frame", params={"vod_id": VOD})
    assert again.content == response.content
    assert twitch.queries == [] and twitch.downloads == []


@needs_ffmpeg
def test_frame_without_a_local_vod_uses_twitchs_picture(client, settings, twitch, tmp_path) -> None:
    twitch.image = _jpeg(tmp_path / "thumb.jpg")
    response = client.get("/campaign/profiles/skiimasktimmy/frame", params={"vod_id": VOD})
    assert response.status_code == 200, response.text
    assert "previewThumbnailURL(width: 1920, height: 1080)" in twitch.queries[0]
    assert twitch.downloads == [THUMB]
    info = probe(settings.work_dir / "frames" / f"{VOD}.jpg")
    assert (info["width"], info["height"]) == (1920, 1080)
    client.get("/campaign/profiles/skiimasktimmy/frame", params={"vod_id": VOD})
    assert len(twitch.downloads) == 1
    assert [p.name for p in (settings.work_dir / "frames").iterdir()] == [f"{VOD}.jpg"]


@needs_ffmpeg
def test_frame_uses_the_latest_run_and_refuses_strange_pictures(client, settings, twitch, tmp_path) -> None:
    twitch.image = _jpeg(tmp_path / "thumb.jpg")
    assert client.get("/campaign/profiles/skiimasktimmy/frame").status_code == 404
    ClipLog(settings.data_dir / "klipp.db").add_run(1, VOD, "skiimasktimmy", None)
    assert client.get("/campaign/profiles/skiimasktimmy/frame").status_code == 200

    twitch.videos["111111111"] = "https://evil.example.com/x.jpg"
    assert client.get("/campaign/profiles/skiimasktimmy/frame", params={"vod_id": "111111111"}).status_code == 404
    twitch.videos["222222222"] = "http://static-cdn.jtvnw.net/x.jpg"
    assert client.get("/campaign/profiles/skiimasktimmy/frame", params={"vod_id": "222222222"}).status_code == 404
    assert client.get("/campaign/profiles/skiimasktimmy/frame", params={"vod_id": "999999999"}).status_code == 404
    assert twitch.downloads == [THUMB]
    twitch.error = OSError("nere")
    assert client.get("/campaign/profiles/skiimasktimmy/frame", params={"vod_id": "333333333"}).status_code == 502
