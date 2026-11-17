"""Transkribering via Berget. Inga riktiga anrop görs."""

from __future__ import annotations

import httpx
import pytest

from campaign.transcribe import LEAD_S, berget_post, to_segments, transcribe_vod
from clipsmith.models.transcript import Transcript

from .conftest import make_clip, needs_ffmpeg


def probe_audio(path) -> float:
    import subprocess

    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout)


def _answer(offset_free_words: list[tuple[str, float, float]], segments: list[tuple[float, float, str]]) -> dict:
    return {
        "text": " ".join(s[2] for s in segments),
        "duration": 2.0,
        "language": "en",
        "segments": [{"id": i, "start": s, "end": e, "text": t} for i, (s, e, t) in enumerate(segments)],
        "words": [{"word": w, "start": s, "end": e, "score": 0.8} for w, s, e in offset_free_words],
    }


def test_to_segments_moves_times_and_puts_words_in_their_segment() -> None:
    answer = _answer(
        [("no", 0.1, 0.3), ("way", 0.4, 0.9), ("let's", 2.1, 2.4), ("go", 2.5, 2.8)],
        [(0.0, 1.0, "no way"), (2.0, 3.0, "let's go")],
    )
    segments = to_segments(answer, offset_s=1800.0)

    assert [(s.start, s.end, s.text) for s in segments] == [
        (1800.0, 1801.0, "no way"),
        (1802.0, 1803.0, "let's go"),
    ]
    assert [w.word for w in segments[0].words] == ["no", "way"]
    assert [w.word for w in segments[1].words] == ["let's", "go"]
    assert segments[1].words[0].start == 1802.1
    assert segments[1].words[0].probability == 0.8


def test_to_segments_without_word_times_keeps_the_text() -> None:
    answer = _answer([], [(0.0, 1.0, "hello")])
    del answer["words"]
    segments = to_segments(answer, offset_s=0.0)
    assert segments[0].text == "hello"
    assert segments[0].words == []


@needs_ffmpeg
def test_transcribe_vod_sends_each_part_and_writes_clipsmiths_transcript(settings) -> None:
    mp4 = make_clip(settings.work_dir / "123" / "123.mp4", size="320x180", seconds=5)
    out = mp4.parent / "transcript.json"
    sent: list[dict] = []
    lengths: list[float] = []

    def fake_post(audio: bytes, fields: dict) -> dict:
        sent.append(fields)
        part = settings.root / "del.mp3"
        part.write_bytes(audio)
        lengths.append(probe_audio(part))
        # Berget's times count from the start of the audio it got, which begins with LEAD_S of silence.
        return _answer([("hi", LEAD_S + 0.5, LEAD_S + 0.9)], [(LEAD_S + 0.2, LEAD_S + 1.0, f"part {len(sent)}")])

    transcribe_vod(mp4, "123", out, post=fake_post, model="KBLab/kb-whisper-large", language="en", chunk_s=3)

    # Whisper dropped the first 7 s of speech in a file that talks from 0.1 s; with silence first it did not.
    assert lengths == [pytest.approx(3 + LEAD_S, abs=0.1), pytest.approx(2 + LEAD_S, abs=0.1)]
    assert len(sent) == 2
    assert sent[0]["model"] == "KBLab/kb-whisper-large"
    assert sent[0]["language"] == "en"
    assert sent[0]["response_format"] == "verbose_json"
    assert sent[0]["align"] == "true"
    transcript = Transcript.from_json(out.read_text(encoding="utf-8"))
    assert transcript.video_id == "123"
    assert transcript.language == "en"
    assert [(s.start, s.text) for s in transcript.segments] == [(0.2, "part 1"), (3.2, "part 2")]
    assert transcript.segments[1].words[0].start == 3.5


def _fake_send(statuses: list[int], headers: dict | None = None):
    """Svarar med statuskoderna i tur och ordning. Inga riktiga anrop."""
    calls: list[dict] = []

    def send(url, **kwargs) -> httpx.Response:
        calls.append(kwargs)
        status = statuses[len(calls) - 1]
        body = _answer([], [(0.0, 1.0, "ok")]) if status == 200 else {"error": {"message": "fel"}}
        return httpx.Response(status, json=body, headers=headers or {})

    return send, calls


def test_berget_post_tries_again_after_500() -> None:
    send, calls = _fake_send([500, 500, 200])
    pauses: list[float] = []
    post = berget_post("hemlig-nyckel", send=send, sleep=pauses.append)

    assert post(b"ljud", {})["segments"][0]["text"] == "ok"
    assert len(calls) == 3
    assert pauses == [10, 30]


