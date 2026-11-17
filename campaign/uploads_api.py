"""Egna videor: en uppladdad video görs om till 9:16 med spegling, filter och hastighet, och sparas som ett klipp
som Robin granskar. Ett godkänt klipp hamnar i Att posta som alla andra. Samma val som en ny version finns också:
font, klipp i början, egen overlay och klistermärke, med kampanjens regler.

Vägarna registreras på campaign.api.router, som importerar den här modulen sist.
"""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

from fastapi import Depends, File, Form, HTTPException, UploadFile

from clipsmith.api.deps import verify_api_key

from . import versions
from .api import _log, _profile, _profile_names, _public, get_settings, router
from .caption import build_caption
from .config import Settings
from .finish import ENCODER_ARGS, HEIGHT, WIDTH, finish_clip
from .media import need, probe, run
from .rules import check_clip

VIDEO_TYPES = (".mp4", ".mov", ".m4v", ".webm", ".mkv")
UPLOAD = "upload"  # vod_id och mappen under out/
# Fyll hela 9:16-bilden och skär bort det som sticker ut.
FILL = f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},setsar=1"
# Hela bilden, så stor som ryms, på en suddig kopia av sig själv som fyller 9:16. Samma bild som clipsmiths
# läge fit, men en stående video ryms också. Kopian suddas i en fjärdedels storlek: snabbare och ser likadan ut.
FIT = (f"split[bg][fg];[bg]scale={WIDTH // 4}:{HEIGHT // 4}:force_original_aspect_ratio=increase,"
       f"crop={WIDTH // 4}:{HEIGHT // 4},boxblur=10:2,scale={WIDTH}:{HEIGHT}:flags=bilinear[blur];"
       f"[fg]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos[pic];"
       f"[blur][pic]overlay=(W-w)/2:(H-h)/2,setsar=1")
FRAMINGS = {"fill": FILL, "fit": FIT}


@router.get("/uploads/options", summary="Valen för egna videor i en kampanj")
def upload_options(profile: str, settings: Settings = Depends(get_settings)) -> dict:
    if profile not in _profile_names(settings):
        raise HTTPException(400, f"Kampanjen {profile} finns inte.")
    campaign = _profile(profile, settings)
    return {"fonts": [{"name": name, "label": name} for name in versions.fonts()],
            "stickers": versions._files(settings.root / "stickers", "/campaign/stickers"),
            "overlays": versions._files(settings.root / "overlays", "/campaign/overlays", "overlays/"),
            "overlay_locked": campaign.overlay_required, "max_start_cut_s": versions.MAX_CUT_S,
            "min_seconds": campaign.min_seconds, "hook_text": bool(campaign.hook_text), "framings": list(FRAMINGS),
            "source_allowed": (campaign.raw.get("source") or {}).get("allowed")}  # visas under kampanjväljaren


def _comma(value: float, fmt: str) -> str:
    """Tal med komma, som 4,6."""
    return format(value, fmt).replace(".", ",")


def _with_advice(problems: list[str]) -> list[str]:
    """En egen video går inte att göra kampanjklar igen: säg vad Robin kan göra i stället."""
    return [f"{p} Ladda upp en längre video eller tryck Nej." if "Kampanjen kräver minst" in p else p for p in problems]


@router.post("/uploads", status_code=201, summary="Ladda upp en egen video och gör om den till ett klipp att granska",
             dependencies=[Depends(verify_api_key)])
