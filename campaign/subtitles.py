"""Undertexten som ord i en fil bredvid råklippet, så att Robin kan rätta ord i redigeraren.

Appens körning skär råklippet utan undertext och sparar orden i clip_NN_*.words.json. finish_clip bränner in
undertexten. Ordens tider räknas från råklippets (originalets) start, samma tidslinje som trimningen i
editor.edit_meta. Filen har källans storlek och ändringstid: hör den inte till råklippet som ligger där nu
(en ny körning, eller ett gammalt klipp med inbränd undertext) används den inte, så att undertexten aldrig
bränns in två gånger.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from clipsmith.models.transcript import Segment, Transcript, Word
from clipsmith.rendering.captions import _hallucinated, _write_ass
from clipsmith.settings import CaptionConfig, ReframeConfig

WORD_CHARS = 40  # samma gräns som redigeraren (editor._check_words)


def words_path(raw: Path) -> Path:
    return raw.with_suffix(".words.json")


def pick_words(transcript: Transcript, start: float, end: float) -> list[dict]:
    """Orden i klippet, valda som clipsmiths captions gör, med tider från klippets start."""
    words = []
    for seg in transcript.segments:
        if seg.end < start or seg.start > end or _hallucinated(seg):
            continue
        for w in seg.words:
            text = w.word.strip()[:WORD_CHARS]  # Whisper kan ge skräpord på 180 tecken; servern tar högst 40
            if text and start <= w.start and w.end <= end + 0.5:
                words.append({"start": round(w.start - start, 3), "end": round(w.end - start, 3), "word": text})
    return words


def save_words(raw: Path, words: list[dict], caption: CaptionConfig, reframe: ReframeConfig,
               source: Path | None = None) -> None:
    """Spara orden med källans identitet (originalet om klippet är trimmat, annars råklippet)."""
    stat = (source or raw).stat()
    data = {"words": words, "caption": caption.model_dump(),
            "reframe": {"mode": reframe.mode, "split_ratio": reframe.split_ratio},
            "raw_size": stat.st_size, "raw_mtime_ns": stat.st_mtime_ns}
    path = words_path(raw)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def load_words(raw: Path, source: Path) -> dict | None:
    """Ordfilen, om den hör till källan som ligger där nu. Samma kontroll som editor.edit_meta."""
    try:
        data = json.loads(words_path(raw).read_text(encoding="utf-8"))
        stat = source.stat()
    except (OSError, ValueError):
        return None
    same = (isinstance(data, dict) and isinstance(data.get("words"), list)
            and data.get("raw_size") == stat.st_size and data.get("raw_mtime_ns") == stat.st_mtime_ns)
    return data if same else None


def write_ass(data: dict, in_s: float, out_s: float, out: Path) -> None:
    """Undertexten för intervallet in_s–out_s, som clipsmith skriver den. Orden är redan valda och rättade,
    så inget slängs som påhittat."""
    # Ett segment per ord, så att clipsmiths reserv utan ordtider aldrig visar hela texten på en gång.
    segments = [Segment(w.start, w.end, w.word, [w]) for w in
                (Word(float(x["start"]), float(x["end"]), str(x["word"]), 1.0) for x in data["words"])]
    reframe = ReframeConfig(mode=data["reframe"]["mode"], split_ratio=data["reframe"]["split_ratio"])
    _write_ass(Transcript("", "", segments), in_s, out_s, CaptionConfig(**data["caption"]), out, reframe)
