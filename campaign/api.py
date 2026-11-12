"""Kampanjlagrets vägar i clipsmiths server. Webbappen använder dem via /api/campaign/..."""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from clipsmith.api.deps import get_db, verify_api_key
from clipsmith.db.models import PipelineEvent, Run, RunStatus

from . import editor, estimate, streams
from .budget import BudgetExceeded, budget_from_settings
from .caption import check_caption
from .cliplog import POST_STATUSES, STAT_KEYS, STATUSES, ClipLog
from .config import Settings, load_settings
from .media import need, probe, run
from .pipeline import finish_run, run_campaign_vod
from .profile import Profile, load_profile
from .vodinfo import VodNotFound, vod_facts

router = APIRouter(prefix="/campaign", tags=["campaign"])
logger = logging.getLogger(__name__)
_start_lock = threading.Lock()  # bara en körning i taget, också när två anrop kommer samtidigt

PLATFORMS = ("tiktok", "youtube_shorts", "instagram_reels")

# Vilka steg ett klipp får ta. Från underkand kommer man bara vidare genom att rätta problemen.
# Efter postningen går det att ta ett steg bakåt, så att ett felklick går att ångra.
NEXT_STATUS: dict[str, set[str]] = {
    "renderad": {"godkand", "nekad"},
    "underkand": {"nekad"},
    "godkand": {"postad", "nekad", "renderad"},
    "nekad": {"renderad"},
    "postad": {"inskickad", "godkand"},
    "inskickad": {"analytics_inskickad", "betald", "postad"},
    "analytics_inskickad": {"betald", "inskickad"},
    "betald": {"inskickad", "analytics_inskickad"},
}

# Postlänkens domän per plattform.
PLATFORM_HOSTS = {"tiktok": ("tiktok.com",), "youtube_shorts": ("youtube.com", "youtu.be"),
                  "instagram_reels": ("instagram.com",)}
PLATFORM_NAMES = {"tiktok": "TikTok", "youtube_shorts": "YouTube Shorts", "instagram_reels": "Instagram Reels"}


def _link_problem(link: str, platform: str | None) -> str | None:
    """Felet i postlänken, eller None. En riktig https-adress, på plattformens domän om den är vald."""
    parts = urlparse(link)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or "." not in host:
        return "Länken måste vara en hel adress som börjar med https://, till exempel https://www.tiktok.com/@du/video/123."
    hosts = PLATFORM_HOSTS.get(platform or "", ())
    if hosts and not any(host == h or host.endswith("." + h) for h in hosts):
        return f"Länken går till {host}, men plattformen är {PLATFORM_NAMES.get(platform or "", platform)}. Kontrollera länken och plattformen."
    return None


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


def _turned_off(settings: Settings) -> set[str]:
    """Kampanjer med active: false. Deras streams, projekt, klipp och siffror döljs i appen."""
    off = set()
    for name in _profile_names(settings):
        try:
            active = load_profile(name, settings.profiles_dir).active
        except (SystemExit, Exception):  # en trasig fil räknas som på, så att den syns och kan rättas
            active = True
        if not active:
            off.add(name)
    return off


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
        "active": profile.active,
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
                           "formats": [], "ready": False, "active": True,
                           "problems": [f"Profilen går inte att läsa: {error}"]})
    return result


# ---------- VOD ----------


LIVE = "Streamen pågår. Vänta tills den är slut."
SSD_MISSING = "SSD:n är inte ansluten."


def _mp4(vod_id: str, settings: Settings) -> Path:
    return settings.work_dir / vod_id / f"{vod_id}.mp4"


def _facts(vod_id: str, fresh: bool = False) -> dict:
    try:
        return vod_facts(vod_id, fresh)
    except VodNotFound as error:
        raise HTTPException(404, f"Hittar ingen VOD med id {vod_id} på Twitch.") from error
    except Exception as error:  # twitch-dl kastar olika fel när Twitch inte svarar
        raise HTTPException(502, "Twitch svarar inte. Försök igen om en stund.") from error


def storage(settings: Settings) -> dict:
    """Ledigt utrymme där work/ ligger. work/ och out/ kan vara länkar till SSD:n. En trasig länk betyder
    att SSD:n inte sitter i: då visar appen det, och ingen körning startar."""
    if any(path.is_symlink() and not path.exists() for path in (settings.work_dir, settings.out_dir)):
        return {"free_gb": 0.0, "total_gb": 0.0, "connected": False, "problem": SSD_MISSING}
    free_gb, total_gb = estimate.disk_gb(settings.work_dir)  # disk_gb följer länken till SSD:n
    return {"free_gb": free_gb, "total_gb": total_gb, "connected": True, "problem": None}


