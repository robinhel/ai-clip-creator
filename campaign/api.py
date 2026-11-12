"""Kampanjlagrets vägar i clipsmiths server. Webbappen använder dem via /api/campaign/..."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from clipsmith.api.deps import get_db, verify_api_key
from clipsmith.db.models import PipelineEvent, Run, RunStatus

from . import editor, estimate
from .budget import budget_from_settings
from .caption import check_caption
from .cliplog import STATUSES, ClipLog
from .config import Settings, load_settings
from .pipeline import finish_run, run_campaign_vod
from .profile import Profile, load_profile
from .vodinfo import VodNotFound, vod_facts

router = APIRouter(prefix="/campaign", tags=["campaign"])
_start_lock = threading.Lock()  # bara en körning i taget, också när två anrop kommer samtidigt

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
    except (SystemExit, Exception) as error:
        raise HTTPException(400, f"Kampanjen {name} går inte att läsa.") from error


def _profile_names(settings: Settings) -> list[str]:
    return sorted(p.stem for p in settings.profiles_dir.glob("*.yaml") if not p.stem.startswith("_"))


# ---------- kampanjer ----------


def _describe_profile(name: str, settings: Settings, path: Path | None = None) -> dict:
    """Kampanjen som den visas i appen. `path` läser profilen från en annan fil, till exempel en tmp-fil
    som ska kontrolleras innan den ersätter profilen."""
    from .profile_edit import config_file, read_config

    profile = load_profile(path or name, settings.profiles_dir)
    problems = profile.problems(settings.root)
    blocking: list[str] = []
    channel = profile.streamer.lower()
    clip_min_s = clip_max_s = None
    if not profile.clipsmith_config:
        blocking.append("Profilen saknar clipsmith_config.")
    else:
        try:  # bara en .yaml-fil i projektmappen, och felets text citerar aldrig filen
            cfg = read_config(config_file(settings.root, profile.clipsmith_config))
        except ValueError as error:
            blocking.append(str(error))
        else:
            channel = cfg.channels[0] if cfg.channels else channel
            clip_min_s, clip_max_s = cfg.clip.min_seconds, cfg.clip.max_seconds
            if cfg.reframe.mode == "stacked" and not (cfg.reframe.webcam_rect and cfg.reframe.gameplay_rect):
                blocking.append("Facecam- och spelrutan är inte inställda. Dra dem under Facecam.")
    if not settings.berget_api_key:
        blocking.append("BERGET_API_KEY saknas i .env.")
    if not settings.anthropic_api_key:
        blocking.append("ANTHROPIC_API_KEY saknas i .env.")
    return {
        "name": name,
        "streamer": profile.streamer,
        "campaign": profile.raw.get("campaign") or profile.raw.get("name") or name,
        "language": profile.language,
        "channel": channel,
        "clip_min_s": clip_min_s,
        "clip_max_s": clip_max_s,
        "formats": [{"id": f["id"], "name": f.get("name", f["id"]),
                     "description": f.get("description", "")} for f in profile.formats],
        "problems": blocking + problems,
        "ready": not blocking,
    }


@router.get("/profiles", summary="Kampanjer som går att köra")
def list_profiles(settings: Settings = Depends(get_settings)) -> list[dict]:
    result = []
    for name in _profile_names(settings):
        try:
            result.append(_describe_profile(name, settings))
        except (SystemExit, Exception) as error:  # en trasig fil ska inte dölja de andra kampanjerna
            result.append({"name": name, "streamer": "", "campaign": name, "language": "",
                           "channel": "", "clip_min_s": None, "clip_max_s": None,
                           "formats": [], "ready": False,
                           "problems": [f"Profilen går inte att läsa: {error}"]})
    return result


# ---------- VOD ----------


def _mp4(vod_id: str, settings: Settings) -> Path:
    return settings.work_dir / vod_id / f"{vod_id}.mp4"


def _facts(vod_id: str, fresh: bool = False) -> dict:
    try:
        return vod_facts(vod_id, fresh)
    except VodNotFound as error:
        raise HTTPException(404, f"Hittar ingen VOD med id {vod_id} på Twitch.") from error
    except Exception as error:  # twitch-dl kastar olika fel när Twitch inte svarar
        raise HTTPException(502, "Twitch svarar inte. Försök igen om en stund.") from error


def _estimate(facts: dict, settings: Settings) -> dict:
    downloaded = _mp4(facts["vod_id"], settings).exists()
    free_gb, _ = estimate.disk_gb(settings.work_dir)
    return {"downloaded": downloaded,
            "estimate": estimate.estimate(facts["duration_s"], free_gb, downloaded)}


@router.get("/vod/{vod_id}", summary="Fakta om en Twitch-VOD före körningen")
def vod_info(vod_id: str = PathParam(pattern=r"^\d{6,15}$"),
             settings: Settings = Depends(get_settings)) -> dict:
    facts = _facts(vod_id)
    campaigns = [p["name"] for p in list_profiles(settings)
                 if p["channel"] and p["channel"].lower() == facts["channel"]]
    return {**facts, **_estimate(facts, settings), "campaigns": campaigns}


# ---------- körningar ----------


class CampaignRunCreate(BaseModel):
    profile: str
    vod_id: str = Field(pattern=r"^\d{6,15}$")
    game: str | None = None
    start_s: float = 0.0
    end_s: float = 0.0
    focus: str = Field(default="", max_length=300)
    min_s: int | None = None
    max_s: int | None = None


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
    ready = _describe_profile(profile.name, settings)
    if not ready["ready"]:
        raise HTTPException(400, " ".join(ready["problems"]))

    low, high = ready["clip_min_s"], ready["clip_max_s"]
    min_s, max_s = body.min_s or low, body.max_s or high
    if not low <= min_s <= max_s <= high:
        raise HTTPException(400, f"Klippens längd måste ligga mellan {low} och {high} sekunder, "
                                 "och den kortaste längden får inte vara längre än den längsta.")
    facts = _facts(body.vod_id, fresh=True)  # färska fakta, så att diskkollen har rätt längd
    if not facts["channel"] or facts["channel"] != ready["channel"].lower():
        raise HTTPException(400, f"VOD:en är från {facts['channel_name'] or 'en okänd kanal'}, inte från "
                                 f"kampanjens kanal {ready['channel']}. Kampanjen får bara använda "
                                 "streamerns egna streams.")
    space = _estimate(facts, settings)
    if not space["estimate"]["fits"]:
        need = space["estimate"]["download_gb"] * 1.1 + 1
        raise HTTPException(400, f"Disken räcker inte. VOD:en behöver ungefär {need:.1f} GB med marginal, "
                                 f"och det finns {space['estimate']['disk_free_gb']} GB ledigt.")

    focus = body.focus.strip()
    game = (body.game or "").strip() or None
    with _start_lock:
        if request.app.state.active_run_id is not None:
            raise HTTPException(429, "En körning pågår redan. Vänta tills den är klar.")
        if editor.lock.locked():  # redigeraren kollar active_run_id under samma lås
            raise HTTPException(409, "Ett klipp redigeras just nu. Vänta tills redigeringen är klar.")
        run = Run(vod_id=body.vod_id, channel=ready["channel"], status=RunStatus.pending)
        db.add(run)
        db.commit()
        db.refresh(run)
        request.app.state.active_run_id = run.id
    _log(settings).add_run(run.id, body.vod_id, profile.name, game, focus=focus or None,
                           min_s=min_s, max_s=max_s, title=facts["title"],
                           channel_name=facts["channel_name"], thumbnail_url=facts["thumbnail_url"],
                           duration_s=facts["duration_s"], start_s=body.start_s, end_s=body.end_s)
    background_tasks.add_task(run_campaign_vod, run.id, body.vod_id, profile.name, game, settings,
                              start_s=body.start_s, end_s=body.end_s, focus=focus, min_s=min_s,
                              max_s=max_s, app=request.app)
    return run.to_dict()


@router.post("/runs/{run_id}/retry", status_code=201, summary="Försök igen med en misslyckad körning",
             dependencies=[Depends(verify_api_key)])
def retry_run(
    run_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    """En ny körning med samma kampanj, VOD och val. Samma kontroller som en ny körning."""
    meta = _log(settings).run(run_id)
    if meta is None:
        raise HTTPException(404, "Körningen finns inte.")
    run = db.get(Run, run_id)
    if run is not None and run.status != RunStatus.failed:  # saknas den i clipsmiths databas visas den som failed
        raise HTTPException(400, "Bara en misslyckad körning kan köras igen.")
    body = CampaignRunCreate(profile=meta["profile"], vod_id=meta["vod_id"], game=meta["game"],
                             start_s=meta.get("start_s") or 0.0, end_s=meta.get("end_s") or 0.0,
                             focus=meta["focus"] or "", min_s=meta["min_s"], max_s=meta["max_s"])
    return create_run(body, request, background_tasks, db, settings)


def _run_clips(meta: dict, clips: list[dict]) -> list[dict]:
    """Klippen från en körning. shortcut: klipploggen vet inte run_id, så det är klippen från samma
    VOD och kampanj. Två körningar av samma VOD delar klipp; spara run_id per klipp om det behövs."""
    return [c for c in clips if c["vod_id"] == meta["vod_id"] and c["profile"] == meta["profile"]]


def _with_facts(meta: dict, log: ClipLog) -> dict:
    """Körningar från före VOD-fakta: hämta och spara dem. Svarar inte Twitch visas körningen som förut."""
    if meta["title"] is not None:
        return meta
    try:
        facts = vod_facts(meta["vod_id"])
    except Exception:  # twitch-dl kastar olika fel; utan fakta visar webben VOD-numret
        return meta
    fields = {key: facts[key] for key in ("title", "channel_name", "thumbnail_url", "duration_s")}
    log.set_run_facts(meta["run_id"], **fields)
    return {**meta, **fields}


def _describe_run(meta: dict, db: Session, clips: list[dict], steps: bool = False) -> dict:
    run = db.get(Run, meta["run_id"])
    if run is None:
        status, error, created, events = "failed", "Körningen saknas i clipsmiths databas.", None, []
    else:
        status, error, created = run.status.value, run.error, run.created_at.isoformat()
        events = [(e.stage, e.pct, e.created_at) for e in db.query(PipelineEvent)
                  .filter(PipelineEvent.run_id == run.id).order_by(PipelineEvent.id)]
    progress = estimate.progress(status, events, meta["duration_s"])
    counts: dict[str, int] = {}
    for clip in _run_clips(meta, clips):
        counts[clip["status"]] = counts.get(clip["status"], 0) + 1
    out = {key: meta[key] for key in ("run_id", "vod_id", "profile", "game", "focus", "min_s", "max_s",
                                      "title", "channel_name", "thumbnail_url", "duration_s")}
    out.update(start_s=meta.get("start_s") or 0, end_s=meta.get("end_s") or 0)  # 0 för gamla körningar
    out.update(created=meta["created"] or created, status=status, stage=progress["stage"],
               pct=progress["pct"], error=error, clip_counts=counts)
    if steps:
        out.update(steps=progress["steps"], eta_s=progress["eta_s"],
                   cost_estimate_usd=estimate.cost_usd(meta["duration_s"]))
    return out


@router.get("/runs", summary="Projekten, nyast först")
def list_runs(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> list[dict]:
    log = _log(settings)
    clips = log.list()
    return [_describe_run(_with_facts(meta, log), db, clips) for meta in reversed(log.runs())]


@router.get("/runs/{run_id}", summary="En körning med förloppet per steg")
def get_run(run_id: int, db: Session = Depends(get_db),
            settings: Settings = Depends(get_settings)) -> dict:
    log = _log(settings)
    meta = log.run(run_id)
    if meta is None:
        raise HTTPException(404, "Körningen finns inte.")
    return _describe_run(_with_facts(meta, log), db, log.list(), steps=True)


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
    with _start_lock:
        if request.app.state.active_run_id is not None:
            raise HTTPException(429, "En körning pågår redan. Vänta tills den är klar.")
        if editor.lock.locked():  # redigeraren kollar active_run_id under samma lås
            raise HTTPException(409, "Ett klipp redigeras just nu. Vänta tills redigeringen är klar.")
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
    out["reason"] = clip["reason"] or ""
    out["engagement"] = ClipLog.engagement(clip)
    try:
        profile = load_profile(clip["profile"], settings.profiles_dir)
        out["engagement_min"] = profile.engagement_min(clip["views"] or 0)
    except SystemExit:
        out["engagement_min"] = None
    # ?v= byts när klippet ändras, så att webbläsaren inte visar en gammal version efter en redigering.
    out["video_url"] = f"/campaign/clips/{clip['id']}/file?v={quote(clip.get('updated') or '')}"
    return out


def _get(log: ClipLog, clip_id: int) -> dict:
    try:
        return log.get(clip_id)
    except SystemExit as error:
        raise HTTPException(404, "Klippet finns inte.") from error


@router.get("/clips", summary="Klipp i klipploggen")
def list_clips(status: str | None = None, vod_id: str | None = None, run_id: int | None = None,
               settings: Settings = Depends(get_settings)) -> list[dict]:
    log = _log(settings)
    clips = log.list(status or None)
    if vod_id:
        clips = [c for c in clips if c["vod_id"] == vod_id]
    if run_id is not None:
        meta = log.run(run_id)
        clips = _run_clips(meta, clips) if meta else []
    return [_public(c, settings) for c in clips]


@router.get("/clips/{clip_id}/transcript", summary="Det som sägs i klippet")
def clip_transcript(clip_id: int, settings: Settings = Depends(get_settings)) -> dict:
    clip = _get(_log(settings), clip_id)
    path = settings.work_dir / clip["vod_id"] / "transcript.json"
    start, end = clip["start_s"], clip["end_s"]
    if start is None or end is None or not path.exists():
        return {"text": "", "segments": []}
    try:
        segments = [{"start": s["start"], "end": s["end"], "text": s["text"].strip()}
                    for s in json.loads(path.read_text(encoding="utf-8")).get("segments", [])
                    if s["end"] > start and s["start"] < end]
    except (ValueError, KeyError, TypeError, AttributeError):  # trasig fil: visa inget i stället för 500
        return {"text": "", "segments": []}
    return {"text": " ".join(s["text"] for s in segments), "segments": segments}


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
    # Status och caption kan byta klippets status, så de väntar inte in en redigering som pågår.
    changes_status = bool({"status", "caption"} & body.model_fields_set)
    if changes_status and not editor.lock.acquire(blocking=False):
        raise HTTPException(409, "Klippet redigeras just nu. Försök igen när redigeringen är klar.")
    try:
        return _patch_clip(clip_id, body, settings)
    finally:
        if changes_status:
            editor.lock.release()


def _patch_clip(clip_id: int, body: ClipPatch, settings: Settings) -> dict:
    log = _log(settings)
    clip = _get(log, clip_id)
    profile = _profile(clip["profile"], settings)
    fields = body.model_dump(exclude_unset=True)
    if "status" in fields and fields["status"] is None:
        raise HTTPException(400, "Status får inte vara tom.")

    if "format" in fields and profile.formats and fields["format"] not in profile.format_ids:
        raise HTTPException(400, f"Formatet {fields['format']!r} finns inte i kampanjen.")
    if "platform" in fields and fields["platform"] not in PLATFORMS:
        raise HTTPException(400, "Plattformen måste vara tiktok, youtube_shorts eller instagram_reels.")
    if "url" in fields and fields["url"] and not fields["url"].startswith(("https://", "http://")):
        raise HTTPException(400, "Länken måste börja med https://.")

    problems = clip["problems"] or []
    status = clip["status"]
    if "caption" in fields:
        if fields["caption"] != clip["caption"] and body.status not in (None, status, "nekad"):
            raise HTTPException(400, "Spara captionen först. Sedan behövs ett nytt ja.")
        problems = _caption_problems(problems, fields["caption"], profile)
        fields["problems"] = problems
        # Ett ja gäller den caption som fanns. Ändras den måste klippet godkännas igen.
        if status in ("renderad", "underkand", "godkand") and body.status in (None, status):
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
            if problems:
                raise HTTPException(400, "Klippet har problem. Det får inte postas.")
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
    free_gb, total_gb = estimate.disk_gb(settings.work_dir)
    return {"budget": budget_from_settings(settings).summary(), "counts": counts, "todo": log.todo(),
            "disk": {"free_gb": free_gb, "total_gb": total_gb}}
