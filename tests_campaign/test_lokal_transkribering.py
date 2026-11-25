"""Transkribering: Berget eller en lokal Whisper-modell. Ingen riktig modell laddas ner och Berget anropas aldrig."""

from __future__ import annotations

import json
import os
import shutil
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from campaign import api, estimate, keys, local_whisper, pipeline
from campaign.budget import budget_from_settings
from campaign.config import Settings, load_settings
from campaign.profile import load_profile
from clipsmith.models.transcript import Transcript

from .conftest import PAKET, make_clip, needs_ffmpeg

ENGINE_NAMES = ("TRANSCRIBE_ENGINE", "LOCAL_WHISPER_MODEL", "BERGET_API_KEY")


@pytest.fixture(autouse=True)
def no_download(monkeypatch):
    """Ingen modell hämtas från Hugging Face: modellens namn används som dess mapp."""
    monkeypatch.setattr(local_whisper, "_download", lambda model: model)


@pytest.fixture
def env(settings, monkeypatch):
    for name in ENGINE_NAMES:
        monkeypatch.setenv(name, "")
    monkeypatch.delenv("CLIPSMITH_API_KEY", raising=False)
    monkeypatch.delenv("KLIPP_ENV_FILE", raising=False)
    monkeypatch.setattr(local_whisper, "is_downloaded", lambda model: model == "small")
    path = settings.root / ".env"
    path.write_text("# nycklar\nOTHER=1\n")
    return path


def _client(settings, host: str = "127.0.0.1") -> TestClient:
    app = FastAPI()
    app.include_router(keys.router)
    app.dependency_overrides[api.get_settings] = lambda: load_settings(settings.root)
    return TestClient(app, base_url="http://127.0.0.1:8000", client=(host, 50000))


# ---------- standardval ----------

def test_the_default_is_berget_with_a_key_and_local_without(tmp_path) -> None:
    assert Settings(root=tmp_path, berget_api_key="x").engine == "berget"
    assert Settings(root=tmp_path).engine == "local"
    assert Settings(root=tmp_path, berget_api_key=None).engine == "local"  # type: ignore[arg-type]
    assert Settings(root=tmp_path, berget_api_key="x", transcribe_engine="local").engine == "local"
    assert Settings(root=tmp_path, transcribe_engine="berget").engine == "berget"
    assert Settings(root=tmp_path).local_whisper_model == "small"


def test_settings_read_the_choice_from_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BERGET_API_KEY", "x")
    monkeypatch.setenv("TRANSCRIBE_ENGINE", "local")
    monkeypatch.setenv("LOCAL_WHISPER_MODEL", "medium")
    settings = load_settings(tmp_path)
    assert (settings.engine, settings.local_whisper_model) == ("local", "medium")
    monkeypatch.setenv("TRANSCRIBE_ENGINE", "nonsense")  # ett trasigt värde i .env: standardvalet
    monkeypatch.setenv("LOCAL_WHISPER_MODEL", "huge")
    settings = load_settings(tmp_path)
    assert (settings.engine, settings.local_whisper_model) == ("berget", "small")


# ---------- Settings: läsa och spara ----------

def test_status_shows_choice_model_download_and_size(env, settings) -> None:
    body = _client(settings).get("/campaign/transcription").json()
    assert body == {"engine": "local", "model": "small", "berget_key": False,  # ingen nyckel och inget val
                    "models": [{"id": "small", "size_gb": 0.5, "downloaded": True},
                               {"id": "medium", "size_gb": 1.5, "downloaded": False},
                               {"id": "large-v3", "size_gb": 3.0, "downloaded": False}]}


def test_saving_the_choice_writes_env_and_works_at_once(env, settings) -> None:
    client = _client(settings)
    response = client.put("/campaign/transcription", json={"engine": "local", "model": "medium"})
    assert response.status_code == 200
    body = response.json()
    assert (body["engine"], body["model"]) == ("local", "medium")
    assert env.read_text() == "# nycklar\nOTHER=1\nTRANSCRIBE_ENGINE=local\nLOCAL_WHISPER_MODEL=medium\n"
    assert os.environ["TRANSCRIBE_ENGINE"] == "local"
    assert load_settings(settings.root).local_whisper_model == "medium"

    assert client.put("/campaign/transcription", json={"engine": "berget"}).json()["engine"] == "berget"
    assert "TRANSCRIBE_ENGINE=berget\nLOCAL_WHISPER_MODEL=medium\n" in env.read_text()
    assert client.get("/campaign/transcription").json()["engine"] == "berget"


