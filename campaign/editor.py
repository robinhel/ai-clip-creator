"""Enkel redigerare: trimma ett klipp och byt rubrik.

Klippet trimmas ur clipsmiths råklipp out/<vod>/clip_NN.mp4 (före overlay och text). Första gången
sparas råklippet som out/<vod>/original/clip_NN.mp4, med trimningen i original/clip_NN.json, så att
varje ny trimning utgår från originalet. Sedan görs klippet kampanjklart igen med finish_clip och
regelkontrollen körs med den sparade bildgranskningen.

Fel ger ValueError med svensk text.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path

from .caption import build_caption
from .cliplog import ClipLog
from .config import Settings
from .finish import ENCODER_ARGS, finish_clip
from .media import need, probe, run
from .pipeline import clipsmith_config
from .profile import Profile
from .rules import check_clip

lock = threading.Lock()  # en redigering åt gången
LOCKED = ("postad", "inskickad", "analytics_inskickad", "betald")


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


def source_path(raw: Path) -> Path:
    """Originalet om klippet har trimmats, annars råklippet."""
    original, _ = _original(raw)
    return original if original.exists() else raw


def limits(profile: Profile, settings: Settings) -> tuple[float, float]:
    """Kampanjens längd ur clipsmiths config, som vid start av en körning. Annars profilens."""
    try:
        clip = clipsmith_config(profile, settings).clip
        return clip.min_seconds, clip.max_seconds
    except ValueError:
        return profile.min_seconds, profile.max_seconds


def why_not(clip: dict, raw: Path | None) -> str | None:
    if clip["status"] in LOCKED:
        return "Klippet är postat. Det går inte att ändra."
    if clip["status"] == "nekad":
        return "Klippet är nekat. Ångra nejet först om du vill ändra det."
    if raw is None or not raw.is_file():
        return "Råklippet saknas, så klippet går inte att trimma."
    return None


def _state(clip: dict, raw: Path) -> dict:
    """Originalets längd och början i VOD:en, och nuvarande trimning. Skriver inget."""
    _, meta_path = _original(raw)
    duration = round(probe(source_path(raw))["duration"], 2)
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    start = meta.get("start_s", clip["start_s"])
    return {"duration_s": duration, "start_s": start, "in_s": meta.get("in_s", 0.0),
            "out_s": meta.get("out_s", duration)}


def edit_info(clip: dict, profile: Profile, settings: Settings) -> dict:
    raw = raw_path(clip, settings)
    reason = why_not(clip, raw)
    min_s, max_s = limits(profile, settings)
    original = trim = None
    if raw is not None and raw.is_file():
        state = _state(clip, raw)
        start = state["start_s"]
        original = {"duration_s": state["duration_s"], "start_s": start,
                    "end_s": None if start is None else round(start + state["duration_s"], 2),
                    "url": f"/campaign/clips/{clip['id']}/original"}
        trim = {"in_s": state["in_s"], "out_s": state["out_s"]}
    return {"clip_id": clip["id"], "title": clip["title"], "original": original, "trim": trim,
            "min_s": min_s, "max_s": max_s, "editable": reason is None, "reason": reason}


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


def edit_clip(clip: dict, profile: Profile, settings: Settings, log: ClipLog, *,
              in_s: float | None = None, out_s: float | None = None, title: str | None = None) -> None:
    """Trimma och/eller byt rubrik. Anroparen håller `lock`."""
    raw = raw_path(clip, settings)
    reason = why_not(clip, raw)
    if reason:
        raise ValueError(reason)
    assert raw is not None
    state = _state(clip, raw)
    new_in = state["in_s"] if in_s is None else in_s
    new_out = state["out_s"] if out_s is None else out_s
    trimmed = abs(new_in - state["in_s"]) > 0.005 or abs(new_out - state["out_s"]) > 0.005
    if trimmed:
        if not 0 <= new_in < new_out <= state["duration_s"]:
            raise ValueError(f"Start och slut måste ligga inom klippet: 0 till {state['duration_s']:g} s, "
                             "och starten före slutet.")
        min_s, max_s = limits(profile, settings)
        length = round(new_out - new_in, 3)  # exakt kampanjens gränser, som i webben
        if not min_s <= length <= max_s:
            raise ValueError(f"Klippet blir {length:.1f} s. Kampanjen tillåter {min_s:g}–{max_s:g} s.")
    new_title = clip["title"] if title is None else title.strip()
    if not new_title:
        raise ValueError("Rubriken får inte vara tom.")

    done = raw.parent / "klara" / raw.name
    caption = _caption(clip, new_title, profile, _game(log, clip))
    rendered = None
    if trimmed or not done.exists():
        original, meta_path = _original(raw)
        tmp_raw, tmp_done = _tmp(raw, "trim"), _tmp(done, "edit")
        try:
            if trimmed:
                _trim(source_path(raw), tmp_raw, new_in, new_out, settings.encoder)
            rendered = finish_clip(tmp_raw if trimmed else raw, tmp_done, profile, settings.root, settings.encoder)
            if trimmed and not original.exists():
                original.parent.mkdir(exist_ok=True)
                shutil.copy2(raw, original.with_name(f".{original.name}.tmp"))
                os.replace(original.with_name(f".{original.name}.tmp"), original)
            if trimmed:
                meta_path.write_text(json.dumps({"start_s": state["start_s"], "in_s": new_in, "out_s": new_out}),
                                     encoding="utf-8")
                os.replace(tmp_raw, raw)
            os.replace(tmp_done, done)
        finally:
            tmp_raw.unlink(missing_ok=True)
            tmp_done.unlink(missing_ok=True)
            original.with_name(f".{original.name}.tmp").unlink(missing_ok=True)
    manifest = {"overlay_applied": rendered["overlay_applied"], "text_applied": rendered["text_applied"]} \
        if rendered else {"overlay_applied": _has_overlay(profile, settings), "text_applied": bool(profile.text)}
    problems, warnings = check_clip(probe(done), manifest, profile, caption, clip["format"], clip["review"])
    done.with_suffix(".txt").write_text(caption + "\n", encoding="utf-8")

    fields: dict = {"title": new_title, "caption": caption, "problems": problems, "warnings": warnings,
                    "file": str(done), "status": "underkand" if problems else "renderad"}
    if trimmed and state["start_s"] is not None:
        fields.update(start_s=round(state["start_s"] + new_in, 3), end_s=round(state["start_s"] + new_out, 3))
    log.update(clip["id"], **fields)


def _has_overlay(profile: Profile, settings: Settings) -> bool:
    overlay = profile.overlay_path(settings.root)
    return overlay is not None and overlay.exists()
