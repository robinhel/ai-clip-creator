"""Gör ett färdigt 9:16-klipp kampanjklart: overlay, text på skärmen, jämn ljudnivå.

Det här är ett eget ffmpeg-pass ovanpå klippet från clipsmith. Det betyder en
extra omkodning. Fas 2 i byggplanen flyttar in stegen i clipsmiths egen
rendering (rendering/clipper.py) så att klippet bara kodas en gång.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from .media import find_font, need, probe, run
from .profile import Profile

WIDTH, HEIGHT = 1080, 1920
HOOK_SECONDS = 3
HOOK_FONT_SIZE = 64
HOOK_LINE_CHARS = 22  # ryms i 1080 px vid HOOK_FONT_SIZE

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


def _hook(
    profile: Profile, title: str, font: str | None, last: str, text_on_top: bool
) -> list[str]:
    """Titeln centrerad i övre säkra zonen de första sekunderna, på upp till tre rader."""
    lines = textwrap.wrap(title, HOOK_LINE_CHARS, max_lines=3, placeholder="…")
    top = int(HEIGHT * profile.safe_top) + (int(profile.text_font_size * 1.6) if text_on_top else 0)
    font_part = f"fontfile='{_esc(font)}':" if font else ""
    parts = []
    for i, line in enumerate(lines):
        out = "withhook" if i == len(lines) - 1 else f"hook{i}"
        parts.append(
            f"[{last}]drawtext={font_part}expansion=none:text={_hook_esc(line)}:"
            f"fontsize={HOOK_FONT_SIZE}:fontcolor=white:borderw=5:bordercolor=black@0.9:"
            f"x=(w-text_w)/2:y={top + i * int(HOOK_FONT_SIZE * 1.25)}:"
            f"enable='lt(t,{HOOK_SECONDS})'[{out}]"
        )
        last = out
    return parts


def build_filter(
    profile: Profile, has_overlay: bool, has_audio: bool, font: str | None, title: str = ""
) -> str:
    """Filtergrafen. Ingång 0 är klippet, ingång 1 är overlay-bilden om den finns."""
    parts = ["[0:v]setsar=1[base]"]
    last = "base"
    if has_overlay:
        if profile.overlay_mode == "full":
            parts.append(f"[1:v]scale={WIDTH}:{HEIGHT}[ov]")
            position = "0:0"
        else:
            parts.append(f"[1:v]scale={WIDTH}:-1[ov]")
            position = "0:0" if profile.overlay_mode == "top" else "0:H-h"
        parts.append(f"[{last}][ov]overlay={position}[withov]")
        last = "withov"
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
    title = title.strip()
    if profile.hook_text and title:
        text_on_top = bool(profile.text) and not has_overlay and profile.text_position == "top"
        parts += _hook(profile, title, font, last, text_on_top)
        last = "withhook"
    parts.append(f"[{last}]format=yuv420p[vout]")
    if has_audio:
        parts.append(f"[0:a]loudnorm=I={profile.lufs}:TP=-1.5:LRA=11[aout]")
    return ";".join(parts)


def finish_clip(
    src: Path, out: Path, profile: Profile, root: Path, encoder: str = "libx264", title: str = ""
) -> dict:
    """Rendera den kampanjklara versionen. Returnerar ett manifest om vad som gjordes."""
    info = probe(src)
    overlay = profile.overlay_path(root)
    has_overlay = overlay is not None and overlay.exists()
    font = find_font(profile.text_font_file)
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = [need("ffmpeg"), "-y", "-v", "error", "-i", str(src)]
    if has_overlay:
        cmd += ["-i", str(overlay)]
    cmd += [
        "-filter_complex",
        build_filter(profile, has_overlay, info["has_audio"], font, title),
        "-map",
        "[vout]",
    ]
    if info["has_audio"]:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
    cmd += ENCODER_ARGS.get(encoder, ENCODER_ARGS["libx264"])
    cmd += ["-movflags", "+faststart", str(out)]
    run(cmd)
    return {
        "source": str(src),
        "file": str(out),
        "overlay_applied": has_overlay,
        "text_applied": bool(profile.text),
        "text": profile.text,
        "hook_text": title.strip() if profile.hook_text and title.strip() else None,
    }
