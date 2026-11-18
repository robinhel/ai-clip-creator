"""Ljudet i egna videor: Behåll (som förut), Tyst (ett tyst ljudspår) eller Musik ur kampanjens mapp musik/<kampanj>/,
med ett reglage för originalljudet. Regeln om saknat ljud gäller bara Twitch-klipp."""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api, drafts, editor_api, finish
from campaign.cliplog import ClipLog
from campaign.media import probe
from campaign.profile import load_profile
from campaign.rules import check_clip

from .conftest import PAKET, needs_ffmpeg, set_profile

BASE = {"profile": "skiimasktimmy", "title": "Ljudprov", "speed": "1.0"}
MUSIC = "ton.wav"
NO_SOUND = "Klippet saknar ljud."


@pytest.fixture
def client(settings, monkeypatch):
    monkeypatch.setitem(finish.ENCODER_ARGS, "libx264", ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "30"])
    monkeypatch.delenv("CLIPSMITH_API_KEY", raising=False)
    set_profile(settings, clip={"min_seconds": 3, "max_seconds": 60}, active=True,
                on_screen_text={"text": "", "hook_text": True}, overlay={"file": None, "required": False})
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(editor_api.router)
    app.dependency_overrides[api.get_settings] = lambda: settings
    return TestClient(app)


def _video(path, tone: int | None = 220, seconds: float = 4):
    """Liten liggande video med en ton (eller utan ljud)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sound = ["-f", "lavfi", "-i", f"sine=frequency={tone}:sample_rate=48000", "-c:a", "aac"] if tone else []
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30", *sound,
                    "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
                   check=True)
    return path


def _music(path):
    """Musik: 440 Hz de första 10 sekunderna, sedan 880 Hz i 10 sekunder."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:d=10",
                    "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:d=10",
                    "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1", str(path)], check=True)
    return path


