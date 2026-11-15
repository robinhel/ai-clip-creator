"""Nattens rättningar i körningen (B1): Claude nere, format, titel och språk, förlopp vid hämtning,
en mapp per körning och en förklaring när en körning ger noll klipp.
Påhittade Claude, Twitch och ffmpeg. Inga riktiga anrop."""

from __future__ import annotations

import json
import shutil
import time
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from campaign import download, pipeline
from campaign.cliplog import ClipLog
from campaign.profile import load_profile
from clipsmith.candidates.builder import save_candidates
from clipsmith.llm.anthropic_provider import AnthropicProvider
from clipsmith.llm.base import ClipPick, LLMUnavailable
from clipsmith.llm.prompts import build_stream_context
from clipsmith.models.candidates import CandidateMoment
from clipsmith.models.transcript import Segment, Transcript
from clipsmith.selection.selector import select_clips
from clipsmith.settings import ClipConfig

from .conftest import PAKET, make_clip, needs_ffmpeg
from .test_api import client  # noqa: F401  (fixture)

FORMATS = [{"id": "clutch", "name": "Clutch plays", "description": "Multi kills and 1v3s."},
           {"id": "funny", "name": "Funny moments", "description": "Bad deaths and rage."}]
REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _status(code: int) -> anthropic.APIStatusError:
    response = httpx.Response(code, request=REQUEST)
    body = {"type": "error", "error": {"type": "x", "message": "Your credit balance is too low."}}
    return anthropic.Anthropic(api_key="test")._make_status_error("fel", body=body, response=response)


def _answer(**fields) -> SimpleNamespace:
    payload = {"include": True, "start_offset_s": 100.0, "end_offset_s": 120.0, "title_es": "Big play",
               "reason": "Hype.", "score": 70, **fields}
    usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
                            cache_creation_input_tokens=0)
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(payload))], usage=usage)


def _provider(answers: list, monkeypatch) -> tuple[AnthropicProvider, list, list]:
    """En provider vars svar tas i tur och ordning. Ett undantag i listan kastas."""
    provider = AnthropicProvider("test-key")
    calls: list = []
    sleeps: list = []

    def create(**kwargs):
        calls.append(kwargs)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    provider._client = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setattr(time, "sleep", sleeps.append)  # tenacity väntar med time.sleep
    return provider, calls, sleeps


def _candidate(t: float = 120.0) -> CandidateMoment:
    return CandidateMoment(t_center=t, score=25.0, sources=["chat_density"], reasons=["x"])


# ---------- 1. Claude nere eller fel nyckel ----------


@pytest.mark.parametrize("error", [lambda: _status(529), lambda: _status(500), lambda: _status(429),
                                   lambda: anthropic.APIConnectionError(request=REQUEST)])
def test_claude_is_asked_again_after_5_and_15_seconds(monkeypatch, error) -> None:
    provider, calls, sleeps = _provider([error(), error(), _answer()], monkeypatch)
    pick = provider.pick("text", _candidate(), "ctx")
    assert pick is not None and pick.include
    assert (len(calls), sleeps) == (3, [5, 15])


def test_claude_down_for_good_stops_the_run_after_three_more_tries(monkeypatch) -> None:
    provider, calls, sleeps = _provider([_status(529)] * 4, monkeypatch)
    with pytest.raises(LLMUnavailable) as error:
        provider.pick("text", _candidate(), "ctx")
    assert (len(calls), sleeps) == (4, [5, 15, 30])
    assert str(error.value) == "Claude svarade inte: Claude är överbelastat (529). Kör igen när det fungerar."


@pytest.mark.parametrize(("code", "reason"), [
    (401, "fel API-nyckel (401)"),
    (403, "nyckeln får inte använda Claude (403)"),
    (400, "fel 400, Your credit balance is too low."),
])
def test_wrong_key_stops_the_run_at_once_with_a_clear_swedish_error(monkeypatch, code, reason) -> None:
    provider, calls, sleeps = _provider([_status(code)], monkeypatch)
    with pytest.raises(LLMUnavailable) as error:
        provider.pick("text", _candidate(), "ctx")
    assert (len(calls), sleeps) == (1, [])
    assert str(error.value) == f"Claude svarade inte: {reason}. Kör igen när det fungerar."