def test_berget_post_respects_retry_after_up_to_120_s() -> None:
    send, _ = _fake_send([429, 200], headers={"Retry-After": "500"})
    pauses: list[float] = []
    berget_post("k", send=send, sleep=pauses.append)(b"ljud", {})
    assert pauses == [120]


def test_berget_post_tries_again_after_network_error() -> None:
    calls: list[int] = []

    def send(url, **kwargs) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("nätet borta")
        return httpx.Response(200, json=_answer([], [(0.0, 1.0, "ok")]))

    berget_post("k", send=send, sleep=lambda s: None)(b"ljud", {})
    assert len(calls) == 2


def test_berget_post_gives_up_at_once_on_400() -> None:
    send, calls = _fake_send([400, 200])
    with pytest.raises(RuntimeError, match="400"):
        berget_post("k", send=send, sleep=lambda s: None)(b"ljud", {})
    assert len(calls) == 1


@needs_ffmpeg
def test_four_500_in_a_row_names_the_part_and_hides_the_key(settings) -> None:
    mp4 = make_clip(settings.work_dir / "123" / "123.mp4", size="320x180", seconds=5)
    send, calls = _fake_send([500] * 4)
    pauses: list[float] = []
    post = berget_post("hemlig-nyckel", send=send, sleep=pauses.append)

    with pytest.raises(RuntimeError) as error:
        transcribe_vod(mp4, "123", mp4.parent / "transcript.json", post=post, model="m", language="en", chunk_s=3)

    assert len(calls) == 4
    assert pauses == [10, 30, 60]
    message = str(error.value)
    assert "Del 1 av 2" in message
    assert "sparade" in message
    assert "fortsätter" in message
    assert "hemlig-nyckel" not in message


def _scripted_post(texts: list[str | None]):
    """Ett svar per anrop, None betyder att anropet misslyckas."""
    sent: list[dict] = []

    def post(audio: bytes, fields: dict) -> dict:
        sent.append(fields)
        text = texts[len(sent) - 1]
        if text is None:
            raise RuntimeError("Berget svarade 500")
        return _answer([("hi", 0.5, 0.9)], [(0.2, 1.0, text)])

    return post, sent


@needs_ffmpeg
def test_a_new_run_sends_only_the_missing_parts(settings) -> None:
    clean = make_clip(settings.work_dir / "ren" / "ren.mp4", size="320x180", seconds=5)
    post, _ = _scripted_post(["a", "b", "c"])
    transcribe_vod(clean, "123", clean.parent / "transcript.json", post=post, model="m", language="en", chunk_s=2)

    mp4 = make_clip(settings.work_dir / "123" / "123.mp4", size="320x180", seconds=5)
    out = mp4.parent / "transcript.json"
    post, _ = _scripted_post(["a", None])
    with pytest.raises(RuntimeError, match="Del 2 av 3"):
        transcribe_vod(mp4, "123", out, post=post, model="m", language="en", chunk_s=2)
    assert (mp4.parent / "berget" / "del_01.json").exists()
    assert not (mp4.parent / "berget" / "del_02.json").exists()

    post, sent = _scripted_post(["b", "c"])
    transcribe_vod(mp4, "123", out, post=post, model="m", language="en", chunk_s=2)

    assert len(sent) == 2
    assert out.read_text(encoding="utf-8") == (clean.parent / "transcript.json").read_text(encoding="utf-8")
    assert not (mp4.parent / "berget").exists()


@needs_ffmpeg
def test_saved_parts_are_not_used_when_the_model_changes(settings) -> None:
    mp4 = make_clip(settings.work_dir / "123" / "123.mp4", size="320x180", seconds=5)
    out = mp4.parent / "transcript.json"
    post, _ = _scripted_post(["gammal", None])
    with pytest.raises(RuntimeError):
        transcribe_vod(mp4, "123", out, post=post, model="gammal-modell", language="en", chunk_s=2)

    post, sent = _scripted_post(["a", "b", "c"])
    transcript = transcribe_vod(mp4, "123", out, post=post, model="ny-modell", language="en", chunk_s=2)

    assert len(sent) == 3
    assert {f["model"] for f in sent} == {"ny-modell"}
    assert [s.text for s in transcript.segments] == ["a", "b", "c"]
