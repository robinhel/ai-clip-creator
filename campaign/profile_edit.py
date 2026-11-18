"""Ändra kampanjer från appen: ny profil och config, overlay-bildens storlek, kontroll av profilens
YAML och radvis ändring av config-filen, så att kommentarerna finns kvar.

Fel ger ValueError med svensk text. Filer skrivs atomiskt (tmp-fil och os.replace).
"""

from __future__ import annotations

import copy
import json
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

from .media import need, run
from .profile import Profile, load_profile

CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")  # radbrytningar, tabbar och andra styrtecken
ID = re.compile(r"^[a-z0-9][a-z0-9_]{1,39}$")  # filnamnet; inget _ först, sådana profiler visas inte
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SOURCE_W, SOURCE_H = 1920, 1080  # rutorna mäts i en 1080p-bild; nedladdningen ber om 1080p60
PANEL_W, PANEL_H = 1080, 1920
BANNER_W = 880  # en banner i en hel overlay: bredden i klippet, med luft på sidorna
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


def is_banner(width: int, height: int) -> bool:
    """En smal remsa, till exempel kampanjens TWITCH STREAM OVERLAY i 1756x288."""
    return width >= 2 * height


def check_overlay(data: bytes, mode: str) -> None:
    """Samma storlek som finish.py förutsätter: full täcker 1080x1920, top och bottom är 1080 breda.
    Med full går en banner också, den görs om till 1080x1920 med banner_to_full."""
    size = png_size(data[:24])
    if size is None:
        raise ValueError("Filen är inte en PNG-bild.")
    width, height = size
    if mode not in ("full", "top", "bottom"):
        raise ValueError(f"overlay.mode är {mode!r}. Den måste vara full, top eller bottom.")
    if mode == "full" and size != (PANEL_W, PANEL_H) and not is_banner(width, height):
        raise ValueError(f"Overlay-bilden är {width}x{height}. Med mode: full måste den vara 1080x1920, "
                         "eller en banner som är minst dubbelt så bred som hög.")
    if mode in ("top", "bottom") and (width != PANEL_W or height > PANEL_H):
        raise ValueError(f"Overlay-bilden är {width}x{height}. Med mode: {mode} måste den vara 1080 bred "
                         "och högst 1920 hög.")


def banner_to_full(data: bytes, seam_y: int) -> bytes:
    """Lägg bannern på en genomskinlig bild i 1080x1920: BANNER_W bred, mitt på sidan och centrerad på
    höjden seam_y, skarven mellan kameran och spelet."""
    with tempfile.TemporaryDirectory() as tmp:
        banner, out = Path(tmp) / "banner.png", Path(tmp) / "overlay.png"
        banner.write_bytes(data)
        graph = (f"[1:v]scale={BANNER_W}:-1,format=rgba[b];"
                 f"[0:v][b]overlay=(W-w)/2:'min(max({seam_y}-h/2,0),H-h)':format=auto,format=rgba")
        try:
            run([need("ffmpeg"), "-v", "error", "-y",
                 "-f", "lavfi", "-i", f"color=c=black@0.0:s={PANEL_W}x{PANEL_H},format=rgba", "-i", str(banner),
                 "-filter_complex", graph, "-frames:v", "1", str(out)])
        except RuntimeError:
            raise ValueError("Bannern gick inte att göra om till en hel overlay. Spara den som PNG igen.") from None
        return out.read_bytes()


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


# PyYAML:s vanligaste fel i enkel svenska. Nyckeln är början på felets text.
YAML_PROBLEMS = (
    ("expected ',' or ']'", "En lista inom [ ] är inte rätt skriven. Kontrollera kommatecken och klamrar."),
    ("expected ',' or '}'", "En karta inom { } är inte rätt skriven. Kontrollera kommatecken och klamrar."),
    ("mapping values are not allowed", "Ett kolon står på fel ställe. Sätt texten inom citattecken om den har ett kolon."),
    ("found character '\\t'", "Raden har en tabb. Använd mellanslag för indrag."),
    ("could not find expected ':'", "Ett kolon saknas efter fältets namn."),
    ("found unexpected end of stream", "Ett citattecken är inte stängt."),
    ("expected <block end>", "Indraget är fel. Rader i samma del ska ha lika många mellanslag."),
    ("did not find expected", "Indraget är fel. Rader i samma del ska ha lika många mellanslag."),
    ("expected a comment or a line break", "Något står efter värdet på raden. Sätt texten inom citattecken."),
)


