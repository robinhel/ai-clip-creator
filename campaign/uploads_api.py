"""Egna videor: en uppladdad video görs om till 9:16 med spegling, filter och hastighet, och sparas som ett klipp
som Robin granskar. Ett godkänt klipp hamnar i Att posta som alla andra.

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


@router.post("/uploads", status_code=201, summary="Ladda upp en egen video och gör om den till ett klipp att granska",
             dependencies=[Depends(verify_api_key)])
def upload(file: UploadFile = File(...), profile: str = Form(...), title: str = Form(""),
           mirror: bool = Form(False), speed: float = Form(1.0), filter: str = Form("none"),
           brightness: float = Form(0.0), saturation: float = Form(0.0),
           settings: Settings = Depends(get_settings)) -> dict:
    name = Path(file.filename or "")
    if name.suffix.lower() not in VIDEO_TYPES:
        raise HTTPException(400, f"Filtypen {name.suffix or 'saknas'} går inte. Välj {', '.join(VIDEO_TYPES)}.")
    if profile not in _profile_names(settings):  # bara ett namn, ingen sökväg
        raise HTTPException(400, f"Kampanjen {profile} finns inte.")
    campaign = _profile(profile, settings)
    if speed not in versions.SPEEDS:
        raise HTTPException(400, f"Hastigheten {speed:g} går inte. Välj {', '.join(f'{s:g}' for s in versions.SPEEDS)}.")
    try:
        look = versions.look(mirror, filter, brightness, saturation)
    except versions.VersionError as error:
        raise HTTPException(400, str(error)) from error

    stem = f"{re.sub(r'[^A-Za-z0-9_-]+', '_', name.stem).strip('_')[:40] or 'video'}-{uuid.uuid4().hex[:6]}"
    folder = settings.out_dir / UPLOAD
    source, raw, done = folder / f".{stem}.src{name.suffix.lower()}", folder / f"{stem}.mp4", folder / "klara" / f"{stem}.mp4"
    done.parent.mkdir(parents=True, exist_ok=True)
    title = title.strip() or name.stem
    try:
        with source.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        try:
            probe(source)
        except RuntimeError as error:
            raise HTTPException(400, "Filen går inte att läsa som video.") from error
        run([need("ffmpeg"), "-y", "-v", "error", "-i", str(source), "-vf", FILL, "-map", "0:v:0", "-map", "0:a:0?",
             *ENCODER_ARGS.get(settings.encoder, ENCODER_ARGS["libx264"]), "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", str(raw)])
        manifest = finish_clip(raw, done, campaign, settings.root, settings.encoder, title=title, look=look,
                               speed=speed)
    except RuntimeError as error:  # ffmpeg misslyckades
        raise HTTPException(500, f"Videon gick inte att göra om: {error}") from error
    except BaseException:
        raw.unlink(missing_ok=True)
        done.unlink(missing_ok=True)
        raise
    finally:
        source.unlink(missing_ok=True)

    caption = build_caption(title, campaign)
    problems, warnings = check_clip(probe(done), manifest, campaign, caption)
    log = _log(settings)
    clip_id = log.add(vod_id=UPLOAD, profile=profile, file=str(done), title=title, caption=caption,
                      status="underkand" if problems else "renderad", problems=problems, warnings=warnings)
    return _public(log.get(clip_id), settings)
