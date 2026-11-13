"""Picture cuts via ffmpeg's scene score, for video without speech or chat (trailers)."""

from __future__ import annotations

import json
import logging
import re
import subprocess  # nosec B404
from pathlib import Path

log = logging.getLogger(__name__)


def find_scene_cuts(
    mp4: Path, threshold: float = 0.3, *, cache_dir: Path | None = None
) -> list[tuple[float, float]]:
    """Return (t, scene_score) for every cut above threshold. Cached per threshold in cache_dir."""
    cache = cache_dir / f"scene_cuts_{threshold:g}.json" if cache_dir is not None else None
    if cache is not None and cache.exists():
        return [tuple(p) for p in json.loads(cache.read_text(encoding="utf-8"))]  # type: ignore[misc]
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(mp4), "-an",
        "-vf", f"scale=320:-2,select='gt(scene,{threshold})',metadata=print:file=-",
        "-f", "null", "-",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout  # nosec B603
    cuts = [
        (round(float(t), 2), float(score))
        for t, score in re.findall(r"pts_time:(\S+)\s+lavfi\.scene_score=(\S+)", out)
    ]
    log.info("scene cuts: %d above %.2f in %s", len(cuts), threshold, mp4.name)
    if cache is not None:
        cache.write_text(json.dumps(cuts), encoding="utf-8")
    return cuts
