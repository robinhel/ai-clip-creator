"""Små hjälpfunktioner runt ffmpeg och ffprobe."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",  # macOS
    "C:/Windows/Fonts/arialbd.ttf",  # Windows
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def need(tool: str) -> str:
    path = shutil.which(tool)
    if path is None:
        raise SystemExit(f"{tool} was not found. Install ffmpeg and try again.")
    return path


def run(cmd: list[str]) -> None:
    """Kör ett kommando och visa slutet av felutskriften om det misslyckas."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} failed:\n{result.stderr[-1500:]}")


def probe(path: Path) -> dict:
    """Bredd, höjd, längd, bildrutor per sekund och om filen har ljud."""
    out = subprocess.run(
        [need("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe could not read {path}: {out.stderr[-500:]}")
    data = json.loads(out.stdout)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if video is None:
        raise RuntimeError(f"{path} has no video stream.")
    return {
        "width": int(video["width"]),
        "height": int(video["height"]),
        "duration": float(data["format"].get("duration", 0.0)),
        "fps": _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate")) or 30.0,
        "has_audio": any(s["codec_type"] == "audio" for s in data["streams"]),
    }


def _rate(value: str | None) -> float:
    """"30000/1001" blir 29,97. "0/0" eller inget blir 0."""
    num, _, den = (value or "0/0").partition("/")
    try:
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def grab_frames(path: Path, out_dir: Path, count: int = 4, width: int = 540) -> list[Path]:
    """Spara några bildrutor jämnt utspridda över klippet (för granskning)."""
    duration = probe(path)["duration"]
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: list[Path] = []
    for i in range(count):
        at = duration * (i + 0.5) / count
        frame = out_dir / f"{path.stem}_ruta{i + 1}.jpg"
        run(
            [need("ffmpeg"), "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", str(path),
             "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(frame)]
        )
        frames.append(frame)
    return frames


def find_font(preferred: str | None = None) -> str | None:
    """Första typsnittsfil som finns på datorn. None betyder: låt ffmpeg välja."""
    for candidate in [preferred, *FONT_CANDIDATES]:
        if candidate and Path(candidate).exists():
            return candidate
    return None