def _downloaded(facts: dict, settings: Settings) -> bool:
    """Hela VOD:en finns på disken. En fil som hämtades medan streamen pågick är kortare och räknas inte."""
    mp4 = _mp4(facts["vod_id"], settings)
    if not mp4.exists():
        return False
    try:
        return probe(mp4)["duration"] >= facts["duration_s"] - 60
    except RuntimeError:  # ffprobe kan inte läsa filen: den är trasig eller halv
        return False


def _remove_half_download(vod_id: str, settings: Settings) -> None:
    """En halv video och det som gjordes av den. Körningen hämtar och gör om allt."""
    folder = settings.work_dir / vod_id
    for name in (f"{vod_id}.mp4", "transcript.json", "chat.json"):
        (folder / name).unlink(missing_ok=True)
    shutil.rmtree(folder / "berget", ignore_errors=True)


def _estimate(facts: dict, settings: Settings, disk: dict) -> dict:
    downloaded = _downloaded(facts, settings)
    return {"downloaded": downloaded,
            "estimate": estimate.estimate(facts["duration_s"], disk["free_gb"], downloaded)}


@router.get("/vod/{vod_id}", summary="Fakta om en Twitch-VOD före körningen")
def vod_info(vod_id: str = PathParam(pattern=r"^\d{6,15}$"),
             settings: Settings = Depends(get_settings)) -> dict:
    facts = _facts(vod_id)
    disk = storage(settings)
    problems = [text for text, hit in ((LIVE, facts["live"]), (SSD_MISSING, not disk["connected"])) if hit]
    campaigns = [p["name"] for p in list_profiles(settings)
                 if p["active"] and p["channel"] and p["channel"].lower() == facts["channel"]]
    return {**facts, **_estimate(facts, settings, disk), "campaigns": campaigns, "problems": problems}


