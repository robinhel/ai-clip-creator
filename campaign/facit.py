"""Facit: mät hur många av dina handplockade ögonblick verktyget hittar."""

from __future__ import annotations

from pathlib import Path

import yaml


def parse_time(value) -> float:
    """'1:23:45', '83:45' eller sekunder som tal blir sekunder."""
    if isinstance(value, (int, float)):
        return float(value)
    parts = [float(p) for p in str(value).strip().split(":")]
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"Cannot read the time {value!r}. Write e.g. 1:23:45.")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def load_facit(path: Path) -> dict:
    """Läs en facit-fil: {vod: '123', moments: [{time: '0:41:10', note: '...'}]}."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    moments = [
        {"time": parse_time(m["time"]), "note": m.get("note", "")}
        for m in data.get("moments") or []
    ]
    return {"vod": str(data.get("vod", "")), "moments": moments}


def score(moments: list[dict], clips: list[tuple[float, float]], tolerance: float = 10.0) -> dict:
    """Ett facit-ögonblick räknas som hittat om det ligger i ett klipp (± tolerance sekunder)."""
    hit, missed = [], []
    for moment in moments:
        t = moment["time"]
        found = any(start - tolerance <= t <= end + tolerance for start, end in clips)
        (hit if found else missed).append(moment)
    return {"hits": len(hit), "total": len(moments), "hit": hit, "missed": missed}