def test_an_answer_that_is_not_json_skips_only_that_candidate(monkeypatch) -> None:
    bad = SimpleNamespace(content=[SimpleNamespace(type="text", text="inte json")], usage=_answer().usage)
    provider, _, _ = _provider([bad], monkeypatch)
    assert provider.pick("text", _candidate(), "ctx") is None


def _transcript() -> Transcript:
    return Transcript("123", "en", [Segment(start=t, end=t + 5, text="wow", words=[]) for t in range(0, 600, 5)])


class FakePicker:
    def __init__(self, answers: list):
        self.answers = answers
        self.contexts: list[str] = []

    def pick(self, window, candidate, stream_context):
        self.contexts.append(stream_context)
        return self.answers.pop(0)


def test_when_every_answer_fails_the_selection_stops_instead_of_giving_no_clips() -> None:
    cfg = ClipConfig(min_picks=0)
    with pytest.raises(LLMUnavailable, match="Claude svarade inte"):
        select_clips([_candidate(100), _candidate(400)], _transcript(), FakePicker([None, None]), "ctx", cfg)

    no = ClipPick(include=False, start_offset_s=0, end_offset_s=20, title_es="x", reason="dull")
    assert select_clips([_candidate(100), _candidate(400)], _transcript(), FakePicker([None, no]), "ctx",
                        cfg) == []


# ---------- 2 och 3. Format, titel, spel och språk till Claude ----------


def test_stream_context_has_title_game_language_and_the_campaigns_formats() -> None:
    ctx = build_stream_context("timmy", "3x Playtest Key GIVEAWAY", "3h52m30s", game="Bodycam",
                               language="sv", formats=FORMATS)
    assert "VOD title: 3x Playtest Key GIVEAWAY" in ctx
    assert "Game: Bodycam" in ctx
    assert "Language: sv" in ctx and "Write title_es in sv" in ctx
    assert "- clutch: Clutch plays. Multi kills and 1v3s." in ctx
    assert "- funny: Funny moments. Bad deaths and rage." in ctx
    assert '"format"' in ctx


def test_stream_context_without_campaign_is_unchanged() -> None:
    ctx = build_stream_context("timmy", "t", "1h")
    assert "Language: English\n" in ctx
    assert "format" not in ctx and "Game:" not in ctx and "Write title_es" not in ctx


def test_claudes_format_is_kept_in_the_pick() -> None:
    pick = ClipPick.from_json(json.dumps({"include": True, "start_offset_s": 1, "end_offset_s": 20,
                                          "title_es": "a", "reason": "b", "format": "clutch"}))
    assert pick.format == "clutch" and pick.to_dict()["format"] == "clutch"
    assert ClipPick.from_dict({"include": True}).format is None


# ---------- make_clips med påhittad Claude ----------


