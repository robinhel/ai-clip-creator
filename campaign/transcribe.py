"""Transkribering via Berget i stället för på den egna datorn.

Ljudet delas i bitar på 30 minuter (Berget tar högst 100 MB och 30 minuters
bearbetning per anrop). Resultatet sparas som clipsmiths transcript.json, så att
`clipsmith run-vod ... --skip-transcribe` använder det.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from clipsmith.models.transcript import Segment, Transcript, Word

from .budget import Budget
from .media import need, probe

BERGET_URL = "https://api.berget.ai/v1/audio/transcriptions"
EUR_PER_MINUTE = 1.98 / 1000  # docs.berget.ai/models/overview, november 2026
PAUSES_S = (10, 30, 60)  # paus före omförsök 1, 2 och 3
# Tystnad före varje bit. Whisper tappade talet i början av en fil som pratar från 0,1 s: texten började
# först vid 7,1 s. Med en sekunds tystnad först kom allt med (provat mot Berget 17 november 2026).
LEAD_S = 1.0

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
                start=round(w["start"] + offset_s, 3),
                end=round(w["end"] + offset_s, 3),
                word=w["word"],
                probability=w.get("score", 1.0),
            )
            for w in words
            if seg["start"] <= w["start"] < seg["end"]
        ]
        segments.append(
            Segment(start=round(seg["start"] + offset_s, 3), end=round(seg["end"] + offset_s, 3), text=seg["text"], words=mine)
        )
    return segments


def _audio_part(mp4: Path, start_s: float, length_s: float) -> bytes:
    """En bit av ljudet som mp3, mono, 16 kHz (cirka 11 MB per 30 minuter), med LEAD_S tystnad först."""
    result = subprocess.run(
        [need("ffmpeg"), "-v", "error", "-ss", str(start_s), "-t", str(length_s), "-i", str(mp4),
         "-vn", "-ac", "1", "-ar", "16000", "-af", f"adelay={int(LEAD_S * 1000)}:all=1",
         "-b:a", "48k", "-f", "mp3", "pipe:1"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg could not extract the audio:\n{result.stderr.decode()[-1500:]}")
    return result.stdout


def berget_post(
    api_key: str,
    timeout_s: float = 1800.0,
    *,
    send: Callable[..., httpx.Response] = httpx.post,
    sleep: Callable[[float], None] = time.sleep,
) -> Post:
    """Skicka en ljudbit till Berget och returnera svaret.

    Vid 429, 5xx och nätverksfel görs upp till tre omförsök. Övriga fel ger fel direkt.
    """

    def post(audio: bytes, fields: dict) -> dict:
        for pause in (*PAUSES_S, None):
            try:
                response = send(
                    BERGET_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    data=fields,
                    files={"file": ("del.mp3", audio, "audio/mpeg")},
                    timeout=timeout_s,
                )
            except httpx.TransportError as exc:
                if pause is None:
                    raise RuntimeError(f"Network error talking to Berget: {exc}") from exc
                print(f"  network error talking to Berget, trying again in {pause} s", flush=True)
                sleep(pause)
                continue
            if response.status_code == 200:
                return response.json()
            error = f"Berget answered {response.status_code}: {response.text[:500]}"
            if pause is None or (response.status_code != 429 and response.status_code < 500):
                raise RuntimeError(error)
            try:
                wait = min(float(response.headers["Retry-After"]), 120.0)
            except (KeyError, ValueError):
                wait = pause  # saknas eller är ett datum: använd vår egen paus
            print(f"  {error[:80]}, trying again in {wait:.0f} s", flush=True)
            sleep(wait)
        raise AssertionError("unreachable")

    return post


def _save_json(path: Path, data: dict) -> None:
    """Skriv atomiskt, så att en avbruten körning aldrig lämnar en halv fil."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def transcribe_vod(
    mp4: Path,
    video_id: str,
    out_path: Path,
    *,
    post: Post,
    model: str,
    language: str | None,
    chunk_s: float = 1800.0,
    on_part: Callable[[int, int], None] | None = None,
    budget: Budget | None = None,
) -> Transcript:
    """Transkribera hela videon bit för bit och spara transcript.json.

    Varje dels svar sparas i berget/ bredvid out_path, så att en ny körning efter
    ett fel bara skickar de delar som saknas. Mappen tas bort när allt är klart.
    """
    duration = probe(mp4)["duration"]
    fields = {"model": model, "response_format": "verbose_json", "align": "true"}
    if language is not None:  # utan språk gissar whisper det
        fields["language"] = language
    saved = out_path.parent / "berget"
    settings = {"model": model, "language": language, "chunk_s": chunk_s, "lead_s": LEAD_S}  # sparade delar utan tystnad gäller inte
    meta = saved / "inställningar.json"
    if saved.exists() and (not meta.exists() or json.loads(meta.read_text(encoding="utf-8")) != settings):
        shutil.rmtree(saved)
    saved.mkdir(parents=True, exist_ok=True)
    _save_json(meta, settings)

    segments: list[Segment] = []
    parts = int(-(-duration // chunk_s))
    for number in range(1, parts + 1):
        if on_part:
            on_part(number, parts)
        start = (number - 1) * chunk_s
        part_file = saved / f"del_{number:02d}.json"
        if part_file.exists():
            print(f"  part {number} of {parts} is already saved", flush=True)
            answer = json.loads(part_file.read_text(encoding="utf-8"))
        else:
            print(f"  part {number} of {parts} ...", flush=True)
            length = min(chunk_s, duration - start)
            audio = _audio_part(mp4, start, length)
            try:
                answer = post(audio, fields)
            except Exception as exc:
                raise RuntimeError(
                    f"Part {number} of {parts} failed: {exc}\n"
                    f"Finished parts are saved in {saved}. Run the project again and it continues where it stopped."
                ) from exc
            _save_json(part_file, answer)
            if budget is not None:  # bara skickade delar kostar; sparade delar är redan betalda
                budget.record_audio(model, length / 60)
        segments += to_segments(answer, offset_s=start - LEAD_S)  # svarets tider räknar med tystnaden
    transcript = Transcript(video_id=video_id, language=language, segments=segments)
    out_path.write_text(transcript.to_json(), encoding="utf-8")
    shutil.rmtree(saved)
    return transcript
