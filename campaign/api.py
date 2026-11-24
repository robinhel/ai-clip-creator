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
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from clipsmith.api.deps import get_db, verify_api_key
from clipsmith.db.models import Run, RunStatus

from . import editor, estimate, pipeline, preferences, runinfo, streams, vodfiles
from .budget import BudgetExceeded, budget_from_settings
from .caption import check_caption
from .cliplog import POST_STATUSES, STAT_KEYS, STATUSES, ClipLog, UploadRunning, card_runs
from .config import Settings, load_settings
from .media import need, probe, run
from .metrics import clip_length, reached_min_7d
from .pipeline import finish_run, run_campaign_vod
from .profile import PLATFORMS, Profile, load_profile
from .rules import NO_FORMAT
from .schedule import ScheduleError, _require_campaign
from .vodinfo import VodNotFound, vod_facts

router = APIRouter(prefix="/campaign", tags=["campaign"])
logger = logging.getLogger(__name__)
_start_lock = threading.Lock()  # bara en körning i taget, också när två anrop kommer samtidigt


# Vilka steg ett klipp får ta. Från underkand kommer man bara vidare genom att rätta problemen.
# Efter postningen går det att ta ett steg bakåt, så att ett felklick går att ångra.
NEXT_STATUS: dict[str, set[str]] = {
    "renderad": {"godkand", "nekad"},
    "underkand": {"nekad"},
    "godkand": {"postad", "postad_utan_lank", "nekad", "renderad"},
    "postad_utan_lank": {"godkand"},  # Ångra på Att posta
    "nekad": {"renderad", "godkand"},  # godkand: Ångra på Att posta; samma kontroller som ett nytt ja
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
        return "The link must be a full address starting with https://, for example https://www.tiktok.com/@you/video/123."
    hosts = PLATFORM_HOSTS.get(platform or "", ())
    if hosts and not any(host == h or host.endswith("." + h) for h in hosts):
        name = PLATFORM_NAMES.get(platform or "", platform)
        return f"The link goes to {host}, but the platform is {name}. Check the link and the platform."
    return None


def get_settings() -> Settings:
    return load_settings()


def _log(settings: Settings) -> ClipLog:
    return ClipLog(settings.data_dir / "klipp.db")


def _profile(name: str, settings: Settings) -> Profile:
    try:
        return load_profile(name, settings.profiles_dir)
    except (SystemExit, Exception) as error:
        raise HTTPException(400, f"The campaign {name} cannot be read.") from error


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

# Nycklarna gäller hela appen. De står i problems, så att en körning stoppas, och i key_problems, så att
# webben kan visa dem en gång i stället för på varje kampanj.
KEY_PROBLEMS = (("berget_api_key", "Berget key (BERGET_API_KEY) is missing. Add it under Settings."),
                ("anthropic_api_key", "Claude key (ANTHROPIC_API_KEY) is missing. Add it under Settings."))
# Profilens problem som gör varje klipp underkänt i regelkontrollen (campaign/rules.py).
FAILS_CLIPS = ("Overlay-bilden saknas", "on_screen_text.text är tom")


def campaign_state(blocking: list[str], problems: list[str]) -> str:
    """Kampanjens läge i ord, utan nycklarna. Kampanjer, kampanjsidan och Kontroll visar samma text."""
    if blocking:
        return "Inte redo"
    if not problems:
        return "Redo"
    if any(problem.startswith(FAILS_CLIPS) for problem in problems):
        return "Går att köra, men klippen blir underkända tills varningarna är rättade"
    return "Går att köra, men rätta varningarna i kampanjens inställningar först"


def _whole(seconds: float) -> float | int:
    return int(seconds) if float(seconds).is_integer() else seconds


def _describe_profile(name: str, settings: Settings, path: Path | None = None) -> dict:
    """Kampanjen som den visas i appen. `path` läser profilen från en annan fil, till exempel en tmp-fil
    som ska kontrolleras innan den ersätter profilen."""
    from .profile_edit import config_file, read_config

    profile = load_profile(path or name, settings.profiles_dir)
    problems = profile.problems(settings.root)
    blocking: list[str] = []
    channel = profile.streamer.lower()
    clip_min_s = clip_max_s = None
    mode, rects_set = None, False
    if not profile.clipsmith_config:
        blocking.append("The campaign has no clipsmith_config, the settings file. Add it under Advanced (YAML).")
    else:
        try:  # bara en .yaml-fil i projektmappen, och felets text citerar aldrig filen
            cfg = read_config(config_file(settings.root, profile.clipsmith_config))
        except ValueError as error:
            blocking.append(str(error))
        else:
            # Ingen kanal i config-filen: kampanjen använder bara egna videor, och Twitch frågas aldrig.
            channel = cfg.channels[0] if cfg.channels else ""
            clip_min_s, clip_max_s = cfg.clip.min_seconds, cfg.clip.max_seconds
            mode = cfg.reframe.mode
            rects_set = bool(cfg.reframe.webcam_rect and cfg.reframe.gameplay_rect)
            if mode == "stacked" and not rects_set:
                blocking.append("Facecam and game boxes are not set up. Drag them under Facecam.")
    keys = [text for field, text in KEY_PROBLEMS if not getattr(settings, field)]
    return {
        "name": name,
        "streamer": profile.streamer,
        "campaign": profile.raw.get("campaign") or profile.raw.get("name") or name,
        "language": profile.language,
        "channel": channel,
        "source": "twitch" if channel else "local",
        "clip_min_s": clip_min_s,
        "clip_max_s": clip_max_s,
        # Det kampanjen tillåter. Regelkontrollen använder det; clip_min_s och clip_max_s är det appen klipper.
        "allowed_min_s": _whole(profile.min_seconds),
        "allowed_max_s": _whole(profile.max_seconds),
        "formats": [{"id": f["id"], "name": f.get("name", f["id"]),
                     "description": f.get("description", "")} for f in profile.formats],
        "problems": blocking + keys + problems,
        "key_problems": keys,
        "state": campaign_state(blocking, problems),
        "ready": not (blocking or keys),
        "active": profile.active,
        "facecam": {"required": profile.facecam_required, "camera": profile.facecam_required or mode == "stacked",
                    "rects_set": rects_set},
        "scout_ref": profile.raw.get("scout_ref") if isinstance(profile.raw.get("scout_ref"), str) else None,
    }


@router.get("/profiles", summary="Campaigns that can run")
def list_profiles(settings: Settings = Depends(get_settings)) -> list[dict]:
    result = []
    for name in _profile_names(settings):
        try:
            result.append(_describe_profile(name, settings))
        except (SystemExit, Exception) as error:  # en trasig fil ska inte dölja de andra kampanjerna
            result.append({"name": name, "streamer": "", "campaign": name, "language": "",
                           "channel": "", "clip_min_s": None, "clip_max_s": None,
                           "formats": [], "ready": False, "active": True, "source": "twitch",
                           "problems": [f"The campaign file cannot be read: {error}"], "key_problems": [],
                           "state": "Inte redo"})
    return result


# ---------- VOD ----------


LIVE = "The stream is live. Wait until it ends."
SSD_MISSING = "The SSD is not connected."


def _mp4(vod_id: str, settings: Settings) -> Path:
    return settings.work_dir / vod_id / f"{vod_id}.mp4"


def _facts(vod_id: str, fresh: bool = False) -> dict:
    try:
        return vod_facts(vod_id, fresh)
    except VodNotFound as error:
        raise HTTPException(404, f"No VOD with ID {vod_id} found on Twitch.") from error
    except Exception as error:  # twitch-dl kastar olika fel när Twitch inte svarar
        raise HTTPException(502, "Twitch is not responding. Try again in a while.") from error


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


def _berget_price(settings: Settings) -> float | None:
    """Bergets pris per minut ljud för modellen i .env, från priser.yaml. None om det inte står där."""
    price = (budget_from_settings(settings).prices.get("transcription") or {}).get(settings.transcribe_model)
    return None if price is None else float(price)


def _estimate(facts: dict, settings: Settings, disk: dict) -> dict:
    downloaded = _downloaded(facts, settings)
    transcribed = (settings.work_dir / facts["vod_id"] / "transcript.json").exists()
    return {"downloaded": downloaded,
            "estimate": estimate.estimate(facts["duration_s"], disk["free_gb"], downloaded,
                                          _berget_price(settings), transcribed)}


@router.get("/vod/{vod_id}", summary="Facts about a Twitch VOD before the project starts")
def vod_info(vod_id: str = PathParam(pattern=r"^\d{6,15}$"),
             settings: Settings = Depends(get_settings)) -> dict:
    facts = _facts(vod_id)
    disk = storage(settings)
    problems = [text for text, hit in ((LIVE, facts["live"]), (SSD_MISSING, not disk["connected"])) if hit]
    campaigns = [p["name"] for p in list_profiles(settings)
                 if p["active"] and p["channel"] and p["channel"].lower() == facts["channel"]]
    return {**facts, **_estimate(facts, settings, disk), "campaigns": campaigns, "problems": problems,
            "fast_campaigns": [name for name in campaigns if runinfo.fast(name, settings)]}


@router.get("/streams", summary="The latest streams for the campaigns' channels")
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
        raise HTTPException(502, "Twitch is not responding. Try again in a while.")
    log = _log(settings)
    clips = log.list()
    latest = card_runs(log.runs(), lambda m: m["vod_id"])  # den senaste, men inte chattens om det finns en annan
    for stream in found:
        stream.update(runinfo.stream_run(latest.get(stream["vod_id"]), db, clips))
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


@router.post("/runs", status_code=201, summary="Start a project for a campaign",
             dependencies=[Depends(verify_api_key)])
def create_run(
    body: CampaignRunCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    if body.profile.startswith("_"):  # dold kampanj, som _ingen: bara livebevakningen kör den
        raise HTTPException(404, f"The campaign {body.profile} does not exist.")
    return _create_run(body, request, background_tasks, db, settings)


def _own_file_missing(vod_id: str, settings: Settings) -> str | None:
    """En körning från en egen fil (python -m campaign local) har filens namn som VOD-id. Den körs om
    från work/<id>/<id>.mp4. Finns filen inte kvar, kan appen inte köra den igen."""
    if vod_id.isdigit() or _mp4(vod_id, settings).exists():
        return None
    return (f"The project's source file no longer exists ({vod_id}.mp4 in work/), for example because it was deleted "
            "to free up space. Run again does not work. Run the file again with python -m campaign local.")


def _create_run(body: CampaignRunCreate, request: Request, background_tasks: BackgroundTasks, db: Session,
                settings: Settings, own_file: dict | None = None) -> dict:
    """own_file: klipploggens rad för en körning från en egen fil. Den körs från filen i work/, utan Twitch."""
    profile = _profile(body.profile, settings)
    ready = _describe_profile(profile.name, settings)
    if not ready["ready"]:
        raise HTTPException(400, " ".join(ready["problems"]))

    low, high = ready["clip_min_s"], ready["clip_max_s"]
    min_s, max_s = body.min_s or low, body.max_s or high
    if not low <= min_s <= max_s <= high:
        raise HTTPException(400, f"The clip length must be between {low} and {high} seconds, "
                                 "and the shortest length cannot be longer than the longest.")
    disk = storage(settings)
    if not disk["connected"]:
        raise HTTPException(400, SSD_MISSING)
    try:
        budget_from_settings(settings).check()  # dags- och månadstaket, innan 15 GB hämtas
    except BudgetExceeded as error:
        raise HTTPException(400, f"{error} The project does not start. Wait until the cap resets, "
                                 "or raise it in .env.") from error
    if own_file is not None:
        problem = _own_file_missing(body.vod_id, settings)
        if problem:
            raise HTTPException(409, problem)
        facts = {key: own_file[key] for key in ("title", "channel_name", "thumbnail_url", "duration_s")}
    else:
        facts = _facts(body.vod_id, fresh=True)  # färska fakta, så att diskkollen har rätt längd
        if facts["live"]:
            raise HTTPException(400, LIVE)
        if not facts["channel"] or facts["channel"] != ready["channel"].lower():
            raise HTTPException(400, f"The VOD is from {facts['channel_name'] or 'an unknown channel'}, not from "
                                     f"the campaign's channel {ready['channel']}. The campaign may only use "
                                     "the streamer's own streams.")
        space = _estimate(facts, settings, disk)
        if not space["estimate"]["fits"]:
            need = space["estimate"]["download_gb"] * 1.1 + 1
            raise HTTPException(400, f"Not enough disk. The VOD needs about {need:.1f} GB with margin, "
                                     f"and {space['estimate']['disk_free_gb']} GB is free.")

    focus = body.focus.strip()
    game = (body.game or "").strip() or None
    with _start_lock:
        if request.app.state.active_run_id is not None:
            raise HTTPException(429, "A project is already getting clips. Wait until it is done.")
        if editor.lock.locked():  # redigeraren kollar active_run_id under samma lås
            raise HTTPException(409, "A clip is being edited right now. Wait until the edit is done.")
        # Kollas igen här, där ingen annan körning kan skriva filen: en halv video används aldrig.
        if own_file is None and _mp4(body.vod_id, settings).exists() and not _downloaded(facts, settings):
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
        run.status, run.error = RunStatus.failed, "The project could not be saved in the clip log. Try again."
        db.commit()
        request.app.state.active_run_id = None
        raise
    background_tasks.add_task(run_campaign_vod, run.id, body.vod_id, profile.name, game, settings,
                              start_s=body.start_s, end_s=body.end_s, focus=focus, min_s=min_s,
                              max_s=max_s, local=own_file is not None, app=request.app)
    return run.to_dict()


@router.post("/runs/{run_id}/retry", status_code=201, summary="Run a failed project again",
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
        raise HTTPException(404, "The project does not exist.")
    run = db.get(Run, run_id)
    if run is not None and run.status != RunStatus.failed:  # saknas den i clipsmiths databas visas den som failed
        raise HTTPException(400, "Only a project that failed or was cancelled can run again.")
    values = dict(profile=meta["profile"], vod_id=meta["vod_id"], game=meta["game"],
                  start_s=meta.get("start_s") or 0.0, end_s=meta.get("end_s") or 0.0,
                  focus=meta["focus"] or "", min_s=meta["min_s"], max_s=meta["max_s"])
    if meta["vod_id"].isdigit():
        return _create_run(CampaignRunCreate(**values), request, background_tasks, db, settings)
    # En egen fil: VOD-id är filens namn och klarar inte kontrollen för Twitch-id.
    return _create_run(CampaignRunCreate.model_construct(**values), request, background_tasks, db, settings,
                       own_file=meta)


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


@router.get("/runs", summary="The projects, one per stream and campaign, newest first")
def list_runs(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> list[dict]:
    log = _log(settings)
    clips = log.list()
    off = _turned_off(settings)
    # Ett projekt per stream och kampanj: Kör igen och en ny körning på samma VOD visas som samma projekt,
    # med den senaste körningen. Klippen delas redan per VOD och kampanj (runinfo.run_clips). En körning från
    # chatten tar inte över kortet. Egna filer har också project_id: webben visar dem i kampanjens projekt,
    # men Klippen, Att posta och MCP-servern letar upp körningen här med vod_id och kampanj.
    latest = card_runs([meta for meta in log.runs() if meta["profile"] not in off],
                       lambda m: (m["vod_id"], m["profile"]))
    return [runinfo.describe(_with_facts(meta, log), db, clips, settings)[0]
            for meta in sorted(latest.values(), key=lambda m: m["run_id"], reverse=True)]


# ---------- projekt för egna filer ----------


def _describe_project(project: dict, log: ClipLog, db: Session, settings: Settings) -> dict:
    runs, clips = log.runs(), log.list()
    files = runinfo.project_files([m for m in runs if m["project_id"] == project["id"]], db, clips, settings)
    streamer = project["title"].removeprefix("Egna filer för ")  # titeln har streamerns namn
    return runinfo.describe_project(project, streamer, files, runinfo.project_clips(project, runs, clips))


@router.get("/projects", summary="The campaigns' projects for own files and uploads, latest first")
def list_projects(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> list[dict]:
    log = _log(settings)
    off = _turned_off(settings)
    found = [_describe_project(project, log, db, settings) for project in log.projects()
             if project["profile"] not in off]
    return sorted(found, key=lambda p: (p["updated"] or "", p["id"]), reverse=True)


@router.get("/projects/{project_id}", summary="A project for own files, with the files and clips")
def get_project(project_id: int, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    log = _log(settings)
    project = log.get_project(project_id)
    if project is None:
        raise HTTPException(404, "The project does not exist.")
    return _describe_project(project, log, db, settings)


@router.get("/runs/{run_id}", summary="A project with progress per step")
def get_run(run_id: int, db: Session = Depends(get_db),
            settings: Settings = Depends(get_settings)) -> dict:
    log = _log(settings)
    meta = log.run(run_id)
    if meta is None:
        raise HTTPException(404, "The project does not exist.")
    meta = _with_facts(meta, log)
    out, progress = runinfo.describe(meta, db, log.list(), settings)
    out.update(runinfo.steps_and_cost(progress, meta["duration_s"], _berget_price(settings),
                                      runinfo.fast(meta["profile"], settings)))
    out["retry_problem"] = _own_file_missing(meta["vod_id"], settings)  # varför Kör igen inte går
    return out


@router.post("/runs/{run_id}/cancel", status_code=202, summary="Cancel a running project",
             dependencies=[Depends(verify_api_key)])
def cancel_run(run_id: int, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    """Körningen stoppar vid nästa steg: före nästa anrop till Claude, före nästa klipp, och hämtningen direkt.
    Klara filer behålls, en halv nedladdning tas bort. Den markeras som misslyckad med texten CANCELLED."""
    if _log(settings).run(run_id) is None:
        raise HTTPException(404, "The project does not exist.")
    run = db.get(Run, run_id)
    in_progress = run is not None and run.status in (RunStatus.pending, RunStatus.running)
    if not (in_progress and pipeline.cancel(run_id)):
        raise HTTPException(409, "The project is not getting clips right now, so there is nothing to cancel.")
    return {"cancelling": True}


def _vod_in_use(vod_id: str, run_id: int, request: Request, db: Session) -> bool:
    """Projektet pågår, eller körningen som pågår i appen använder samma VOD: en annan kampanj eller chatten."""
    own = db.get(Run, run_id)
    if own is not None and own.status in (RunStatus.pending, RunStatus.running):
        return True
    active_id = request.app.state.active_run_id
    active = db.get(Run, active_id) if active_id is not None else None
    return active is not None and active.vod_id == vod_id


@router.delete("/runs/{run_id}/vod", summary="Delete the project's VOD file and parts to free up space",
               dependencies=[Depends(verify_api_key)])
def delete_run_vod(run_id: int, request: Request, db: Session = Depends(get_db),
                   settings: Settings = Depends(get_settings)) -> dict:
    """Bara work/<id>/<id>.mp4 och delarna. Klippen, transkriptet, chatten och klipploggen finns kvar.
    Kör igen hämtar VOD:en från Twitch igen. En egen fil går inte att köra igen efteråt."""
    meta = _log(settings).run(run_id)
    if meta is None:
        raise HTTPException(404, "The project does not exist.")
    with _start_lock:  # ingen körning startar medan filerna tas bort
        if _vod_in_use(meta["vod_id"], run_id, request, db):
            raise HTTPException(409, "The project is using the VOD file right now. Wait until it is done.")
        try:
            freed = vodfiles.remove(meta["vod_id"], settings)
        except vodfiles.UnsafeVod as error:
            raise HTTPException(400, str(error)) from error
    return {"freed_bytes": freed, "freed_gb": round(freed / estimate.GB, 2)}


@router.post("/runs/{run_id}/finish", status_code=202, summary="Make the clips campaign-ready again",
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
        raise HTTPException(404, "The project does not belong to any campaign.")
    run = db.get(Run, run_id)
    if run is not None and run.status == RunStatus.failed:
        raise HTTPException(400, "The project failed. Press Run again.")
    with _start_lock:
        if request.app.state.active_run_id is not None:
            raise HTTPException(429, "A project is already getting clips. Wait until it is done.")
        if editor.lock.locked():  # redigeraren kollar active_run_id under samma lås
            raise HTTPException(409, "A clip is being edited right now. Wait until the edit is done.")
        request.app.state.active_run_id = run_id
    background_tasks.add_task(finish_run, run_id, meta["vod_id"], meta["profile"], meta["game"],
                              settings, app=request.app)
    return {"started": True}


# ---------- klipp ----------


REVIEW_PREFIX = "Bildgranskning: "


def _split_warnings(clip: dict) -> tuple[list[str], list[str]]:
    """Varningarna som visas, och äldre klipps bildgranskning som låg bland varningarna."""
    warnings = clip["warnings"] or []
    # Äldre klipp har bildgranskningens beskrivningar som varningar. De visas med bildgranskningen i stället.
    old_notes = [w.removeprefix(REVIEW_PREFIX) for w in warnings if w.startswith(REVIEW_PREFIX)]
    # Varningen om format sparades när klippet gjordes. Den gäller inte längre när formatet är valt.
    hidden = {NO_FORMAT} if clip["format"] else set()
    shown = [w for w in warnings if not w.startswith(REVIEW_PREFIX) and w not in hidden]
    return shown, old_notes


def _public(clip: dict, settings: Settings) -> dict:
    out = {k: v for k, v in clip.items() if k != "file"}
    out["filename"] = Path(clip["file"] or "").name
    out["problems"] = clip["problems"] or []
    out["warnings"], old_notes = _split_warnings(clip)
    if old_notes:
        review = dict(clip["review"] or {})
        review["notes"] = [*(review.get("notes") or []), *old_notes]
        out["review"] = review
    out["signals"] = clip["signals"] or []
    out["reason"] = clip["reason"] or ""
    out["engagement"] = ClipLog.engagement(clip)
    out["content_rewards_url"], out["platforms"], out["audience_countries"] = None, list(PLATFORMS), None
    log = _log(settings)
    out["length_s"] = clip_length(log, clip)
    try:
        profile = load_profile(clip["profile"], settings.profiles_dir)
        out["engagement_min"] = profile.engagement_min(clip["views"] or 0)
        out["min_views"] = profile.min_views
        out["platforms"] = profile.platforms
        out["content_rewards_url"] = profile.content_rewards_url
        out["audience_countries"] = profile.audience_countries
        # Statistik visar märket Avstängd. Alla svar med klippet har fältet, så att märket står kvar efter en sparning.
        out["campaign_off"] = not profile.active
    except SystemExit:
        out["engagement_min"] = None
        out["min_views"] = None
        out["campaign_off"] = False  # en trasig profil räknas som påslagen, som i _turned_off
    # ?v= byts när klippet ändras, så att webbläsaren inte visar en gammal version efter en redigering.
    version = quote(clip.get("updated") or "")
    out["posts"] = _public_posts(clip, settings, log)
    out["video_url"] = f"/campaign/clips/{clip['id']}/file?v={version}"
    out["poster_url"] = f"/campaign/clips/{clip['id']}/poster?v={version}"
    return out


def _public_posts(clip: dict, settings: Settings, log: ClipLog) -> list[dict]:
    """Klippets inlägg med engagemang, uppskattad intäkt och om kampanjens gräns nåddes inom 7 dagar."""
    try:
        profile = load_profile(clip["profile"], settings.profiles_dir)
        payment, min_views = profile.payment, profile.min_views
    except (SystemExit, Exception):  # kampanjen finns inte längre: ingen uppskattning och ingen gräns
        payment, min_views = {}, 0
    return [{**post, "engagement": ClipLog.engagement(post),
             "estimate_usd": _payout(post["views"] or 0, payment) if post["views"] else None,
             "reached_min_7d": reached_min_7d(post["created"], log.views_history(post["id"]), min_views)}
            for post in log.posts(clip["id"])]


def _get(log: ClipLog, clip_id: int) -> dict:
    try:
        return log.get(clip_id)
    except SystemExit as error:
        raise HTTPException(404, "The clip does not exist.") from error


def _scoped(log: ClipLog, clips: list[dict], run_id: int | None, project_id: int | None) -> list[dict] | None:
    """Klippen i ett projekt för egna filer (filernas klipp och kampanjens uppladdningar) eller i en körning.
    None när inget av dem är valt."""
    if project_id is not None:
        project = log.get_project(project_id)
        return runinfo.project_clips(project, log.runs(), clips) if project else []
    if run_id is not None:
        meta = log.run(run_id)
        return runinfo.run_clips(meta, clips) if meta else []
    return None


@router.get("/clips", summary="Clips in the clip log")
def list_clips(status: str | None = None, vod_id: str | None = None, run_id: int | None = None,
               profile: str | None = None, project_id: int | None = None, posted_off: bool = False,
               settings: Settings = Depends(get_settings)) -> list[dict]:
    """posted_off: Statistik visar också postade klipp från avstängda kampanjer, med campaign_off true."""
    log = _log(settings)
    clips = log.list(status or None)
    if vod_id:
        clips = [c for c in clips if c["vod_id"] == vod_id]
    if profile:  # kampanjsidan visar sina klipp även när kampanjen är avstängd
        clips = [c for c in clips if c["profile"] == profile]
    scoped = _scoped(log, clips, run_id, project_id)
    if scoped is not None:
        clips = scoped
    elif not profile:  # en direktlänk till ett projekt visar dess klipp även när kampanjen är avstängd
        clips = _without_off(clips, settings, posted_off)
    return [_public(c, settings) for c in clips]


def _without_off(clips: list[dict], settings: Settings, posted_off: bool) -> list[dict]:
    """Klippen utan avstängda kampanjers klipp. posted_off: de postade klippen från avstängda kampanjer är kvar."""
    off = _turned_off(settings)
    shown_off = set(POST_STATUSES) if posted_off else set()
    return [c for c in clips if c["profile"] not in off or c["status"] in shown_off]


@router.get("/clips/{clip_id}/transcript", summary="What is said in the clip")
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


@router.get("/clips/{clip_id}/poster", summary="A frame from the clip, 360 wide")
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
            raise HTTPException(500, "Could not make a frame of the clip.") from error
        finally:
            tmp.unlink(missing_ok=True)
    return FileResponse(poster, media_type="image/jpeg")


@router.get("/clips/{clip_id}/file", summary="Play or download a clip")
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
    others = [p for p in problems if not p.startswith("Captionen ")]  # alla problem från check_caption
    return others + check_caption(caption, profile)


@router.patch("/clips/{clip_id}", summary="Change a clip: status, caption, link or stats",
              dependencies=[Depends(verify_api_key)])
def patch_clip(clip_id: int, body: ClipPatch, settings: Settings = Depends(get_settings)) -> dict:
    # Status och caption kan byta klippets status, så de väntar inte in en redigering som pågår.
    changes_status = bool({"status", "caption"} & body.model_fields_set)
    if changes_status and not editor.lock.acquire(blocking=False):
        if editor.editing == clip_id:
            raise HTTPException(409, "The clip is being edited right now. Try again when the edit is done.")
        changes_status = False  # låset gäller ett annat klipp: det här klippet rörs inte av redigeringen
    try:
        return _patch_clip(clip_id, body, settings)
    finally:
        if changes_status:
            editor.lock.release()


def _check_campaign(clip: dict, status: str = "postad") -> None:
    """Ett klipp utan kampanj (_ingen) blir aldrig godkänt eller postat, oavsett problemlistan.
    Samma spärr som schemat."""
    if status not in ("godkand", *POST_STATUSES):
        return
    try:
        _require_campaign(clip)
    except ScheduleError as error:
        raise HTTPException(400, str(error)) from error


def _patch_clip(clip_id: int, body: ClipPatch, settings: Settings) -> dict:
    log = _log(settings)
    clip = _get(log, clip_id)
    profile = _profile(clip["profile"], settings)
    fields = body.model_dump(exclude_unset=True)
    if "status" in fields and fields["status"] is None:
        raise HTTPException(400, "Status cannot be empty.")

    if "format" in fields and profile.formats and fields["format"] not in profile.format_ids:
        raise HTTPException(400, f"The format {fields['format']!r} does not exist in the campaign.")
    if "platform" in fields and fields["platform"] not in PLATFORMS:
        raise HTTPException(400, "The platform must be tiktok, youtube_shorts or instagram_reels.")
    if "url" in fields and fields["url"]:
        problem = _link_problem(fields["url"], fields.get("platform") or clip["platform"])
        if problem:
            raise HTTPException(400, problem)

    problems = clip["problems"] or []
    status = clip["status"]
    if "caption" in fields:
        if fields["caption"] != clip["caption"] and body.status not in (None, status, "nekad"):
            raise HTTPException(400, "Save the caption first. Then a new yes is needed.")
        problems = _caption_problems(problems, fields["caption"], profile)
        fields["problems"] = problems
        # Ett ja gäller den caption som fanns. Ändras den måste klippet godkännas igen.
        if status in ("renderad", "underkand", "godkand") and body.status in (None, status):
            fields["status"] = "underkand" if problems else "renderad"

    new = body.status
    if new and new != status:
        if new not in NEXT_STATUS[status]:
            raise HTTPException(400, f"A clip with status {status} cannot become {new}.")
        _check_campaign(clip, new)
        if new == "godkand":
            if problems:
                raise HTTPException(400, "The clip has problems. Fix them before you approve.")
            if profile.formats and not (fields.get("format") or clip["format"]):
                raise HTTPException(400, "Choose a format before you approve.")
        if new == "renderad" and problems:
            fields["status"] = "underkand"
        if new == "postad":
            if problems:
                raise HTTPException(400, "The clip has problems. It may not be posted.")
            if not (fields.get("url") or clip["url"]):
                raise HTTPException(400, "Paste the link to the posted clip.")
            if not (fields.get("platform") or clip["platform"]):
                raise HTTPException(400, "Choose a platform.")
        if new == "analytics_inskickad":
            _check_min_views(fields.get("views", clip["views"]), profile)

    _apply_to_posts(log, clip, fields)
    if fields:
        try:  # ett klipp som lämnar godkänt får sina väntande uppladdningar avbrutna i klipploggen
            log.update(clip_id, **fields)
        except UploadRunning as error:
            raise HTTPException(409, str(error)) from error
    return _public(log.get(clip_id), settings)


def _check_min_views(views: int | None, profile: Profile) -> None:
    """Analytics inskickad går först när inlägget har nått kampanjens min_views."""
    if (views or 0) < profile.min_views:
        raise HTTPException(400, f"Analytics is submitted only at {profile.min_views} views. "
                                 f"The post has {views or 0}.")


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
            raise HTTPException(400, "The clip has several posts. Change link and numbers per post.")
        log.update_post(posts[0]["id"], **link, **stats)
    elif link or stats:
        fields.update(link, **stats)


# ---------- inställningar ----------


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")  # okända nycklar ger 422
    submit_helper: bool


@router.get("/preferences", summary="Robin's choices: the submit help for Content Rewards")
def get_preferences(settings: Settings = Depends(get_settings)) -> dict:
    return {"submit_helper": preferences.submit_helper(settings)}


@router.put("/preferences", summary="Turn the submit help on or off", dependencies=[Depends(verify_api_key)])
def put_preferences(body: Preferences, settings: Settings = Depends(get_settings)) -> dict:
    preferences.save_submit_helper(settings, body.submit_helper)
    return {"submit_helper": preferences.submit_helper(settings)}


# ---------- översikt ----------


@router.get("/summary", summary="Budget, number of clips per status and to do")
def summary(settings: Settings = Depends(get_settings)) -> dict:
    log = _log(settings)
    off = _turned_off(settings)
    every = log.list()
    clips = [c for c in every if c["profile"] not in off]
    counts: dict[str, int] = {}
    for clip in clips:
        counts[clip["status"]] = counts.get(clip["status"], 0) + 1
    # Intäkterna räknar också postade klipp från avstängda kampanjer, som Statistik visar.
    earned = [c for c in every if c["profile"] not in off or c["status"] in POST_STATUSES]
    # Budgeten gäller alla kampanjer, så den räknas som förut.
    return {"budget": budget_from_settings(settings).summary(), "counts": counts, "todo": log.todo(skip=off),
            "disk": storage(settings), "earnings": _earnings(earned, settings, log.posts())}


def _payout(views: int, payment: dict) -> float:
    """Vad ett klipp tjänar enligt kampanjens payment: 0 under min_views, visningar / 1000 * cpm_usd,
    högst max_payout_per_clip_usd, och 0 under min_payout_usd."""
    if views < int(payment.get("min_views") or 0):
        return 0.0
    usd = views / 1000 * float(payment.get("cpm_usd") or 0)
    if payment.get("max_payout_per_clip_usd") is not None:
        usd = min(usd, float(payment["max_payout_per_clip_usd"]))
    return round(usd, 2) if usd >= float(payment.get("min_payout_usd") or 0) else 0.0


def _earnings(clips: list[dict], settings: Settings, posts: list[dict]) -> dict:
    """Intäkter per klipp och månad. min_views och taket gäller varje inlägg för sig, så ett klipp med
    inlägg räknas per inlägg. Utbetalt gäller betalda inlägg: vad Whop betalade när beloppet är ifyllt, annars
    uppskattningen.
    Gamla klipp utan inlägg räknas på klippets egna siffror."""
    payments: dict[str, dict] = {}
    by_clip: dict[int, list[dict]] = {}
    for post in posts:
        by_clip.setdefault(post["clip_id"], []).append(post)
    per_clip, months, paid = [], {}, 0.0
    for clip in clips:
        if not clip["views"]:
            continue
        if clip["profile"] not in payments:
            try:
                payments[clip["profile"]] = load_profile(clip["profile"], settings.profiles_dir).payment
            except (SystemExit, Exception):  # kampanjen finns inte längre: inget att räkna med
                payments[clip["profile"]] = {}
        parts = by_clip.get(clip["id"]) or [{"views": clip["views"], "status": clip["status"], "paid_usd": None}]
        usd = 0.0
        for part in parts:
            estimate = _payout(part["views"] or 0, payments[clip["profile"]])
            usd += estimate
            if part["status"] == "betald":  # efter Ångra från Betald står beloppet kvar, men det räknas inte
                paid += estimate if part["paid_usd"] is None else float(part["paid_usd"])
        usd = round(usd, 2)
        per_clip.append({"id": clip["id"], "views": clip["views"], "usd": usd})
        if clip["created"]:
            months[clip["created"][:7]] = months.get(clip["created"][:7], 0.0) + usd
    return {"estimated_usd": round(sum(c["usd"] for c in per_clip), 2), "paid_usd": round(paid, 2),
            "by_month": [{"month": m, "usd": round(usd, 2)} for m, usd in sorted(months.items())],
            "per_clip": per_clip}


# Vägarna för inlägg ligger i posts_api.py och registreras på samma router.
from . import chat_api, posts_api, uploads_api, versions_api  # noqa: E402, F401