def _yaml_problem(error: yaml.YAMLError) -> str:
    problem = str(getattr(error, "problem", None) or "")
    return next((text for start, text in YAML_PROBLEMS if problem.startswith(start)),
                "Texten går inte att läsa. Kontrollera klamrar, citattecken, kolon och indrag.")


def parse_yaml(text: str) -> object:
    """Läs YAML utan ankare och alias, så att en liten fil inte kan växa till en enorm (billion laughs)."""
    try:
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
                raise ValueError("YAML med ankare och alias (& och *) går inte att spara. Skriv ut värdena.")
        return yaml.safe_load(text)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = f"Fel på rad {mark.line + 1}" if mark else "Fel i texten"
        raise ValueError(f"{where}: {_yaml_problem(error)}") from error


def _check_types(data: dict, root: Path) -> None:
    def text(value: object, label: str) -> None:
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{label} måste vara text.")

    def texts(value: object, label: str) -> None:
        if value is not None and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
            raise ValueError(f"{label} måste vara en lista med text.")

    for key in ("name", "streamer", "campaign", "language"):
        text(data.get(key), key)
    if data.get("active") is not None and not isinstance(data["active"], bool):
        raise ValueError("active måste vara true eller false.")
    sections = {key: data.get(key) or {} for key in ("caption", "on_screen_text", "overlay")}
    for key, section in sections.items():
        if not isinstance(section, dict):
            raise ValueError(f"{key} måste vara en karta med fält.")
    texts(sections["caption"].get("hashtags"), "caption.hashtags")
    texts(sections["caption"].get("must_include"), "caption.must_include")
    text(sections["caption"].get("template"), "caption.template")
    text(sections["caption"].get("tag_line"), "caption.tag_line")
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
    data = parse_yaml(text)
    if not isinstance(data, dict):
        raise ValueError("Kampanjens fil måste vara en YAML-karta med fält som name och streamer.")
    _check_types(data, root)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{profile_id}.yaml"
        path.write_text(text, encoding="utf-8")
        try:
            profile = load_profile(path, Path(tmp))
        except SystemExit as error:
            raise ValueError(str(error)) from error
        except (TypeError, ValueError, AttributeError) as error:
            raise ValueError(f"Kampanjens fil går inte att läsa: {error}") from error
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


# ---------- formuläret Ställ in: ändra fält och låt resten av filen vara ----------

DELETE = object()  # värdet i update_profile: ta bort fältet
UNSAVED = "Ändringen gick inte att skriva in i filen. Gör den under Avancerat (YAML)."


def _literal(value: object) -> str:
    """Värdet som YAML på en rad. JSON är giltig YAML. Ett flyttal skrivs alltid med punkt, aldrig som 1e-05."""
    if isinstance(value, float):
        text = f"{value:.6f}".rstrip("0")
        return text + "0" if text.endswith(".") else text
    if isinstance(value, list):
        return "[" + ", ".join(_literal(v) for v in value) + "]"
    if value is None or isinstance(value, bool | int | str):
        return json.dumps(value, ensure_ascii=False)
    raise ValueError(UNSAVED)


def _quote_starts(line: str, i: int) -> bool:
    return line[i] in "\"'" and line[:i].rstrip()[-1:] in ("", ":", "[", "{", ",", "-")


def _comment_starts(line: str, i: int) -> bool:
    return line[i] == "#" and line[i - 1:i] in (" ", "\t")


def _closing_quote(line: str, i: int) -> int:
    """Platsen för citattecknet som stänger det på plats i. Inom "..." hoppar \\ över nästa tecken."""
    quote, i = line[i], i + 1
    while i < len(line) and line[i] != quote:
        i += 2 if quote == '"' and line[i] == "\\" else 1
    return i


