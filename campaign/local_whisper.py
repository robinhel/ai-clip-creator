"""Transkribering på den egna datorn med clipsmiths faster-whisper, i stället för via Berget.

Modellen laddas ner från Hugging Face första gången (till HF_HOME, i Docker en volym) och används sedan
därifrån. Resultatet är samma transcript.json som Berget-vägen skriver, så resten av flödet märker ingen
skillnad. Lokal transkribering kostar inget och loggas inte i budgeten.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from clipsmith.config.models import TranscribeConfig
from clipsmith.models.transcript import Transcript
from clipsmith.transcription.transcriber import transcribe

from .config import LOCAL_MODELS

REPO = "Systran/faster-whisper-{}"  # samma namn som faster_whisper.utils._MODELS använder


def is_downloaded(model: str, cache_dir: str | Path | None = None) -> bool:
    """Finns modellen redan i Hugging Face-cachen? Läser bara cachen, laddar aldrig ner något."""
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:  # pragma: no cover - kommer med faster-whisper
        return False
    found = try_to_load_from_cache(REPO.format(model), "model.bin", cache_dir=cache_dir)
    return isinstance(found, str)


def loading_message(model: str) -> str:
    if is_downloaded(model):
        return f"Loading Whisper {model}"
    return f"Loading Whisper {model} (downloading about {LOCAL_MODELS[model]:g} GB the first time)"


def transcribe_local(
    mp4: Path,
    video_id: str,
    out_path: Path,
    config: TranscribeConfig,
    *,
    model: str,
    language: str,
    on_part: Callable[[int, int], None] | None = None,
) -> Transcript:
    """Transkribera med kampanjens transcribe-inställningar (compute_type, bitar, trådar), den valda modellen
    och kampanjens språk. on_part(n, total) anropas när del n börjar, som för Berget."""
    settings = config.model_copy(update={"model": model, "language": language})

    def chunk(done: int, total: int) -> None:
        if on_part and done < total:
            on_part(done + 1, total)

    return transcribe(mp4, video_id, settings, out_path=out_path, overwrite=True, on_chunk=chunk)
