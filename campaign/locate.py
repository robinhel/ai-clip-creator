"""Stället i originalströmmen för ett viralt klipp.

Twitch-klipp har vod_offset och ger platsen direkt. Andra klipp (TikTok, YouTube, omladdade) matchas med sina
egna ord mot VOD-transkripten i work/<vod>/transcript.json: ordfönster om fyra ord, normaliserade, och den plats
där flest fönster pekar på samma start vinner.
"""

from __future__ import annotations

import logging
import re
import tempfile
from collections import defaultdict
from pathlib import Path

from clipsmith.config.models import TranscribeConfig
from clipsmith.models.transcript import Transcript

from .budget import budget_from_settings
from .config import BERGET_MISSING, load_settings
from .local_whisper import transcribe_local
from .transcribe import berget_post, transcribe_vod

log = logging.getLogger(__name__)

WINDOW = 4  # fyra ord: kort nog att tåla ett felstavat eller saknat ord, långt nog att sällan träffa av en slump
MIN_WORDS = 8  # färre ord än så räcker inte för att skilja ett ställe från ett annat
MIN_HITS = 3  # så många fönster måste peka på samma start; ett eller två kan vara vanliga fraser
SPREAD_S = 2.0  # fönster vars starter ligger så här nära räknas som samma ställe (tidsstämplarna glider lite)


def from_twitch(clip: dict) -> dict | None:
    """Helix-klippets plats i VOD:en. None när VOD:en är borta (tomt video_id) eller offset saknas."""
    if not clip.get("video_id") or clip.get("vod_offset") is None:
        return None
    # Helix ger klippets SLUT i vod_offset, trots dokumentationen. Kontrollen mot VOD:en visade det
    # (4 av 5 klipp), och TwitchDownloader issue 1285 säger samma sak.
    end = float(clip["vod_offset"])
    return {
        "vod_id": str(clip["video_id"]),
        "start_s": max(0.0, end - float(clip.get("duration") or 0)),
        "end_s": end,
        "method": "vod_offset",
        "confidence": 0.8,  # kontrollen mot VOD:en gav rätt plats för 4 av 5 klipp
    }


def candidate_vods(work_dir: Path | None = None) -> dict[str, Path]:
    """VOD:er som går att söka i: de som har work/<vod>/transcript.json."""
    work = work_dir or load_settings().work_dir
    return {path.parent.name: path for path in sorted(work.glob("*/transcript.json"))}


def _token(text: str) -> str:
    return re.sub(r"[^\w]", "", text.lower())


def _tokens(words: list[dict]) -> list[tuple[str, float]]:
    """(normaliserat ord, starttid), utan ord som bara var skiljetecken."""
    pairs = ((_token(str(w.get("word", ""))), float(w.get("start", 0.0))) for w in words)
    return [(token, start) for token, start in pairs if token]


def _transcript_words(transcript: Transcript) -> list[dict]:
    return [
        {"word": w.word, "start": w.start, "end": w.end}
        for seg in transcript.segments
        for w in seg.words
    ]


def _transcribe_clip(clip: dict) -> list[dict]:
    """Klippets ord, med Berget eller lokal whisper som valt i Settings. Bara en lokal fil; en adress måste
    laddas ner först."""
    source = clip.get("path") or clip.get("video_url") or ""
    if not source or not Path(source).is_file():
        return []
    with tempfile.TemporaryDirectory() as tmp:
        transcript = _transcribe(Path(source), str(clip.get("id") or "klipp"), Path(tmp) / "transcript.json")
    return _transcript_words(transcript)


def _transcribe(video: Path, video_id: str, out_path: Path) -> Transcript:
    """Samma motorval som pipeline: Berget kostar och loggas i budgeten, lokal whisper är gratis.
    Språket skickas inte (None), så att whisper gissar det; "auto" är ingen giltig språkkod."""
    settings = load_settings()
    if settings.engine == "local":
        return transcribe_local(video, video_id, out_path, TranscribeConfig(),
                                model=settings.local_whisper_model, language=None)
    if not settings.berget_api_key:
        raise ValueError(BERGET_MISSING)
    post = berget_post(settings.berget_api_key)
    return transcribe_vod(
        video, video_id, out_path,
        post=post,
        model=settings.transcribe_model, language=None, budget=budget_from_settings(settings),
    )


def _windows(tokens: list[tuple[str, float]]) -> list[tuple[tuple[str, ...], float]]:
    return [
        (tuple(t for t, _ in tokens[i : i + WINDOW]), tokens[i][1])
        for i in range(len(tokens) - WINDOW + 1)
    ]


def _offsets(clip_windows: list[tuple[tuple[str, ...], float]], vod_path: Path) -> list[float]:
    """För varje fönster som finns i VOD:en: var klippet skulle börja i VOD:en om fönstret är rätt."""
    transcript = Transcript.from_json(vod_path.read_text(encoding="utf-8"))
    index: dict[tuple[str, ...], list[float]] = defaultdict(list)
    for key, start in _windows(_tokens(_transcript_words(transcript))):
        index[key].append(start)
    return sorted(
        vod_start - clip_start
        for key, clip_start in clip_windows
        for vod_start in index.get(key, [])
    )


def _best_place(offsets: list[float]) -> tuple[int, float]:
    """Flest starter inom SPREAD_S sekunder: (antal, mittersta starten)."""
    best: list[float] = []
    for i, first in enumerate(offsets):
        group = [o for o in offsets[i:] if o - first <= SPREAD_S]
        if len(group) > len(best):
            best = group
    return len(best), (best[len(best) // 2] if best else 0.0)


def locate(
    clip: dict, vod_transcripts: dict[str, Path] | None = None, words: list[dict] | None = None
) -> dict | None:
    """Stället i en VOD för ett klipp utan vod_offset, via klippets ord. None när det inte går att avgöra."""
    tokens = _tokens(words if words is not None else _transcribe_clip(clip))
    if len(tokens) < MIN_WORDS:
        return None
    clip_windows = _windows(tokens)
    places = {
        vod_id: _best_place(_offsets(clip_windows, path))
        for vod_id, path in (
            vod_transcripts if vod_transcripts is not None else candidate_vods()
        ).items()
    }
    if not places:
        return None
    vod_id, (hits, offset) = max(places.items(), key=lambda item: item[1][0])
    if hits < MIN_HITS:
        return None
    start = max(0.0, offset)
    return {
        "vod_id": vod_id,
        "start_s": start,
        "end_s": start + float(clip.get("duration") or tokens[-1][1]),
        "method": "transcript",
        "confidence": min(1.0, hits / len(clip_windows)),
    }


def try_locate(clip: dict) -> dict | None:
    """locate, men ett fel (till exempel i whisper) ger en varning och None i stället för att avbryta.
    Platsen är en bonus; klippet och mätningen sparas ändå."""
    try:
        return locate(clip)
    except Exception as error:
        log.warning("Could not locate %s in the stream (%s)", clip.get("id"), type(error).__name__)
        return None