def _comment(line: str) -> str:
    """Kommentaren sist på raden, med blanktecknen före, annars tom text. # inom citattecken räknas inte."""
    i = 0
    while i < len(line):
        if _quote_starts(line, i):
            i = _closing_quote(line, i)
        elif _comment_starts(line, i):
            return line[len(line[:i].rstrip(" \t")):].rstrip("\r\n")
        i += 1
    return ""


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _skipped(line: str) -> bool:
    """Tomma rader och kommentarer."""
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def _list_item(line: str) -> bool:
    stripped = line.strip()
    return stripped == "-" or stripped.startswith("- ")


def _continues(line: str, ind: int) -> bool:
    """Raden hör till fältet med indraget ind: större indrag, eller en listrad med samma indrag."""
    level = _indent(line)
    return level > ind or (level == ind and _list_item(line))


def _end(lines: list[str], head: int) -> int:
    """Raden efter fältets sista rad. Kommentarer och tomma rader efter fältet hör till nästa fält."""
    ind, last = _indent(lines[head]), head
    for i in range(head + 1, len(lines)):
        if _skipped(lines[i]):
            continue
        if not _continues(lines[i], ind):
            break
        last = i
    return last + 1


def _find(lines: list[str], key: str, rows: range, ind: int) -> int | None:
    pattern = re.compile(rf"[ \t]{{{ind}}}{re.escape(key)}:(\s|$)")
    return next((i for i in rows if pattern.match(lines[i]) and _indent(lines[i]) == ind), None)


def _is_formats(value: object) -> bool:
    """En lista med kartor, som formats."""
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


def _render_items(items: list[dict], ind: str) -> list[str]:
    return [f"{ind}  {'- ' if n == 0 else '  '}{k}: {_literal(v)}\n"
            for item in items for n, (k, v) in enumerate(item.items())]


def _render(key: str, value: object, ind: str) -> list[str]:
    if isinstance(value, dict) and value:
        return [f"{ind}{key}:\n", *(line for k, v in value.items() for line in _render(k, v, ind + "  "))]
    if isinstance(value, dict):
        return [f"{ind}{key}: {{}}\n"]
    if _is_formats(value):
        return [f"{ind}{key}:\n", *_render_items(value, ind)]  # type: ignore[arg-type]
    return [f"{ind}{key}: {_literal(value)}\n"]


def _with_comment(old: str, new: list[str]) -> list[str]:
    """En kommentar på den gamla raden står kvar när fältet är en rad."""
    comment = _comment(old)
    return [new[0].rstrip("\n") + comment + "\n"] if comment and len(new) == 1 else new


def _replace_field(lines: list[str], head: int, key: str, value: object) -> list[str]:
    """Byt fältet på raden head, med samma indrag."""
    end = _end(lines, head)
    if value is DELETE:
        return lines[:head] + lines[end:]
    new = _render(key, value, lines[head][:_indent(lines[head])])
    if end == head + 1:
        new = _with_comment(lines[head], new)
    return lines[:head] + new + lines[end:]


def _added(lines: list[str], key: str, value: object) -> list[str]:
    """Ett nytt fält sist i filen."""
    return lines if value is DELETE else [*lines, "\n", *_render(key, value, "")]


def _one_line(line: str) -> bool:
    """Delen står på en rad, till exempel caption: {a: 1}."""
    after_colon = line.split(":", 1)[1].strip()
    return bool(after_colon) and not after_colon.startswith("#")


def _child_indent(lines: list[str]) -> str:
    children = [line for line in lines if not _skipped(line)]
    return children[0][:_indent(children[0])] if children else "  "


def _set_child(lines: list[str], rows: range, key: str, value: object) -> list[str]:
    """Ändra fältet key bland raderna rows, som är en dels fält."""
    ind = _child_indent([lines[i] for i in rows])
    found = _find(lines, key, rows, len(ind))
    if found is not None:
        return _replace_field(lines, found, key, value)
    return lines if value is DELETE else lines[:rows.stop] + _render(key, value, ind) + lines[rows.stop:]