@pytest.fixture
def vod(settings, monkeypatch):
    """En nedladdad och transkriberad VOD. process_vod hittar kandidaterna i state.candidates."""
    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    mp4 = settings.work_dir / "123" / "123.mp4"
    mp4.parent.mkdir(parents=True)
    mp4.touch()
    (mp4.parent / "transcript.json").write_text(_transcript().to_json(), encoding="utf-8")
    state = SimpleNamespace(candidates=[_candidate(100), _candidate(400)], picker=None, cut=None,
                            finish=None, events=[], process=None)

    def process_vod(video, cfg, secrets, **kwargs):
        state.process = (video, kwargs)
        save_candidates(state.candidates, mp4.parent / "candidates.json")

    def cut_all_clips(mp4_path, transcript, picks, out_dir, cfg):
        state.cut = out_dir
        return [make_file(out_dir / f"clip_{i:02d}_x.mp4") for i, _ in enumerate(picks, 1)]

    def make_file(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path

    def finish_vod(*args, **kwargs):
        state.finish = kwargs
        return [{"id": 1, "file": "clip_01_x.mp4"}]

    monkeypatch.setattr(pipeline, "download_vod", lambda vod_id, work_dir, **k: mp4)
    monkeypatch.setattr(pipeline, "process_vod", process_vod)
    monkeypatch.setattr(pipeline, "get_provider", lambda cfg, secrets, **_: state.picker)
    monkeypatch.setattr(pipeline, "cut_all_clips", cut_all_clips)
    monkeypatch.setattr(pipeline, "finish_vod", finish_vod)
    state.mp4 = mp4
    return state


def _yes(fmt: str | None = None, start: float = 100.0) -> ClipPick:
    return ClipPick(include=True, start_offset_s=start, end_offset_s=start + 20, title_es="Big play",
                    reason="Hype.", score=80, format=fmt)


def _no() -> ClipPick:
    return ClipPick(include=False, start_offset_s=0, end_offset_s=20, title_es="x", reason="Dull.")


def _make(settings, vod, run_id: int | None = 7, **kwargs):
    log = ClipLog(settings.data_dir / "klipp.db")
    if run_id:
        log.add_run(run_id, "123", "skiimasktimmy", kwargs.get("game"), title="3x Playtest Key GIVEAWAY",
                    duration_s=13950)
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    return pipeline.make_clips("123", profile, settings, run_id=run_id, emit=lambda *e: vod.events.append(e),
                               **{"game": None, **kwargs})


def test_claude_gets_the_streams_title_game_language_focus_and_formats(settings, vod) -> None:
    vod.picker = FakePicker([_yes("clutch"), _no()])
    _make(settings, vod, game="Bodycam", focus="jump scares")
    ctx = vod.picker.contexts[0]
    assert "VOD title: 3x Playtest Key GIVEAWAY" in ctx and "Duration: 3h52m30s" in ctx
    assert "Game: Bodycam" in ctx and "Language: en" in ctx
    assert "especially looking for: jump scares" in ctx
    assert "- clutch: Clutch plays." in ctx and "- first_impressions: First impressions." in ctx
    assert "end_offset_s must be 15-45 seconds" in ctx
    video, kwargs = vod.process
    assert video.title == "3x Playtest Key GIVEAWAY" and kwargs["skip_select"]
    assert vod.finish["game"] == "Bodycam"


def test_the_format_is_saved_in_picks_json_and_an_unknown_id_becomes_no_format(settings, vod) -> None:
    vod.picker = FakePicker([_yes("clutch"), _yes("dance", start=400)])
    _make(settings, vod)
    saved = json.loads((settings.out_dir / "123" / "run_7" / "picks.json").read_text(encoding="utf-8"))
    assert [item["pick"]["format"] for item in saved] == ["clutch", None]


# ---------- 5. en mapp per körning ----------


def test_each_run_writes_its_clips_and_picks_in_its_own_folder(settings, vod) -> None:
    vod.picker = FakePicker([_yes("clutch"), _no()])
    _make(settings, vod, run_id=7)
    run_dir = settings.out_dir / "123" / "run_7"
    assert vod.cut == run_dir and vod.finish["subdir"] == "run_7"
    assert json.loads((run_dir / "picks.json").read_text())[0]["pick"]["title_es"] == "Big play"
    # clipsmiths egen läsning och facit läser senaste körningens val i work/<vod>/picks.json
    assert (settings.work_dir / "123" / "picks.json").read_text() == (run_dir / "picks.json").read_text()


def test_an_old_runs_clips_keep_their_picks_when_a_new_run_comes(settings, vod) -> None:
    old = settings.out_dir / "123" / "clip_01_old.mp4"
    old.parent.mkdir(parents=True)
    old.touch()
    old_picks = '[{"candidate": {}, "pick": {"title_es": "Old run"}}]'
    (settings.work_dir / "123" / "picks.json").write_text(old_picks, encoding="utf-8")
    vod.picker = FakePicker([_yes("clutch"), _no()])
    _make(settings, vod, run_id=7)
    assert (settings.out_dir / "123" / "picks.json").read_text(encoding="utf-8") == old_picks
    vod.picker = FakePicker([_yes("funny"), _no()])
    _make(settings, vod, run_id=8)
    assert (settings.out_dir / "123" / "picks.json").read_text(encoding="utf-8") == old_picks


def test_finish_again_uses_the_runs_folder_and_old_runs_their_vod_folder(settings, monkeypatch) -> None:
    seen: list = []
    monkeypatch.setattr(pipeline, "finish_vod", lambda *a, **k: seen.append(k["subdir"]) or [])
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    (settings.out_dir / "123" / "run_7").mkdir(parents=True)
    pipeline.finish("123", profile, settings, None, run_id=7)
    pipeline.finish("123", profile, settings, None, run_id=3)  # gammal körning, klippen ligger i out/123/
    assert seen == ["run_7", None]


@needs_ffmpeg
def test_clips_from_two_runs_get_their_own_picks(settings) -> None:
    from campaign.workflow import finish_vod

    shutil.copy(PAKET / "config.timmy.yaml", settings.root / "config.timmy.yaml")
    for run, title, start in (("run_1", "Old run", 10), ("run_2", "New run", 500)):
        make_clip(settings.out_dir / "123" / run / "clip_01_x.mp4")
        (settings.out_dir / "123" / run / "picks.json").write_text(json.dumps([{"candidate": {}, "pick": {
            "start_offset_s": start, "end_offset_s": start + 20, "title_es": title, "format": "funny"}}]))
    log = ClipLog(settings.data_dir / "klipp.db")
    profile = load_profile("skiimasktimmy", settings.profiles_dir)
    finish_vod("123", profile, settings, log, review=False, subdir="run_1")
    finish_vod("123", profile, settings, log, review=False, subdir="run_2")
    clips = {c["title"]: c for c in log.list()}
    assert (clips["Old run"]["start_s"], clips["New run"]["start_s"]) == (10, 500)
    assert clips["New run"]["file"] == str(settings.out_dir / "123" / "run_2" / "klara" / "clip_01_x.mp4")
    assert clips["New run"]["format"] == "funny"


def test_a_clip_in_a_run_folder_and_an_old_clip_both_work_in_the_app(client, settings) -> None:  # noqa: F811
    from campaign import editor

    log = ClipLog(settings.data_dir / "klipp.db")
    for folder in (settings.out_dir / "123", settings.out_dir / "123" / "run_7"):
        done = folder / "klara" / "clip_01_x.mp4"
        done.parent.mkdir(parents=True)
        done.write_bytes(b"mp4")
        clip_id = log.add(vod_id="123", profile="skiimasktimmy", file=str(done), title="t", caption="c")
        assert client.get(f"/campaign/clips/{clip_id}/file").status_code == 200
        assert editor.raw_path(log.get(clip_id), settings) == (folder / "clip_01_x.mp4").resolve()


# ---------- 6. noll klipp ----------


def test_no_candidates_gives_a_message_and_nothing_is_cut(settings, vod) -> None:
    vod.candidates = []
    _, message = _make(settings, vod)
    assert message == "Inga kandidater hittades."
    assert vod.cut is None and vod.finish is None


def test_claude_choosing_none_gives_a_message(settings, vod) -> None:
    vod.picker = FakePicker([_no(), _no()])
    _, message = _make(settings, vod)
    assert message == "Claude valde inga av 2 kandidater."
    assert vod.finish is None


def test_picks_that_were_all_skipped_give_a_message(settings, vod, monkeypatch) -> None:
    vod.picker = FakePicker([_yes("clutch"), _no()])
    monkeypatch.setattr(pipeline, "finish_vod", lambda *a, **k: [
        {"file": "clip_01_x.mp4", "skipped": "samma del av VOD:en är redan klippt"}])
    _, message = _make(settings, vod)
    assert message == "Inga nya klipp. Claude valde 1, men samma del av VOD:en är redan klippt."


def test_a_run_with_clips_has_no_message(settings, vod) -> None:
    vod.picker = FakePicker([_yes("clutch"), _no()])
    assert _make(settings, vod)[1] is None


def _run(client, settings, monkeypatch, result) -> dict:  # noqa: F811
    from clipsmith.db.models import Run, RunStatus
    from clipsmith.db.session import get_session

    db = get_session()
    run = Run(vod_id="123", channel="skiimasktimmy", status=RunStatus.pending)
    db.add(run)
    db.commit()
    run_id = run.id
    db.close()
    ClipLog(settings.data_dir / "klipp.db").add_run(run_id, "123", "skiimasktimmy", None)
    monkeypatch.setattr(pipeline, "_harvest_clips", lambda *a: None)
    monkeypatch.setattr(pipeline, "make_clips", result)
    pipeline.run_campaign_vod(run_id, "123", "skiimasktimmy", None, settings)
    return client.get(f"/campaign/runs/{run_id}").json()


def test_a_run_with_no_clips_shows_why_as_its_message(client, settings, monkeypatch) -> None:  # noqa: F811
    run = _run(client, settings, monkeypatch, lambda *a, **k: (None, "Claude valde inga av 18 kandidater."))
    assert (run["status"], run["message"]) == ("done", "Claude valde inga av 18 kandidater.")
    run = _run(client, settings, monkeypatch, lambda *a, **k: (None, None))
    assert (run["status"], run["message"]) == ("done", "Klart")


def test_claude_down_fails_the_run_with_the_swedish_error(client, settings, monkeypatch) -> None:  # noqa: F811
    def down(*a, **k):
        raise LLMUnavailable("Claude svarade inte: fel API-nyckel (401). Kör igen när det fungerar.")

    run = _run(client, settings, monkeypatch, down)
    assert (run["status"], run["error"]) == (
        "failed", "Claude svarade inte: fel API-nyckel (401). Kör igen när det fungerar.")


# ---------- 4. förlopp medan VOD:en hämtas ----------


class FakeTwitchDl:
    """twitch-dl skriver förloppet med \\r och utan radbrytning, som i en terminal."""

    # En bit per utskrift. "Downloaded 2/4 VODs" delas mellan två bitar, som det kan bli i en pipe.
    output = (b"Downloaded 0/4 VODs 0%", b"\r\x1b[2KDownloaded 1/4 VODs 25% of ~1GB",
              b"\r\x1b[2KDownloaded 1/4 VODs 26%", b"\r\x1b[2KDownloaded 2/", b"4 VODs 50%",
              b"\rDownloaded 4/4 VODs 100%\n")
    code = 0

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        pieces = list(self.output)
        self.stdout = SimpleNamespace(read1=lambda size: pieces.pop(0) if pieces else b"")

    def wait(self):
        return self.code


def test_download_reports_progress_from_twitch_dl(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(download, "_resolve_quality", lambda vod_id, quality: quality)
    monkeypatch.setattr(download.subprocess, "Popen", FakeTwitchDl)
    monkeypatch.setattr(download, "join_segments", lambda parts, out: out.write_bytes(b"mp4"))
    (tmp_path / "123").mkdir()  # twitch-dl skapar mappen
    seen: list = []
    out = download.download_vod("123", tmp_path, on_progress=lambda done, total: seen.append((done, total)))
    assert out == tmp_path / "123" / "123.mp4" and out.exists()
    assert seen == [(0, 4), (1, 4), (2, 4), (4, 4)]


def test_a_failed_download_still_raises(tmp_path, monkeypatch) -> None:
    import subprocess

    monkeypatch.setattr(download, "_resolve_quality", lambda vod_id, quality: quality)
    monkeypatch.setattr(download.subprocess, "Popen", type("Broken", (FakeTwitchDl,), {"code": 1}))
    with pytest.raises(subprocess.CalledProcessError):
        download.download_vod("123", tmp_path)


def test_make_clips_shows_the_download_in_percent_between_5_and_15(settings, vod, monkeypatch) -> None:
    vod.picker = FakePicker([_yes("clutch"), _no()])

    def fake_download(vod_id, work_dir, on_progress):
        for done in (0, 1, 2, 4):
            on_progress(done, 4)
        return vod.mp4

    monkeypatch.setattr(pipeline, "download_vod", fake_download)
    _make(settings, vod)
    hämtar = [e for e in vod.events if e[0] == "download" and e[2].startswith("Hämtar: ")]
    assert [e[2] for e in hämtar] == ["Hämtar: 0 %", "Hämtar: 25 %", "Hämtar: 50 %", "Hämtar: 100 %"]
    assert [e[1] for e in hämtar] == [5.0, 7.5, 10.0, 15.0]


def test_a_long_vod_gives_one_download_event_per_percent(settings, vod, monkeypatch) -> None:
    vod.picker = FakePicker([_yes("clutch"), _no()])

    def fake_download(vod_id, work_dir, on_progress):
        for done in range(1401):  # en VOD på 4 timmar har ungefär 1 400 delar
            on_progress(done, 1400)
        return vod.mp4

    monkeypatch.setattr(pipeline, "download_vod", fake_download)
    _make(settings, vod)
    assert len([e for e in vod.events if e[2].startswith("Hämtar: ")]) == 101


def test_clipsmith_gets_the_channels_id_and_the_vods_start_so_viewer_clips_are_counted(
        settings, vod, monkeypatch) -> None:
    """Med tomt user_id hoppade clipsmith över tittarnas klipp, den starkaste signalen (+100)."""
    monkeypatch.setenv("TWITCH_CLIENT_ID", "id")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "secret")

    class FakeTwitch:
        def __init__(self, client_id, client_secret):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def get_user_id(self, login):
            return {"skiimasktimmy": "318314979"}[login]

    monkeypatch.setattr(pipeline, "TwitchClient", FakeTwitch)
    monkeypatch.setattr(pipeline, "vod_facts", lambda vod_id: {"created_at": "2026-11-04T18:00:00Z"})
    vod.picker = FakePicker([_no(), _no()])
    _make(settings, vod)
    video, _ = vod.process
    assert (video.user_id, video.created_at) == ("318314979", "2026-11-04T18:00:00Z")


def test_without_twitch_keys_the_run_still_works_without_viewer_clips(settings, vod, monkeypatch) -> None:
    monkeypatch.setenv("TWITCH_CLIENT_ID", "")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "")
    monkeypatch.setattr(pipeline, "vod_facts", lambda vod_id: (_ for _ in ()).throw(ConnectionError("nere")))
    vod.picker = FakePicker([_no(), _no()])
    _make(settings, vod)
    video, _ = vod.process
    assert video.user_id == ""


