"""GET /campaign/runs/{run_id}/timeline: where the signals were in a project's VOD, and what the AI picked.

Read from the files the pipeline leaves in out/<vod>/run_<id>/ and work/<vod>/. Nothing is computed again.
work/<vod>/ holds only the latest run's candidates and picks, so an older run never reads them there.
Broken rows in the files are skipped.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from .api import _log, get_settings
from .config import Settings

router = APIRouter(prefix="/campaign", tags=["campaign"])

DENSITY_BINS = 200  # chat messages are counted in this many windows over the VOD
MIN_BIN_S = 10.0
AUDIO_POINTS = 400  # the audio series is averaged down to at most this many points
CANDIDATE_HALF_S = 15.0  # a candidate is a point in time; the timeline shows it as a window around it
VIEWER_SOURCES = ("existing_clip", "clip_command")

# The start of each reason in candidates.json, and the signal it comes from (clipsmith/candidates/builder.py).
REASON_SOURCES = (
    ("Twitch clip", "existing_clip"),
    ("!clip by", "clip_command"),
    ("chat density", "chat_density"),
    ("hype in transcript", "transcript_hype"),
    ("voice spike", "audio_energy"),
    ("picture cut", "scene_change"),
    ("evenly-spaced sample", "transcript_sample"),
)


def _read(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _number(value: object) -> float | None:
    """A float, or None for null, text and other broken values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _strings(value: object) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def _chat_times(work: Path) -> list[float]:
    chat = _read(work / "chat.json")
    messages = chat.get("messages", []) if isinstance(chat, dict) else []
    times = (_number(m.get("time_in_seconds")) for m in messages if isinstance(m, dict))
    return [t for t in times if t is not None]


def _density(times: list[float], duration_s: float) -> list[list[float]]:
    """Messages per window, scaled so that the busiest window is 1.0."""
    if not times or duration_s <= 0:
        return []
    size = max(MIN_BIN_S, duration_s / DENSITY_BINS)
    counts = [0] * (int(duration_s // size) + 1)
    for t in times:
        if 0 <= t <= duration_s:
            counts[int(t // size)] += 1
    top = max(counts) or 1
    return [[round(min((i + 0.5) * size, duration_s), 1), round(n / top, 3)] for i, n in enumerate(counts)]


def _audio(work: Path) -> list[list[float]]:
    """The cached RMS series (t, dB) from clipsmith's audio signal, scaled to 0..1. Empty when it was never made."""
    data = _read(work / "audio_rms_v2.json")
    pairs = [(_number(p[0]), _number(p[1])) for p in data if isinstance(p, list) and len(p) == 2] \
        if isinstance(data, list) else []
    points = [(t, v) for t, v in pairs if t is not None and v is not None]
    if not points:
        return []
    step = -(-len(points) // AUDIO_POINTS)  # ceil
    averaged = [
        (sum(t for t, _ in chunk) / len(chunk), sum(v for _, v in chunk) / len(chunk))
        for chunk in (points[i:i + step] for i in range(0, len(points), step))
    ]
    lo = min(v for _, v in averaged)
    hi = max(v for _, v in averaged)
    span = (hi - lo) or 1.0
    return [[round(t, 1), round((v - lo) / span, 3)] for t, v in averaged]


def _breakdown(candidate: dict) -> dict[str, int]:
    """How many events of each signal were merged into the candidate."""
    counts: dict[str, int] = {}
    for reason in candidate["reasons"]:
        source = next((s for start, s in REASON_SOURCES if str(reason).startswith(start)), None)
        if source:
            counts[source] = counts.get(source, 0) + 1
    return counts or {source: 1 for source in candidate["sources"]}


def _picks(path: Path) -> list[dict]:
    data = _read(path)
    picks = [p.get("pick") for p in data if isinstance(p, dict)] if isinstance(data, list) else []
    result = []
    for pick in picks:
        if not isinstance(pick, dict):
            continue
        start, end = _number(pick.get("start_offset_s")), _number(pick.get("end_offset_s"))
        if start is not None and end is not None:
            result.append({"start_s": start, "end_s": end, "title": pick.get("title") or pick.get("title_es") or ""})
    return result


def _is_latest(settings: Settings, vod_id: str, run_id: int) -> bool:
    """True when no later run of the same VOD exists: then work/<vod>/ holds this run's files."""
    return all(r["run_id"] <= run_id for r in _log(settings).runs() if r["vod_id"] == vod_id)


def _run_file(run_dir: Path, work: Path, name: str, latest: bool) -> Path:
    """The run's own copy. Older runs did not keep one; work/<vod>/ is theirs only for the latest run."""
    own = run_dir / name
    return own if own.exists() or not latest else work / name


@router.get("/runs/{run_id}/timeline", summary="The signals in a project's VOD and the AI's picks")
def get_timeline(run_id: int, settings: Settings = Depends(get_settings)) -> dict:
    meta = _log(settings).run(run_id)
    if meta is None:
        raise HTTPException(404, "The project does not exist.")
    vod_id = meta["vod_id"]
    work = settings.work_dir / vod_id
    run_dir = settings.out_dir / vod_id / f"run_{run_id}"
    latest = _is_latest(settings, vod_id, run_id)
    candidates = _read(_run_file(run_dir, work, "candidates.json", latest))
    if not isinstance(candidates, list):
        raise HTTPException(404, f"No signals for this project: out/{vod_id}/run_{run_id}/candidates.json is missing. "
                                 "They are saved when the project has found its candidates.")
    candidates = [{**c, "t_center": _number(c.get("t_center")), "score": _number(c.get("score")) or 0.0,
                   "sources": _strings(c.get("sources")), "reasons": _strings(c.get("reasons"))}
                  for c in candidates if isinstance(c, dict)]
    candidates = [c for c in candidates if c["t_center"] is not None]
    chat = _chat_times(work)
    audio = _audio(work)
    picks = _picks(_run_file(run_dir, work, "picks.json", latest))

    duration_s = float(meta.get("duration_s") or 0) or max(
        [c["t_center"] for c in candidates] + [p["end_s"] for p in picks] + chat + [t for t, _ in audio],
        default=0.0,
    )

    def times(sources: tuple[str, ...]) -> list[float]:
        return sorted(c["t_center"] for c in candidates if set(sources) & set(c["sources"]))

    return {
        "duration_s": duration_s,
        "chat_density": _density(chat, duration_s),
        "audio_energy": audio,
        "hype": times(("transcript_hype",)),
        "viewer_clips": times(VIEWER_SOURCES),
        "candidates": [
            {
                "start_s": max(0.0, c["t_center"] - CANDIDATE_HALF_S),
                "end_s": min(duration_s, c["t_center"] + CANDIDATE_HALF_S),
                "score": c["score"],
                "sources": c["sources"],
                "breakdown": _breakdown(c),
            }
            for c in candidates
        ],
        "picks": picks,
    }
