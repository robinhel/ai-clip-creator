"""Ny version av ett godkänt klipp för ett annat konto: ny hook, ny startpunkt (tidigare eller senare), annan font,
egen overlay och klistermärke, spegling, färgfilter, ljusstyrka, mättnad och hastighet.

Eget bidrag är en ny hook eller en startpunkt flyttad minst NEW_START_S. Utan något av dem får versionen en varning:
bara spegling och hastighet kan ge strike på YouTube och stoppa klippet från För dig på TikTok.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from urllib.parse import quote

from . import editor
from .caption import headline_text
from .cliplog import POST_STATUSES, ClipLog
from .config import Settings
from .finish import HEIGHT, WIDTH, finish_clip, hook_clean, hook_fits, top_text_end
from .media import probe
from .profile import Profile, load_profile
from .profile_edit import overlay_target
from .rules import check_clip
from .schedule import NAMES, Schedule, ScheduleError, _norm
from .subtitles import load_words
from .timemap import prepare

# Fonter som finns på Macen och som libass hittar med namnet (fontconfig). Filen är för drawtext.
FONT_DIRS = (Path("/System/Library/Fonts/Supplemental"), Path("/Library/Fonts"))
FONTS = {"Arial": "Arial Bold.ttf", "Arial Black": "Arial Black.ttf", "Impact": "Impact.ttf",
         "Verdana": "Verdana Bold.ttf", "Trebuchet MS": "Trebuchet MS Bold.ttf", "Georgia": "Georgia Bold.ttf",
         "Comic Sans MS": "Comic Sans MS Bold.ttf"}
MAX_CUT_S = 5.0  # uploads_api.py använder den; make() har ingen spärr på 5 s längre
NEW_START_S = 3.0  # så långt flyttad startpunkt, tidigare eller senare, är ett eget bidrag
STICKER_S = (0.5, 10.0)
NO_WORDS = "A new version needs a clip that the app cut with a word file."
HARD_SPACES = str.maketrans({"\xa0": " ", "\u202f": " "})
HOOK_CONTROL = "The hook cannot have line breaks or other control characters."
HOOK_TOO_LONG = "The hook does not fit in the frame. Make it shorter."
NO_OWN = ("The version has no new hook and no new start point. Only mirroring, speed, filter or font can get a "
          "strike on YouTube and keep the clip out of For You on TikTok.")
NO_ACCOUNT = ("The clip has a post without an account on {}. It may already be on this account. Check before "
              "you post.")

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
        raise VersionError(f"The sticker {file} does not exist. Choose a .png in stickers/.")
    return target


def sticker_box(position: str, width: int, height: int, profile: Profile, text_end: int = 0) -> tuple[int, int]:
    """Var klistermärket hamnar: precis utanför kampanjens säkra zoner, MARGIN från sidan. Uppe hamnar det under
    texten uppe (text_end, från finish.top_text_end), så att rubriken syns. Ryms det inte blir det 400."""
    if position not in POSITIONS:
        raise VersionError(f"The position {position!r} does not exist. Choose {', '.join(POSITIONS)}.")
    top, bottom = math.ceil(HEIGHT * profile.safe_top), math.floor(HEIGHT * (1 - profile.safe_bottom))
    first = max(top, text_end + TEXT_GAP) if text_end else top
    x = {"left": MARGIN, "right": WIDTH - width - MARGIN}.get(position.split("_")[-1], (WIDTH - width) // 2)
    y = {"top": first, "bottom": bottom - height}.get(position.split("_")[0], (HEIGHT - height) // 2)
    if y < top or y + height > bottom:
        raise VersionError("The sticker goes into the campaign's safe zone, where the platform's buttons and text are. "
                           "Choose a smaller sticker or another position.")
    if y < text_end:
        raise VersionError("The sticker covers the headline or the campaign text at the top. "
                           "Choose a smaller sticker or another position.")
    return x, y


def look(mirror: bool = False, filter: str = "none", brightness: float = 0.0, saturation: float = 0.0) -> str:
    """Filtren före undertexten, med komma först (eller tomt). Kastar VersionError för ett okänt val."""
    if filter not in FILTERS:
        raise VersionError(f"The filter {filter!r} does not exist. Choose {', '.join(FILTERS)}.")
    if not BRIGHTNESS[0] <= brightness <= BRIGHTNESS[1]:
        raise VersionError("Brightness can be -0.3–0.3.")
    if not SATURATION[0] <= saturation <= SATURATION[1]:
        raise VersionError("Saturation can be -0.5–0.5.")
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


def plain_spaces(text: str) -> str:
    """Hårt blanksteg (U+00A0) och smalt hårt blanksteg (U+202F) blir vanliga, så att hook_problem godtar dem."""
    return text.translate(HARD_SPACES)


def hook_problem(hook: str, fits: Callable[[str], bool]) -> str | None:
    """Varför hooken inte går att använda, eller None. fits: finish.hook_fits med klippets profil, overlay,
    bildläge och font, så att samma radbrytning som i bilden gäller, men utan att korta: det som inte ryms utan
    "…" stoppas."""
    if not hook.isprintable():
        return HOOK_CONTROL
    if not fits(hook):
        return HOOK_TOO_LONG
    return None


def hooks_used(log: ClipLog, root: dict) -> list[str]:
    """Texten i bilden (caption.headline_text) på originalet och alla dess versioner, utan platshållare och
    dubbletter."""
    titles = (headline_text(log.get(clip_id)) for clip_id in log.family(root["id"]))
    return list(dict.fromkeys(title for title in titles if title))


def _files(folder: Path, url: str, prefix: str = "") -> list[dict]:
    return [{"file": f"{prefix}{p.name}", "url": f"{url}/{quote(p.name)}"}
            for p in sorted(folder.glob("*.png")) if p.is_file()]


def _ready(settings: Settings, log: ClipLog, clip: dict, platform: str, account: str) -> dict:
    """Klippet (originalet, om det är en version) och allt som behövs, efter kontraktets regler."""
    root = log.get(clip["version_of"]) if clip.get("version_of") else clip
    if root["status"] not in ("godkand", *POST_STATUSES) or root["problems"]:
        raise VersionError("Only approved clips without problems can get a new version.")
    raw = editor.raw_path(root, settings)
    data = load_words(raw, editor.source_path(raw)) if raw is not None and raw.is_file() else None
    if raw is None or data is None:
        raise VersionError(NO_WORDS)
    try:
        profile = load_profile(root["profile"], settings.profiles_dir)
    except SystemExit as error:
        raise VersionError("The campaign cannot be read.") from error
    key, name = NAMES[platform]
    if not profile.multi(key):
        raise VersionError(f"The campaign has only one account on {name}. A new version is not needed.")
    schedule = Schedule(settings)
    try:
        schedule.check_account(root, platform, account, datetime.now(UTC), counts=False)
    except ScheduleError as error:
        raise VersionError(str(error), error.status) from error
    uses = schedule._uses(root["id"], platform)
    if account in uses:
        raise VersionError("The account already has the clip or a version of it.", 409)
    state = editor._state(root, raw)  # originalets trimning: råklippet börjar vid in_s
    return {"root": root, "profile": profile, "raw": raw, "data": data, "in_s": state["in_s"],
            "out_s": state["out_s"], "account_warning": NO_ACCOUNT.format(name) if None in uses else None}


def _style(settings: Settings, profile: Profile | None, platform: str, account: str) -> dict:
    """Kontots fasta stil ur profilen, med id, användarnamn eller handle som nyckel."""
    if not profile or not profile.accounts_style:
        return {}
    names = Schedule(settings)._names(platform, account) | {account.lower()}
    return next((style for key, style in profile.accounts_style.items() if key.lstrip("@").lower() in names), {})


def _own_options(log: ClipLog, ready: dict | None) -> dict:
    """Valen för eget bidrag: startpunkten (negativ är tidigare än originalet) och hookarna. Tomma när versionen
    inte går att göra."""
    if ready is None:
        return {"min_start_cut_s": 0.0, "max_start_cut_s": 0.0, "new_start_s": NEW_START_S, "hook": "",
                "hooks_used": [], "original_url": None, "in_s": 0.0}
    root, profile = ready["root"], ready["profile"]
    length = ready["out_s"] - ready["in_s"]
    earliest = min(0.0, max(-ready["in_s"], length - profile.max_seconds))
    latest = max(0.0, length - profile.min_seconds)
    # Hundradelar inåt: trimningen kan ha fler decimaler, och gränsen ska alltid gå att göra.
    return {"min_start_cut_s": math.ceil(earliest * 100 - 1e-6) / 100,
            "max_start_cut_s": math.floor(latest * 100 + 1e-6) / 100, "new_start_s": NEW_START_S,
            "hook": headline_text(root), "hooks_used": hooks_used(log, root),
            "original_url": f"/campaign/clips/{root['id']}/original", "in_s": ready["in_s"]}


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
    return {"allowed": ready is not None, "reason": reason,
            "account_warning": ready["account_warning"] if ready else None,
            "fonts": [{"name": name, "label": name} for name in fonts()],
            "stickers": _files(settings.root / "stickers", "/campaign/stickers"),
            "overlays": _files(settings.root / "overlays", "/campaign/overlays", "overlays/"),
            "overlay_locked": bool(profile and profile.overlay_required),
            **_own_options(log, ready), "hook_text": bool(profile and profile.hook_text),
            "speeds": list(SPEEDS), "filters": list(FILTERS),
            "defaults": {"font": style.get("font") or (ready["data"]["caption"].get("font") if ready else None),
                         "overlay": style.get("overlay"), "sticker": style.get("sticker"),
                         "start_cut_s": float(style.get("start_cut_s") or 0.0)}}


def _overlay(settings: Settings, profile: Profile, overlay: str | None) -> Path | None:
    """Versionens overlay. None: kampanjens (eller ingen). Kräver kampanjen sin overlay gäller den."""
    if overlay is None:
        return None
    if profile.overlay_required and overlay != profile.overlay_file:
        raise VersionError("The campaign requires its own overlay. Do not choose another.")
    try:
        path = overlay_target(settings.root, overlay)
    except ValueError as error:
        raise VersionError(str(error)) from error
    if not path.is_file():
        raise VersionError(f"The overlay {overlay} does not exist.")
    return path


def _sticker(settings: Settings, profile: Profile, sticker: dict | None, length: float, title: str = "",
             overlay: Path | None = None, frame: str = "", font_file: str | None = None) -> dict | None:
    """Klistermärket kontrollerat och placerat. title, overlay, frame (bildläget) och font_file är klippets:
    texten uppe ska synas."""
    if sticker is None:
        return None
    path = sticker_path(settings.root, sticker.get("file"))
    try:
        start, duration = float(sticker.get("start_s") or 0), float(sticker.get("duration_s") or 0)
    except (TypeError, ValueError) as error:
        raise VersionError("The sticker times must be numbers.") from error
    if not 0 <= start < length:
        shown = format(length, ".1f")
        raise VersionError(f"The sticker must start within the clip, between 0 and {shown} s.")
    if not STICKER_S[0] <= duration <= STICKER_S[1]:
        raise VersionError("The sticker can show for 0.5–10 seconds.")
    try:
        info = probe(path)
    except RuntimeError as error:
        raise VersionError(f"The sticker {path.name} cannot be read.") from error
    height = round(STICKER_W * info["height"] / info["width"])
    text_end = top_text_end(profile, settings.root, title, overlay, frame, font_file)
    x, y = sticker_box(str(sticker.get("position")), STICKER_W, height, profile, text_end)
    return {"path": path, "w": STICKER_W, "h": height, "x": x, "y": y, "start_s": start, "end_s": start + duration}


def _start(ready: dict, start_cut_s: float, speed: float) -> tuple[float, float]:
    """Versionens start i originalfilen och längd före hastigheten. start_cut_s är startpunkten räknad från
    originalets start: negativ är tidigare, positiv senare. Slutet (out_s) ligger kvar. En start före
    originalfilen, för kort efter hastigheten, eller för lång med en tidigare start, ger 400. Utan tidigare start
    bedömer regelkontrollen längden, som förut. Längden avrundas, så att gränserna i options alltid går."""
    profile = ready["profile"]
    if start_cut_s < -ready["in_s"] - editor.ROUNDING_S:
        raise VersionError(f"The start point is before the beginning of the original file. It can move at most "
                           f"{ready['in_s']:.1f} s earlier.")
    start = max(0.0, ready["in_s"] + start_cut_s)
    seconds = round((ready["out_s"] - start) / speed, 3)
    if seconds < profile.min_seconds:
        raise VersionError(f"The version will be {max(0.0, seconds):.1f} s. The campaign requires at least "
                           f"{profile.min_seconds:g} s.")
    if start < ready["in_s"] and seconds > profile.max_seconds:
        raise VersionError(f"The version will be {seconds:.1f} s. The campaign allows at most {profile.max_seconds:g} s.")
    return start, ready["out_s"] - start


def _hook(hook: str | None, fits: Callable[[str], bool]) -> str | None:
    """Versionens hook utan blanksteg runt, eller None för originalets (också när den är tom). Ett hårt blanksteg
    blir ett vanligt. Fel hook, en som inte ryms i versionens bild (fits, som hook_problem) eller en som blir tom i
    bilden (hook_clean) ger 400."""
    hook = plain_spaces(hook or "").strip()
    if not hook:
        return None
    if problem := hook_problem(hook, fits):
        raise VersionError(problem)
    if not hook_clean(hook):  # bara emoji eller tecken utan glyf: inget kvar i bilden
        raise VersionError("The hook must have letters or digits. Emoji do not show in the frame.")
    return hook


def _new_hook(log: ClipLog, ready: dict, shown: str) -> bool:
    """Hooken i bilden är ny: den finns inte på originalet eller en version (_norm, som schemat). Visar kampanjen
    ingen hook räknas den inte."""
    if not (ready["profile"].hook_text and shown):
        return False
    return _norm(shown) not in {_norm(hook) for hook in hooks_used(log, ready["root"])}


def _own_warnings(log: ClipLog, ready: dict, shown: str, new_start: bool) -> list[str]:
    """Versionens egna varningar: inget eget bidrag (ingen ny startpunkt och ingen ny hook), och ett inlägg utan
    konto på plattformen (_ready)."""
    found = [] if new_start or _new_hook(log, ready, shown) else [NO_OWN]
    return [*found, ready["account_warning"]] if ready["account_warning"] else found


def _next_file(raw: Path) -> Path:
    """Nästa lediga clip_NN.vN.mp4 i klara/."""
    done = raw.parent / "klara" / raw.name
    number = 1
    while (out := done.with_name(f"{raw.stem}.v{number}.mp4")).exists():
        number += 1
    return out


def make(settings: Settings, log: ClipLog, clip: dict, platform: str, account: str, font: str | None = None,
         start_cut_s: float = 0.0, overlay: str | None = None, sticker: dict | None = None, mirror: bool = False,
         speed: float = 1.0, filter: str = "none", brightness: float = 0.0, saturation: float = 0.0,
         hook: str | None = None) -> int:
    """Rendera en ny version och spara den som ett eget klipp med version_of. Anroparen håller editor.lock.
    Råklippet, originalets klara fil och ordfilen ändras inte. hook None: originalets hook, rubrik och caption.
    En versions headline är texten som faktiskt visas i bilden, också när den är originalets titel. I ett vanligt
    klipp är headline en egen text, där "" betyder titeln."""
    ready = _ready(settings, log, clip, platform, account)
    root, profile, raw, data = ready["root"], ready["profile"], ready["raw"], ready["data"]
    found = fonts()
    if font is not None and font not in found:
        raise VersionError(f"The font {font} does not exist. Choose one from the list.")
    if speed not in SPEEDS:
        raise VersionError(f"The speed {speed:g} is not possible. Choose {', '.join(f'{s:g}' for s in SPEEDS)}.")
    filters = look(mirror, filter, brightness, saturation)
    start, length = _start(ready, start_cut_s, speed)
    own = _overlay(settings, profile, overlay)
    font_file = found.get(font or "")
    hook = _hook(hook, partial(hook_fits, profile, settings.root, overlay=own, frame=data["reframe"]["mode"],
                               font_file=font_file))
    title = (root["title"] or "") if hook is None else hook
    shown = headline_text({"headline": hook} if hook else root)  # texten i bilden, sparas som headline
    placed = _sticker(settings, profile, sticker, length, shown, own, data["reframe"]["mode"], font_file)
    caption = root["caption"] if hook is None else editor._caption(root, hook, profile, editor._game(log, root))
    new_start = abs(start - ready["in_s"]) >= NEW_START_S - 1e-6  # verklig flytt, efter klämningen i _start
    style = {**data["caption"], **({"font": font} if font else {})}  # bara versionens undertext
    # Ny startpunkt: ordfilen utan hook, så att ingen cold open görs. Versionen ska börja där Robin valde.
    words = {**data, "caption": style, **({"hook": None} if new_start else {})}

    out = _next_file(raw)
    tmp_src, tmp_out = editor._tmp(raw, "version"), editor._tmp(out, "render")
    ass = raw.with_name(f".{raw.stem}.version.ass")  # punkt först, som _tmp
    try:
        src = raw
        if start_cut_s:  # klipp ur originalfilen, så att en tidigare start också går
            editor._trim(editor.source_path(raw), tmp_src, start, ready["out_s"], settings.encoder)
            src = tmp_src
        plan = prepare(src, words, start, ready["out_s"], profile, ass)
        manifest = finish_clip(src, tmp_out, profile, settings.root, settings.encoder,
                               title=shown, subtitles=ass, plan=plan,
                               overlay=own, font_file=font_file, sticker=placed, look=filters,
                               speed=speed, frame=data["reframe"]["mode"])
        os.replace(tmp_out, out)
    finally:
        for path in (tmp_src, tmp_out, ass):
            path.unlink(missing_ok=True)
    out.with_suffix(".txt").write_text(caption + "\n", encoding="utf-8")
    problems, warnings = check_clip(probe(out), manifest, profile, caption, root["format"], root["review"])
    return log.add(
        vod_id=root["vod_id"], profile=root["profile"], file=str(out), title=title, caption=caption, headline=shown,
        status="underkand" if problems else "renderad",
        start_s=None if root["start_s"] is None else round(root["start_s"] + start - ready["in_s"], 3),
        end_s=root["end_s"], clip_format=root["format"], signals=root["signals"], problems=problems,
        warnings=[*warnings, *_own_warnings(log, ready, shown, new_start)],
        review=root["review"], score=root["score"], reason=root["reason"] or "", subscores=root["subscores"],
        version_of=root["id"])
