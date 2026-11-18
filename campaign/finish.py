"""Gör ett färdigt 9:16-klipp kampanjklart: overlay, text på skärmen, jämn ljudnivå.

Det här är ett eget ffmpeg-pass ovanpå klippet från clipsmith. Det betyder en
extra omkodning. Fas 2 i byggplanen flyttar in stegen i clipsmiths egen
rendering (rendering/clipper.py) så att klippet bara kodas en gång.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from clipsmith.rendering.clipper import ZOOM_TOP

from .audio import replace_audio
from .media import find_font, need, probe, run
from .profile import Profile

WIDTH, HEIGHT = 1080, 1920
HOOK_FONT_SIZE = 64
HOOK_LINE_PX = 960  # bredaste raden, så att texten inte når bildens kanter (1080 px)
HOOK_MAX_LINES = 3
HOOK_GAP = 24  # zoom: luft mellan hookens sista rad och bilden
HOOK_LINE_H = int(HOOK_FONT_SIZE * 1.25)  # en rad hook, med luft
HOOK_PAD = 24  # det svarta fältets luft ovanför första raden; under sista raden är luften raden ut
HOOK_SECONDS = 3  # stacked: så länge rubriken syns
FONTS = Path(__file__).parent / "fonts"
HOOK_FONTS = {"sans": FONTS / "Montserrat-ExtraBold.ttf", "serif": FONTS / "DMSerifDisplay-Regular.ttf"}
# Emoji, pilar, symboler, dingbats (✨ ❤), ZWJ, keycap och U+FE0F: fonterna saknar dem och ritar tomma rutor
NO_GLYPH = re.compile("[\U00010000-\U0010ffff\u2190-\u2bff\u200d\u20e3\ufe0f]")


def _load_widths() -> dict[str, tuple[dict[str, float], float]]:
    """Per font i fonts/: (pixlar per tecken vid HOOK_FONT_SIZE, bredaste tecknet). widths.json är genererad
    ur fontfilerna (advance width och unitsPerEm)."""
    tables = {}
    for name, font in json.loads((FONTS / "widths.json").read_text(encoding="utf-8")).items():
        scale = HOOK_FONT_SIZE / font["units_per_em"]
        px = {char: width * scale for char, width in font["widths"].items()}
        tables[name] = (px, max(px.values()))
    return tables


HOOK_WIDTHS = _load_widths()

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


def hook_clean(title: str) -> str:
    """Rubriken som den bränns in: styrtecken blir blanksteg (NUL stoppar ffmpeg-anropet), tecken utan glyf i
    fonterna tas bort och blanksteg som blir kvar slås ihop."""
    return " ".join(NO_GLYPH.sub("", re.sub(r"[\x00-\x1f]", " ", title)).split())


def _text_on_top(profile: Profile, has_overlay: bool) -> bool:
    """Kampanjens vita text ritas uppe. Overlayen har redan kanalens text, så den ritas bara utan overlay."""
    return bool(profile.text) and not has_overlay and profile.text_position == "top"


def _hook_start(profile: Profile, text_on_top: bool) -> int:
    """Högsta raden hooken får börja på: under den övre säkra zonen och kampanjens text."""
    return int(HEIGHT * profile.safe_top) + (int(profile.text_font_size * 1.6) if text_on_top else 0)


def _max_lines(start: int, frame: str) -> int:
    """I zoom bara så många rader som ryms mellan start och bilden, minst en."""
    if frame != "zoom":
        return HOOK_MAX_LINES
    room = ZOOM_TOP - HOOK_GAP - HOOK_FONT_SIZE - start
    return max(1, min(HOOK_MAX_LINES, 1 + room // HOOK_LINE_H))


def _style_font(profile: Profile) -> Path:
    return HOOK_FONTS["serif" if profile.hook_style == "serif" else "sans"]


def _line_px(line: str, fonts: list[str]) -> float:
    """Radens bredd enligt tabellen: det bredaste värdet per tecken i fonterna. Ett okänt tecken räknas som
    fontens bredaste."""
    return sum(max(HOOK_WIDTHS[font][0].get(char, HOOK_WIDTHS[font][1]) for font in fonts) for char in line)


def _split_word(word: str, fits: Callable[[str], bool]) -> list[str]:
    """Ett ord som ensamt är bredare än en rad delas, som textwrap gör."""
    pieces = [""]
    for char in word:
        if pieces[-1] and not fits(pieces[-1] + char):
            pieces.append("")
        pieces[-1] += char
    return pieces


def _with_ellipsis(line: str, fits: Callable[[str], bool]) -> str:
    """Sista raden med "…", kortad ord för ord tills den ryms."""
    while line and not fits(line + "…"):
        line = line.rsplit(" ", 1)[0] if " " in line else line[:-1]
    return line + "…"


def _wrap(text: str, fits: Callable[[str], bool], max_lines: int) -> list[str]:
    """Raderna efter bredd: så många ord som ryms på varje rad, högst max_lines rader, annars "…" sist."""
    lines: list[str] = []
    for word in text.split():
        joined = f"{lines[-1]} {word}" if lines else word
        if lines and fits(joined):
            lines[-1] = joined
        else:
            lines += _split_word(word, fits)
    if len(lines) <= max_lines:
        return lines
    return lines[: max_lines - 1] + [_with_ellipsis(lines[max_lines - 1], fits)]


def _hook_wrap(profile: Profile, title: str, own_font: bool, max_lines: int) -> list[str]:
    """Titelns rader, högst HOOK_LINE_PX breda och högst max_lines, också när kampanjen inte visar hooken. Stilen
    serif skrivs och mäts i versaler. Med en egen font (own_font) mäts raden med det bredaste värdet ur båda
    stilarnas tabeller. Emoji tas bort."""
    title = hook_clean(title)
    if profile.hook_style == "serif":
        title = title.upper()
    fonts = list(HOOK_WIDTHS) if own_font else [_style_font(profile).name]
    return _wrap(title, lambda line: _line_px(line, fonts) <= HOOK_LINE_PX, max_lines)


def _hook_lines(
    profile: Profile, title: str, text_on_top: bool = False, frame: str = "", own_font: bool = False
) -> list[str]:
    """Hookens rader i bilden (_hook_wrap), inga när kampanjen inte visar hooken."""
    if not profile.hook_text:
        return []
    return _hook_wrap(profile, title, own_font, _max_lines(_hook_start(profile, text_on_top), frame))


def _hook_top(profile: Profile, text_on_top: bool, lines: int = 1, frame: str = "") -> int:
    """Hookens första rad. I bildläget zoom står den på det svarta precis ovanför bilden, men aldrig i den
    övre säkra zonen."""
    top = _hook_start(profile, text_on_top)
    if frame == "zoom":
        top = max(top, ZOOM_TOP - HOOK_GAP - HOOK_FONT_SIZE - (lines - 1) * HOOK_LINE_H)
    return top


def _overlay_used(profile: Profile, root: Path, overlay: Path | None) -> Path | None:
    """Overlayen klippet får: en egen eller kampanjens, om filen finns."""
    overlay = overlay or profile.overlay_path(root)
    return overlay if overlay is not None and overlay.exists() else None


def top_text_end(
    profile: Profile, root: Path, title: str, overlay: Path | None = None, frame: str = "",
    font_file: str | None = None,
) -> int:
    """Raden i bilden där texten uppe slutar: kampanjens text och hooken med sitt svarta fält. 0 när ingen text
    ritas uppe. Samma läge som build_filter ritar dem på, så att ett klistermärke kan läggas under. font_file:
    samma som till finish_clip, eftersom en egen font kan ge fler rader."""
    on_top = _text_on_top(profile, _overlay_used(profile, root, overlay) is not None)
    lines = _hook_lines(profile, title, on_top, frame, font_file is not None)
    if lines:
        return _hook_top(profile, on_top, len(lines), frame) + len(lines) * HOOK_LINE_H
    return _hook_top(profile, on_top) if on_top else 0


def hook_fits(
    profile: Profile, root: Path, title: str, overlay: Path | None = None, frame: str = "",
    font_file: str | None = None,
) -> bool:
    """Texten ryms i bilden utan "…", med samma radbrytning som build_filter och samma argument som top_text_end.
    Mäts också när kampanjen inte visar hooken (hook_text), eftersom den kan slås på."""
    on_top = _text_on_top(profile, _overlay_used(profile, root, overlay) is not None)
    most = _max_lines(_hook_start(profile, on_top), frame)
    return len(_hook_wrap(profile, title, font_file is not None, most + 1)) <= most


def _hook(
    profile: Profile, title: str, font: str | None, last: str, text_on_top: bool, frame: str = ""
) -> list[str]:
    """Titeln vit och centrerad under övre säkra zonen hela klippet, på upp till tre rader (_hook_top). Utom i
    zoom, där bakgrunden redan är svart, ligger ett svart fält i full bredd bakom. I stacked ligger streamerns
    kamera överst, så där får texten en svart kant i stället för fält och syns bara de första HOOK_SECONDS.
    font: stilens font om None."""
    lines = _hook_lines(profile, title, text_on_top, frame, font is not None)
    top = _hook_top(profile, text_on_top, len(lines), frame)
    font = font or str(_style_font(profile))
    parts = []
    color = "fontcolor=white"
    if frame == "stacked":
        color += f":borderw=5:bordercolor=black@0.9:enable='lt(t,{HOOK_SECONDS})'"
    elif frame != "zoom":
        height = HOOK_PAD + len(lines) * HOOK_LINE_H
        parts.append(f"[{last}]drawbox=x=0:y={top - HOOK_PAD}:w=iw:h={height}:color=black:t=fill[hookbar]")
        last = "hookbar"
    for i, line in enumerate(lines):
        out = "withhook" if i == len(lines) - 1 else f"hook{i}"
        parts.append(
            f"[{last}]drawtext=fontfile='{_esc(font)}':expansion=none:text={_hook_esc(line)}:"
            f"x=(w-text_w)/2:y={top + i * HOOK_LINE_H}:fontsize={HOOK_FONT_SIZE}:{color}[{out}]"
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
    sticker: dict | None = None, look: str = "", speed: float = 1.0, frame: str = "", hook_font: str | None = None,
) -> str:
    """Filtergrafen. Ingång 0 är klippet, sedan hooken (cold_open), overlay-bilden och klistermärket om de finns.
    Med bitar (tidskartan) sätts de ihop först. Undertexten bränns in sedan, så att overlayen ligger ovanpå den.
    sticker: {w, h, x, y, start_s, end_s}, en ny versions klistermärke ovanpå overlayen under en stund.
    look: filter (",hflip,eq=...") på bilden före undertexten, så att texten inte speglas. speed: hela klippet
    snabbare sist, så att undertexten och hooken följer med. frame: råklippets bildläge (ordfilens reframe.mode),
    som bestämmer var hooken står. hook_font: en egen font till hooken; None ger stilens (profile.hook_style).
    font är kampanjens vita text."""
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
        parts += _hook(profile, title, hook_font, last, _text_on_top(profile, has_overlay), frame)
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
    overlay, en fontfil till texten på skärmen och hooken, och ett klistermärke (med path). Utan fontfil får
    hooken stilens font. Returnerar ett manifest om vad som gjordes."""
    title = hook_clean(title)
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
                     plan["pieces"] if plan else None, cold_open, sticker, look, speed, frame, font_file),
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
        "hook_text": title if profile.hook_text and title else None,
        **sound,
    }
