"""Tidskartan i finish: vilka bitar av källan som blir det färdiga klippet, och var orden hamnar.

Allt som Robin redigerar ligger kvar på originalets tidslinje (trimningen och ordfilen). Bara renderingen gör
klippet kortare: tystnad och utfyllnadsord tas bort. En plan är bitar (start, slut) i källans tid, alltså
filen som finish_clip får (0 = originalets in_s). Det färdiga klippet är bitarna efter varandra.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .media import need, probe
from .profile import Profile
from .subtitles import write_ass

SILENCE_DB, SILENCE_S = -40, 0.6  # spelljud räknas som ljud; bara riktig tystnad tas bort
PAD_S = 0.15  # så mycket tystnad som blir kvar på varje sida
FILLERS = {"um", "uh", "uhm", "umm", "uhh", "erm", "er", "ehm", "eh", "hmm", "hm", "mm", "mhm"}
FILLER_MAX_S = 1.2
MIN_PIECE_S = MIN_GAP_S = 0.1  # kortare bitar tas bort, kortare luckor slås ihop


def silences(src: Path) -> list[tuple[float, float]]:
    """Tystnaderna i källans ljud (ffmpeg silencedetect). En tystnad till slutet får slutet None -> inf."""
    result = subprocess.run(
        [need("ffmpeg"), "-hide_banner", "-nostats", "-i", str(src), "-vn",
         "-af", f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_S}", "-f", "null", "-"],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg kunde inte leta tystnad:\n{result.stderr[-1500:]}")
    starts = [max(0.0, float(x)) for x in re.findall(r"silence_start: (-?[\d.]+)", result.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", result.stderr)]
    return [(start, ends[i] if i < len(ends) else float("inf")) for i, start in enumerate(starts)]


def is_filler(word: dict) -> bool:
    text = re.sub(r"[^\w]", "", str(word["word"]).lower())
    return text in FILLERS and word["end"] - word["start"] <= FILLER_MAX_S


def _overlap(word: dict, piece: tuple[float, float]) -> float:
    return max(0.0, min(word["end"], piece[1]) - max(word["start"], piece[0]))


def _frame(t: float, fps: float) -> float:
    return round(round(t * fps) / fps, 6)


def plan(duration: float, fps: float, words: list[dict], silences: list[tuple[float, float]],
         hook: list[float] | None, min_s: float, max_s: float, cut_fillers: bool = True) -> dict | None:
    """Bitarna för källan. Ord och tystnader har källans tider. None: inget att ändra, rendera som förut."""
    removed = [(start + PAD_S, end - PAD_S) for start, end in silences if end - start > 2 * PAD_S]
    silence_cut = sum(min(end, duration) - start for start, end in removed if start < duration)
    fillers = [w for w in words if cut_fillers and is_filler(w)]
    removed += [(w["start"], w["end"]) for w in fillers]

    pieces: list[tuple[float, float]] = []
    at = 0.0
    for start, end in sorted(removed):
        if start > at:
            pieces.append((at, min(start, duration)))
        at = max(at, end)
    if at < duration:
        pieces.append((at, duration))
    joined: list[tuple[float, float]] = []
    for start, end in ((_frame(s, fps), _frame(e, fps)) for s, e in pieces):  # hela bildrutor: ljud och bild i takt
        if joined and start - joined[-1][1] < MIN_GAP_S:
            joined[-1] = (joined[-1][0], end)
        else:
            joined.append((start, end))
    main = [(start, end) for start, end in joined if end - start >= MIN_PIECE_S]
    length = sum(end - start for start, end in main)
    if main == [(0.0, _frame(duration, fps))] or length < min_s:
        return None  # inget togs bort, eller klippet blir kortare än kampanjen tillåter
    return {"pieces": main, "cold_open": False, "silence_cut_s": round(silence_cut, 2),
            "fillers_cut": sum(1 for w in fillers if not _kept(w, main)), "cold_open_s": 0.0}


def _kept(word: dict, pieces: list[tuple[float, float]]) -> bool:
    """Ett utfyllnadsord är borttaget när högst hälften av det finns kvar (en för liten lucka slås ihop)."""
    return sum(_overlap(word, p) for p in pieces) > (word["end"] - word["start"]) / 2


def move_words(words: list[dict], plan: dict) -> list[dict]:
    """Orden på det färdiga klippets tidslinje. Hookens ord visas två gånger: i hooken och i klippet."""
    pieces = plan["pieces"]
    offsets = [0.0]
    for start, end in pieces:
        offsets.append(offsets[-1] + end - start)

    def place(w: dict, i: int) -> dict:
        start, end = pieces[i]
        return {**w, "start": round(offsets[i] + max(w["start"], start) - start, 3),
                "end": round(offsets[i] + min(w["end"], end) - start, 3)}

    first = 1 if plan["cold_open"] else 0
    out = [place(w, 0) for w in words if first and _overlap(w, pieces[0]) > 0]
    main = range(first, len(pieces))
    for w in words:
        if is_filler(w) and not _kept(w, pieces[first:]):
            continue
        best = max(main, key=lambda i: _overlap(w, pieces[i]))
        if _overlap(w, pieces[best]) > 0:
            out.append(place(w, best))
    return out


def prepare(src: Path, data: dict, in_s: float, out_s: float, profile: Profile, ass: Path) -> dict | None:
    """Planen för källan src och undertexten i ass. Samma för körningen (finish_vod) och redigeraren.
    None: klippet renderas som förut, med undertexten för in_s–out_s."""
    info = probe(src)
    words = [{**w, "start": w["start"] - in_s, "end": w["end"] - in_s} for w in data["words"]]
    found = silences(src) if profile.cut_silence and info["has_audio"] else []
    result = plan(info["duration"], info["fps"], words, found, None, profile.min_seconds, profile.max_seconds,
                  cut_fillers=profile.cut_fillers)
    if result is None:
        write_ass(data, in_s, out_s, ass)
        return None
    total = sum(end - start for start, end in result["pieces"])
    write_ass({**data, "words": move_words(words, result)}, 0.0, total, ass)
    return result
