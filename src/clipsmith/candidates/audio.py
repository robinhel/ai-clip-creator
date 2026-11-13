"""Audio RMS energy extraction and peak detection via ffmpeg astats filter."""

from __future__ import annotations

import json
import logging
import re
import subprocess  # nosec B403
from pathlib import Path

log = logging.getLogger(__name__)


def compute_audio_rms_series(
    mp4: Path,
    window_s: float = 2.0,
    *,
    cache_dir: Path | None = None,
) -> list[tuple[float, float]]:
    """Return (t_center, rms_db) per analysis window. Skips silence (db <= -90).

    Results are cached to <cache_dir>/audio_rms_v2.json so the expensive ffmpeg
    scan only runs once per VOD. Pass cache_dir=None to disable caching.
    Uses ffmpeg astats + ametadata filters — no Python audio library required.
    """
    # v2: per-window values. audio_rms.json from before holds a running mean and is not read.
    cache_path = (cache_dir / "audio_rms_v2.json") if cache_dir is not None else None

    if cache_path is not None and cache_path.exists():
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        points: list[tuple[float, float]] = [tuple(p) for p in data]  # type: ignore[misc]
        log.info(
            "audio RMS series: loaded %d windows from cache (%s)", len(points), cache_path.name
        )
        return _dedupe_windows(points, window_s)

    sample_rate = 16000
    reset_samples = int(window_s * sample_rate)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(mp4),
        "-vn",
        "-af",
        f"aresample={sample_rate},"
        # astats counts reset in frames, not samples: one frame per window, reset after each frame.
        f"asetnsamples=n={reset_samples}:p=0,"
        "astats=metadata=1:reset=1,"
        "ametadata=mode=print:file=-",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)  # nosec B603 — cmd built from internal config and mp4 path only
    points = []
    for m in re.finditer(
        r"pts_time:(\S+).*?lavfi\.astats\.Overall\.RMS_level=(\S+)",
        result.stdout,
        re.DOTALL,
    ):
        try:
            t = float(m.group(1)) + window_s / 2
            db = float(m.group(2))
            if db > -90:
                points.append((t, db))
        except ValueError:
            continue
    points = _dedupe_windows(points, window_s)

    log.info("audio RMS series: %d windows from %s", len(points), mp4.name)

    if cache_path is not None:
        cache_path.write_text(json.dumps(points), encoding="utf-8")
        log.info("audio RMS series cached to %s", cache_path.name)

    return points


def _dedupe_windows(
    points: list[tuple[float, float]], window_s: float
) -> list[tuple[float, float]]:
    """Collapse per-frame entries to one sample per analysis window.

    ffmpeg ametadata outputs one line per audio frame inside each reset window,
    so raw parsed data has ~10-20 near-identical entries per window_s interval.
    Keep the last frame per bucket (most accumulated stats).
    """
    if not points:
        return points
    bucketed: dict[int, tuple[float, float]] = {}
    for t, db in points:
        bucketed[int((t - window_s / 2) / window_s)] = (t, db)
    return sorted(bucketed.values())


def find_rms_peaks(
    series: list[tuple[float, float]],
    peak_multiplier: float = 2.0,
) -> list[tuple[float, float]]:
    """Return (t_center, norm_score) for windows at least peak_multiplier σ above mean.

    mean and std come from the louder half of the windows, so long pauses and fades
    do not widen std until no window can reach the threshold.
    norm_score = (rms_db - mean) / std — louder spikes score proportionally higher.
    Returns [] for empty or essentially flat (std < 0.5 dB) series.
    """
    if len(series) < 2:
        return []
    dbs = sorted(db for _, db in series)[len(series) // 2 :]
    mean = sum(dbs) / len(dbs)
    std = (sum((d - mean) ** 2 for d in dbs) / len(dbs)) ** 0.5
    if std < 0.5:
        return []
    threshold = mean + peak_multiplier * std
    return [(t, (db - mean) / std) for t, db in series if db >= threshold]