@pytest.mark.parametrize("body", [
    {"engine": "cloud"},
    {"engine": "local", "model": "tiny"},
    {"model": "large-v2"},
    {"engine": 1},
    {"engine": "local\nANTHROPIC_API_KEY=x"},
    {},
    [],
])
def test_a_wrong_value_gives_400_and_saves_nothing(env, settings, body) -> None:
    before = env.read_text()
    assert _client(settings).put("/campaign/transcription", json=body).status_code == 400
    assert env.read_text() == before


def test_saving_the_choice_needs_the_same_guard_as_the_keys(env, settings, monkeypatch) -> None:
    before = env.read_text()
    assert _client(settings, host="192.168.1.20").put("/campaign/transcription",
                                                       json={"engine": "local"}).status_code == 403
    monkeypatch.setenv("CLIPSMITH_API_KEY", "server-key")
    assert _client(settings).put("/campaign/transcription", json={"engine": "local"}).status_code == 401
    assert env.read_text() == before


def test_is_downloaded_reads_the_hugging_face_cache_without_downloading(tmp_path) -> None:
    assert local_whisper.is_downloaded("small", cache_dir=tmp_path) is False
    repo = tmp_path / "models--Systran--faster-whisper-small"
    (repo / "refs").mkdir(parents=True)
    (repo / "refs" / "main").write_text("abc123")
    (repo / "snapshots" / "abc123").mkdir(parents=True)
    (repo / "snapshots" / "abc123" / "model.bin").write_bytes(b"x")
    assert local_whisper.is_downloaded("small", cache_dir=tmp_path) is True
    assert local_whisper.is_downloaded("medium", cache_dir=tmp_path) is False


# ---------- körningen ----------

class FakeWhisper:
    """Låtsas-faster-whisper: säger "hello" i varje bit."""

    created: list[tuple] = []

    def __init__(self, name, device="cpu", compute_type="int8"):
        FakeWhisper.created.append((name, compute_type))

    def transcribe(self, path, language=None, **kwargs):
        word = SimpleNamespace(start=0.5, end=0.9, word=" hello", probability=0.9)
        return iter([SimpleNamespace(start=0.4, end=1.0, text=" hello", words=[word])]), SimpleNamespace(
            language=language)


@pytest.fixture
def run(settings, monkeypatch):
    """make_clips med hämtning och clipsmith mockade. Returnerar (kör, anrop, händelser)."""
    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    monkeypatch.chdir(settings.root)
    mp4 = settings.work_dir / "123" / "123.mp4"
    calls: dict = {}
    events: list[tuple] = []

    def fake_download(vod_id, work_dir, **kwargs):
        if not mp4.exists():
            mp4.parent.mkdir(parents=True, exist_ok=True)
            mp4.touch()
        return mp4

    def fake_berget(*args, **kwargs):
        calls["berget"] = kwargs
        args[2].write_text(Transcript(video_id="123", language="en", segments=[]).to_json(), encoding="utf-8")

    monkeypatch.setattr(pipeline, "download_vod", fake_download)
    monkeypatch.setattr(pipeline, "transcribe_vod", fake_berget)
    monkeypatch.setattr(pipeline, "berget_post", lambda key: calls.setdefault("post", key))
    monkeypatch.setattr(pipeline, "process_vod", lambda video, cfg, secrets, **k: calls.update(
        process=k) or (mp4.parent / "candidates.json").write_text("[]"))
    FakeWhisper.created = []
    monkeypatch.setattr("faster_whisper.WhisperModel", FakeWhisper)
    profile = load_profile("skiimasktimmy", settings.profiles_dir)

    def go():
        pipeline.make_clips("123", profile, settings, game=None, emit=lambda *e: events.append(e))

    return go, calls, events, mp4


