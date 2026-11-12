"""Ändra kampanjer från appen: ny profil och config, overlay-bildens storlek, kontroll av profilens
YAML och radvis ändring av config-filen, så att kommentarerna finns kvar.

Fel ger ValueError med svensk text. Filer skrivs atomiskt (tmp-fil och os.replace).
"""

from __future__ import annotations

import os
import re
import stat
import struct
import tempfile
from collections.abc import Callable
from datetime import date
from pathlib import Path

import yaml

from .profile import Profile, load_profile

ID = re.compile(r"^[a-z0-9][a-z0-9_]{1,39}$")  # filnamnet; inget _ först, sådana profiler visas inte
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SOURCE_W, SOURCE_H = 1920, 1080  # rutorna mäts i en 1080p-bild; nedladdningen ber om 1080p60
PANEL_W, PANEL_H = 1080, 1920
MIN_SIDE = 50
SHAPE_TOLERANCE = 0.03
NEW_REFRAME = (
    "reframe:\n"
    "  mode: stacked         # facecam överst, gameplay under\n"
    "  # Rutorna ställs in i appen under Kampanjer: Facecam och spelruta.\n"
    "  split_ratio: 0.4      # facecam får 40 % av höjden\n"
)


def write_atomic(path: Path, data: bytes, check: Callable[[Path], None] | None = None) -> None:
    """Skriv en tmp-fil bredvid, kontrollera den (om check finns) och byt sedan ut filen."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
                                     delete=False) as file:
        file.write(data)
    tmp = Path(file.name)
    try:
        os.chmod(tmp, mode)
        if check:
            check(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


# ---------- overlay ----------


def png_size(head: bytes) -> tuple[int, int] | None:
    """Bredd och höjd ur PNG-filens första 24 byte, eller None om det inte är en PNG."""
    if len(head) < 24 or not head.startswith(PNG_SIGNATURE) or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def check_overlay(data: bytes, mode: str) -> None:
    """Samma storlek som finish.py förutsätter: full täcker 1080x1920, top och bottom är 1080 breda."""
    size = png_size(data[:24])
    if size is None:
        raise ValueError("Filen är inte en PNG-bild.")
    width, height = size
    if mode not in ("full", "top", "bottom"):
        raise ValueError(f"overlay.mode är {mode!r}. Den måste vara full, top eller bottom.")
    if mode == "full" and size != (PANEL_W, PANEL_H):
        raise ValueError(f"Overlay-bilden är {width}x{height}. Med mode: full måste den vara 1080x1920.")
    if mode in ("top", "bottom") and (width != PANEL_W or height > PANEL_H):
        raise ValueError(f"Overlay-bilden är {width}x{height}. Med mode: {mode} måste den vara 1080 bred "
                         "och högst 1920 hög.")


# ---------- profilens YAML ----------


def check_profile_text(text: str, profile_id: str, root: Path) -> Profile:
    """Samma kontroller som load_profile, plus det som annars kraschar senare i kedjan."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = f" på rad {mark.line + 1}" if mark else ""
        raise ValueError(f"YAML-fel{where}: {getattr(error, 'problem', None) or error}") from error
    if not isinstance(data, dict):
        raise ValueError("Profilen måste vara en YAML-karta med fält som name och streamer.")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{profile_id}.yaml"
        path.write_text(text, encoding="utf-8")
        try:
            profile = load_profile(path, Path(tmp))
        except SystemExit as error:
            raise ValueError(str(error)) from error
        except (TypeError, ValueError, AttributeError) as error:
            raise ValueError(f"Profilen går inte att läsa: {error}") from error
    if any(not isinstance(f, dict) or not f.get("id") for f in profile.formats):
        raise ValueError("Varje format i formats behöver ett id.")
    try:
        profile.caption_template.format(title="x")
    except (KeyError, IndexError, ValueError) as error:
        raise ValueError("caption.template får bara ha {title} inom klamrar.") from error
    if profile.clipsmith_config:
        config = (root / profile.clipsmith_config).resolve()
        if not config.is_relative_to(root.resolve()) or not config.exists():
            raise ValueError(f"clipsmith_config pekar på {profile.clipsmith_config}, som saknas.")
    return profile