def _volume(path, chain: str = "") -> dict[str, float]:
    """mean_volume och max_volume i dB för ljudet efter filtren i chain."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", f"{chain}volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True, check=True).stderr
    return {k: float(v) for k, v in re.findall(r"(mean_volume|max_volume): (\S+) dB", out)}


def _band(path, freq: int, start: float, end: float) -> float:
    """Medelnivån i dB nära frekvensen freq mellan start och slut i klippet."""
    narrow = f"bandpass=f={freq}:width_type=h:w=30,"
    return _volume(path, f"atrim=start={start}:end={end},{narrow}{narrow}")["mean_volume"]


def _draft(client, path) -> str:
    with path.open("rb") as file:
        answer = client.post("/campaign/uploads/drafts", files={"file": (path.name, file, "video/mp4")})
    assert answer.status_code == 201, answer.text
    return answer.json()["draft_id"]


def _add_music(client, path, profile="skiimasktimmy"):
    with path.open("rb") as file:
        return client.post("/campaign/uploads/music", files={"file": (path.name, file, "audio/wav")},
                           data={"profile": profile})


def _render(client, draft_id, tmp_path, name="klipp.mp4", **choices):
    answer = client.post(f"/campaign/uploads/drafts/{draft_id}/render", data={**BASE, **choices})
    assert answer.status_code == 200, answer.text
    video = tmp_path / name
    video.write_bytes(client.get(answer.json()["video_url"]).content)
    return answer.json(), video


def _packets(video) -> str:
    return subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-count_packets", "-show_entries",
                           "stream=nb_read_packets", "-of", "csv=p=0", str(video)],
                          capture_output=True, text=True, check=True).stdout.strip()


def _count(monkeypatch) -> list:
    calls: list = []
    real = drafts.finish_clip
    monkeypatch.setattr(drafts, "finish_clip", lambda *a, **k: calls.append(k) or real(*a, **k))
    return calls


# ---------- 1. Tyst ----------

@needs_ffmpeg
def test_mute_gives_a_silent_track_with_the_clips_length_and_the_same_picture(client, tmp_path) -> None:
    draft_id = _draft(client, _video(tmp_path / "a.mp4"))
    kept, keep_video = _render(client, draft_id, tmp_path, "behall.mp4", speed="1.1")
    muted, mute_video = _render(client, draft_id, tmp_path, "tyst.mp4", speed="1.1", audio="mute")
    assert probe(mute_video)["has_audio"]
    assert _volume(mute_video)["max_volume"] < -60
    assert _volume(keep_video)["max_volume"] > -30  # kontroll: Behåll har källans ton
    assert muted["problems"] == []
    assert muted["length_s"] == pytest.approx(kept["length_s"], abs=0.05)
    assert _packets(mute_video) == _packets(keep_video)  # samma bildrutor, ingen tappad


@needs_ffmpeg
def test_an_own_video_without_sound_is_not_refused_but_a_twitch_clip_is(client, settings, tmp_path) -> None:
    silent = _video(tmp_path / "tyst.mp4", tone=None)
    muted, video = _render(client, _draft(client, silent), tmp_path, audio="mute")
    assert probe(video)["has_audio"] and muted["problems"] == []
    kept, video = _render(client, _draft(client, silent), tmp_path, "behall.mp4")
    assert not probe(video)["has_audio"]
    assert not any(NO_SOUND in p for p in kept["problems"]), kept["problems"]
    with silent.open("rb") as file:  # det gamla anropet: en uppladdning är en egen video
        clip = client.post("/campaign/uploads", files={"file": ("tyst.mp4", file, "video/mp4")}, data=BASE).json()
    assert not any(NO_SOUND in p for p in clip["problems"]), clip["problems"]
    # Ett Twitch-klipp utan ljud underkänns som förut.
    campaign = load_profile("skiimasktimmy", settings.profiles_dir)
    info = {**probe(video), "width": 1080, "height": 1920}
    problems, _ = check_clip(info, {"text_applied": True, "overlay_applied": True}, campaign, "#ad")
    assert any(NO_SOUND in p for p in problems)


# ---------- 2 och 3. Musik ----------

@needs_ffmpeg
def test_music_from_its_start_time_replaces_or_joins_the_original(client, tmp_path) -> None:
    added = _add_music(client, _music(tmp_path / MUSIC))
    assert added.status_code == 201, added.text
    assert added.json() == {"file": MUSIC, "duration_s": pytest.approx(20, abs=0.1)}
    draft_id = _draft(client, _video(tmp_path / "a.mp4", tone=220))
    music = {"audio": "music", "music": MUSIC, "music_start_s": "10"}
    only, video = _render(client, draft_id, tmp_path, "musik.mp4", **music, original_volume="0")
    assert only["problems"] == [] and probe(video)["has_audio"]
    high, low, source = (_band(video, f, 1.0, 3.0) for f in (880, 440, 220))
    assert high > -45, high
    assert high > low + 20  # musiken från 10 s, inte från början
    assert high > source + 25  # inget av originalet vid 0
    _, video = _render(client, draft_id, tmp_path, "blandat.mp4", **music, original_volume="1")
    full = _band(video, 220, 1.0, 3.0) - _band(video, 880, 1.0, 3.0)
    assert full > -12  # originalet hörs också
    _, video = _render(client, draft_id, tmp_path, "svagt.mp4", **music, original_volume="0.3")
    weak = _band(video, 220, 1.0, 3.0) - _band(video, 880, 1.0, 3.0)
    assert full - 14 < weak < full - 7  # 30 % är cirka 10 dB svagare än 100 %


@needs_ffmpeg
def test_music_plays_at_normal_speed_when_the_clip_is_faster(client, tmp_path) -> None:
    _add_music(client, _music(tmp_path / MUSIC))
    draft_id = _draft(client, _video(tmp_path / "a.mp4", seconds=7))
    _, video = _render(client, draft_id, tmp_path, speed="1.1", audio="music", music=MUSIC, music_start_s="5",
                       original_volume="0")
    # Tonen byts 5 s in i klippet. Uppsnabbad hade den bytts vid 4,55 s, och tonen hade varit 968 Hz.
    assert _band(video, 440, 4.6, 4.9) > _band(video, 880, 4.6, 4.9) + 15
    assert _band(video, 880, 5.1, 5.3) > _band(video, 440, 5.1, 5.3) + 15


@needs_ffmpeg
def test_music_works_for_a_video_without_sound(client, tmp_path) -> None:
    _add_music(client, _music(tmp_path / MUSIC))
    draft_id = _draft(client, _video(tmp_path / "tyst.mp4", tone=None))
    state, video = _render(client, draft_id, tmp_path, audio="music", music=MUSIC, original_volume="0.3")
    assert state["problems"] == [] and _band(video, 440, 1.0, 3.0) > -45


# ---------- 4. Fel namn och fel filer ----------

@needs_ffmpeg
@pytest.mark.parametrize(("form", "text"), [
    ({"music": "../ton.wav"}, "Musiken ../ton.wav finns inte"),
    ({"music": "finns-inte.wav"}, "Musiken finns-inte.wav finns inte"),
    ({"music": ""}, "Välj en musikfil"),
    ({"music": MUSIC, "original_volume": "1.5"}, "Originalljudet"),
    ({"music": MUSIC, "music_start_s": "-1"}, "Musiken börjar"),
    ({"music": MUSIC, "music_start_s": "25"}, "Musiken är 20 s lång"),
    ({"audio": "hogt"}, "Ljudet 'hogt' finns inte"),
])
def test_a_bad_music_choice_is_400(client, settings, tmp_path, form, text) -> None:
    _add_music(client, _music(tmp_path / MUSIC))
    (settings.root / "musik" / MUSIC).write_bytes((settings.root / "musik" / "skiimasktimmy" / MUSIC).read_bytes())
    draft_id = _draft(client, _video(tmp_path / "a.mp4"))
    answer = client.post(f"/campaign/uploads/drafts/{draft_id}/render", data={**BASE, "audio": "music", **form})
    assert answer.status_code == 400 and text in answer.json()["detail"], answer.text
    # Bilden bryr sig inte om ljudet.
    picture = client.get(f"/campaign/uploads/drafts/{draft_id}/frame", params={**BASE, "audio": "music", **form})
    assert picture.status_code == 200, picture.text


@needs_ffmpeg
def test_only_sound_files_can_be_added_as_music(client, settings, tmp_path) -> None:
    text = tmp_path / "a.txt"
    text.write_text("inte musik")
    answer = _add_music(client, text)
    assert answer.status_code == 400 and "Filtypen .txt går inte" in answer.json()["detail"]
    answer = _add_music(client, _video(tmp_path / "tyst.mp4", tone=None))
    assert answer.status_code == 400 and "har inget ljud" in answer.json()["detail"]
    assert _add_music(client, _music(tmp_path / MUSIC), profile="finns-inte").status_code == 400
    assert _add_music(client, _music(tmp_path / MUSIC), profile="../profiles").status_code == 400
    assert not (settings.root / "musik").exists() or not list((settings.root / "musik").rglob("*.*"))
    # En video med ljud går, och samma namn två gånger skriver inte över.
    first = _add_music(client, _video(tmp_path / "låt.mp4"))
    second = _add_music(client, _video(tmp_path / "låt.mp4"))
    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json()["file"] != second.json()["file"]
    assert sorted(p.name for p in (settings.root / "musik" / "skiimasktimmy").iterdir()) == \
        sorted([first.json()["file"], second.json()["file"]])


def test_adding_music_needs_the_api_key(client, monkeypatch) -> None:
    monkeypatch.setenv("CLIPSMITH_API_KEY", "hemlig")
    answer = client.post("/campaign/uploads/music", files={"file": ("a.wav", b"x", "audio/wav")},
                         data={"profile": "skiimasktimmy"})
    assert answer.status_code == 401


# ---------- 5. Spara ----------

@needs_ffmpeg
def test_save_with_the_same_sound_keeps_the_file_and_other_sound_makes_it_again(client, settings, tmp_path,
                                                                               monkeypatch) -> None:
    calls = _count(monkeypatch)
    draft_id = _draft(client, _video(tmp_path / "a.mp4"))
    state, preview = _render(client, draft_id, tmp_path, audio="mute")
    saved = client.post(f"/campaign/uploads/drafts/{draft_id}/save", data={**BASE, "audio": "mute"})
    assert saved.status_code == 201, saved.text
    stored = ClipLog(settings.data_dir / "klipp.db").get(saved.json()["id"])
    assert preview.read_bytes() == open(stored["file"], "rb").read()
    assert len(calls) == 1
    choices = json.loads((settings.out_dir / "upload" / "val" / f"{saved.json()['filename'][:-4]}.json").read_text())
    assert {k: choices[k] for k in ("audio", "music", "music_start_s", "original_volume")} == \
        {"audio": "mute", "music": "", "music_start_s": 0.0, "original_volume": 0.3}

    draft_id = _draft(client, _video(tmp_path / "b.mp4"))
    _render(client, draft_id, tmp_path, "b.mp4", audio="mute")
    saved = client.post(f"/campaign/uploads/drafts/{draft_id}/save", data={**BASE, "audio": "keep"})
    assert saved.status_code == 201, saved.text
    assert len(calls) == 3  # ändrat ljud: klippet görs om
    assert _volume(ClipLog(settings.data_dir / "klipp.db").get(saved.json()["id"])["file"])["max_volume"] > -30


# ---------- 6. Redigera ----------

@needs_ffmpeg
def test_edit_keeps_the_sound_choice(client, settings, tmp_path) -> None:
    draft_id = _draft(client, _video(tmp_path / "a.mp4"))
    clip = client.post(f"/campaign/uploads/drafts/{draft_id}/save", data={**BASE, "audio": "mute"}).json()
    path = ClipLog(settings.data_dir / "klipp.db").get(clip["id"])["file"]
    answer = client.post(f"/campaign/clips/{clip['id']}/edit", json={"title": "Ny rubrik"})
    assert answer.status_code == 200, answer.text
    assert probe(path)["has_audio"] and _volume(path)["max_volume"] < -60
    assert answer.json()["problems"] == []


@needs_ffmpeg
def test_a_shorter_clip_fades_the_music_out_at_the_new_end(client, settings, tmp_path) -> None:
    _add_music(client, _music(tmp_path / MUSIC))
    draft_id = _draft(client, _video(tmp_path / "a.mp4", seconds=6))
    form = {**BASE, "audio": "music", "music": MUSIC, "music_start_s": "11", "original_volume": "0"}
    clip = client.post(f"/campaign/uploads/drafts/{draft_id}/save", data=form).json()
    path = ClipLog(settings.data_dir / "klipp.db").get(clip["id"])["file"]
    answer = client.post(f"/campaign/clips/{clip['id']}/edit", json={"out_s": 4.0})
    assert answer.status_code == 200, answer.text
    length = probe(path)["duration"]
    assert length == pytest.approx(4.1, abs=0.1)
    middle, end = _band(path, 880, 1.0, 2.5), _band(path, 880, length - 0.3, length)
    assert end < middle - 10  # tonar ut mot det nya slutet


# ---------- 7. Valen och Redigera-info ----------

@needs_ffmpeg
def test_options_list_the_music_and_the_campaigns_sound_rules(client, settings, tmp_path) -> None:
    shutil.copy(PAKET / "profiles" / "blizzard_nordics.yaml", settings.profiles_dir)
    blizzard = client.get("/campaign/uploads/options", params={"profile": "blizzard_nordics"}).json()
    assert blizzard["music"] == []  # ingen mapp ännu
    assert blizzard["audio_rules"] == ["Bakgrundsmusik får finnas om originalljudet hörs tydligt."]
    assert "fonts" in blizzard and "framings" in blizzard  # det som fanns finns kvar
    _add_music(client, _music(tmp_path / MUSIC))
    timmy = client.get("/campaign/uploads/options", params={"profile": "skiimasktimmy"}).json()
    assert timmy["music"] == [{"file": MUSIC, "duration_s": pytest.approx(20, abs=0.1)}]
    assert timmy["audio_rules"] == []


@needs_ffmpeg
def test_edit_info_gives_the_extra_length_of_an_own_video(client, settings, tmp_path) -> None:
    draft_id = _draft(client, _video(tmp_path / "a.mp4"))
    clip = client.post(f"/campaign/uploads/drafts/{draft_id}/save", data=BASE).json()
    assert client.get(f"/campaign/clips/{clip['id']}/edit").json()["length_extra_s"] == drafts.EXTRA_S
    (settings.out_dir / "upload" / "val" / f"{clip['filename'][:-4]}.json").unlink()  # ett gammalt klipp
    assert client.get(f"/campaign/clips/{clip['id']}/edit").json()["length_extra_s"] == 0
