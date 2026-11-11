"""Transkribering via Berget i stället för på den egna datorn.

Ljudet delas i bitar på 30 minuter (Berget tar högst 100 MB och 30 minuters
bearbetning per anrop). Resultatet sparas som clipsmiths transcript.json, så att
`clipsmith run-vod ... --skip-transcribe` använder det.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import httpx

from clipsmith.models.transcript import Segment, Transcript, Word

from .media import need, probe

BERGET_URL = "https://api.berget.ai/v1/audio/transcriptions"
EUR_PER_MINUTE = 1.98 / 1000  # docs.berget.ai/models/overview, november 2026

Post = Callable[[bytes, dict], dict]


def to_segments(answer: dict, offset_s: float) -> list[Segment]:
    """Gör om Bergets svar (verbose_json) till clipsmiths meningar med ord.

    Berget ger meningarna och orden i två platta listor. Varje ord läggs i den
    mening där det börjar. Alla tider flyttas med offset_s, bitens start i videon.
    """
    words = sorted(answer.get("words") or [], key=lambda w: w["start"])
    segments: list[Segment] = []
    for seg in answer["segments"]:
        mine = [
            Word(
                start=w["start"] + offset_s,
                end=w["end"] + offset_s,
                word=w["word"],
                probability=w.get("score", 1.0),
            )
            for w in words
            if seg["start"] <= w["start"] < seg["end"]
        ]
        segments.append(
            Segment(start=seg["start"] + offset_s, end=seg["end"] + offset_s, text=seg["text"], words=mine)
        )
    return segments


def _audio_part(mp4: Path, start_s: float, length_s: float) -> bytes:
    """En bit av ljudet som mp3, mono, 16 kHz (cirka 11 MB per 30 minuter)."""
    result = subprocess.run(
        [need("ffmpeg"), "-v", "error", "-ss", str(start_s), "-t", str(length_s), "-i", str(mp4),
         "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", "-f", "mp3", "pipe:1"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg kunde inte ta ut ljudet:\n{result.stderr.decode()[-1500:]}")
    return result.stdout


def berget_post(api_key: str, timeout_s: float = 1800.0) -> Post:
    """Skicka en ljudbit till Berget och returnera svaret."""

    def post(audio: bytes, fields: dict) -> dict:
        response = httpx.post(
            BERGET_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            data=fields,
            files={"file": ("del.mp3", audio, "audio/mpeg")},
            timeout=timeout_s,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Berget svarade {response.status_code}: {response.text[:500]}")
        return response.json()

    return post


def transcribe_vod(
    mp4: Path,
    video_id: str,
    out_path: Path,
    *,
    post: Post,
    model: str,
    language: str,
    chunk_s: float = 1800.0,
) -> Transcript:
    """Transkribera hela videon bit för bit och spara transcript.json."""
    duration = probe(mp4)["duration"]
    fields = {"model": model, "language": language, "response_format": "verbose_json", "align": "true"}
    segments: list[Segment] = []
    start = 0.0
    parts = int(-(-duration // chunk_s))
    while start < duration:
        print(f"  del {int(start // chunk_s) + 1} av {parts} ...", flush=True)
        audio = _audio_part(mp4, start, min(chunk_s, duration - start))
        segments += to_segments(post(audio, fields), offset_s=start)
        start += chunk_s
    transcript = Transcript(video_id=video_id, language=language, segments=segments)
    out_path.write_text(transcript.to_json(), encoding="utf-8")
    return transcript
