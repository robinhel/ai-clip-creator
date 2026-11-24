"""GET /campaign/runs/{run_id}/timeline: where the signals were in a project's VOD, and what the AI picked.

Read from the files the pipeline leaves in work/<vod>/ and out/<vod>/run_<id>/. Nothing is computed again.
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


def _chat_times(work: Path) -> list[float]:
    chat = _read(work / "chat.json")
    messages = chat.get("messages", []) if isinstance(chat, dict) else []
    return [float(m["time_in_seconds"]) for m in messages if isinstance(m, dict) and "time_in_seconds" in m]


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
    points = [(float(p[0]), float(p[1])) for p in data if isinstance(p, list) and len(p) == 2] if isinstance(data, list) else []
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
    for reason in candidate.get("reasons", []):
        source = next((s for start, s in REASON_SOURCES if str(reason).startswith(start)), None)
        if source:
            counts[source] = counts.get(source, 0) + 1
    return counts or {source: 1 for source in candidate.get("sources", [])}


def _picks(settings: Settings, vod_id: str, run_id: int) -> list[dict]:
    """This run's picks. Older runs kept them in out/<vod>/, and the latest run also in work/<vod>/."""
    for path in (settings.out_dir / vod_id / f"run_{run_id}" / "picks.json",
                 settings.out_dir / vod_id / "picks.json",
                 settings.work_dir / vod_id / "picks.json"):
        data = _read(path)
        if isinstance(data, list):
            picks = [p.get("pick", {}) for p in data if isinstance(p, dict)]
            return [{"start_s": float(p["start_offset_s"]), "end_s": float(p["end_offset_s"]),
                     "title": p.get("title") or p.get("title_es") or ""}
                    for p in picks if "start_offset_s" in p and "end_offset_s" in p]
    return []


@router.get("/runs/{run_id}/timeline", summary="The signals in a project's VOD and the AI's picks")
def get_timeline(run_id: int, settings: Settings = Depends(get_settings)) -> dict:
    meta = _log(settings).run(run_id)
    if meta is None:
        raise HTTPException(404, "The project does not exist.")
    vod_id = meta["vod_id"]
    work = settings.work_dir / vod_id
    candidates = _read(work / "candidates.json")
    if not isinstance(candidates, list):
        raise HTTPException(404, f"No signals for this project yet: work/{vod_id}/candidates.json is missing. "
                                 "They are saved when the project has found its candidates.")
    candidates = [c for c in candidates if isinstance(c, dict) and "t_center" in c]
    chat = _chat_times(work)
    audio = _audio(work)
    picks = _picks(settings, vod_id, run_id)

    duration_s = float(meta.get("duration_s") or 0) or max(
        [float(c["t_center"]) for c in candidates] + [p["end_s"] for p in picks] + chat + [t for t, _ in audio],
        default=0.0,
    )

    def times(sources: tuple[str, ...]) -> list[float]:
        return sorted(float(c["t_center"]) for c in candidates if set(sources) & set(c.get("sources", [])))

    return {
        "duration_s": duration_s,
        "chat_density": _density(chat, duration_s),
        "audio_energy": audio,
        "hype": times(("transcript_hype",)),
        "viewer_clips": times(VIEWER_SOURCES),
        "candidates": [
            {
                "start_s": max(0.0, float(c["t_center"]) - CANDIDATE_HALF_S),
                "end_s": min(duration_s, float(c["t_center"]) + CANDIDATE_HALF_S) if duration_s else
                float(c["t_center"]) + CANDIDATE_HALF_S,
                "score": float(c.get("score", 0)),
                "sources": list(c.get("sources", [])),
                "breakdown": _breakdown(c),
            }
            for c in candidates
        ],
        "picks": picks,
    }