def upload(file: UploadFile = File(...), profile: str = Form(...), title: str = Form(""),
           mirror: bool = Form(False), speed: float = Form(1.0), filter: str = Form("none"),
           brightness: float = Form(0.0), saturation: float = Form(0.0), framing: str = Form("fit"),
           font: str = Form(""), start_cut_s: float = Form(0.0), overlay: str = Form(""), sticker: str = Form(""),
           sticker_position: str = Form("top_right"), sticker_start_s: float = Form(0.0),
           sticker_duration_s: float = Form(3.0), settings: Settings = Depends(get_settings)) -> dict:
    name = Path(file.filename or "")
    if name.suffix.lower() not in VIDEO_TYPES:
        raise HTTPException(400, f"Filtypen {name.suffix or 'saknas'} går inte. Välj {', '.join(VIDEO_TYPES)}.")
    if profile not in _profile_names(settings):  # bara ett namn, ingen sökväg
        raise HTTPException(400, f"Kampanjen {profile} finns inte.")
    campaign = _profile(profile, settings)
    if speed not in versions.SPEEDS:
        raise HTTPException(400, f"Hastigheten {speed:g} går inte. Välj {', '.join(f'{s:g}' for s in versions.SPEEDS)}.")
    if framing not in FRAMINGS:
        raise HTTPException(400, f"Bildläget {framing!r} finns inte. Välj {', '.join(FRAMINGS)}.")
    found = versions.fonts()
    if font and font not in found:
        raise HTTPException(400, f"Fonten {font} finns inte. Välj en ur listan.")
    if not 0 <= start_cut_s <= versions.MAX_CUT_S:
        raise HTTPException(400, f"Klipp i början får vara 0–{versions.MAX_CUT_S:g} sekunder.")
    try:
        look = versions.look(mirror, filter, brightness, saturation)
        own = versions._overlay(settings, campaign, overlay or None)  # tomt: kampanjens overlay
    except versions.VersionError as error:
        raise HTTPException(400, str(error)) from error

    stem = f"{re.sub(r'[^A-Za-z0-9_-]+', '_', name.stem).strip('_')[:40] or 'video'}-{uuid.uuid4().hex[:6]}"
    folder = settings.out_dir / UPLOAD
    source, raw, done = folder / f".{stem}.src{name.suffix.lower()}", folder / f"{stem}.mp4", folder / "klara" / f"{stem}.mp4"
    done.parent.mkdir(parents=True, exist_ok=True)
    hook = title.strip()  # bara en rubrik Robin skriver bränns in, aldrig filens namn
    try:
        with source.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        try:
            length = probe(source)["duration"] - start_cut_s
        except RuntimeError as error:
            raise HTTPException(400, "Filen går inte att läsa som video.") from error
        if start_cut_s and length / speed < campaign.min_seconds:  # utan klipp i början: regelkontrollen säger till
            raise HTTPException(400, f"Klippet blir {_comma(max(length / speed, 0), '.1f')} s. "
                                     f"Kampanjen kräver minst {_comma(campaign.min_seconds, 'g')} s.")
        wanted = {"file": sticker, "position": sticker_position, "start_s": sticker_start_s,
                  "duration_s": sticker_duration_s} if sticker else None
        try:
            placed = versions._sticker(settings, campaign, wanted, length, hook, own)
        except versions.VersionError as error:
            raise HTTPException(400, str(error)) from error
        cut = ["-ss", f"{start_cut_s:.3f}"] if start_cut_s else []
        run([need("ffmpeg"), "-y", "-v", "error", *cut, "-i", str(source), "-vf", FRAMINGS[framing],
             "-map", "0:v:0", "-map", "0:a:0?",
             *ENCODER_ARGS.get(settings.encoder, ENCODER_ARGS["libx264"]), "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", str(raw)])
        manifest = finish_clip(raw, done, campaign, settings.root, settings.encoder, title=hook, look=look,
                               speed=speed, overlay=own, font_file=found.get(font), sticker=placed)
    except BaseException as error:
        raw.unlink(missing_ok=True)
        done.unlink(missing_ok=True)
        if isinstance(error, RuntimeError):  # ffmpeg misslyckades
            raise HTTPException(500, f"Videon gick inte att göra om: {error}") from error
        raise
    finally:
        source.unlink(missing_ok=True)

    title = hook or name.stem  # namnet syns bara i listan
    caption = build_caption(hook, campaign)  # utan rubrik: bara kampanjens text, aldrig filens namn
    problems, warnings = check_clip(probe(done), manifest, campaign, caption)
    problems = _with_advice(problems)
    log = _log(settings)
    clip_id = log.add(vod_id=UPLOAD, profile=profile, file=str(done), title=title, caption=caption,
                      status="underkand" if problems else "renderad", problems=problems, warnings=warnings)
    return _public(log.get(clip_id), settings)
