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

from clipsmith.config.loaders import load_config
from clipsmith.config.models import AppConfig

from .profile import Profile, load_profile

CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")  # radbrytningar, tabbar och andra styrtecken
ID = re.compile(r"^[a-z0-9][a-z0-9_]{1,39}$")  # filnamnet; inget _ först, sådana profiler visas inte
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SOURCE_W, SOURCE_H = 1920, 1080  # rutorna mäts i en 1080p-bild; nedladdningen ber om 1080p60
PANEL_W, PANEL_H = 1080, 1920
MIN_SIDE = 50
MAX_WEBCAM_RECTS = 4
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


# ---------- filer som profilen pekar på ----------


def config_file(root: Path, name: object) -> Path:
    """Kampanjens clipsmith-config. Bara en .yaml-fil direkt i projektmappen, så att ingen annan fil
    (till exempel .env) kan läsas via profilen."""
    bad = ValueError(f"clipsmith_config är {name!r}. Den måste vara en .yaml-fil direkt i projektmappen, "
                     "till exempel config.kanal.yaml.")
    if (not isinstance(name, str) or not name or Path(name).name != name or name.startswith(".")
            or not name.endswith(".yaml")):
        raise bad
    path = root / name
    if not path.is_file():
        raise ValueError(f"Config-filen {name} saknas.")
    real = path.resolve()  # en symlänk får inte peka ut någon annan fil
    if real.parent != root.resolve() or real.name.startswith(".") or real.suffix != ".yaml":
        raise bad
    return path


def read_config(path: Path) -> AppConfig:
    """Läs config-filen. Felets text skrivs aldrig ut, eftersom den kan citera filens innehåll."""
    try:
        return load_config(path)
    except Exception:  # yaml- och pydantic-fel citerar innehållet
        raise ValueError(f"Config-filen {path.name} går inte att läsa.") from None


def overlay_target(root: Path, file: object) -> Path:
    target = (root / str(file)).resolve()
    if (not isinstance(file, str) or not target.is_relative_to((root / "overlays").resolve())
            or target.suffix.lower() != ".png"):
        raise ValueError(f"overlay.file är {file}. Den måste ligga under overlays/ och sluta på .png.")
    return target


# ---------- profilens YAML ----------


def _parse(text: str) -> object:
    """Läs YAML utan ankare och alias, så att en liten fil inte kan växa till en enorm (billion laughs)."""
    try:
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
                raise ValueError("YAML med ankare och alias (& och *) går inte att spara. Skriv ut värdena.")
        return yaml.safe_load(text)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = f" på rad {mark.line + 1}" if mark else ""
        raise ValueError(f"YAML-fel{where}: {getattr(error, 'problem', None) or error}") from error


def _check_types(data: dict, root: Path) -> None:
    def text(value: object, label: str) -> None:
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{label} måste vara text.")

    def texts(value: object, label: str) -> None:
        if value is not None and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
            raise ValueError(f"{label} måste vara en lista med text.")

    for key in ("name", "streamer", "campaign", "language"):
        text(data.get(key), key)
    sections = {key: data.get(key) or {} for key in ("caption", "on_screen_text", "overlay")}
    for key, section in sections.items():
        if not isinstance(section, dict):
            raise ValueError(f"{key} måste vara en karta med fält.")
    texts(sections["caption"].get("hashtags"), "caption.hashtags")
    texts(sections["caption"].get("must_include"), "caption.must_include")
    text(sections["caption"].get("template"), "caption.template")
    text(sections["on_screen_text"].get("text"), "on_screen_text.text")
    formats = data.get("formats")
    if formats is not None and not (isinstance(formats, list) and all(
            isinstance(f, dict) and isinstance(f.get("id"), str) and f["id"] for f in formats)):
        raise ValueError("Varje format i formats behöver ett id.")
    if sections["overlay"].get("file") is not None:
        overlay_target(root, sections["overlay"]["file"])
    font = sections["on_screen_text"].get("font_file")
    if font is not None and not (isinstance(font, str) and (root / font).resolve().is_relative_to(root.resolve())
                                 and Path(font).suffix.lower() in (".ttf", ".otf")):
        raise ValueError("on_screen_text.font_file måste vara null eller en .ttf- eller .otf-fil i projektmappen.")


def check_profile_text(text: str, profile_id: str, root: Path) -> Profile:
    """Samma kontroller som load_profile, plus det som annars kraschar senare i kedjan."""
    data = _parse(text)
    if not isinstance(data, dict):
        raise ValueError("Profilen måste vara en YAML-karta med fält som name och streamer.")
    _check_types(data, root)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{profile_id}.yaml"
        path.write_text(text, encoding="utf-8")
        try:
            profile = load_profile(path, Path(tmp))
        except SystemExit as error:
            raise ValueError(str(error)) from error
        except (TypeError, ValueError, AttributeError) as error:
            raise ValueError(f"Profilen går inte att läsa: {error}") from error
    try:
        profile.caption_template.format(title="x")
    except (KeyError, IndexError, ValueError) as error:
        raise ValueError("caption.template får bara ha {title} inom klamrar.") from error
    if data.get("clipsmith_config") is not None:
        read_config(config_file(root, data["clipsmith_config"]))
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
    # En kommentar i kolumn 0 avslutar inte delen; bara en ny nyckel på toppnivå gör det.
    while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t#"):
        end += 1
    while end > head + 1 and (not lines[end - 1].strip() or lines[end - 1].startswith("#")):
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


def check_rects(rects: object, aspect: float) -> list[list[int]]:
    """1 till 4 kamerarutor. clipsmith väljer per klipp den ruta där ansiktet syns."""
    if not (isinstance(rects, list) and 1 <= len(rects) <= MAX_WEBCAM_RECTS):
        raise ValueError(f"webcam_rects måste vara en lista med 1 till {MAX_WEBCAM_RECTS} rutor.")
    return [check_rect(rect, aspect, f"Facecam-ruta {i}") for i, rect in enumerate(rects, 1)]


def reframe_text(text: str, webcams: list[list[int]], gameplay: list[int], split_ratio: float) -> str:
    def as_list(rect: list[int]) -> str:
        return "[" + ", ".join(str(v) for v in rect) + "]"

    # mode: stacked behövs, annars används inte rutorna (clipsmiths standard är center).
    # webcam_rect är den första rutan, så att den som bara läser webcam_rect får den förvalda.
    values = {"mode": "stacked", "webcam_rect": as_list(webcams[0]),
              "webcam_rects": "[" + ", ".join(as_list(r) for r in webcams) + "]",
              "gameplay_rect": as_list(gameplay), "split_ratio": repr(split_ratio)}
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
    streamer = " ".join(str(body["streamer"]).split())  # en radbrytning här skulle lämna kommentaren
    header = (f"# Kampanjprofil: {streamer}. Skapad i appen {date.today().isoformat()}.\n"
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