def _set(lines: list[str], path: str, value: object, expected: dict) -> list[str]:
    top, _, sub = path.partition(".")
    head = _find(lines, top, range(len(lines)), 0)
    if head is None:
        return _added(lines, top, {sub: value} if sub else value)
    if not sub:
        return _replace_field(lines, head, top, value)
    end = _end(lines, head)
    if _one_line(lines[head]):  # en del på en rad, {a: 1}: skriv om hela delen
        return lines[:head] + _render(top, expected.get(top) or {}, "") + lines[end:]
    return _set_child(lines, range(head + 1, end), sub, value)


def _section(expected: dict, top: str) -> dict:
    if expected.get(top) is None:
        expected[top] = {}
    section = expected[top]
    if not isinstance(section, dict):
        raise ValueError(f"{top} måste vara en karta med fält. Ändra den under Avancerat (YAML).")
    return section


def _change(expected: dict, path: str, value: object) -> None:
    """Gör ändringen i expected, profilen som den ska bli."""
    top, _, sub = path.partition(".")
    target, key = (_section(expected, top), sub) if sub else (expected, top)
    if value is DELETE:
        target.pop(key, None)
    else:
        target[key] = value


def _expected(text: str, changes: dict[str, object]) -> dict:
    """Profilen som den ska bli efter ändringarna."""
    before = parse_yaml(text)
    if not isinstance(before, dict):
        raise ValueError("Kampanjens fil måste vara en YAML-karta med fält som name och streamer.")
    expected = copy.deepcopy(before)
    for path, value in changes.items():
        _change(expected, path, value)
    return expected


def _lines(text: str) -> list[str]:
    """Filens rader. Den sista raden får en radbrytning."""
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    return lines


def _parses_to(text: str, expected: dict) -> bool:
    try:
        return parse_yaml(text) == expected
    except ValueError:
        return False


def update_profile(text: str, changes: dict[str, object]) -> str:
    """Byt fälten i changes, till exempel "campaign", "caption.template" och "formats", och låt resten av
    filen vara. Kommentarer i fält som inte ändras finns kvar. DELETE tar bort fältet. ValueError om
    resultatet inte blir exakt profilen med ändringarna."""
    expected = _expected(text, changes)
    lines = _lines(text)
    for path, value in changes.items():
        lines = _set(lines, path, value, expected)
    new = "".join(lines)
    if not _parses_to(new, expected):
        raise ValueError(UNSAVED)
    return new


def set_active(text: str, active: bool) -> str:
    """Byt värdet på raden `active:` överst i profilen, eller lägg till raden sist. Resten av filen är orörd."""
    value = "true" if active else "false"
    new, count = re.subn(r"^active:[ \t]*[^#\r\n]*?(?=[ \t]*(#|\r?$))", f"active: {value}", text, count=1,
                         flags=re.MULTILINE)
    if count:
        return new
    end = "" if not text or text.endswith("\n") else "\n"
    return f"{text}{end}active: {value}   # false = kampanjen döljs i appen, utom under Kampanjer\n"


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
    checked = [check_rect(rect, aspect, f"Facecam-ruta {i}") for i, rect in enumerate(rects, 1)]
    for i, rect in enumerate(checked, 1):
        if rect in checked[:i - 1]:
            raise ValueError(f"Facecam-ruta {i} är samma som Facecam-ruta {checked.index(rect) + 1}. "
                             "Flytta den eller ta bort den.")
    return checked


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


def format_ids(formats: list[dict]) -> list[dict]:
    """Formaten med ett id var. Ett id som finns står kvar; ett nytt görs av namnet."""
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


def _given(body: dict, key: str, default: object) -> object:
    """Värdet i body, eller default när det saknas. En tom lista räknas som given."""
    return default if body.get(key) is None else body[key]


def _new_caption(body: dict, profile_id: str, link: str | None) -> dict:
    """Captionen. Utan Twitch-länk (egna videor) blir standardvärdena tomma."""
    return {
        "must_include": _given(body, "must_include", [link] if link else []),
        "hashtags": _given(body, "hashtags", [f"#{profile_id}", "#twitchclips"] if link else []),
        "game_hashtag": True,
        "template": body.get("caption_template") or (f"{{title}} 👉 {link}" if link else "{title}"),
    }