@needs_ffmpeg
def test_local_choice_transcribes_with_clipsmiths_whisper_and_writes_the_same_transcript(run, settings) -> None:
    go, calls, events, mp4 = run
    make_clip(mp4, size="320x180", seconds=3)
    settings.berget_api_key = "test"
    settings.transcribe_engine = "local"
    settings.local_whisper_model = "medium"

    go()

    assert "berget" not in calls and "post" not in calls
    assert FakeWhisper.created == [("medium", "int8")]  # compute_type från kampanjens config
    transcript = Transcript.from_json((mp4.parent / "transcript.json").read_text(encoding="utf-8"))
    assert (transcript.video_id, transcript.language) == ("123", "en")  # kampanjens språk
    assert [(s.start, s.text) for s in transcript.segments] == [(0.4, " hello")]
    assert [(w.word, w.start, w.probability) for w in transcript.segments[0].words] == [(" hello", 0.5, 0.9)]
    assert set(json.loads((mp4.parent / "transcript.json").read_text())) == {"video_id", "language", "segments"}
    messages = [m for stage, _, m in events if stage == "transcribe"]
    assert messages[0].startswith("Loading Whisper medium")
    assert "Transcribing locally with Whisper medium, part 1 of 1" in messages
    assert calls["process"]["skip_transcribe"] is True
    assert budget_from_settings(settings).summary()["by_purpose"] == {}  # lokal transkribering kostar inget


def test_berget_choice_uses_berget(run, settings) -> None:
    go, calls, events, mp4 = run
    settings.berget_api_key = "test"
    go()
    assert calls["berget"]["model"] == settings.transcribe_model and calls["post"] == "test"
    assert FakeWhisper.created == []
    assert any(m.startswith("Transcribing with Berget") for stage, _, m in events if stage == "transcribe")


def test_berget_chosen_without_a_key_says_local_is_possible(run, settings) -> None:
    go, calls, events, mp4 = run
    settings.transcribe_engine = "berget"
    with pytest.raises(ValueError) as error:
        go()
    assert "BERGET_API_KEY" in str(error.value) and "Local" in str(error.value) and "Settings" in str(error.value)
    assert "berget" not in calls and FakeWhisper.created == []


# ---------- kampanjens läge, uppskattning och kontroll ----------

def test_a_missing_berget_key_only_blocks_when_berget_is_chosen(settings) -> None:
    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    local = api._describe_profile("skiimasktimmy", settings)
    assert local["key_problems"] == [] and local["ready"] is True
    settings.transcribe_engine = "berget"
    berget = api._describe_profile("skiimasktimmy", settings)
    assert berget["key_problems"] == [keys.BERGET_MISSING] and berget["ready"] is False


def test_the_estimate_has_no_berget_cost_and_a_slower_transcription_when_local(settings) -> None:
    settings.berget_api_key = "x"
    def rate() -> float:
        return estimate.transcribe_rate(settings.engine, settings.local_whisper_model)

    cloud = estimate.estimate(4 * 3600, 100.0, True, api._berget_price(settings), transcribe_rate=rate())
    settings.transcribe_engine = "local"
    local = estimate.estimate(4 * 3600, 100.0, True, api._berget_price(settings), transcribe_rate=rate())
    assert cloud["cost"]["berget_usd"] > 0 and local["cost"]["berget_usd"] == 0
    assert 30 <= local["minutes"] - cloud["minutes"] + 8 <= 90  # small: 30-90 min för 4 timmar
    settings.local_whisper_model = "large-v3"
    large = estimate.estimate(4 * 3600, 100.0, True, 0.0, transcribe_rate=rate())
    assert large["minutes"] > local["minutes"]


def test_setup_check_shows_the_transcription_choice(env, settings, monkeypatch) -> None:
    rows = {r["id"]: r for r in _client(settings).get("/campaign/setup").json()["checks"]}
    assert rows["berget"]["ok"] is True and "Local" in rows["berget"]["detail"]
    monkeypatch.setenv("TRANSCRIBE_ENGINE", "berget")
    rows = {r["id"]: r for r in _client(settings).get("/campaign/setup").json()["checks"]}
    assert rows["berget"]["ok"] is False


# ---------- delarna: inga dubbla meningar och avbryt direkt ----------

