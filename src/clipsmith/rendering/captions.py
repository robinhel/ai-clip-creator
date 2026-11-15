"""ASS subtitle generation for burned-in karaoke captions."""

from __future__ import annotations

import re
from pathlib import Path

from ..models.transcript import Transcript, Word
from ..settings import CaptionConfig, ReframeConfig

_WORDS_PER_LINE = 5
_BOX_WORDS_PER_LINE = 3
_SAFE_BOTTOM = (
    0.20  # same as the campaign profile's default safe_zone.bottom (TikTok buttons and caption)
)
_BOX_ON = "{\\3a&H00&}"
_BOX_OFF = "{\\3a&HFF&}"
_MAX_LINE_CHARS = 28
_BOX_MAX_LINE_CHARS = 18  # bold capitals at 72 pt: about 18 fit in 1080 px
_ASS_SPECIAL = re.compile(r"[{}\\]")  # would start an override tag or an escape in ASS
_MAX_GAP = 0.6  # a longer pause between words starts a new line, so no text shows during silence
_MAX_WORD = 1.0  # whisper sometimes stretches a word over the silence after it
_HOLD = 0.3  # keep a line up a little after its last word, but never into the next line


def _write_ass(
    transcript: Transcript,
    clip_start: float,
    clip_end: float,
    config: CaptionConfig,
    out_path: Path,
    reframe: ReframeConfig | None = None,
) -> None:
    lines = _caption_lines(transcript, clip_start, clip_end, config.style)
    out_path.write_text(_render_ass(lines, config, reframe), encoding="utf-8-sig")


def _caption_lines(
    transcript: Transcript,
    clip_start: float,
    clip_end: float,
    style: str = "outline",
) -> list[tuple[float, float, str]]:
    """Return (rel_start, rel_end, ass_text) tuples for the clip range."""
    words: list[Word] = []
    for seg in transcript.segments:
        if seg.end < clip_start or seg.start > clip_end:
            continue
        for w in seg.words:
            if clip_start <= w.start and w.end <= clip_end + 0.5:
                words.append(w)

    if words:
        return _group_words(words, clip_start, style)
    return _fallback_segments(transcript, clip_start, clip_end, style)


def _group_words(
    words: list[Word], clip_start: float, style: str = "outline"
) -> list[tuple[float, float, str]]:
    if style == "box":
        chunks = _split_lines(words, _BOX_WORDS_PER_LINE, _BOX_MAX_LINE_CHARS, fit=True)
    else:
        chunks = _split_lines(words, _WORDS_PER_LINE)
    groups = [_make_karaoke_line(c, clip_start) for c in chunks]
    lines: list[tuple[float, float, str]] = []
    for i, (start, end, text) in enumerate(groups):
        end = min(end + _HOLD, groups[i + 1][0]) if i + 1 < len(groups) else end + _HOLD
        if style == "box":
            lines += _box_events(chunks[i], clip_start, end)
        else:
            lines.append((start, end, text))
    return lines


def _split_lines(
    words: list[Word], max_words: int, max_chars: int = _MAX_LINE_CHARS, fit: bool = False
) -> list[list[Word]]:
    """Split words into lines at a pause, at max_words, or at max_chars. fit: a word that would make
    the line longer than max_chars starts a new line, so a too long word stands alone."""
    chunks: list[list[Word]] = []
    chunk: list[Word] = []

    for w in words:
        too_wide = fit and len(_chunk_text([*chunk, w])) > max_chars
        if chunk and (w.start - _spoken_end(chunk[-1]) > _MAX_GAP or too_wide):
            chunks.append(chunk)
            chunk = []
        chunk.append(w)
        if len(chunk) >= max_words or len(_chunk_text(chunk)) >= max_chars:
            chunks.append(chunk)
            chunk = []
    if chunk:
        chunks.append(chunk)
    return chunks


def _box_events(
    words: list[Word], clip_start: float, line_end: float
) -> list[tuple[float, float, str]]:
    """One event per word: the whole line, with the box shown behind the word being said."""
    times = [max(0.0, w.start - clip_start) for w in words] + [line_end]
    texts = [_clean(w.word).upper() for w in words]
    return [
        (
            times[i],
            max(times[i], times[i + 1]),
            " ".join(f"{_BOX_ON}{t}{_BOX_OFF}" if j == i else t for j, t in enumerate(texts)),
        )
        for i in range(len(texts))
    ]


def _spoken_end(w: Word) -> float:
    return min(w.end, w.start + _MAX_WORD)


