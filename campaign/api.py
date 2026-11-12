"""Kampanjlagrets vägar i clipsmiths server. Webbappen använder dem via /api/campaign/..."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from clipsmith.api.deps import get_db, verify_api_key
from clipsmith.db.models import Run, RunStatus

from .budget import budget_from_settings
from .caption import check_caption
from .cliplog import STATUSES, ClipLog
from .config import Settings, load_settings
from .pipeline import finish_run, run_campaign_vod
from .profile import Profile, load_profile

router = APIRouter(prefix="/campaign", tags=["campaign"])

PLATFORMS = ("tiktok", "youtube_shorts", "instagram_reels")

# Vilka steg ett klipp får ta. Från underkand kommer man bara vidare genom att rätta problemen.
NEXT_STATUS: dict[str, set[str]] = {
    "renderad": {"godkand", "nekad"},
    "underkand": {"nekad"},
    "godkand": {"postad", "nekad", "renderad"},
    "nekad": {"renderad"},
    "postad": {"inskickad"},
    "inskickad": {"analytics_inskickad", "betald"},
    "analytics_inskickad": {"betald"},
    "betald": set(),
}


def get_settings() -> Settings:
    return load_settings()


def _log(settings: Settings) -> ClipLog:
    return ClipLog(settings.data_dir / "klipp.db")


def _profile(name: str, settings: Settings) -> Profile:
    try:
        return load_profile(name, settings.profiles_dir)
    except SystemExit as error:
        raise HTTPException(400, f"Okänd kampanj: {name}") from error


def _profile_names(settings: Settings) -> list[str]:
    return sorted(p.stem for p in settings.profiles_dir.glob("*.yaml") if not p.stem.startswith("_"))


# ---------- kampanjer ----------


@router.get("/profiles", summary="Kampanjer som går att köra")
def list_profiles(settings: Settings = Depends(get_settings)) -> list[dict]:
    from clipsmith.config.loaders import load_config

    result = []
    for name in _profile_names(settings):
        profile = load_profile(name, settings.profiles_dir)
        problems = profile.problems(settings.root)
        blocking: list[str] = []
        channel = profile.streamer.lower()
        config = settings.root / profile.clipsmith_config if profile.clipsmith_config else None
        if config is None:
            blocking.append("Profilen saknar clipsmith_config.")
        elif not config.exists():
            blocking.append(f"Inställningsfilen {profile.clipsmith_config} saknas.")
        else:
            channels = load_config(config).channels
            channel = channels[0] if channels else channel
        if not settings.berget_api_key:
            blocking.append("BERGET_API_KEY saknas i .env.")
        if not settings.anthropic_api_key:
            blocking.append("ANTHROPIC_API_KEY saknas i .env.")
        result.append({
            "name": profile.name,
            "streamer": profile.streamer,
            "campaign": profile.raw.get("campaign", profile.name),
            "language": profile.language,
            "channel": channel,
            "formats": [{"id": f["id"], "name": f.get("name", f["id"]),
                         "description": f.get("description", "")} for f in profile.formats],
            "problems": blocking + problems,
            "ready": not blocking,
        })
    return result


# ---------- körningar ----------


class CampaignRunCreate(BaseModel):
    profile: str
    vod_id: str = Field(pattern=r"^\d{6,15}$")
    game: str | None = None
    start_s: float = 0.0
    end_s: float = 0.0


@router.post("/runs", status_code=201, summary="Starta en körning för en kampanj",
             dependencies=[Depends(verify_api_key)])
def create_run(
    body: CampaignRunCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    profile = _profile(body.profile, settings)
    ready = next(p for p in list_profiles(settings) if p["name"] == profile.name)
    if not ready["ready"]:
        raise HTTPException(400, " ".join(p for p in ready["problems"]))
    if request.app.state.active_run_id is not None:
        raise HTTPException(429, "En körning pågår redan. Vänta tills den är klar.")

    game = (body.game or "").strip() or None
    run = Run(vod_id=body.vod_id, channel=ready["channel"], status=RunStatus.pending)
    db.add(run)
    db.commit()
    db.refresh(run)
    _log(settings).add_run(run.id, body.vod_id, profile.name, game)
    request.app.state.active_run_id = run.id
    background_tasks.add_task(run_campaign_vod, run.id, body.vod_id, profile.name, game, settings,
                              start_s=body.start_s, end_s=body.end_s, app=request.app)
    return run.to_dict()


@router.get("/runs", summary="Vilken kampanj varje körning hör till")
def list_runs(settings: Settings = Depends(get_settings)) -> list[dict]:
    return _log(settings).runs()


@router.post("/runs/{run_id}/finish", status_code=202, summary="Gör klippen kampanjklara igen",
             dependencies=[Depends(verify_api_key)])
def refinish_run(
    run_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
) -> dict:
    meta = _log(settings).run(run_id)
    if meta is None:
        raise HTTPException(404, "Körningen hör inte till någon kampanj.")
    if request.app.state.active_run_id is not None:
        raise HTTPException(429, "En körning pågår redan. Vänta tills den är klar.")
    request.app.state.active_run_id = run_id
    background_tasks.add_task(finish_run, run_id, meta["vod_id"], meta["profile"], meta["game"],
                              settings, app=request.app)
    return {"started": True}


# ---------- klipp ----------


def _public(clip: dict, settings: Settings) -> dict:
    out = {k: v for k, v in clip.items() if k != "file"}
    out["filename"] = Path(clip["file"] or "").name
    out["problems"] = clip["problems"] or []
    out["warnings"] = clip["warnings"] or []
    out["signals"] = clip["signals"] or []
    out["engagement"] = ClipLog.engagement(clip)
    try:
        profile = load_profile(clip["profile"], settings.profiles_dir)
        out["engagement_min"] = profile.engagement_min(clip["views"] or 0)
    except SystemExit:
        out["engagement_min"] = None
    out["video_url"] = f"/campaign/clips/{clip['id']}/file"
    return out


def _get(log: ClipLog, clip_id: int) -> dict:
    try:
        return log.get(clip_id)
    except SystemExit as error:
        raise HTTPException(404, "Klippet finns inte.") from error


@router.get("/clips", summary="Klipp i klipploggen")
def list_clips(status: str | None = None, vod_id: str | None = None,
               settings: Settings = Depends(get_settings)) -> list[dict]:
    clips = _log(settings).list(status or None)
    if vod_id:
        clips = [c for c in clips if c["vod_id"] == vod_id]
    return [_public(c, settings) for c in clips]


@router.get("/clips/{clip_id}/file", summary="Spela upp eller ladda ner ett klipp")
def clip_file(clip_id: int, download: bool = False,
              settings: Settings = Depends(get_settings)) -> FileResponse:
    clip = _get(_log(settings), clip_id)
    out_root = settings.out_dir.resolve()
    path = Path(clip["file"] or "").resolve()
    if not path.is_relative_to(out_root) or path.suffix != ".mp4" or not path.exists():
        raise HTTPException(404, "Filen finns inte.")
    return FileResponse(path, media_type="video/mp4", filename=path.name if download else None,
                        content_disposition_type="attachment" if download else "inline")


class ClipPatch(BaseModel):
    status: Literal[tuple(STATUSES)] | None = None  # type: ignore[valid-type]
    caption: str | None = None
    title: str | None = None
    format: str | None = None
    platform: str | None = None
    url: str | None = None
    views: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    shares: int | None = Field(default=None, ge=0)
    note: str | None = None


def _caption_problems(problems: list[str], caption: str, profile: Profile) -> list[str]:
    """Byt ut captionens problem mot en ny kontroll. Övriga problem ligger kvar."""
    others = [p for p in problems if not p.startswith("Captionen saknar")]
    return others + check_caption(caption, profile)


@router.patch("/clips/{clip_id}", summary="Ändra ett klipp: status, caption, länk eller statistik",
              dependencies=[Depends(verify_api_key)])
def patch_clip(clip_id: int, body: ClipPatch, settings: Settings = Depends(get_settings)) -> dict:
    log = _log(settings)
    clip = _get(log, clip_id)
    profile = _profile(clip["profile"], settings)
    fields = body.model_dump(exclude_unset=True)

    if "format" in fields and profile.formats and fields["format"] not in profile.format_ids:
        raise HTTPException(400, f"Formatet {fields['format']!r} finns inte i kampanjen.")
    if "platform" in fields and fields["platform"] not in PLATFORMS:
        raise HTTPException(400, "Plattformen måste vara tiktok, youtube_shorts eller instagram_reels.")
    if "url" in fields and fields["url"] and not fields["url"].startswith(("https://", "http://")):
        raise HTTPException(400, "Länken måste börja med https://.")

    problems = clip["problems"] or []
    status = clip["status"]
    if "caption" in fields:
        problems = _caption_problems(problems, fields["caption"], profile)
        fields["problems"] = problems
        if status in ("renderad", "underkand") and body.status is None:
            fields["status"] = "underkand" if problems else "renderad"

    new = body.status
    if new and new != status:
        if new not in NEXT_STATUS[status]:
            raise HTTPException(400, f"Ett klipp med status {status} kan inte bli {new}.")
        if new == "godkand":
            if problems:
                raise HTTPException(400, "Klippet har problem. Rätta dem innan du godkänner.")
            if profile.formats and not (fields.get("format") or clip["format"]):
                raise HTTPException(400, "Välj ett format innan du godkänner.")
        if new == "renderad" and problems:
            fields["status"] = "underkand"
        if new == "postad":
            if not (fields.get("url") or clip["url"]):
                raise HTTPException(400, "Klistra in länken till det postade klippet.")
            if not (fields.get("platform") or clip["platform"]):
                raise HTTPException(400, "Välj plattform.")

    if fields:
        log.update(clip_id, **fields)
    return _public(log.get(clip_id), settings)


# ---------- översikt ----------


@router.get("/summary", summary="Budget, antal klipp per status och att göra")
def summary(settings: Settings = Depends(get_settings)) -> dict:
    log = _log(settings)
    counts: dict[str, int] = {}
    for clip in log.list():
        counts[clip["status"]] = counts.get(clip["status"], 0) + 1
    return {"budget": budget_from_settings(settings).summary(), "counts": counts, "todo": log.todo()}
