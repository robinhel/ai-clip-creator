"""Gör ett färdigt 9:16-klipp kampanjklart: overlay, text på skärmen, jämn ljudnivå.

Det här är ett eget ffmpeg-pass ovanpå klippet från clipsmith. Det betyder en
extra omkodning. Fas 2 i byggplanen flyttar in stegen i clipsmiths egen
rendering (rendering/clipper.py) så att klippet bara kodas en gång.
"""

from __future__ import annotations

import re
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


def _pieces(pieces: list[tuple[float, float]], has_audio: bool) -> list[str]:
    """Bitarna ur klippet efter varandra (tidskartan). Ut: [cv] och, med ljud, [ca]."""
    parts, inputs = [], ""
    for i, (start, end) in enumerate(pieces):
        span = f"start={start:.6f}:end={end:.6f}"
        parts.append(f"[0:v]trim={span},setpts=PTS-STARTPTS[v{i}]")
        inputs += f"[v{i}]"
        if has_audio:
            parts.append(f"[0:a]atrim={span},asetpts=PTS-STARTPTS[a{i}]")
            inputs += f"[a{i}]"
    outputs = "[cv][ca]" if has_audio else "[cv]"
    parts.append(f"{inputs}concat=n={len(pieces)}:v=1:a={int(has_audio)}{outputs}")
    return parts


def build_filter(
    profile: Profile, has_overlay: bool, has_audio: bool, font: str | None, title: str = "",
    subtitles: Path | None = None, pieces: list[tuple[float, float]] | None = None,
) -> str:
    """Filtergrafen. Ingång 0 är klippet, ingång 1 är overlay-bilden om den finns. Med bitar (tidskartan)
    sätts de ihop först. Undertexten bränns in sedan, så att overlayen ligger ovanpå den."""
    burn = f",subtitles=filename='{_esc(str(subtitles))}'" if subtitles else ""
    parts = _pieces(pieces, has_audio) if pieces else []
    video, audio = ("cv", "ca") if pieces else ("0:v", "0:a")
    parts.append(f"[{video}]setsar=1{burn}[base]")
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
    title = re.sub(r"[\x00-\x1f]", " ", title).strip()  # ett styrtecken (NUL) stoppar ffmpeg-anropet
    if profile.hook_text and title:
        text_on_top = bool(profile.text) and not has_overlay and profile.text_position == "top"
        parts += _hook(profile, title, font, last, text_on_top)
        last = "withhook"
    parts.append(f"[{last}]format=yuv420p[vout]")
    if has_audio:
        parts.append(f"[{audio}]loudnorm=I={profile.lufs}:TP=-1.5:LRA=11[aout]")
    return ";".join(parts)


def finish_clip(
    src: Path, out: Path, profile: Profile, root: Path, encoder: str = "libx264", title: str = "",
    subtitles: Path | None = None, plan: dict | None = None,
) -> dict:
    """Rendera den kampanjklara versionen. plan är tidskartan (campaign.timemap). Returnerar ett manifest
    om vad som gjordes."""
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
        build_filter(profile, has_overlay, info["has_audio"], font, title, subtitles, plan["pieces"] if plan else None),
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
        "subtitles_applied": subtitles is not None,
        "silence_cut_s": plan["silence_cut_s"] if plan else 0.0,
        "fillers_cut": plan["fillers_cut"] if plan else 0,
        "cold_open_s": plan["cold_open_s"] if plan else 0.0,
        "text_applied": bool(profile.text),
        "text": profile.text,
        "hook_text": title.strip() if profile.hook_text and title.strip() else None,
    }