# ---------- radvis ändring ----------


def _block(lines: list[str], section: str) -> tuple[int, int] | None:
    """Rubrikraden för en del på toppnivå och raden efter delens sista rad."""
    head = next((i for i, line in enumerate(lines) if re.match(rf"{re.escape(section)}:(\s|$)", line)), None)
    if head is None:
        return None
    if lines[head].split("#")[0].strip() != f"{section}:":
        raise ValueError(f"{section}: står på en rad. Ändra den i filen.")
    end = head + 1
    while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t"):
        end += 1
    while end > head + 1 and not lines[end - 1].strip():
        end -= 1
    return head, end


def set_keys(text: str, section: str, values: dict[str, str]) -> str:
    """Byt värdet på raderna `key:` direkt under `section:`. Kommentaren på raden finns kvar.
    Rader som saknas läggs till direkt under `section:`. Saknas delen läggs den till sist."""
    lines = text.splitlines(keepends=True)
    found = _block(lines, section)
    if found is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        return "".join([*lines, f"{section}:\n", *(f"  {k}: {v}\n" for k, v in values.items())])
    head, end = found
    children = [line for line in lines[head + 1:end] if line.strip() and not line.lstrip().startswith("#")]
    indent = children[0][: len(children[0]) - len(children[0].lstrip(" \t"))] if children else "  "
    missing = []
    for key, value in values.items():
        pattern = re.compile(rf"^({re.escape(indent)}{re.escape(key)}:[ \t]*)[^#\r\n]*?([ \t]+#[^\r\n]*)?(\r?\n)?$")
        for i in range(head + 1, end):
            match = pattern.match(lines[i])
            if match:
                lines[i] = match.group(1) + value + (match.group(2) or "") + (match.group(3) or "")
                break
        else:
            missing.append(f"{indent}{key}: {value}\n")
    if not lines[head].endswith("\n"):
        lines[head] += "\n"
    lines[head + 1:head + 1] = missing
    return "".join(lines)


def _replace_block(text: str, section: str, block: str) -> str:
    lines = text.splitlines(keepends=True)
    found = _block(lines, section)
    if found is None:
        raise ValueError(f"Mallen saknar {section}:.")
    head, end = found
    lines[head:end] = [block]
    return "".join(lines)


# ---------- facecam och spelruta ----------


def panel_aspects(split_ratio: float) -> tuple[float, float]:
    """Formen (bredd/höjd) som rutorna måste ha. Samma uträkning som clipper._stacked_filter_complex."""
    top = int(PANEL_H * split_ratio)
    return round(PANEL_W / top, 5), round(PANEL_W / (PANEL_H - top), 5)


def check_rect(rect: object, aspect: float, label: str) -> list[int]:
    if not (isinstance(rect, list) and len(rect) == 4
            and all(isinstance(v, int) and not isinstance(v, bool) for v in rect)):
        raise ValueError(f"{label} måste vara fyra heltal: x, y, bredd, höjd.")
    x, y, w, h = rect
    if w < MIN_SIDE or h < MIN_SIDE:
        raise ValueError(f"{label} är för liten. Bredd och höjd måste vara minst {MIN_SIDE} pixlar.")
    if x < 0 or y < 0 or x + w > SOURCE_W or y + h > SOURCE_H:
        raise ValueError(f"{label} går utanför bilden ({SOURCE_W}x{SOURCE_H}).")
    if abs(w / h - aspect) / aspect > SHAPE_TOLERANCE:
        raise ValueError(f"{label} har fel form: {w}x{h} är {w / h:.3f} (bredd/höjd), panelen är {aspect:.3f}. "
                         "Skillnaden får vara högst 3 %, annars blir bilden utdragen.")
    return rect


