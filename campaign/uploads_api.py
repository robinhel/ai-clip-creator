"""Egna videor: en uppladdad video görs om till 9:16 med spegling, filter och hastighet, och sparas som ett klipp
som Robin granskar. Ett godkänt klipp hamnar i Att posta som alla andra. Samma val som en ny version finns också:
font, klipp i början, egen overlay och klistermärke, med kampanjens regler.

Ett utkast visar klippet innan det sparas: en bild som följer valen, ett helt klipp som förhandsvisning och
Spara som använder samma fil. Logiken ligger i campaign.drafts.

Vägarna registreras på campaign.api.router, som importerar den här modulen sist.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Annotated

from fastapi import Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse

from clipsmith.api.deps import verify_api_key

from . import drafts, versions
from .api import _log, _profile, _profile_names, _public, get_settings, router
from .config import Settings
from .media import probe
from .drafts import FIT, FILL, FRAMINGS, UPLOAD, VIDEO_TYPES, Choices, FrameChoices, Plan  # noqa: F401
from .profile import Profile

KEY = [Depends(verify_api_key)]


@router.get("/uploads/options", summary="Valen för egna videor i en kampanj")
def upload_options(profile: str, settings: Settings = Depends(get_settings)) -> dict:
    if profile not in _profile_names(settings):
        raise HTTPException(400, f"Kampanjen {profile} finns inte.")
    campaign = _profile(profile, settings)
    return {"fonts": [{"name": name, "label": name} for name in versions.fonts()],
            "stickers": versions._files(settings.root / "stickers", "/campaign/stickers"),
            "overlays": versions._files(settings.root / "overlays", "/campaign/overlays", "overlays/"),
            "overlay_locked": campaign.overlay_required, "max_start_cut_s": versions.MAX_CUT_S,
            "min_seconds": campaign.min_seconds, "max_seconds": campaign.max_seconds,
            "safe_zone": {"top": campaign.safe_top, "bottom": campaign.safe_bottom},
            "hook_text": bool(campaign.hook_text), "framings": list(FRAMINGS),
            "source_allowed": (campaign.raw.get("source") or {}).get("allowed")}  # visas under kampanjväljaren


def _video_name(file: UploadFile) -> Path:
    name = Path(file.filename or "")
    if name.suffix.lower() not in VIDEO_TYPES:
        raise HTTPException(400, f"Filtypen {name.suffix or 'saknas'} går inte. Välj {', '.join(VIDEO_TYPES)}.")
    return name


def _campaign(choices: Choices, settings: Settings) -> Profile:
    if choices.profile not in _profile_names(settings):  # bara ett namn, ingen sökväg
        raise HTTPException(400, f"Kampanjen {choices.profile} finns inte.")
    return _profile(choices.profile, settings)


def _plan(settings: Settings, choices: Choices, duration: float, strict: bool) -> Plan:
    """Valen kontrollerade. strict: kampanjens min och max gäller alltid (utkasten). Annars bara när start
    eller slut är valt; utan dem säger regelkontrollen till, som förut."""
    campaign = _campaign(choices, settings)
    try:
        plan = drafts.make_plan(settings, campaign, choices, duration)
        if strict or plan.trimmed:
            drafts.check_length(plan)
    except versions.VersionError as error:
        raise HTTPException(error.status, str(error)) from error
    return plan


def _ffmpeg_failed(error: RuntimeError) -> HTTPException:
    return HTTPException(500, f"Videon gick inte att göra om: {error}")


@router.post("/uploads", status_code=201, summary="Ladda upp en egen video och gör om den till ett klipp att granska",
             dependencies=KEY)
def upload(choices: Annotated[Choices, Form()], file: UploadFile = File(...),
           settings: Settings = Depends(get_settings)) -> dict:
    name = _video_name(file)
    _campaign(choices, settings)
    stem = drafts.stem_for(name.name)
    folder = settings.out_dir / UPLOAD
    source, raw, done = folder / f".{stem}.src{name.suffix.lower()}", folder / f"{stem}.mp4", folder / "klara" / f"{stem}.mp4"
    done.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        try:
            duration = probe(source)["duration"]
        except RuntimeError as error:
            raise HTTPException(400, "Filen går inte att läsa som video.") from error
        plan = replace(_plan(settings, choices, duration, strict=False), name=name.name)
        drafts.make_raw(source, raw, plan)
        manifest = drafts.finish(raw, done, plan)
    except BaseException as error:
        raw.unlink(missing_ok=True)
        done.unlink(missing_ok=True)
        if isinstance(error, RuntimeError):  # ffmpeg misslyckades
            raise _ffmpeg_failed(error) from error
        raise
    finally:
        source.unlink(missing_ok=True)
    log = _log(settings)
    return _public(log.get(drafts.store(log, plan, done, manifest)), settings)


# ---------- Utkast ----------

def _draft(settings: Settings, draft_id: str) -> Path:
    try:
        return drafts.folder(settings, draft_id)
    except versions.VersionError as error:
        raise HTTPException(error.status, str(error)) from error


@contextmanager
def _locked(settings: Settings, draft_id: str) -> Iterator[Path]:
    """Utkastets mapp, med utkastets lås: render, save och delete väntar på varandra."""
    if not drafts.DRAFT_ID.fullmatch(draft_id):
        raise HTTPException(404, "Utkastet finns inte.")
    with drafts.lock(draft_id):
        yield _draft(settings, draft_id)


@router.post("/uploads/drafts", status_code=201, summary="Ladda upp en video som ett utkast, utan kampanj",
             dependencies=KEY)
def create_draft(file: UploadFile = File(...), settings: Settings = Depends(get_settings)) -> dict:
    name = _video_name(file)
    try:
        return drafts.create(settings, file.file, name.name)
    except versions.VersionError as error:
        raise HTTPException(error.status, str(error)) from error


@router.get("/uploads/drafts/{draft_id}/frame", summary="En bild ur klippet vid tiden t, som den blir",
            response_class=Response)
def draft_frame(draft_id: str, choices: Annotated[FrameChoices, Query()],
                settings: Settings = Depends(get_settings)) -> Response:
    path = _draft(settings, draft_id)
    plan = _plan(settings, choices, drafts.meta(path)["duration_s"], strict=False)
    try:
        picture = drafts.frame(path, plan, choices.t)
    except RuntimeError as error:
        raise _ffmpeg_failed(error) from error
    return Response(picture, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/uploads/drafts/{draft_id}/render", summary="Gör hela klippet som förhandsvisning", dependencies=KEY)
def render_draft(draft_id: str, choices: Annotated[Choices, Form()],
                 settings: Settings = Depends(get_settings)) -> dict:
    with _locked(settings, draft_id) as path:
        plan = _plan(settings, choices, drafts.meta(path)["duration_s"], strict=True)
        try:
            state = drafts.render(path, plan)
        except RuntimeError as error:
            raise _ffmpeg_failed(error) from error
    return {"video_url": f"/campaign/uploads/drafts/{draft_id}/video?v={state['key']}",
            "length_s": state["length_s"], "problems": state["problems"], "warnings": state["warnings"],
            "caption": state["caption"], "choices_key": state["key"]}


@router.get("/uploads/drafts/{draft_id}/video", summary="Förhandsvisningen (med Range-stöd)")
def draft_video(draft_id: str, settings: Settings = Depends(get_settings)) -> FileResponse:
    video = _draft(settings, draft_id) / "render.mp4"
    if not video.is_file():
        raise HTTPException(404, "Klippet är inte gjort än. Tryck Förhandsvisa.")
    return FileResponse(video, media_type="video/mp4")


@router.post("/uploads/drafts/{draft_id}/save", status_code=201, summary="Spara utkastet som ett klipp att granska",
             dependencies=KEY)
def save_draft(draft_id: str, choices: Annotated[Choices, Form()],
               settings: Settings = Depends(get_settings)) -> dict:
    log = _log(settings)
    with _locked(settings, draft_id) as path:
        plan = _plan(settings, choices, drafts.meta(path)["duration_s"], strict=True)
        try:
            clip_id = drafts.save(log, path, plan)
        except RuntimeError as error:
            raise _ffmpeg_failed(error) from error
    return _public(log.get(clip_id), settings)


@router.delete("/uploads/drafts/{draft_id}", status_code=204, summary="Ta bort utkastet", dependencies=KEY)
def delete_draft(draft_id: str, settings: Settings = Depends(get_settings)) -> Response:
    try:
        with _locked(settings, draft_id) as path:
            shutil.rmtree(path)
    except HTTPException as error:
        if error.status_code != 404 or not drafts.DRAFT_ID.fullmatch(draft_id):
            raise  # ett utkast som redan är borta: klart ändå
    return Response(status_code=204)