class MinuteWhisper:
    """Låtsas-faster-whisper: ett segment var 20:e sekund i varje del, efter delens längd."""

    calls: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    def transcribe(self, path, **kwargs):
        MinuteWhisper.calls.append(path)
        seconds = PART_SECONDS[path]
        segments = [SimpleNamespace(start=k * 20 + 5.0, end=k * 20 + 7.0, text=f" line {k}", words=[])
                    for k in range(int(seconds // 20))]
        return iter(segments), SimpleNamespace(language="en")


PART_SECONDS: dict[str, float] = {}


@pytest.fixture
def parts(monkeypatch):
    """Ingen riktig modell och ingen ffmpeg: längden styr hur många delar det blir."""
    from clipsmith.transcription import transcriber

    MinuteWhisper.calls = []
    PART_SECONDS.clear()
    monkeypatch.setattr("faster_whisper.WhisperModel", MinuteWhisper)
    monkeypatch.setattr(transcriber, "_extract_audio_chunk",
                        lambda mp4, start, dur, wav: PART_SECONDS.__setitem__(str(wav), dur))

    def set_duration(seconds: float) -> None:
        monkeypatch.setattr(transcriber, "video_duration", lambda mp4: seconds)

    return set_duration


def test_local_parts_do_not_repeat_the_speech_after_each_border(parts, tmp_path) -> None:
    from clipsmith.config.models import TranscribeConfig

    parts(3600.0)  # två delar à 30 minuter
    config = TranscribeConfig(chunk_minutes=30, chunk_overlap_s=30, max_workers=1)  # som config.timmy.yaml
    transcript = local_whisper.transcribe_local(tmp_path / "v.mp4", "v", tmp_path / "transcript.json", config,
                                                model="small", language="en")
    starts = [s.start for s in transcript.segments]
    assert len(MinuteWhisper.calls) == 2
    assert len(starts) == len(set(starts)) == 180


def test_cancel_stops_the_local_parts_that_have_not_started(parts, tmp_path) -> None:
    from campaign.workflow import RunCancelled
    from clipsmith.config.models import TranscribeConfig

    parts(4 * 60.0)  # fyra delar à en minut, en tråd

    def on_part(n: int, total: int) -> None:
        if n >= 2:
            raise RunCancelled("You cancelled.")

    with pytest.raises(RunCancelled):
        local_whisper.transcribe_local(tmp_path / "v.mp4", "v", tmp_path / "transcript.json",
                                       TranscribeConfig(chunk_minutes=1, max_workers=1),
                                       model="small", language="en", on_part=on_part)
    assert len(MinuteWhisper.calls) < 4


def test_a_model_that_cannot_be_loaded_gives_a_short_message_and_logs_the_error(monkeypatch, tmp_path,
                                                                                caplog) -> None:
    from clipsmith.config.models import TranscribeConfig

    def offline(model):
        raise OSError("We couldn't connect to 'https://huggingface.co' to load the files")

    monkeypatch.setattr(local_whisper, "_download", offline)
    with pytest.raises(RuntimeError) as error:
        local_whisper.transcribe_local(tmp_path / "v.mp4", "v", tmp_path / "transcript.json",
                                       TranscribeConfig(chunk_minutes=30), model="medium", language="en")
    assert str(error.value) == ("Could not load the local Whisper model medium. Check the internet connection "
                                "and free disk space (about 1.5 GB).")
    assert "huggingface.co" in caplog.text


# ---------- python -m campaign transcribe ----------

@pytest.fixture
def cli_vod(settings, monkeypatch):
    from campaign import cli

    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    mp4 = settings.work_dir / "123" / "123.mp4"
    mp4.parent.mkdir(parents=True)
    mp4.touch()
    calls: list = []

    def fake_local(mp4, vod_id, out, config, *, model, language, on_part=None):
        calls.append((vod_id, model, language, config.chunk_minutes))
        on_part(1, 1)
        return Transcript(video_id=vod_id, language=language, segments=[])

    monkeypatch.setattr(cli, "transcribe_local", fake_local)
    monkeypatch.setattr(cli, "transcribe_vod", lambda *a, **k: pytest.fail("Berget anropades"))
    args = SimpleNamespace(vod_id="123", model=None, profile="skiimasktimmy", overwrite=False)
    return lambda: cli.cmd_transcribe(args, settings), calls


def test_the_transcribe_command_uses_the_local_model_without_a_cost_line(cli_vod, settings, capsys) -> None:
    go, calls = cli_vod
    settings.transcribe_engine, settings.local_whisper_model = "local", "medium"
    assert go() == 0
    assert calls == [("123", "medium", "en", 30)]  # kampanjens clipsmith-config
    printed = capsys.readouterr().out
    assert "Loading Whisper medium" in printed and "Part 1 of 1" in printed
    assert "euro" not in printed


def test_the_transcribe_command_with_berget_and_no_key_gives_the_common_text(cli_vod, settings) -> None:
    go, calls = cli_vod
    settings.transcribe_engine = "berget"
    with pytest.raises(SystemExit) as error:
        go()
    assert str(error.value) == keys.BERGET_MISSING and calls == []
