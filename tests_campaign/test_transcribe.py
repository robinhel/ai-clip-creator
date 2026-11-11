"""Transkribering via Berget. Inga riktiga anrop görs."""

from __future__ import annotations

from campaign.transcribe import to_segments, transcribe_vod
from clipsmith.models.transcript import Transcript

from .conftest import make_clip, needs_ffmpeg


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

    def fake_post(audio: bytes, fields: dict) -> dict:
        sent.append(fields)
        assert len(audio) > 0
        return _answer([("hi", 0.5, 0.9)], [(0.2, 1.0, f"part {len(sent)}")])

    transcribe_vod(mp4, "123", out, post=fake_post, model="KBLab/kb-whisper-large", language="en", chunk_s=3)

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
