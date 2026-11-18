"""Projektet som webben visar det: status, förlopp, klipp, bild, avbrott och kostnad. Används av api.py."""

from __future__ import annotations

from collections import Counter
from urllib.parse import quote

from sqlalchemy.orm import Session

from clipsmith.db.models import PipelineEvent, Run

from . import estimate, pipeline, vodfiles
from .cliplog import UPLOADS, card_runs
from .config import Settings
from .profile import load_profile

FIELDS = ("run_id", "vod_id", "profile", "game", "focus", "min_s", "max_s",
          "title", "channel_name", "thumbnail_url", "duration_s", "project_id", "origin")
FILE_FIELDS = ("run_id", "vod_id", "title", "status", "stage", "pct", "error", "duration_s", "vod_bytes", "created")
MISSING = "Projektet saknas i clipsmiths databas."
SIGNALS_STEP = "Signalerna väljer, utan Claude"


def run_clips(meta: dict, clips: list[dict]) -> list[dict]:
    """Klippen från en körning. shortcut: klipploggen vet inte run_id, så det är klippen från samma
    VOD och kampanj. Två körningar av samma VOD delar klipp; spara run_id per klipp om det behövs."""
    return [c for c in clips if c["vod_id"] == meta["vod_id"] and c["profile"] == meta["profile"]]


def fast(name: str, settings: Settings) -> bool:
    """Väljer kampanjen klipp med signalerna, utan Claude (snabbläget)? Då kostar körningen inget hos Claude."""
    try:
        return pipeline.signals_mode(load_profile(name, settings.profiles_dir), settings)
    except (SystemExit, Exception):  # en trasig profil syns redan i list_profiles
        return False


def _state(meta: dict, db: Session) -> tuple[str, str | None, str | None, list]:
    """Status, fel, när körningen skapades och dess händelser, ur clipsmiths databas."""
    run = db.get(Run, meta["run_id"])
    if run is None:
        return "failed", MISSING, None, []
    events = [(e.stage, e.pct, e.created_at, e.message) for e in db.query(PipelineEvent)
              .filter(PipelineEvent.run_id == run.id).order_by(PipelineEvent.id)]
    return run.status.value, run.error, run.created_at.isoformat(), events


def _poster_url(own: list[dict]) -> str | None:
    """Bilden från projektets första klipp, för projekt utan Twitch-bild, som en egen fil."""
    first = min(own, key=lambda c: c["id"], default=None)
    return f"/campaign/clips/{first['id']}/poster?v={quote(first['updated'] or '')}" if first else None


def describe(meta: dict, db: Session, clips: list[dict], settings: Settings) -> tuple[dict, dict]:
    """Projektet som Hem och Projekt visar det, och förloppet med stegen för projektsidan."""
    status, error, created, events = _state(meta, db)
    progress = estimate.progress(status, events, meta["duration_s"])
    own = run_clips(meta, clips)
    out = {key: meta[key] for key in FIELDS}
    out.update(start_s=meta.get("start_s") or 0, end_s=meta.get("end_s") or 0)  # 0 för gamla körningar
    out.update(created=meta["created"] or created, status=status, stage=progress["stage"],
               pct=progress["pct"], message=progress["message"], error=error,
               clip_counts=dict(Counter(c["status"] for c in own)), poster_url=_poster_url(own),
               cancelled=status == "failed" and error == pipeline.CANCELLED,
               cancellable=status in ("pending", "running") and pipeline.is_cancellable(meta["run_id"]),
               vod_bytes=vodfiles.size(meta["vod_id"], settings))  # det som Ta bort VOD-filen frigör
    return out, progress


def steps_and_cost(progress: dict, duration_s: float | None, berget_usd_per_min: float | None,
                   fast_mode: bool) -> dict:
    """Stegen, tid kvar och kostnaden för projektsidan. I snabbläget väljer signalerna, utan anrop till Claude."""
    cost = estimate.cost(duration_s, berget_usd_per_min)
    steps = progress["steps"]
    if fast_mode and cost:
        cost = {**cost, "claude_usd": 0.0, "usd": cost["berget_usd"]}
        steps = [{**s, "label": SIGNALS_STEP} if s["id"] == "select" else s for s in steps]
    return {"steps": steps, "eta_s": progress["eta_s"], "fast": fast_mode,
            "cost_estimate_usd": cost and cost["usd"], "cost_estimate": cost}


def stream_run(meta: dict | None, db: Session, clips: list[dict]) -> dict:
    """Streamens senaste projekt för kortet på Hem: nummer, status, om Robin avbröt och antal klipp."""
    if meta is None:
        return {"run_id": None, "run_status": None, "run_cancelled": False, "clip_count": 0}
    run = db.get(Run, meta["run_id"])
    return {"run_id": meta["run_id"], "run_status": run.status.value if run else "failed",
            "run_cancelled": bool(run and run.error == pipeline.CANCELLED),
            "clip_count": len(run_clips(meta, clips))}


def project_clips(project: dict, runs: list[dict], clips: list[dict]) -> list[dict]:
    """Klippen i kampanjens projekt för egna filer: filernas klipp och kampanjens uppladdningar."""
    files = {meta["vod_id"] for meta in runs if meta["project_id"] == project["id"]} | {UPLOADS}
    return [c for c in clips if c["profile"] == project["profile"] and c["vod_id"] in files]


def project_files(mine: list[dict], db: Session, clips: list[dict], settings: Settings) -> list[dict]:
    """Filerna i ett projekt, en per vod_id: filens senaste körning, inte chattens om det finns en annan.
    Kör igen och Ta bort VOD-filen går fortfarande per fil, med run_id."""
    shown = sorted(card_runs(mine, lambda m: m["vod_id"]).values(), key=lambda m: m["run_id"])
    return [{key: out[key] for key in FILE_FIELDS} for out in (describe(m, db, clips, settings)[0] for m in shown)]


def describe_project(project: dict, streamer: str, files: list[dict], own: list[dict]) -> dict:
    """Projektet som Hem och Projekt visar det. `own`: projektets klipp (project_clips)."""
    working = [f for f in files if f["status"] in ("running", "pending")]
    active = min(working, key=lambda f: f["status"] != "running", default=None)  # den som körs, annars den i kö
    times = [t for t in (*(f["created"] for f in files), *(c["updated"] for c in own)) if t]
    return {"id": project["id"], "profile": project["profile"], "streamer": streamer, "title": project["title"],
            "created": project["created"], "updated": max(times, default=project["created"]),
            "active": bool(working), "pct": active and active["pct"], "poster_url": _poster_url(own),
            "clip_counts": dict(Counter(c["status"] for c in own)), "files": files}
