"""Gör ett färdigt 9:16-klipp kampanjklart: overlay, text på skärmen, jämn ljudnivå.

Det här är ett eget ffmpeg-pass ovanpå klippet från clipsmith. Det betyder en
extra omkodning. Fas 2 i byggplanen flyttar in stegen i clipsmiths egen
rendering (rendering/clipper.py) så att klippet bara kodas en gång.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

from clipsmith.rendering.clipper import ZOOM_TOP

from .audio import replace_audio
from .media import find_font, need, probe, run
from .profile import Profile

WIDTH, HEIGHT = 1080, 1920
HOOK_SECONDS = 3
HOOK_FONT_SIZE = 64
HOOK_LINE_CHARS = 22  # ryms i 1080 px vid HOOK_FONT_SIZE
HOOK_GAP = 24  # zoom: luft mellan hookens sista rad och bilden
HOOK_LINE_H = int(HOOK_FONT_SIZE * 1.25)  # en rad hook, med kant och luft

ENCODER_ARGS = {
    "libx264": ["-c:v", "libx264", "-preset", "medium", "-crf", "19"],
    "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21"],
    "h264_videotoolbox": ["-c:v", "h264_videotoolbox", "-b:v", "8M"],
}


def _esc(value: str) -> str:
    """Skydda tecken som betyder något i ett ffmpeg-filter."""
    for char in ("\\", ":", "'", "%", ",", "[", "]", ";"):
        value = value.replace(char, "\\" + char)
    return value


def _hook_esc(value: str) -> str:
    """Text utan citattecken till drawtext: först optionsnivån, sedan filtergrafens nivå."""
    for char in "\\':":
        value = value.replace(char, "\\" + char)
    for char in "\\'[],;":
        value = value.replace(char, "\\" + char)
    return value


def _clean(title: str) -> str:
    return re.sub(r"[\x00-\x1f]", " ", title).strip()  # ett styrtecken (NUL) stoppar ffmpeg-anropet


def _text_on_top(profile: Profile, has_overlay: bool) -> bool:
    """Kampanjens vita text ritas uppe. Overlayen har redan kanalens text, så den ritas bara utan overlay."""
    return bool(profile.text) and not has_overlay and profile.text_position == "top"


def _hook_lines(profile: Profile, title: str) -> list[str]:
    title = _clean(title)
    return textwrap.wrap(title, HOOK_LINE_CHARS, max_lines=3, placeholder="…") if profile.hook_text and title else []


def _hook_top(profile: Profile, text_on_top: bool, lines: int = 1, frame: str = "") -> int:
    """Hookens första rad. I bildläget zoom står den på det svarta precis ovanför bilden, men aldrig i den
    övre säkra zonen."""
    top = int(HEIGHT * profile.safe_top) + (int(profile.text_font_size * 1.6) if text_on_top else 0)
    if frame == "zoom":
        top = max(top, ZOOM_TOP - HOOK_GAP - HOOK_FONT_SIZE - (lines - 1) * HOOK_LINE_H)
    return top


def _overlay_used(profile: Profile, root: Path, overlay: Path | None) -> Path | None:
    """Overlayen klippet får: en egen eller kampanjens, om filen finns."""
    overlay = overlay or profile.overlay_path(root)
    return overlay if overlay is not None and overlay.exists() else None


def top_text_end(profile: Profile, root: Path, title: str, overlay: Path | None = None, frame: str = "") -> int:
    """Raden i bilden där texten uppe slutar: kampanjens text och hooken. 0 när ingen text ritas uppe.
    Samma läge som build_filter ritar dem på, så att ett klistermärke kan läggas under."""
    on_top = _text_on_top(profile, _overlay_used(profile, root, overlay) is not None)
    lines = _hook_lines(profile, title)
    if lines:
        return _hook_top(profile, on_top, len(lines), frame) + len(lines) * HOOK_LINE_H
    return _hook_top(profile, on_top) if on_top else 0


def _hook(
    profile: Profile, title: str, font: str | None, last: str, text_on_top: bool, frame: str = ""
) -> list[str]:
    """Titeln centrerad i övre säkra zonen de första sekunderna, på upp till tre rader (_hook_top)."""
    lines = _hook_lines(profile, title)
    top = _hook_top(profile, text_on_top, len(lines), frame)
    font_part = f"fontfile='{_esc(font)}':" if font else ""
    parts = []
    for i, line in enumerate(lines):
        out = "withhook" if i == len(lines) - 1 else f"hook{i}"
        parts.append(
            f"[{last}]drawtext={font_part}expansion=none:text={_hook_esc(line)}:"
            f"fontsize={HOOK_FONT_SIZE}:fontcolor=white:borderw=5:bordercolor=black@0.9:"
            f"x=(w-text_w)/2:y={top + i * HOOK_LINE_H}:"
            f"enable='lt(t,{HOOK_SECONDS})'[{out}]"
        )
        last = out
    return parts


def _pieces(pieces: list[tuple[float, float]], has_audio: bool, cold_open: bool) -> list[str]:
    """Bitarna ur klippet efter varandra (tidskartan). Ut: [cv] och, med ljud, [ca]. Hooken (cold_open, första
    biten) läses från ingång 1, samma fil från hookens start: ur ingång 0 skulle bildrutorna före den vänta i
    minnet tills hooken är klar."""
    parts, inputs = [], ""
    for i, (start, end) in enumerate(pieces):
        src, span = ("1", f"duration={end - start:.6f}") if cold_open and i == 0 else \
            ("0", f"start={start:.6f}:end={end:.6f}")
        parts.append(f"[{src}:v]trim={span},setpts=PTS-STARTPTS[v{i}]")
        inputs += f"[v{i}]"
        if has_audio:
            parts.append(f"[{src}:a]atrim={span},asetpts=PTS-STARTPTS[a{i}]")
            inputs += f"[a{i}]"
    outputs = "[cv][ca]" if has_audio else "[cv]"
    parts.append(f"{inputs}concat=n={len(pieces)}:v=1:a={int(has_audio)}{outputs}")
    return parts


def build_filter(
    profile: Profile, has_overlay: bool, has_audio: bool, font: str | None, title: str = "",
    subtitles: Path | None = None, pieces: list[tuple[float, float]] | None = None, cold_open: bool = False,
    sticker: dict | None = None, look: str = "", speed: float = 1.0, frame: str = "",
) -> str:
    """Filtergrafen. Ingång 0 är klippet, sedan hooken (cold_open), overlay-bilden och klistermärket om de finns.
    Med bitar (tidskartan) sätts de ihop först. Undertexten bränns in sedan, så att overlayen ligger ovanpå den.
    sticker: {w, h, x, y, start_s, end_s}, en ny versions klistermärke ovanpå overlayen under en stund.
    look: filter (",hflip,eq=...") på bilden före undertexten, så att texten inte speglas. speed: hela klippet
    snabbare sist, så att undertexten och hooken följer med. frame: råklippets bildläge (ordfilens reframe.mode),
    som bestämmer var hooken står."""
    burn = f",subtitles=filename='{_esc(str(subtitles))}'" if subtitles else ""
    parts = _pieces(pieces, has_audio, cold_open) if pieces else []
    ov = 2 if pieces and cold_open else 1
    video, audio = ("cv", "ca") if pieces else ("0:v", "0:a")
    parts.append(f"[{video}]setsar=1{look}{burn}[base]")
    last = "base"
    if has_overlay:
        if profile.overlay_mode == "full":
            parts.append(f"[{ov}:v]scale={WIDTH}:{HEIGHT}[ov]")
            position = "0:0"
        else:
            parts.append(f"[{ov}:v]scale={WIDTH}:-1[ov]")
            position = "0:0" if profile.overlay_mode == "top" else "0:H-h"
        parts.append(f"[{last}][ov]overlay={position}[withov]")
        last = "withov"
    if sticker:
        index = ov + int(has_overlay)
        parts.append(f"[{index}:v]scale={sticker['w']}:{sticker['h']}[st]")
        parts.append(f"[{last}][st]overlay={sticker['x']}:{sticker['y']}:"
                     f"enable='between(t,{sticker['start_s']:.3f},{sticker['end_s']:.3f})'[withsticker]")
        last = "withsticker"
    # Overlayen har redan kanalens text, så den vita texten ritas bara utan overlay.
    if profile.text and not has_overlay:
        if profile.text_position == "top":
            y = str(int(HEIGHT * profile.safe_top))
        else:
            y = f"h-text_h-{int(HEIGHT * profile.safe_bottom)}"
        font_part = f"fontfile='{_esc(font)}':" if font else ""
        parts.append(
            f"[{last}]drawtext={font_part}text='{_esc(profile.text)}':"
            f"fontsize={profile.text_font_size}:fontcolor=white:"
            f"borderw=4:bordercolor=black@0.85:x=(w-text_w)/2:y={y}[withtext]"
        )
        last = "withtext"
    if _hook_lines(profile, title):
        parts += _hook(profile, title, font, last, _text_on_top(profile, has_overlay), frame)
        last = "withhook"
    fast = (f",setpts=PTS/{speed:g}", f",atempo={speed:g}") if speed != 1.0 else ("", "")
    parts.append(f"[{last}]format=yuv420p{fast[0]}[vout]")
    if has_audio:
        parts.append(f"[{audio}]loudnorm=I={profile.lufs}:TP=-1.5:LRA=11{fast[1]}[aout]")
    return ";".join(parts)


def _output_args(has_audio: bool, encoder: str, speed: float) -> list[str]:
    """Ljudet, tidsbasen och kodaren efter -map [vout]."""
    args = ["-map", "[aout]", "-c:a", "aac", "-b:a", "160k", "-ar", "48000"] if has_audio else []
    if speed != 1.0:
        # Alla bildrutor med tiderna från setpts, i ffmpeg 7 och 8 lika. Från ffmpeg 8 behåller setpts
        # källans bildfrekvens, och kodaren räknade då i 1/30 s och kastade var elfte ruta vid 1,1. Förhandsbilden
        # (drafts.frame) räknar med att ruta n i klippet är ruta n i råklippet. Värdet filter finns från ffmpeg 6.1.
        args += ["-enc_time_base:v", "filter"]
    return args + ENCODER_ARGS.get(encoder, ENCODER_ARGS["libx264"])


def finish_clip(
    src: Path, out: Path, profile: Profile, root: Path, encoder: str = "libx264", title: str = "",
    subtitles: Path | None = None, plan: dict | None = None, overlay: Path | None = None,
    font_file: str | None = None, sticker: dict | None = None, look: str = "", speed: float = 1.0,
    frame: str = "", audio: dict | None = None,
) -> dict:
    """Rendera den kampanjklara versionen. plan är tidskartan (campaign.timemap). En ny version kan ge en egen
    overlay, en fontfil till texten på skärmen och ett klistermärke (med path). Returnerar ett manifest om vad
    som gjordes."""
    info = probe(src)
    overlay = _overlay_used(profile, root, overlay)
    has_overlay = overlay is not None
    font = font_file or find_font(profile.text_font_file)
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = [need("ffmpeg"), "-y", "-v", "error", "-i", str(src)]
    cold_open = bool(plan and plan["cold_open"])
    if plan and cold_open:
        start, end = plan["pieces"][0]
        cmd += ["-ss", f"{start:.6f}", "-t", f"{end - start:.6f}", "-i", str(src)]
    if has_overlay:
        cmd += ["-i", str(overlay)]
    if sticker:
        cmd += ["-i", str(sticker["path"])]
    cmd += [
        "-filter_complex",
        build_filter(profile, has_overlay, info["has_audio"], font, title, subtitles,
                     plan["pieces"] if plan else None, cold_open, sticker, look, speed, frame),
        "-map",
        "[vout]",
    ]
    cmd += _output_args(info["has_audio"], encoder, speed)
    cmd += ["-movflags", "+faststart", str(out)]
    run(cmd)
    sound = replace_audio(out, profile, audio)  # en egen video: Tyst eller Musik byter ljudet
    return {
        "source": str(src),
        "file": str(out),
        "overlay_applied": has_overlay,
        "subtitles_applied": subtitles is not None,
        "sticker_applied": sticker is not None,
        "silence_cut_s": plan["silence_cut_s"] if plan else 0.0,
        "fillers_cut": plan["fillers_cut"] if plan else 0,
        "cold_open_s": plan["cold_open_s"] if plan else 0.0,
        "text_applied": bool(profile.text),
        "text": profile.text,
        "hook_text": title.strip() if profile.hook_text and title.strip() else None,
        **sound,
    }