def test_claude_has_room_for_a_long_reason_and_a_cut_answer_is_logged_as_cut(monkeypatch, caplog) -> None:
    """Körning 5: tre svar slutade mitt i en sträng (\"Unterminated string\") med max_tokens 512."""
    cut = SimpleNamespace(content=[SimpleNamespace(type="text", text='{"include": false, "reason": "Long')],
                          usage=_answer().usage, stop_reason="max_tokens")
    provider, calls, _ = _provider([cut], monkeypatch)
    with caplog.at_level("WARNING"):
        assert provider.pick("text", _candidate(), "ctx") is None
    assert calls[0]["max_tokens"] >= 1024
    assert "klipptes av" in caplog.text


def test_text_after_the_json_does_not_lose_the_answer(monkeypatch) -> None:
    answer = _answer()
    answer.content[0].text += "\n\nThe clip has a clear payoff."
    provider, _, _ = _provider([answer], monkeypatch)
    pick = provider.pick("text", _candidate(), "ctx")
    assert pick is not None and pick.include


class RankingPicker(FakePicker):
    def __init__(self, answers: list):
        super().__init__(answers)
        self.ranked: list[list[str]] = []

    def rank(self, lines, stream_context):
        self.ranked.append(lines)
        return [90, 30]


def test_the_app_run_calibrates_the_scores_with_rank(settings, vod) -> None:
    vod.picker = RankingPicker([_yes("clutch"), _yes("clutch", start=400)])
    _make(settings, vod)
    assert len(vod.picker.ranked) == 1
    saved = json.loads((settings.out_dir / "123" / "run_7" / "picks.json").read_text(encoding="utf-8"))
    assert [item["pick"]["score"] for item in saved] == [90, 30]