def _make_karaoke_line(words: list[Word], clip_start: float) -> tuple[float, float, str]:
    rel_start = max(0.0, words[0].start - clip_start)
    rel_end = max(rel_start + 0.1, _spoken_end(words[-1]) - clip_start)
    # Each word lasts until the next one starts, so a short gap inside the line keeps the highlight in step.
    ends = [nxt.start for nxt in words[1:]] + [_spoken_end(words[-1])]
    parts = [
        f"{{\\kf{max(1, round((end - w.start) * 100))}}}{_clean(w.word)}"
        for w, end in zip(words, ends)
    ]
    return rel_start, rel_end, " ".join(parts)


def _clean(word: str) -> str:
    return _ASS_SPECIAL.sub("", word.strip())


def _chunk_text(words: list[Word]) -> str:
    return " ".join(_clean(w.word) for w in words)


def _fallback_segments(
    transcript: Transcript,
    clip_start: float,
    clip_end: float,
    style: str = "outline",
) -> list[tuple[float, float, str]]:
    """Segment-level captions when word timestamps are absent (no box: the word timing is unknown)."""
    per_line = _BOX_WORDS_PER_LINE if style == "box" else _WORDS_PER_LINE
    lines: list[tuple[float, float, str]] = []
    for seg in transcript.segments:
        if seg.end < clip_start or seg.start > clip_end:
            continue
        rel_start = max(0.0, seg.start - clip_start)
        rel_end = seg.end - clip_start
        text = _clean(seg.text)
        seg_words = (text.upper() if style == "box" else text).split()
        n = len(seg_words)
        if not n:
            continue
        for i in range(0, n, per_line):
            chunk = seg_words[i : i + per_line]
            t0 = rel_start + (i / n) * (rel_end - rel_start)
            t1 = rel_start + (min(i + per_line, n) / n) * (rel_end - rel_start)
            lines.append((t0, t1, " ".join(chunk)))
    return lines


def _ass_time(seconds: float) -> str:
    """Seconds → ASS timestamp H:MM:SS.cc"""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = min(99, round((seconds % 1) * 100))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _alignment(position: str) -> int:
    """Map position name to ASS numpad alignment code."""
    return {"top": 8, "middle": 5}.get(position, 2)  # default: bottom-center


def _placement(config: CaptionConfig, reframe: ReframeConfig | None) -> tuple[int, int]:
    """(alignment, MarginV). box: on the seam when stacked, else above the bottom safe zone.
    outline: the old placement, MarginV 80."""
    if config.style == "outline":
        return _alignment(config.position), 80
    if reframe is not None and reframe.mode == "stacked":
        # bottom-aligned, so the line's middle sits on the seam between facecam and gameplay
        return 2, int(1920 * (1 - reframe.split_ratio) - config.font_size / 2)
    alignment = _alignment(config.position)
    return alignment, int(1920 * _SAFE_BOTTOM) if alignment == 2 else 80


def _render_ass(
    lines: list[tuple[float, float, str]],
    config: CaptionConfig,
    reframe: ReframeConfig | None = None,
) -> str:
    align, margin_v = _placement(config, reframe)
    if config.style == "box":
        # BorderStyle 3 draws a box in OutlineColour; per-word \3a tags show it only behind the active word
        styles = (
            f"Style: Box,{config.font},{config.font_size},"
            f"&H00FFFFFF,&H000000FF,{config.box_colour},&H00000000,"
            f"1,0,0,0,100,100,0,0,3,12,0,{align},20,20,{margin_v},1\n"
            f"Style: Text,{config.font},{config.font_size},"
            "&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
            f"1,0,0,0,100,100,0,0,1,{config.outline},0,{align},20,20,{margin_v},1\n"
        )
        events = "\n".join(
            f"Dialogue: 0,{_ass_time(s)},{_ass_time(e)},Box,,0,0,0,,{{\\1a&HFF&}}{_BOX_OFF}{t}\n"
            f"Dialogue: 1,{_ass_time(s)},{_ass_time(e)},Text,,0,0,0,,{re.sub(r'{[^}]*}', '', t)}"
            for s, e, t in lines
        )
    else:
        styles = (
            f"Style: Default,{config.font},{config.font_size},"
            "&H00FFFFFF,&H000000FF,&H00000000,&HA0000000,"
            f"1,0,0,0,100,100,0,0,1,{config.outline},0,"
            f"{align},20,20,{margin_v},1\n"
        )
        events = "\n".join(
            f"Dialogue: 0,{_ass_time(s)},{_ass_time(e)},Default,,0,0,0,,{t}" for s, e, t in lines
        )
    header = (
        "[Script Info]\n"
        "Title: clipsmith\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 1\n"
        "ScaledBorderAndShadow: yes\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{styles}"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    return header + events + "\n"