def _new_extras(body: dict) -> dict:
    """Fälten som bara skrivs när de är ifyllda, och länkarna."""
    data: dict = {}
    if body.get("engagement_min_percent") is not None:
        data["engagement_min_percent"] = [{"views_up_to": None, "min": body["engagement_min_percent"]}]
    if body.get("platforms"):
        data["platforms"] = list(dict.fromkeys(body["platforms"]))
    data["links"] = {"content_rewards": body.get("content_rewards_url") or None,
                     "analytics_form": body.get("analytics_form_url") or None}
    if body.get("scout_ref"):
        data["scout_ref"] = body["scout_ref"]
    return data


def _new_header(streamer: object, local: bool) -> str:
    name = " ".join(str(streamer).split())  # en radbrytning här skulle lämna kommentaren
    return (f"# Kampanj: {name}. Skapad i appen {date.today().isoformat()}.\n"
            + ("# Bara egna videor, ingen Twitch-kanal.\n" if local else "")
            + "# Ändra den under Ställ in i appen eller här. Kontrollera reglerna mot kampanjsidan.\n\n")


def new_profile_text(profile_id: str, body: dict, config_name: str) -> str:
    """Profilen som YAML. Bara kanal (eller id för egna videor) och streamer krävs; resten får standardvärden."""
    local = body.get("source") == "local"
    link = None if local else f"twitch.tv/{profile_id}"
    text = _given(body, "on_screen_text", link or "")
    payment = {k: body[k] for k in ("cpm_usd", "min_views", "max_payout_per_clip_usd") if body.get(k) is not None}
    data = {
        "name": profile_id,
        "streamer": body["streamer"],
        "campaign": body.get("campaign") or body["streamer"],
        "language": body.get("language") or "en",
        "clipsmith_config": config_name,
        "clip": {"min_seconds": body["clip_min_s"], "max_seconds": body["clip_max_s"]},
        "formats": format_ids(body.get("formats") or []),
        "on_screen_text": {"text": text, "required": bool(text), "position": body["text_position"],
                           "font_size": 46, "font_file": None},
        "overlay": {"file": f"overlays/{profile_id}.png", "required": body["overlay_required"],
                    "mode": body["overlay_mode"]},
        "facecam": {"required": body["facecam_required"]},
        "safe_zone": {"top": 0.125, "bottom": 0.20},
        "caption": _new_caption(body, profile_id, link),
        "audio": {"lufs": -14},
        "payment": payment,
        **_new_extras(body),
    }
    return _new_header(body["streamer"], local) + yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


NEW_REFRAME_LOCAL = (
    "reframe:\n"
    "  mode: zoom            # egna videor: mitten av bilden, ingen kamera\n"
)


def new_config_text(template: str, profile_id: str, clip: tuple[int, int], local: bool = False) -> str:
    """En kopia av mallen med kanalen utbytt och utan uppmätta rutor. Övriga kommentarer finns kvar.
    local: bara egna videor, så ingen kanal och ingen kamera."""
    lines = template.splitlines(keepends=True)
    start = 0
    while start < len(lines) and lines[start].startswith("#"):
        start += 1
    run = ("clipsmith process <fil.mp4>" if local else "clipsmith run-vod <VOD-id>")
    text = (f"# clipsmith-inställningar för kampanjen {profile_id}. Skapad i appen {date.today().isoformat()}\n"
            f"# som en kopia av config.timmy.yaml.\n"
            f"# Används så här:  {run} --config config.{profile_id}.yaml\n"
            + "".join(lines[start:]))
    text = _replace_block(text, "channels", "channels: []\n" if local else f"channels:\n  - {profile_id}\n")
    text = _replace_block(text, "reframe", NEW_REFRAME_LOCAL if local else NEW_REFRAME)
    return set_keys(text, "clip", {"min_seconds": str(clip[0]), "max_seconds": str(clip[1])})
