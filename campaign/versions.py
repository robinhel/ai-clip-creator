"""Ny version av ett godkänt klipp för ett annat konto: annan font, klipp i början, egen overlay och klistermärke,
spegling, färgfilter, ljusstyrka, mättnad och hastighet.
"""

from __future__ import annotations

import math
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from . import editor
from .caption import PLACEHOLDER
from .cliplog import POST_STATUSES, ClipLog
from .config import Settings
from .finish import HEIGHT, WIDTH, finish_clip, top_text_end
from .media import probe
from .profile import Profile, load_profile
from .profile_edit import overlay_target
from .rules import check_clip
from .schedule import NAMES, Schedule, ScheduleError
from .subtitles import load_words
from .timemap import prepare

# Fonter som finns på Macen och som libass hittar med namnet (fontconfig). Filen är för drawtext.
FONT_DIRS = (Path("/System/Library/Fonts/Supplemental"), Path("/Library/Fonts"))
FONTS = {"Arial": "Arial Bold.ttf", "Arial Black": "Arial Black.ttf", "Impact": "Impact.ttf",
         "Verdana": "Verdana Bold.ttf", "Trebuchet MS": "Trebuchet MS Bold.ttf", "Georgia": "Georgia Bold.ttf",
         "Comic Sans MS": "Comic Sans MS Bold.ttf"}
MAX_CUT_S = 5.0
STICKER_S = (0.5, 10.0)
NO_WORDS = "Ny version kräver ett klipp som appen har skurit med ordfil."

FILTERS: dict[str, str | None] = {
    "none": None,
    "fresh": "eq=brightness=0.04:saturation=1.08:contrast=1.03",
    "warm": "eq=brightness=0.02:saturation=1.10,colorbalance=rs=.05:gs=.02:bs=-.03",
    "cool": "eq=brightness=0.02:saturation=1.05,colorbalance=rs=-.03:bs=.05",
    "punch": "eq=contrast=1.08:saturation=1.15,vignette=PI/5",
}
SPEEDS = (1.0, 1.05, 1.1, 1.15, 1.2)  # atempo klarar 0,5–2,0 utan kedja
BRIGHTNESS = (-0.3, 0.3)
SATURATION = (-0.5, 0.5)

POSITIONS = ("top_left", "top_right", "center", "bottom_left", "bottom_right")
MARGIN = 40  # från sidan
TEXT_GAP = 20  # luft mellan texten uppe och ett klistermärke under den
STICKER_W = 320  # klistermärkets bredd i bilden; höjden följer bilden


