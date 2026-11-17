"""Enkel redigerare: trimma ett klipp och byt rubrik.

Klippet trimmas ur clipsmiths råklipp out/<vod>/clip_NN.mp4 (före overlay och text). Första gången
sparas råklippet som out/<vod>/original/clip_NN.mp4, med trimningen i original/clip_NN.json, så att
varje ny trimning utgår från originalet. clipsmith tömmer inte out/<vod>/ vid en ny körning, så json-filen
har också råklippets storlek och ändringstid: stämmer de inte är råklippet nytt och originalet gäller inte. Sedan görs klippet kampanjklart igen med finish_clip och
regelkontrollen körs med den sparade bildgranskningen.

Fel ger ValueError med svensk text.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
from pathlib import Path

from clipsmith.config.loaders import load_config
from clipsmith.rendering.clipper import _build_ffmpeg_cmd
from clipsmith.settings import CaptionConfig, ReframeConfig

from .caption import build_caption
from .cliplog import ClipLog
from .config import Settings
from .finish import ENCODER_ARGS, finish_clip
from .media import need, probe, run
from .profile import Profile
from .rules import check_clip
from .subtitles import WORD_CHARS, load_words, save_words, words_path
from .timemap import prepare

lock = threading.Lock()  # en redigering åt gången
editing: int | None = None  # klippet som redigeras just nu; andra klipp går att ändra under tiden
LOCKED = ("postad", "inskickad", "analytics_inskickad", "betald")
ROUNDING_S = 0.05  # webben avrundar till tiondelar
WORDS_MAX = 1000
CONTROL = re.compile(r"[\x00-\x1f\x7f]")  # radbrytning, tabb m.m. skulle bryta raden i undertextfilen
BURNED = ("Undertexten är inbränd i det här klippet, så ord går inte att rätta. Det fungerar för klipp som skärs "
          "från och med nu.")
NO_SUBTITLES = "Kampanjen har inga undertexter."
STALE = "Ordlistan hör inte till klippet längre, så ord går inte att rätta."


class EditConflict(Exception):
    """Klippet ändrades under redigeringen, till exempel postades. Ger 409."""


def raw_path(clip: dict, settings: Settings) -> Path | None:
    """Råklippet. Det färdiga klippet ligger i klara/, råklippet med samma namn en mapp upp."""
    done = Path(clip["file"] or "").resolve()
    raw = done.parent.parent / done.name if done.parent.name == "klara" else done
    if not raw.is_relative_to(settings.out_dir.resolve()) or raw.suffix != ".mp4":
        return None
    return raw


def _original(raw: Path) -> tuple[Path, Path]:
    folder = raw.parent / "original"
    return folder / raw.name, folder / f"{raw.stem}.json"


def edit_meta(raw: Path) -> dict | None:
    """Trimningen i original/clip_NN.json, om den hör till råklippet som ligger där nu."""
    original, meta_path = _original(raw)
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        stat = raw.stat()
    except (OSError, ValueError):
        return None
    same = (isinstance(meta, dict) and meta.get("raw_size") == stat.st_size
            and meta.get("raw_mtime_ns") == stat.st_mtime_ns)
    return meta if same and original.is_file() else None


def source_path(raw: Path) -> Path:
    """Originalet om klippet har trimmats, annars råklippet."""
    return _original(raw)[0] if edit_meta(raw) else raw


def limits(profile: Profile) -> tuple[float, float]:
    """Profilens längd, samma gränser som regelkontrollen (check_clip) använder."""
    return profile.min_seconds, profile.max_seconds


def why_not(clip: dict, raw: Path | None) -> str | None:
    if clip.get("version_of"):  # versionen delar råklipp med originalet
        return "En version går inte att redigera. Gör en ny version av originalet i stället."
    if clip["status"] in LOCKED:
        return "Klippet är postat. Det går inte att ändra."
    if clip["status"] == "nekad":
        return "Klippet är nekat. Ångra nejet först om du vill ändra det."
    if raw is None or not raw.is_file():
        return "Råklippet saknas, så klippet går inte att trimma."
    return None


def _state(clip: dict, raw: Path) -> dict:
    """Originalets längd och början i VOD:en, och nuvarande trimning. Skriver inget."""
    duration = round(probe(source_path(raw))["duration"], 2)
    meta = edit_meta(raw) or {}
    start = meta.get("start_s", clip["start_s"])
    return {"duration_s": duration, "start_s": start, "in_s": meta.get("in_s", 0.0),
            "out_s": meta.get("out_s", duration)}


def _words(raw: Path | None, profile: Profile, settings: Settings) -> tuple[dict | None, str | None]:
    """Ordfilen och, om den inte går att använda, varför."""
    data = load_words(raw, source_path(raw)) if raw is not None and raw.is_file() else None
    if data is not None:
        return data, None
    if raw is not None and words_path(raw).exists():  # till exempel ett nytt råklipp från en ny körning
        return None, STALE
    try:
        enabled = load_config(settings.root / (profile.clipsmith_config or "")).caption.enabled
    except Exception:  # ingen eller trasig config: kampanjen skär inga undertexter
        enabled = False
    return None, BURNED if enabled else NO_SUBTITLES


def _check_words(words: list[dict], duration: float) -> list[dict]:
    """Robins ord, kontrollerade. Ett ord som saknas är borttaget; nya ord kan inte läggas till."""
    if len(words) > WORDS_MAX:
        raise ValueError(f"Undertexten får ha högst {WORDS_MAX} ord.")
    checked, last = [], 0.0
    for w in words:
        text, start, end = str(w["word"]).strip(), float(w["start"]), float(w["end"])
        if not text or len(text) > WORD_CHARS:
            raise ValueError(f"Ett ord får vara 1–{WORD_CHARS} tecken.")
        if CONTROL.search(text):
            raise ValueError(f"Ordet {text!r} får inte ha radbrytningar, tabbar eller andra styrtecken.")
        if not 0 <= start < end <= duration + 0.5:
            raise ValueError(f"Ordet {text!r} måste ligga inom klippet, och börja före sitt slut.")
        if start < last:
            raise ValueError(f"Ordet {text!r} börjar före ordet innan.")
        checked.append({"start": start, "end": end, "word": text})
        last = start
    return checked


def edit_info(clip: dict, profile: Profile, settings: Settings) -> dict:
    raw = raw_path(clip, settings)
    reason = why_not(clip, raw)
    min_s, max_s = limits(profile)
    original = trim = None
    if raw is not None and raw.is_file():
        state = _state(clip, raw)
        start = state["start_s"]
        original = {"duration_s": state["duration_s"], "start_s": start,
                    "end_s": None if start is None else round(start + state["duration_s"], 2),
                    "url": f"/campaign/clips/{clip['id']}/original"}
        trim = {"in_s": state["in_s"], "out_s": state["out_s"]}
    data, why = _words(raw, profile, settings)
    return {"clip_id": clip["id"], "title": clip["title"], "original": original, "trim": trim,
            "min_s": min_s, "max_s": max_s, "editable": reason is None, "reason": reason,
            "subtitles": {"editable": data is not None, "reason": why, "words": data["words"] if data else [],
                          "style": data["caption"].get("style") if data else None}}


def _game(log: ClipLog, clip: dict) -> str | None:
    games = [r["game"] for r in log.runs()
             if r["vod_id"] == clip["vod_id"] and r["profile"] == clip["profile"] and r["game"]]
    return games[-1] if games else None


def _caption(clip: dict, title: str, profile: Profile, game: str | None) -> str:
    """Ny caption från mallen, om den gamla var mallens. En handskriven caption behålls."""
    for old_game in dict.fromkeys((game, None)):
        if clip["caption"] == build_caption(clip["title"] or "", profile, old_game):
            return build_caption(title, profile, old_game)
    return clip["caption"]


def _tmp(path: Path, tag: str) -> Path:
    return path.with_name(f".{path.stem}.{tag}.mp4")  # punkt först: finish_vod letar bara efter clip_*


def _trim(src: Path, out: Path, in_s: float, out_s: float, encoder: str) -> None:
    """Koda om (inte -c copy), så att klippet börjar på rätt bildruta."""
    run([need("ffmpeg"), "-y", "-v", "error", "-ss", f"{in_s:.3f}", "-i", str(src), "-t", f"{out_s - in_s:.3f}",
         "-map", "0:v:0", "-map", "0:a:0?", *ENCODER_ARGS.get(encoder, ENCODER_ARGS["libx264"]),
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _new_trim(state: dict, in_s: float | None, out_s: float | None) -> tuple[float, float]:
    """Webben avrundar till tiondelar: ett slut strax efter originalet kapas till dess längd, och värden
    nära nuvarande trimning räknas som oförändrade, så att ett rubrikbyte inte kodar om klippet."""
    duration = state["duration_s"]
    new_in = state["in_s"] if in_s is None or abs(in_s - state["in_s"]) < ROUNDING_S else in_s
    new_out = state["out_s"] if out_s is None else out_s
    if duration < new_out <= duration + ROUNDING_S:
        new_out = duration
    if abs(new_out - state["out_s"]) < ROUNDING_S:
        new_out = state["out_s"]
    return new_in, new_out


def edit_clip(clip: dict, profile: Profile, settings: Settings, log: ClipLog, *,
              in_s: float | None = None, out_s: float | None = None, title: str | None = None,
              words: list[dict] | None = None) -> None:
    """Trimma och/eller byt rubrik. Anroparen håller `lock`."""
    raw = raw_path(clip, settings)
    reason = why_not(clip, raw)
    if reason:
        raise ValueError(reason)
    assert raw is not None
    state = _state(clip, raw)
    new_in, new_out = _new_trim(state, in_s, out_s)
    trimmed = (new_in, new_out) != (state["in_s"], state["out_s"])
    if trimmed:
        if not 0 <= new_in < new_out <= state["duration_s"]:
            raise ValueError(f"Start och slut måste ligga inom klippet: 0 till {state['duration_s']:g} s, "
                             "och starten före slutet.")
        min_s, max_s = limits(profile)
        length = round(new_out - new_in, 3)  # exakt kampanjens gränser, som i webben
        if not min_s <= length <= max_s:
            raise ValueError(f"Klippet blir {length:.1f} s. Kampanjen tillåter {min_s:g}–{max_s:g} s.")
    new_title = clip["title"] if title is None else title.strip()
    if not new_title:
        raise ValueError("Rubriken får inte vara tom.")
    data, why = _words(raw, profile, settings)
    new_words = data["words"] if data else None
    if words is not None:
        if data is None:
            raise ValueError(why)
        new_words = _check_words(words, state["duration_s"])
    words_changed = data is not None and new_words != data["words"]

    done = raw.parent / "klara" / raw.name
    caption = _caption(clip, new_title, profile, _game(log, clip))
    rendered = None
    original, meta_path = _original(raw)
    tmp_raw, tmp_done = _tmp(raw, "trim"), _tmp(done, "edit")
    tmp_original = original.with_name(f".{original.name}.tmp")
    ass: Path | None = None
    try:
        new_hook = profile.hook_text and new_title != clip["title"]  # rubriken står i bilden
        if trimmed or new_hook or words_changed or not done.exists():
            if trimmed:
                _trim(source_path(raw), tmp_raw, new_in, new_out, settings.encoder)
            src = tmp_raw if trimmed else raw
            plan = None
            if data:  # orden har originalets tider: undertexten för in_s–out_s, samma plan som körningen
                ass = raw.with_name(f".{raw.stem}.edit.ass")  # punkt först, som _tmp
                plan = prepare(src, {**data, "words": new_words}, new_in, new_out, profile, ass)
            rendered = finish_clip(src, tmp_done, profile, settings.root, settings.encoder, title=new_title,
                                   subtitles=ass, plan=plan, frame=data["reframe"]["mode"] if data else "")

        # Precis före bytet: klippet kan ha postats under tiden. Sedan tas ett gammalt ja bort, så att
        # inget godkännande gäller den nya filen om något går fel efteråt.
        now = log.get(clip["id"])
        reason = why_not(now, raw)
        if reason:
            raise EditConflict(f"Klippet ändrades under redigeringen. {reason}")
        log.update(clip["id"], status="renderad")

        if trimmed:
            if source_path(raw) == raw:  # första trimningen av det här råklippet: spara det som original
                original.parent.mkdir(exist_ok=True)
                shutil.copy2(raw, tmp_original)
                os.replace(tmp_original, original)
            os.replace(tmp_raw, raw)
            stat = raw.stat()
            _write_atomic(meta_path, json.dumps({
                "start_s": state["start_s"], "in_s": new_in, "out_s": new_out,
                "raw_size": stat.st_size, "raw_mtime_ns": stat.st_mtime_ns}).encode("utf-8"))
        if rendered:
            os.replace(tmp_done, done)
        if data and (trimmed or words_changed):
            # Efter metafilen: source_path(raw) är nu originalet, så ordfilen hör till det. Efter klippet: går
            # bytet fel gäller de gamla orden fortfarande det klipp som ligger där.
            save_words(raw, new_words or [], CaptionConfig(**data["caption"]), ReframeConfig(**data["reframe"]),
                       source_path(raw), hook=data.get("hook"))
    finally:
        if ass:
            ass.unlink(missing_ok=True)
        tmp_raw.unlink(missing_ok=True)
        tmp_done.unlink(missing_ok=True)
        tmp_original.unlink(missing_ok=True)
    manifest = {"overlay_applied": rendered["overlay_applied"], "text_applied": rendered["text_applied"]} \
        if rendered else {"overlay_applied": _has_overlay(profile, settings), "text_applied": bool(profile.text)}
    problems, warnings = check_clip(probe(done), manifest, profile, caption, clip["format"], clip["review"])
    done.with_suffix(".txt").write_text(caption + "\n", encoding="utf-8")

    fields: dict = {"title": new_title, "caption": caption, "problems": problems, "warnings": warnings,
                    "file": str(done), "status": "underkand" if problems else "renderad"}
    if trimmed and state["start_s"] is not None:
        fields.update(start_s=round(state["start_s"] + new_in, 3), end_s=round(state["start_s"] + new_out, 3))
    log.update(clip["id"], **fields)


def reframe_clip(clip: dict, profile: Profile, settings: Settings, log: ClipLog) -> None:
    """Gör om klippet i kampanjens bildläge (clipsmith-configens reframe). Råklippet skärs om ur källvideon
    work/<vod>/<vod>.mp4 över samma del av den. Rubrik, ord och trimning är kvar, och sedan görs klippet
    kampanjklart som efter en redigering, med en ny regelkontroll. Anroparen håller `lock`."""
    raw = raw_path(clip, settings)
    reason = why_not(clip, raw)
    if reason:
        raise ValueError(reason)
    assert raw is not None
    video = settings.work_dir / clip["vod_id"] / f"{clip['vod_id']}.mp4"
    if not video.is_file():
        raise ValueError(f"Källvideon saknas: {video}. Den behövs för att skära om klippet.")
    state = _state(clip, raw)
    if state["start_s"] is None:
        raise ValueError("Klippet saknar sin tid i källvideon, så det går inte att skära om.")
    reframe = load_config(settings.root / (profile.clipsmith_config or "")).reframe
    data, _ = _words(raw, profile, settings)
    meta, source = edit_meta(raw), source_path(raw)
    done = raw.parent / "klara" / raw.name
    tmp_source, tmp_raw, old_done = _tmp(source, "reframe"), _tmp(raw, "trim"), _tmp(done, "old")
    try:
        start = state["start_s"]
        run(_build_ffmpeg_cmd(video, start, start + state["duration_s"], None, reframe, tmp_source))
        os.replace(tmp_source, source)
        if meta:  # trimmat: råklippet skärs ur det nya originalet, och metafilen får råklippets nya identitet
            _trim(source, tmp_raw, meta["in_s"], meta["out_s"], settings.encoder)
            os.replace(tmp_raw, raw)
            stat = raw.stat()
            _write_atomic(_original(raw)[1], json.dumps({
                **meta, "raw_size": stat.st_size, "raw_mtime_ns": stat.st_mtime_ns}).encode("utf-8"))
        if data:  # samma ord, nu till det nya råklippet och dess bildläge
            save_words(raw, data["words"], CaptionConfig(**data["caption"]), reframe, source_path(raw),
                       hook=data.get("hook"))
        if done.exists():  # edit_clip gör klippet kampanjklart när det färdiga klippet saknas
            os.replace(done, old_done)
        try:
            edit_clip(log.get(clip["id"]), profile, settings, log)
        except Exception:
            if old_done.exists():
                os.replace(old_done, done)
            raise
    finally:
        for path in (tmp_source, tmp_raw, old_done):
            path.unlink(missing_ok=True)


def _has_overlay(profile: Profile, settings: Settings) -> bool:
    overlay = profile.overlay_path(settings.root)
    return overlay is not None and overlay.exists()
