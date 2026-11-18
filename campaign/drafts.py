"""Egna videor: valen, kedjan och utkasten.

En uppladdad video görs om i två steg. Först råklippet out/upload/<stem>.mp4 (bildläget och start och slut),
sedan finish_clip med look, hastighet, overlay, font, rubrik och klistermärke till out/upload/klara/<stem>.mp4.

Ett utkast är en uppladdad video som inte är sparad än, i out/upload/.utkast/<id>/. En bild ur utkastet görs
med samma filter i ett enda ffmpeg-pass. Ett helt klipp (render) görs med samma två steg som när klippet
sparas, och Spara flyttar den filen om valen är desamma. Valen sparas i out/upload/val/<stem>.json, så att
Redigera kan göra om klippet med dem.

Fel ger VersionError med svensk text och HTTP-status.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import BinaryIO

from pydantic import BaseModel, field_validator

from . import versions
from .caption import build_caption
from .cliplog import ClipLog
from .config import Settings
from .finish import ENCODER_ARGS, HEIGHT, WIDTH, _overlay_used, build_filter, finish_clip
from .media import find_font, need, probe, run
from .profile import Profile
from .rules import check_clip
from .versions import VersionError

VIDEO_TYPES = (".mp4", ".mov", ".m4v", ".webm", ".mkv")
UPLOAD = "upload"  # vod_id och mappen under out/
DRAFTS = ".utkast"  # punkt först: inget annat letar där
CHOICES = "val"
MAX_AGE_S = 24 * 3600
DRAFT_ID = re.compile(r"[a-f0-9]{32}")
EXTRA_S = 0.1  # klippet blir så här mycket längre än källan (ljudet)
STICKER_END_S = 0.5  # Redigera: klistermärket börjar senast så här långt före klippets slut
# Fyll hela 9:16-bilden och skär bort det som sticker ut.
FILL = f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},setsar=1"
# Hela bilden, så stor som ryms, på en suddig kopia av sig själv som fyller 9:16. Samma bild som clipsmiths
# läge fit, men en stående video ryms också. Kopian suddas i en fjärdedels storlek: snabbare och ser likadan ut.
FIT = (f"split[bg][fg];[bg]scale={WIDTH // 4}:{HEIGHT // 4}:force_original_aspect_ratio=increase,"
       f"crop={WIDTH // 4}:{HEIGHT // 4},boxblur=10:2,scale={WIDTH}:{HEIGHT}:flags=bilinear[blur];"
       f"[fg]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos[pic];"
       f"[blur][pic]overlay=(W-w)/2:(H-h)/2,setsar=1")
FRAMINGS = {"fill": FILL, "fit": FIT}

_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


class Choices(BaseModel):
    """Valen för en egen video, med samma namn som formuläret. end_s 0 betyder videons slut. start_cut_s är
    det gamla namnet på start_s (0–5 s)."""

    profile: str
    title: str = ""
    mirror: bool = False
    speed: float = 1.0
    filter: str = "none"
    brightness: float = 0.0
    saturation: float = 0.0
    framing: str = "fit"
    font: str = ""
    overlay: str = ""
    sticker: str = ""
    sticker_position: str = "top_right"
    sticker_start_s: float = 0.0
    sticker_duration_s: float = 3.0
    start_s: float = 0.0
    end_s: float = 0.0
    start_cut_s: float = 0.0

    @field_validator("start_s", "end_s", "start_cut_s", mode="before")
    @classmethod
    def _empty_is_zero(cls, value: object) -> object:
        return 0.0 if value in ("", None) else value


class FrameChoices(Choices):
    t: float = 0.0  # sekunder i det färdiga klippet


@dataclass
class Plan:
    """Valen kontrollerade mot videon och kampanjen, klara för ffmpeg."""

    choices: Choices
    campaign: Profile
    settings: Settings
    look: str
    overlay: Path | None
    font_file: str | None
    sticker: dict | None
    start: float  # sekunder i källvideon
    end: float
    trimmed: bool  # start eller slut är valda
    name: str = ""  # filens namn, som syns i listan när rubriken saknas

    @property
    def hook(self) -> str:
        return self.choices.title.strip()  # bara en rubrik Robin skriver bränns in, aldrig filens namn

    @property
    def raw_length(self) -> float:
        return self.end - self.start

    @property
    def length_s(self) -> float:
        """Klippets längd: efter hastigheten, och lite längre än källan."""
        return self.raw_length / self.choices.speed + EXTRA_S

    def saved(self) -> dict:
        """Valen som de sparas och jämförs: start och slut som sekunder i källan."""
        data = self.choices.model_dump(exclude={"start_cut_s"})
        return {**data, "start_s": round(self.start, 3), "end_s": round(self.end, 3)}

    def key(self, *fields: str) -> str:
        data = self.saved()
        picked = {k: data[k] for k in fields} if fields else data
        return hashlib.sha1(json.dumps(picked, sort_keys=True).encode()).hexdigest()[:12]

    @property
    def raw_key(self) -> str:
        return self.key("framing", "start_s", "end_s")  # råklippet beror bara på de här


def comma(value: float, fmt: str) -> str:
    """Tal med komma, som 4,6."""
    return format(value, fmt).replace(".", ",")


def with_advice(problems: list[str]) -> list[str]:
    """En egen video går inte att göra kampanjklar igen: säg vad Robin kan göra i stället."""
    return [f"{p} Ladda upp en längre video eller tryck Nej." if "Kampanjen kräver minst" in p else p for p in problems]


def _span(choices: Choices, duration: float) -> tuple[float, float]:
    if not 0 <= choices.start_cut_s <= versions.MAX_CUT_S:
        raise VersionError(f"Klipp i början får vara 0–{versions.MAX_CUT_S:g} sekunder.")
    start = choices.start_s or choices.start_cut_s
    end = choices.end_s or duration
    if duration < end <= duration + EXTRA_S:  # webben avrundar till tiondelar
        end = duration
    if not 0 <= start < end <= duration:
        raise VersionError(f"Start och slut måste ligga inom videon, 0–{comma(duration, '.1f')} s, "
                           "och starten före slutet.")
    return start, end


def _check_choices(choices: Choices) -> dict[str, str]:
    """Valen som inte beror på videon. Returnerar fonterna som finns."""
    if choices.speed not in versions.SPEEDS:
        raise VersionError(f"Hastigheten {comma(choices.speed, 'g')} går inte. "
                           f"Välj {', '.join(comma(s, 'g') for s in versions.SPEEDS)}.")
    if choices.framing not in FRAMINGS:
        raise VersionError(f"Bildläget {choices.framing!r} finns inte. Välj {', '.join(FRAMINGS)}.")
    found = versions.fonts()
    if choices.font and choices.font not in found:
        raise VersionError(f"Fonten {choices.font} finns inte. Välj en ur listan.")
    return found


def make_plan(settings: Settings, campaign: Profile, choices: Choices, duration: float) -> Plan:
    """Kontrollera valen. Klistermärkets tider gäller råklippet, före hastigheten, som i en ny version."""
    found = _check_choices(choices)
    look = versions.look(choices.mirror, choices.filter, choices.brightness, choices.saturation)
    own = versions._overlay(settings, campaign, choices.overlay or None)  # tomt: kampanjens overlay
    start, end = _span(choices, duration)
    plan = Plan(choices, campaign, settings, look, own, found.get(choices.font), None, start, end,
                trimmed=bool(start or choices.end_s))
    plan.sticker = versions._sticker(settings, campaign, _wanted(choices), plan.raw_length, plan.hook, own)
    return plan


def _wanted(choices: Choices) -> dict | None:
    if not choices.sticker:
        return None
    return {"file": choices.sticker, "position": choices.sticker_position, "start_s": choices.sticker_start_s,
            "duration_s": choices.sticker_duration_s}


def check_length(plan: Plan) -> None:
    """Kampanjens min och max, före rendering."""
    campaign, length = plan.campaign, plan.length_s
    said = f"Klippet blir {comma(max(length, 0), '.1f')} s."
    if length < campaign.min_seconds:
        raise VersionError(f"{said} Kampanjen kräver minst {comma(campaign.min_seconds, 'g')} s. "
                           "Flytta start eller slut på tidslinjen.")
    if length > campaign.max_seconds:
        raise VersionError(f"{said} Kampanjen tillåter högst {comma(campaign.max_seconds, 'g')} s. "
                           "Flytta slutet på tidslinjen.")


def make_raw(source: Path, out: Path, plan: Plan) -> None:
    """Råklippet: bildläget och start och slut."""
    cut = ["-ss", f"{plan.start:.3f}"] if plan.start else []
    end = ["-t", f"{plan.raw_length:.3f}"] if plan.choices.end_s else []
    run([need("ffmpeg"), "-y", "-v", "error", *cut, "-i", str(source), *end, "-vf", FRAMINGS[plan.choices.framing],
         "-map", "0:v:0", "-map", "0:a:0?",
         *ENCODER_ARGS.get(plan.settings.encoder, ENCODER_ARGS["libx264"]), "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", str(out)])


def finish(raw: Path, out: Path, plan: Plan) -> dict:
    return finish_clip(raw, out, plan.campaign, plan.settings.root, plan.settings.encoder, title=plan.hook, look=plan.look,
                       speed=plan.choices.speed, overlay=plan.overlay, font_file=plan.font_file,
                       sticker=plan.sticker)


def frame(path: Path, plan: Plan, t: float) -> bytes:
    """En bild vid tiden t i det färdiga klippet, med samma filter som klippet. Hastigheten läggs på sist i
    klippet (setpts=PTS/speed), så rubriken och klistermärket gäller råtiden r = t × hastighet. Bilden får
    därför tidsstämpeln r, och källan läses från start + r."""
    data = meta(path)
    fps = data["fps"]
    last = max(plan.raw_length - 1 / fps, 0.0)
    r = round(min(max(t, 0.0) * plan.choices.speed, last) * fps) / fps  # en hel bildruta i råklippet
    campaign = plan.campaign
    overlay = _overlay_used(campaign, plan.settings.root, plan.overlay)
    font = plan.font_file or find_font(campaign.text_font_file)
    graph = build_filter(campaign, overlay is not None, False, font, plan.hook, sticker=plan.sticker,
                         look=plan.look, speed=plan.choices.speed)
    head = f"[0:v]{FRAMINGS[plan.choices.framing]},setpts=PTS-STARTPTS+{r:.6f}/TB[framed]"
    graph = head + ";" + graph.replace("[0:v]setsar=1", "[framed]setsar=1", 1)
    inputs = [*(["-i", str(overlay)] if overlay else []), *(["-i", str(plan.sticker["path"])] if plan.sticker else [])]
    seek = max(plan.start + r - 0.001, 0.0)  # strax före, så att avrundning inte ger nästa bildruta
    result = subprocess.run(
        [need("ffmpeg"), "-v", "error", "-ss", f"{seek:.6f}", "-i", str(source(path, data)), *inputs, "-filter_complex", graph,
         "-map", "[vout]", "-frames:v", "1", "-f", "image2pipe", "-c:v", "mjpeg", "-pix_fmt", "yuvj420p",
         "-q:v", "2", "-"], capture_output=True)
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"ffmpeg misslyckades:\n{result.stderr.decode(errors='replace')[-1500:]}")
    return result.stdout


def stem_for(name: str) -> str:
    return f"{re.sub(r'[^A-Za-z0-9_-]+', '_', Path(name).stem).strip('_')[:40] or 'video'}-{uuid.uuid4().hex[:6]}"


def store(log: ClipLog, plan: Plan, done: Path, manifest: dict) -> int:
    """Regelkontrollen, valen i out/upload/val/ och en rad i klipploggen. Returnerar klippets id."""
    campaign = plan.campaign
    caption = build_caption(plan.hook, campaign)  # utan rubrik: bara kampanjens text, aldrig filens namn
    problems, warnings = check_clip(probe(done), manifest, campaign, caption)
    problems = with_advice(problems)
    write_choices(done.parent.parent, done.stem, plan.saved())
    log.project(plan.choices.profile, campaign.streamer)  # kampanjens projekt för egna filer, om det saknas
    return log.add(vod_id=UPLOAD, profile=plan.choices.profile, file=str(done), title=plan.hook or Path(plan.name).stem,
                   caption=caption, status="underkand" if problems else "renderad", problems=problems,
                   warnings=warnings)


def write_choices(folder: Path, stem: str, choices: dict) -> None:
    path = folder / CHOICES / f"{stem}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(choices, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def saved_choices(raw: Path) -> dict | None:
    """Valen för ett uppladdat klipp, eller None för ett klipp utan val-fil."""
    try:
        data = json.loads((raw.parent / CHOICES / f"{raw.stem}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@dataclass(frozen=True)
class EditLook:
    """Valen från uppladdningen, för Redigera. Ett klipp utan val-fil (choices None) redigeras som förut."""

    choices: dict | None
    hook: str = ""  # rubriken i bilden

    @classmethod
    def of(cls, raw: Path) -> EditLook:
        return cls(saved_choices(raw))

    @property
    def speed(self) -> float:
        return float(self.choices["speed"]) if self.choices else 1.0

    def titled(self, old: str, new: str) -> EditLook:
        """Rubriken i bilden. Utan val-fil: klippets rubrik. Annars den Robin skrev vid uppladdningen, tills han
        byter den, aldrig filens namn som står i listan."""
        unchanged = self.choices is not None and new == old
        return replace(self, hook=(self.choices or {}).get("title", "") if unchanged else new)

    def args(self, settings: Settings, campaign: Profile, length: float) -> dict:
        """finish_clip-valen: look, hastighet, egen overlay, font och klistermärket, placerat med klippets nya
        längd (råtid) och rubrik. Klistermärket börjar senast STICKER_END_S före slutet, så att ett kortat klipp
        inte stoppar Redigera. Fel ger ValueError, som redigeraren visar."""
        if not self.choices:
            return {}
        try:
            checked = Choices.model_validate({**self.choices, "title": self.hook, "start_s": 0, "end_s": 0})
            checked.sticker_start_s = min(checked.sticker_start_s, max(length - STICKER_END_S, 0.0))
            found = _check_choices(checked)
            look = versions.look(checked.mirror, checked.filter, checked.brightness, checked.saturation)
            own = versions._overlay(settings, campaign, checked.overlay or None)
            sticker = versions._sticker(settings, campaign, _wanted(checked), length, self.hook, own)
        except (VersionError, ValueError) as error:
            raise ValueError(str(error)) from error
        return {"look": look, "speed": checked.speed, "overlay": own, "font_file": found.get(checked.font),
                "sticker": sticker}

    def remember(self, raw: Path) -> None:
        """Spara rubriken i val-filen, så att nästa redigering har den."""
        if self.choices:
            write_choices(raw.parent, raw.stem, {**self.choices, "title": self.hook})


# ---------- Utkasten ----------

def _root(settings: Settings) -> Path:
    return settings.out_dir / UPLOAD / DRAFTS


def lock(draft_id: str) -> threading.Lock:
    """Ett lås per utkast: render, save och delete på samma utkast väntar på varandra."""
    with _guard:
        return _locks.setdefault(draft_id, threading.Lock())


def folder(settings: Settings, draft_id: str) -> Path:
    """Utkastets mapp. Bara ett id av [a-f0-9], så att ingen sökväg kan skickas in."""
    path = _root(settings) / draft_id
    if not DRAFT_ID.fullmatch(draft_id) or not (path / "meta.json").is_file():
        raise VersionError("Utkastet finns inte. Ladda upp videon igen.", 404)
    with suppress(FileNotFoundError):  # borttaget nyss: felet kommer när meta.json läses
        os.utime(path / "meta.json")  # används nu: _clean_old räknar åldern från meta.json
    return path


def meta(path: Path) -> dict:
    return json.loads((path / "meta.json").read_text(encoding="utf-8"))


def source(path: Path, data: dict) -> Path:
    return path / f"source{data['suffix']}"


def _clean_old(settings: Settings) -> None:
    now = time.time()
    for path in _root(settings).glob("*"):
        stamp = path / "meta.json" if (path / "meta.json").exists() else path
        if path.is_dir() and now - stamp.stat().st_mtime > MAX_AGE_S:
            shutil.rmtree(path, ignore_errors=True)


def create(settings: Settings, file: BinaryIO, name: str) -> dict:
    """Spara videon som ett nytt utkast. Utkast äldre än ett dygn tas bort först."""
    _clean_old(settings)
    draft_id = uuid.uuid4().hex
    path = _root(settings) / draft_id
    path.mkdir(parents=True)
    suffix = Path(name).suffix.lower()
    try:
        with (path / f"source{suffix}").open("wb") as out:
            shutil.copyfileobj(file, out)
        try:
            info = probe(path / f"source{suffix}")
        except RuntimeError as error:
            raise VersionError("Filen går inte att läsa som video.") from error
    except BaseException:
        shutil.rmtree(path, ignore_errors=True)
        raise
    public = {"draft_id": draft_id, "name": name, "duration_s": round(info["duration"], 3),
              "width": info["width"], "height": info["height"], "has_audio": info["has_audio"]}
    (path / "meta.json").write_text(json.dumps({**public, "suffix": suffix, "fps": info["fps"]}), encoding="utf-8")
    return public


def _last(path: Path, plan: Plan) -> dict | None:
    """Senaste render, om den gjordes med samma val och filerna finns kvar."""
    try:
        state = json.loads((path / "render.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    files = (path / f"raw-{plan.raw_key}.mp4", path / "render.mp4")
    same = state.get("key") == plan.key() and all(f.exists() for f in files)
    return state if same else None


def render(path: Path, plan: Plan) -> dict:
    """Hela klippet till render.mp4, med samma två steg som när det sparas. Råklippet återanvänds när bara
    valen i finish_clip ändras. Anroparen håller utkastets lås."""
    data = meta(path)
    raw = path / f"raw-{plan.raw_key}.mp4"
    tmp_raw, tmp_out = path / ".raw.tmp.mp4", path / ".render.tmp.mp4"
    try:
        if not raw.exists():
            make_raw(source(path, data), tmp_raw, plan)
            os.replace(tmp_raw, raw)
        manifest = finish(raw, tmp_out, plan)
        (path / "render.json").unlink(missing_ok=True)
        os.replace(tmp_out, path / "render.mp4")
    finally:
        tmp_raw.unlink(missing_ok=True)
        tmp_out.unlink(missing_ok=True)
    caption = build_caption(plan.hook, plan.campaign)
    problems, warnings = check_clip(probe(path / "render.mp4"), manifest, plan.campaign, caption)
    state = {"key": plan.key(), "raw_key": plan.raw_key, "manifest": manifest,
             "length_s": round(probe(path / "render.mp4")["duration"], 2), "caption": caption,
             "problems": with_advice(problems), "warnings": warnings}
    (path / "render.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    for old in path.glob("raw-*.mp4"):
        if old != raw:
            old.unlink(missing_ok=True)
    return state


def save(log: ClipLog, path: Path, plan: Plan) -> int:
    """Spara utkastet som ett klipp. Samma val som senaste render: filen flyttas, ingen ny rendering. Andra
    val: render först. Utkastet tas bort efteråt. Anroparen håller utkastets lås."""
    state = _last(path, plan) or render(path, plan)
    plan = replace(plan, name=meta(path)["name"])
    stem = stem_for(plan.name)
    folder_out = plan.settings.out_dir / UPLOAD
    done = folder_out / "klara" / f"{stem}.mp4"
    done.parent.mkdir(parents=True, exist_ok=True)
    os.replace(path / f"raw-{plan.raw_key}.mp4", folder_out / f"{stem}.mp4")  # råklippet, som Redigera utgår från
    os.replace(path / "render.mp4", done)
    clip_id = store(log, plan, done, state["manifest"])
    shutil.rmtree(path, ignore_errors=True)
    return clip_id