class VersionError(Exception):
    """Fel med svensk text och HTTP-status för webben."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def sticker_path(root: Path, file: object) -> Path:
    """En .png i stickers/. Ingen sökväg utanför mappen."""
    folder = (root / "stickers").resolve()
    target = (folder / str(file)).resolve()
    if not (isinstance(file, str) and target.is_relative_to(folder) and target.suffix.lower() == ".png"
            and target.is_file()):
        raise VersionError(f"Klistermärket {file} finns inte. Välj en .png i stickers/.")
    return target


def sticker_box(position: str, width: int, height: int, profile: Profile, text_end: int = 0) -> tuple[int, int]:
    """Var klistermärket hamnar: precis utanför kampanjens säkra zoner, MARGIN från sidan. Uppe hamnar det under
    texten uppe (text_end, från finish.top_text_end), så att rubriken syns. Ryms det inte blir det 400."""
    if position not in POSITIONS:
        raise VersionError(f"Platsen {position!r} finns inte. Välj {', '.join(POSITIONS)}.")
    top, bottom = math.ceil(HEIGHT * profile.safe_top), math.floor(HEIGHT * (1 - profile.safe_bottom))
    first = max(top, text_end + TEXT_GAP) if text_end else top
    x = {"left": MARGIN, "right": WIDTH - width - MARGIN}.get(position.split("_")[-1], (WIDTH - width) // 2)
    y = {"top": first, "bottom": bottom - height}.get(position.split("_")[0], (HEIGHT - height) // 2)
    if y < top or y + height > bottom:
        raise VersionError("Klistermärket går in i kampanjens säkra zon, där plattformens knappar och text ligger. "
                           "Välj ett mindre klistermärke eller en annan plats.")
    if y < text_end:
        raise VersionError("Klistermärket täcker rubriken eller kampanjens text uppe. "
                           "Välj ett mindre klistermärke eller en annan plats.")
    return x, y


def look(mirror: bool = False, filter: str = "none", brightness: float = 0.0, saturation: float = 0.0) -> str:
    """Filtren före undertexten, med komma först (eller tomt). Kastar VersionError för ett okänt val."""
    if filter not in FILTERS:
        raise VersionError(f"Filtret {filter!r} finns inte. Välj {', '.join(FILTERS)}.")
    if not BRIGHTNESS[0] <= brightness <= BRIGHTNESS[1]:
        raise VersionError("Ljusstyrkan får vara -0,3–0,3.")
    if not SATURATION[0] <= saturation <= SATURATION[1]:
        raise VersionError("Mättnaden får vara -0,5–0,5.")
    parts = ["hflip"] if mirror else []
    if preset := FILTERS[filter]:
        parts.append(preset)
    if brightness or saturation:  # en andra eq ovanpå filtret
        parts.append(f"eq=brightness={brightness:.3f}:saturation={1 + saturation:.3f}")
    return "".join("," + part for part in parts)


def fonts() -> dict[str, str]:
    """Namn -> fontfil för fonterna som finns på datorn."""
    found = {}
    for name, file in FONTS.items():
        path = next((folder / file for folder in FONT_DIRS if (folder / file).is_file()), None)
        if path:
            found[name] = str(path)
    return found


def _files(folder: Path, url: str, prefix: str = "") -> list[dict]:
    return [{"file": f"{prefix}{p.name}", "url": f"{url}/{quote(p.name)}"}
            for p in sorted(folder.glob("*.png")) if p.is_file()]


def _ready(settings: Settings, log: ClipLog, clip: dict, platform: str, account: str) -> dict:
    """Klippet (originalet, om det är en version) och allt som behövs, efter kontraktets regler."""
    root = log.get(clip["version_of"]) if clip.get("version_of") else clip
    if root["status"] not in ("godkand", *POST_STATUSES) or root["problems"]:
        raise VersionError("Bara godkända klipp utan problem kan få en ny version.")
    raw = editor.raw_path(root, settings)
    data = load_words(raw, editor.source_path(raw)) if raw is not None and raw.is_file() else None
    if raw is None or data is None:
        raise VersionError(NO_WORDS)
    try:
        profile = load_profile(root["profile"], settings.profiles_dir)
    except SystemExit as error:
        raise VersionError("Kampanjen går inte att läsa.") from error
    key, name = NAMES[platform]
    if not profile.multi(key):
        raise VersionError(f"Kampanjen har bara ett konto på {name}. Ny version behövs inte.")
    schedule = Schedule(settings)
    try:
        schedule.check_account(root, platform, account, datetime.now(UTC), counts=False)
    except ScheduleError as error:
        raise VersionError(str(error), error.status) from error
    if account in schedule._uses(root["id"], platform):
        raise VersionError("Kontot har redan klippet eller en version av det.", 409)
    state = editor._state(root, raw)  # originalets trimning: råklippet börjar vid in_s
    return {"root": root, "profile": profile, "raw": raw, "data": data, "in_s": state["in_s"],
            "out_s": state["out_s"]}


def _style(settings: Settings, profile: Profile | None, platform: str, account: str) -> dict:
    """Kontots fasta stil ur profilen, med id, användarnamn eller handle som nyckel."""
    if not profile or not profile.accounts_style:
        return {}
    names = Schedule(settings)._names(platform, account) | {account.lower()}
    return next((style for key, style in profile.accounts_style.items() if key.lstrip("@").lower() in names), {})


def options(settings: Settings, log: ClipLog, clip: dict, platform: str, account: str) -> dict:
    try:
        ready: dict | None = _ready(settings, log, clip, platform, account)
        reason = None
    except VersionError as error:
        ready, reason = None, str(error)
    try:
        profile: Profile | None = ready["profile"] if ready else load_profile(clip["profile"], settings.profiles_dir)
    except SystemExit:
        profile = None
    style = _style(settings, profile, platform, account)
    length = ready["out_s"] - ready["in_s"] if ready else 0.0
    min_s = profile.min_seconds if profile else 0.0
    return {"allowed": ready is not None, "reason": reason,
            "fonts": [{"name": name, "label": name} for name in fonts()],
            "stickers": _files(settings.root / "stickers", "/campaign/stickers"),
            "overlays": _files(settings.root / "overlays", "/campaign/overlays", "overlays/"),
            "overlay_locked": bool(profile and profile.overlay_required),
            "max_start_cut_s": round(max(0.0, min(MAX_CUT_S, length - min_s)), 2),
            "speeds": list(SPEEDS), "filters": list(FILTERS),
            "defaults": {"font": style.get("font") or (ready["data"]["caption"].get("font") if ready else None),
                         "overlay": style.get("overlay"), "sticker": style.get("sticker"),
                         "start_cut_s": float(style.get("start_cut_s") or 0.0)}}


def _overlay(settings: Settings, profile: Profile, overlay: str | None) -> Path | None:
    """Versionens overlay. None: kampanjens (eller ingen). Kräver kampanjen sin overlay gäller den."""
    if overlay is None:
        return None
    if profile.overlay_required and overlay != profile.overlay_file:
        raise VersionError("Kampanjen kräver sin egen overlay. Välj ingen annan.")
    try:
        path = overlay_target(settings.root, overlay)
    except ValueError as error:
        raise VersionError(str(error)) from error
    if not path.is_file():
        raise VersionError(f"Overlayen {overlay} finns inte.")
    return path


def _sticker(settings: Settings, profile: Profile, sticker: dict | None, length: float, title: str = "",
             overlay: Path | None = None, frame: str = "") -> dict | None:
    """Klistermärket kontrollerat och placerat. title, overlay och frame (bildläget) är klippets: texten uppe
    ska synas."""
    if sticker is None:
        return None
    path = sticker_path(settings.root, sticker.get("file"))
    try:
        start, duration = float(sticker.get("start_s") or 0), float(sticker.get("duration_s") or 0)
    except (TypeError, ValueError) as error:
        raise VersionError("Klistermärkets tider måste vara tal.") from error
    if not 0 <= start < length:
        raise VersionError(f"Klistermärket måste börja inom klippet, mellan 0 och {length:.1f} s.")
    if not STICKER_S[0] <= duration <= STICKER_S[1]:
        raise VersionError("Klistermärket får synas 0,5–10 sekunder.")
    try:
        info = probe(path)
    except RuntimeError as error:
        raise VersionError(f"Klistermärket {path.name} går inte att läsa.") from error
    height = round(STICKER_W * info["height"] / info["width"])
    text_end = top_text_end(profile, settings.root, title, overlay, frame)
    x, y = sticker_box(str(sticker.get("position")), STICKER_W, height, profile, text_end)
    return {"path": path, "w": STICKER_W, "h": height, "x": x, "y": y, "start_s": start, "end_s": start + duration}


def make(settings: Settings, log: ClipLog, clip: dict, platform: str, account: str, font: str | None = None,
         start_cut_s: float = 0.0, overlay: str | None = None, sticker: dict | None = None, mirror: bool = False,
         speed: float = 1.0, filter: str = "none", brightness: float = 0.0, saturation: float = 0.0) -> int:
    """Rendera en ny version och spara den som ett eget klipp med version_of. Anroparen håller editor.lock.
    Råklippet, originalets klara fil och ordfilen ändras inte."""
    ready = _ready(settings, log, clip, platform, account)
    root, profile, raw, data = ready["root"], ready["profile"], ready["raw"], ready["data"]
    found = fonts()
    if font is not None and font not in found:
        raise VersionError(f"Fonten {font} finns inte. Välj en ur listan.")
    if not 0 <= start_cut_s <= MAX_CUT_S:
        raise VersionError(f"Klipp i början får vara 0–{MAX_CUT_S:g} sekunder.")
    if speed not in SPEEDS:
        raise VersionError(f"Hastigheten {speed:g} går inte. Välj {', '.join(f'{s:g}' for s in SPEEDS)}.")
    filters = look(mirror, filter, brightness, saturation)
    length = ready["out_s"] - ready["in_s"] - start_cut_s
    if length / speed < profile.min_seconds:
        raise VersionError(f"Versionen blir {length / speed:.1f} s. Kampanjen kräver minst {profile.min_seconds:g} s.")
    own = _overlay(settings, profile, overlay)
    title = root["title"] or ""
    hook = "" if PLACEHOLDER.search(title) else title
    placed = _sticker(settings, profile, sticker, length, hook, own, data["reframe"]["mode"])

    done = raw.parent / "klara" / raw.name
    number = 1
    while (out := done.with_name(f"{raw.stem}.v{number}.mp4")).exists():
        number += 1
    tmp_src, tmp_out = editor._tmp(raw, "version"), editor._tmp(out, "render")
    ass = raw.with_name(f".{raw.stem}.version.ass")  # punkt först, som _tmp
    try:
        src = raw
        if start_cut_s > 0:  # råklippet börjar vid in_s; versionen börjar start_cut_s senare
            editor._trim(raw, tmp_src, start_cut_s, probe(raw)["duration"], settings.encoder)
            src = tmp_src
        caption = {**data["caption"], **({"font": font} if font else {})}  # bara versionens undertext
        plan = prepare(src, {**data, "caption": caption}, ready["in_s"] + start_cut_s, ready["out_s"], profile, ass)
        manifest = finish_clip(src, tmp_out, profile, settings.root, settings.encoder,
                               title=hook, subtitles=ass, plan=plan,
                               overlay=own, font_file=found.get(font or ""), sticker=placed, look=filters,
                               speed=speed, frame=data["reframe"]["mode"])
        os.replace(tmp_out, out)
    finally:
        for path in (tmp_src, tmp_out, ass):
            path.unlink(missing_ok=True)
    out.with_suffix(".txt").write_text(root["caption"] + "\n", encoding="utf-8")
    problems, warnings = check_clip(probe(out), manifest, profile, root["caption"], root["format"], root["review"])
    return log.add(
        vod_id=root["vod_id"], profile=root["profile"], file=str(out), title=title, caption=root["caption"],
        status="underkand" if problems else "renderad",
        start_s=None if root["start_s"] is None else round(root["start_s"] + start_cut_s, 3), end_s=root["end_s"],
        clip_format=root["format"], signals=root["signals"], problems=problems, warnings=warnings,
        review=root["review"], score=root["score"], reason=root["reason"] or "", subscores=root["subscores"],
        version_of=root["id"])
