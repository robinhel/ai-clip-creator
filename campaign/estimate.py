"""Grov uppskattning av plats, tid och kostnad för en körning, och förloppet per steg.

Siffrorna bygger på en körning av VOD 2925991945 (2026-11-12) och några antaganden:
- Storlek: 1080p60 tog 13 GB för 13 950 s, alltså ungefär 0,93 MB per sekund VOD. Mätt.
- Nedladdning: ungefär 20 MB/s. Antagande, inte mätt.
- Transkribering via Berget: 4 timmar VOD tog cirka 8 minuter och 0,46 euro. Mätt.
- Lokal transkribering (faster-whisper, int8, vanlig CPU): small cirka 60 minuter för 4 timmar VOD (30-90),
  medium dubbelt så lång tid och large-v3 fyra gånger. Antagande, inte mätt på en hel VOD. Kostar inget.
- Stegen efter transkriberingen: fasta tider (se FIXED_S). Antagande.
- Claude: ungefär 0,30 USD per körning för att välja och betygsätta (cirka 20 kandidater)
  och granska klippens bilder. Antagande.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

GB = 1024**3
MB_PER_VOD_S = 0.93
DOWNLOAD_MB_PER_S = 20.0
TRANSCRIBE_S_PER_VOD_S = 8 * 60 / (4 * 3600)
LOCAL_S_PER_VOD_S = {"small": 60 * 60 / (4 * 3600), "medium": 120 * 60 / (4 * 3600), "large-v3": 240 * 60 / (4 * 3600)}
USD_PER_EUR = 1.08
BERGET_USD_PER_VOD_S = 0.46 * USD_PER_EUR / (4 * 3600)
CLAUDE_USD = 0.30
FIXED_S = {"moments": 120, "select": 150, "clip": 120, "finish": 180}

STEPS = [
    ("download", "Downloading the VOD"),
    ("transcribe", "Transcribing"),
    ("moments", "Finding moments"),
    ("select", "Claude chooses and rates"),
    ("clip", "Cutting"),
    ("finish", "Making the clips campaign-ready"),
]
STEP_IDS = [step for step, _ in STEPS]
# clipsmiths stage-namn till stegen som webben visar.
STAGE_TO_STEP = {"download": "download", "transcribe": "transcribe", "chat": "moments",
                 "candidates": "moments", "select": "select", "clip": "clip", "finish": "finish"}


def disk_gb(path: Path) -> tuple[float, float]:
    """Ledigt och totalt utrymme i GB där mappen ligger (eller närmaste mapp som finns)."""
    path = Path(path).resolve()
    while not path.exists():
        path = path.parent
    usage = shutil.disk_usage(path)
    return round(usage.free / GB, 1), round(usage.total / GB, 1)


def download_gb(duration_s: float) -> float:
    return round(duration_s * MB_PER_VOD_S / 1000, 1)


def transcribe_rate(engine: str, model: str) -> float:
    """Sekunder transkribering per sekund VOD: Berget eller den lokala modellen."""
    return LOCAL_S_PER_VOD_S[model] if engine == "local" else TRANSCRIBE_S_PER_VOD_S


def step_seconds(duration_s: float | None, downloaded: bool = False,
                 transcribe_rate: float = TRANSCRIBE_S_PER_VOD_S) -> dict[str, int]:
    """Ungefär hur länge varje steg tar. Tomt om VOD:ens längd inte är känd."""
    if not duration_s:
        return {}
    download = 0 if downloaded else download_gb(duration_s) * 1000 / DOWNLOAD_MB_PER_S
    return {"download": round(download), "transcribe": round(duration_s * transcribe_rate),
            **FIXED_S}


def cost(duration_s: float | None, berget_usd_per_min: float | None = None,
         transcribed: bool = False) -> dict | None:
    """Ungefärlig kostnad, delad i Berget (transkribering) och Claude (val och bildgranskning).
    En VOD som redan är transkriberad kostar inget hos Berget."""
    if not duration_s:
        return None
    per_s = BERGET_USD_PER_VOD_S if berget_usd_per_min is None else berget_usd_per_min / 60
    berget = 0.0 if transcribed else round(duration_s * per_s, 2)
    return {"berget_usd": berget, "claude_usd": CLAUDE_USD, "usd": round(berget + CLAUDE_USD, 2)}


def cost_usd(duration_s: float | None, berget_usd_per_min: float | None = None,
             transcribed: bool = False) -> float | None:
    parts = cost(duration_s, berget_usd_per_min, transcribed)
    return parts and parts["usd"]


def estimate(duration_s: float, free_gb: float, downloaded: bool, berget_usd_per_min: float | None = None,
             transcribed: bool = False, transcribe_rate: float = TRANSCRIBE_S_PER_VOD_S) -> dict:
    """Plats, tid och kostnad för en VOD. En nedladdad VOD behöver ingen ny plats."""
    size = 0.0 if downloaded else download_gb(duration_s)
    return {
        "download_gb": size,
        "disk_free_gb": free_gb,
        "fits": downloaded or free_gb >= size * 1.1 + 1,
        "minutes": round(sum(step_seconds(duration_s, downloaded, transcribe_rate).values()) / 60),
        "cost_usd": cost_usd(duration_s, berget_usd_per_min, transcribed),
        "cost": cost(duration_s, berget_usd_per_min, transcribed),
    }


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)  # SQLite tappar tidszonen


def progress(status: str, events: list[tuple[str, float, datetime, str]], duration_s: float | None,
             now: datetime | None = None, transcribe_rate: float = TRANSCRIBE_S_PER_VOD_S) -> dict:
    """Stegen räknade ur clipsmiths händelser (stage, pct, tid, text), i den ordning de skrevs.

    Ett steg är klart när ett senare steg har börjat eller körningen är klar.
    """
    # Körs "Gör klippen kampanjklara igen" kommer nya händelser efter en tidigare done. Räkna bara dem.
    dones = [i for i, (stage, *_) in enumerate(events[:-1]) if stage == "done"]
    if dones:
        events = events[dones[-1] + 1:]
    started: dict[str, datetime] = {}
    messages: dict[str, str] = {}  # senaste text per steg; clipsmiths egna händelser har ingen text
    current = -1
    done_at = None
    for stage, _, at, message in events:
        if stage == "done":
            done_at = _utc(at)
        step = STAGE_TO_STEP.get(stage)
        if step is None:
            continue
        started.setdefault(step, _utc(at))
        if message:
            messages[step] = message
        # max: clipsmith skickar download och transcribe en gång till efter kampanjens egna
        current = max(current, STEP_IDS.index(step))
    if status == "failed" and current < 0:
        current = 0  # misslyckades innan första händelsen

    estimates = step_seconds(duration_s, transcribe_rate=transcribe_rate)
    now = now or datetime.now(UTC)
    steps, eta = [], 0
    for i, (step, label) in enumerate(STEPS):
        if status == "done" or i < current:
            state = "done"
        elif i == current:
            state = "failed" if status == "failed" else "running"
        else:
            state = "waiting"
        later = [started[s] for s in STEP_IDS[i + 1:] if s in started]
        finished = (min(later) if later else done_at) if state == "done" and step in started else None
        steps.append({"id": step, "label": label, "status": state,
                      "started_at": started[step].isoformat() if step in started else None,
                      "finished_at": finished.isoformat() if finished else None,
                      "estimate_s": estimates.get(step), "message": messages.get(step)})
        if state == "waiting":
            eta += estimates.get(step, 0)
        elif state == "running":
            eta += max(estimates.get(step, 0) - int((now - started[step]).total_seconds()), 0)

    last_pct = events[-1][1] if events else 0.0
    return {
        "stage": "done" if status == "done" else (STEP_IDS[current] if current >= 0 else None),
        "pct": 100.0 if status == "done" else last_pct,
        "message": (events[-1][3] or None) if events else None,
        "steps": steps,
        "eta_s": eta if estimates and status in ("pending", "running") else None,
    }