def reframe_text(text: str, webcam: list[int], gameplay: list[int], split_ratio: float) -> str:
    def as_list(rect: list[int]) -> str:
        return "[" + ", ".join(str(v) for v in rect) + "]"

    values = {"webcam_rect": as_list(webcam), "gameplay_rect": as_list(gameplay), "split_ratio": repr(split_ratio)}
    if _block(text.splitlines(keepends=True), "reframe") is None:
        values = {"mode": "stacked", **values}  # utan mode används inte rutorna (clipsmiths standard är center)
    return set_keys(text, "reframe", values)


# ---------- ny kampanj ----------


def _format_ids(formats: list[dict]) -> list[dict]:
    taken: set[str] = set()
    out = []
    for item in formats:
        base = item.get("id") or re.sub(r"[^a-z0-9]+", "_", str(item.get("name", "")).lower()).strip("_") or "format"
        fid, n = base, 2
        while fid in taken:
            fid, n = f"{base}_{n}", n + 1
        taken.add(fid)
        out.append({"id": fid, "name": item.get("name") or fid, "description": item.get("description", "")})
    return out


def new_profile_text(profile_id: str, body: dict, config_name: str) -> str:
    """Profilen som YAML. Bara channel och streamer krävs; resten får standardvärden."""
    link = f"twitch.tv/{profile_id}"
    text = link if body.get("on_screen_text") is None else body["on_screen_text"]
    payment = {k: body[k] for k in ("cpm_usd", "min_views", "max_payout_per_clip_usd") if body.get(k) is not None}
    data = {
        "name": profile_id,
        "streamer": body["streamer"],
        "campaign": body.get("campaign") or body["streamer"],
        "language": body.get("language") or "en",
        "clipsmith_config": config_name,
        "clip": {"min_seconds": body["clip_min_s"], "max_seconds": body["clip_max_s"]},
        "formats": _format_ids(body.get("formats") or []),
        "on_screen_text": {"text": text, "required": bool(text), "position": body["text_position"],
                           "font_size": 46, "font_file": None},
        "overlay": {"file": f"overlays/{profile_id}.png", "required": body["overlay_required"],
                    "mode": body["overlay_mode"]},
        "facecam": {"required": body["facecam_required"]},
        "safe_zone": {"top": 0.07, "bottom": 0.20},
        "caption": {
            "must_include": body["must_include"] if body.get("must_include") is not None else [link],
            "hashtags": body["hashtags"] if body.get("hashtags") is not None else [f"#{profile_id}", "#twitchclips"],
            "game_hashtag": True,
            "template": body.get("caption_template") or f"{{title}} 👉 {link}",
        },
        "audio": {"lufs": -14},
        "payment": payment,
    }
    if body.get("engagement_min_percent") is not None:
        data["engagement_min_percent"] = [{"views_up_to": None, "min": body["engagement_min_percent"]}]
    header = (f"# Kampanjprofil: {body['streamer']}. Skapad i appen {date.today().isoformat()}.\n"
              "# Ändra den under Kampanjer i appen eller här. Kontrollera reglerna mot kampanjsidan.\n\n")
    return header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def new_config_text(template: str, profile_id: str, min_s: int, max_s: int) -> str:
    """En kopia av mallen med kanalen utbytt och utan uppmätta rutor. Övriga kommentarer finns kvar."""
    lines = template.splitlines(keepends=True)
    start = 0
    while start < len(lines) and lines[start].startswith("#"):
        start += 1
    text = (f"# clipsmith-inställningar för kampanjen {profile_id}. Skapad i appen {date.today().isoformat()}\n"
            f"# som en kopia av config.timmy.yaml.\n"
            f"# Används så här:  clipsmith run-vod <VOD-id> --config config.{profile_id}.yaml\n"
            + "".join(lines[start:]))
    text = _replace_block(text, "channels", f"channels:\n  - {profile_id}\n")
    text = _replace_block(text, "reframe", NEW_REFRAME)
    return set_keys(text, "clip", {"min_seconds": str(min_s), "max_seconds": str(max_s)})