@router.get("/streams", summary="De senaste streamsen för kampanjernas kanaler")
def list_streams(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> list[dict]:
    campaigns: dict[str, list[str]] = {}
    games: dict[str, set[str]] = {}  # kanal -> kampanjernas preferred_games, små bokstäver
    for profile in list_profiles(settings):
        if profile["active"] and profile["channel"]:
            channel = profile["channel"].lower()
            campaigns.setdefault(channel, []).append(profile["name"])
            try:
                preferred = load_profile(profile["name"], settings.profiles_dir).preferred_games
            except (SystemExit, Exception):  # en trasig profil syns redan i list_profiles; den ger inga spel
                preferred = []
            games.setdefault(channel, set()).update(g.lower() for g in preferred if g)
    found, failed = [], 0
    for channel, names in campaigns.items():
        try:
            found += [{**s, "campaigns": names} for s in streams.score(streams.channel_streams(channel), games[channel])]
        except Exception as error:  # twitch-dl kastar olika fel när Twitch inte svarar
            logger.warning("Twitch svarade inte för kanalen %s: %s", channel, error)
            failed += 1
    if campaigns and failed == len(campaigns):
        raise HTTPException(502, "Twitch svarar inte. Försök igen om en stund.")
    log = _log(settings)
    clips = log.list()
    latest = {meta["vod_id"]: meta for meta in log.runs()}  # runs() är i ordning, så den sista vinner
    for stream in found:
        meta = latest.get(stream["vod_id"])
        run_row = db.get(Run, meta["run_id"]) if meta else None
        stream.update(run_id=meta["run_id"] if meta else None,
                      run_status=(run_row.status.value if run_row else "failed") if meta else None,
                      clip_count=len(_run_clips(meta, clips)) if meta else 0)
    streams.recommend(found)
    return sorted(found, key=lambda s: s["created_at"] or "", reverse=True)


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
    disk = storage(settings)
    if not disk["connected"]:
        raise HTTPException(400, SSD_MISSING)
    try:
        budget_from_settings(settings).check()  # dags- och månadstaket, innan 15 GB hämtas
    except BudgetExceeded as error:
        raise HTTPException(400, f"{error} Körningen startar inte. Vänta tills taket går över, "
                                 "eller höj det i .env.") from error
    facts = _facts(body.vod_id, fresh=True)  # färska fakta, så att diskkollen har rätt längd
    if facts["live"]:
        raise HTTPException(400, LIVE)
    if not facts["channel"] or facts["channel"] != ready["channel"].lower():
        raise HTTPException(400, f"VOD:en är från {facts['channel_name'] or 'en okänd kanal'}, inte från "
                                 f"kampanjens kanal {ready['channel']}. Kampanjen får bara använda "
                                 "streamerns egna streams.")
    space = _estimate(facts, settings, disk)
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
        # Kollas igen här, där ingen annan körning kan skriva filen: en halv video används aldrig.
        if _mp4(body.vod_id, settings).exists() and not _downloaded(facts, settings):
            _remove_half_download(body.vod_id, settings)
        run = Run(vod_id=body.vod_id, channel=ready["channel"], status=RunStatus.pending)
        db.add(run)
        db.commit()
        db.refresh(run)
        request.app.state.active_run_id = run.id
    try:
        _log(settings).add_run(run.id, body.vod_id, profile.name, game, focus=focus or None,
                               min_s=min_s, max_s=max_s, title=facts["title"],
                               channel_name=facts["channel_name"], thumbnail_url=facts["thumbnail_url"],
                               duration_s=facts["duration_s"], start_s=body.start_s, end_s=body.end_s)
    except Exception:  # annars står körningen som pågående, och appen säger 429 tills servern startas om
        run.status, run.error = RunStatus.failed, "Körningen kunde inte sparas i klipploggen. Försök igen."
        db.commit()
        request.app.state.active_run_id = None
        raise
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
        events = [(e.stage, e.pct, e.created_at, e.message) for e in db.query(PipelineEvent)
                  .filter(PipelineEvent.run_id == run.id).order_by(PipelineEvent.id)]
    progress = estimate.progress(status, events, meta["duration_s"])
    counts: dict[str, int] = {}
    for clip in _run_clips(meta, clips):
        counts[clip["status"]] = counts.get(clip["status"], 0) + 1
    out = {key: meta[key] for key in ("run_id", "vod_id", "profile", "game", "focus", "min_s", "max_s",
                                      "title", "channel_name", "thumbnail_url", "duration_s")}
    out.update(start_s=meta.get("start_s") or 0, end_s=meta.get("end_s") or 0)  # 0 för gamla körningar
    out.update(created=meta["created"] or created, status=status, stage=progress["stage"],
               pct=progress["pct"], message=progress["message"], error=error, clip_counts=counts)
    if steps:
        out.update(steps=progress["steps"], eta_s=progress["eta_s"],
                   cost_estimate_usd=estimate.cost_usd(meta["duration_s"]))
    return out


@router.get("/runs", summary="Projekten, ett per stream och kampanj, nyast först")
def list_runs(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> list[dict]:
    log = _log(settings)
    clips = log.list()
    off = _turned_off(settings)
    # Ett projekt per stream och kampanj: Kör igen och en ny körning på samma VOD visas som samma projekt,
    # med den senaste körningen. Klippen delas redan per VOD och kampanj (_run_clips).
    latest = {(meta["vod_id"], meta["profile"]): meta for meta in log.runs() if meta["profile"] not in off}
    return [_describe_run(_with_facts(meta, log), db, clips)
            for meta in sorted(latest.values(), key=lambda m: m["run_id"], reverse=True)]


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
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    meta = _log(settings).run(run_id)
    if meta is None:
        raise HTTPException(404, "Körningen hör inte till någon kampanj.")
    run = db.get(Run, run_id)
    if run is not None and run.status == RunStatus.failed:
        raise HTTPException(400, "Körningen misslyckades. Tryck Kör igen.")
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


REVIEW_PREFIX = "Bildgranskning: "


def _public(clip: dict, settings: Settings) -> dict:
    out = {k: v for k, v in clip.items() if k != "file"}
    out["filename"] = Path(clip["file"] or "").name
    out["problems"] = clip["problems"] or []
    # Äldre klipp har bildgranskningens beskrivningar som varningar. De visas med bildgranskningen i stället.
    old_notes = [w.removeprefix(REVIEW_PREFIX) for w in clip["warnings"] or [] if w.startswith(REVIEW_PREFIX)]
    out["warnings"] = [w for w in clip["warnings"] or [] if not w.startswith(REVIEW_PREFIX)]
    if old_notes:
        review = dict(clip["review"] or {})
        review["notes"] = [*(review.get("notes") or []), *old_notes]
        out["review"] = review
    out["signals"] = clip["signals"] or []
    out["reason"] = clip["reason"] or ""
    out["engagement"] = ClipLog.engagement(clip)
    try:
        profile = load_profile(clip["profile"], settings.profiles_dir)
        out["engagement_min"] = profile.engagement_min(clip["views"] or 0)
        out["min_views"] = profile.min_views
    except SystemExit:
        out["engagement_min"] = None
        out["min_views"] = None
    # ?v= byts när klippet ändras, så att webbläsaren inte visar en gammal version efter en redigering.
    version = quote(clip.get("updated") or "")
    out["posts"] = _public_posts(clip, settings)
    out["video_url"] = f"/campaign/clips/{clip['id']}/file?v={version}"
    out["poster_url"] = f"/campaign/clips/{clip['id']}/poster?v={version}"
    return out


def _public_posts(clip: dict, settings: Settings) -> list[dict]:
    """Klippets inlägg med engagemang och uppskattad intäkt per inlägg."""
    try:
        payment = load_profile(clip["profile"], settings.profiles_dir).payment
    except (SystemExit, Exception):  # kampanjen finns inte längre: ingen uppskattning
        payment = {}
    return [{**post, "engagement": ClipLog.engagement(post),
             "estimate_usd": _payout(post["views"] or 0, payment) if post["views"] else None}
            for post in _log(settings).posts(clip["id"])]


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
    else:  # en direktlänk till ett projekt visar dess klipp även när kampanjen är avstängd
        off = _turned_off(settings)
        clips = [c for c in clips if c["profile"] not in off]
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


def _clip_path(clip_id: int, settings: Settings) -> Path:
    """Klippets fil. Bara en .mp4 under out/, så att ingen annan fil kan hämtas via klipploggen."""
    clip = _get(_log(settings), clip_id)
    path = Path(clip["file"] or "").resolve()
    if not path.is_relative_to(settings.out_dir.resolve()) or path.suffix != ".mp4" or not path.exists():
        raise HTTPException(404, "Filen finns inte.")
    return path


@router.get("/clips/{clip_id}/poster", summary="En bild ur klippet, 360 bred")
def clip_poster(clip_id: int, settings: Settings = Depends(get_settings)) -> FileResponse:
    path = _clip_path(clip_id, settings)
    poster = path.with_name(f".{path.stem}.poster.jpg")
    if not poster.exists() or poster.stat().st_mtime_ns < path.stat().st_mtime_ns:
        tmp = path.with_name(f".{path.stem}.{uuid.uuid4().hex}.tmp.jpg")  # två anrop samtidigt krockar inte
        try:
            run([need("ffmpeg"), "-y", "-v", "error", "-ss", "1", "-i", str(path), "-frames:v", "1",
                 "-vf", "scale=360:-2", "-q:v", "4", str(tmp)])
            os.replace(tmp, poster)
        except (RuntimeError, OSError, SystemExit) as error:  # SystemExit från need() när ffmpeg saknas
            raise HTTPException(500, "Kunde inte göra en bild av klippet.") from error
        finally:
            tmp.unlink(missing_ok=True)
    return FileResponse(poster, media_type="image/jpeg")


@router.get("/clips/{clip_id}/file", summary="Spela upp eller ladda ner ett klipp")
def clip_file(clip_id: int, download: bool = False,
              settings: Settings = Depends(get_settings)) -> FileResponse:
    path = _clip_path(clip_id, settings)
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
        if editor.editing == clip_id:
            raise HTTPException(409, "Klippet redigeras just nu. Försök igen när redigeringen är klar.")
        changes_status = False  # låset gäller ett annat klipp: det här klippet rörs inte av redigeringen
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
    if "url" in fields and fields["url"]:
        problem = _link_problem(fields["url"], fields.get("platform") or clip["platform"])
        if problem:
            raise HTTPException(400, problem)

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
        if new == "analytics_inskickad":
            _check_min_views(fields.get("views", clip["views"]), profile)

    _apply_to_posts(log, clip, fields)
    if fields:
        log.update(clip_id, **fields)
    return _public(log.get(clip_id), settings)


def _check_min_views(views: int | None, profile: Profile) -> None:
    """Statistik inskickad går först när inlägget har nått kampanjens min_views."""
    if (views or 0) < profile.min_views:
        raise HTTPException(400, f"Statistiken skickas in först vid {profile.min_views} visningar. "
                                 f"Inlägget har {views or 0}.")


def _apply_to_posts(log: ClipLog, clip: dict, fields: dict) -> None:
    """Ändringar på klippnivå som egentligen gäller inläggen. Ett klipp med ett inlägg fungerar som förut.
    Det som gäller inläggen tas bort ur `fields`; klippets värden räknas sedan fram ur inläggen."""
    posts = log.posts(clip["id"])
    new = fields.get("status")
    if new == "postad" and clip["status"] == "godkand":  # första inlägget, som förut med en länk
        log.add_post(clip["id"], fields.pop("platform", None) or clip["platform"], fields.pop("url", None) or clip["url"])
        fields.pop("status")
    elif new == "godkand" and clip["status"] == "postad":  # ångra postningen: inläggen tas bort
        for post in posts:
            log.delete_post(post["id"])
        fields.pop("status")
        # Länken står kvar i klippet, så att samma postning går att göra igen med ett klick.
        fields.setdefault("url", clip["url"])
        fields.setdefault("platform", clip["platform"])
        posts = []
    elif new in POST_STATUSES and posts:  # ett steg för klippet gäller alla dess inlägg
        for post in posts:
            log.update_post(post["id"], status=new)
        fields.pop("status")
    link = {key: fields.pop(key) for key in ("url", "platform") if key in fields}
    stats = {key: fields.pop(key) for key in STAT_KEYS if key in fields}
    if (link or stats) and posts:
        if len(posts) > 1:
            raise HTTPException(400, "Klippet har flera inlägg. Ändra länk och siffror per inlägg.")
        log.update_post(posts[0]["id"], **link, **stats)
    elif link or stats:
        fields.update(link, **stats)


# ---------- översikt ----------


@router.get("/summary", summary="Budget, antal klipp per status och att göra")
def summary(settings: Settings = Depends(get_settings)) -> dict:
    log = _log(settings)
    off = _turned_off(settings)
    clips = [c for c in log.list() if c["profile"] not in off]
    counts: dict[str, int] = {}
    for clip in clips:
        counts[clip["status"]] = counts.get(clip["status"], 0) + 1
    # Budgeten gäller alla kampanjer, så den räknas som förut.
    return {"budget": budget_from_settings(settings).summary(), "counts": counts, "todo": log.todo(skip=off),
            "disk": storage(settings), "earnings": _earnings(clips, settings)}


def _payout(views: int, payment: dict) -> float:
    """Vad ett klipp tjänar enligt kampanjens payment: 0 under min_views, visningar / 1000 * cpm_usd,
    högst max_payout_per_clip_usd, och 0 under min_payout_usd."""
    if views < int(payment.get("min_views") or 0):
        return 0.0
    usd = views / 1000 * float(payment.get("cpm_usd") or 0)
    if payment.get("max_payout_per_clip_usd") is not None:
        usd = min(usd, float(payment["max_payout_per_clip_usd"]))
    return round(usd, 2) if usd >= float(payment.get("min_payout_usd") or 0) else 0.0


def _earnings(clips: list[dict], settings: Settings) -> dict:
    payments: dict[str, dict] = {}
    per_clip, months, paid = [], {}, 0.0
    for clip in clips:
        if not clip["views"]:
            continue
        if clip["profile"] not in payments:
            try:
                payments[clip["profile"]] = load_profile(clip["profile"], settings.profiles_dir).payment
            except (SystemExit, Exception):  # kampanjen finns inte längre: inget att räkna med
                payments[clip["profile"]] = {}
        usd = _payout(clip["views"], payments[clip["profile"]])
        per_clip.append({"id": clip["id"], "views": clip["views"], "usd": usd})
        if clip["status"] == "betald":
            paid += usd
        if clip["created"]:
            months[clip["created"][:7]] = months.get(clip["created"][:7], 0.0) + usd
    return {"estimated_usd": round(sum(c["usd"] for c in per_clip), 2), "paid_usd": round(paid, 2),
            "by_month": [{"month": m, "usd": round(usd, 2)} for m, usd in sorted(months.items())],
            "per_clip": per_clip}


# Vägarna för inlägg ligger i posts_api.py och registreras på samma router.
from . import posts_api  # noqa: E402,F401
