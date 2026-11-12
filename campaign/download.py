"""Hämta en VOD från Twitch.

twitch-dl laddar ner delarna, men dess hopslagning med ffmpeg fastnar när VOD:en var
live vid nedladdningen: spellistan saknar då slutmarkering, och ffmpeg väntar på fler
delar för evigt. Här slås delarna ihop genom att skickas i ordning till ffmpeg, och
varje del tas bort när den är läst. Då räcker disken till en kopia av videon.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from clipsmith.twitch.downloader import _resolve_quality

from .media import need


def join_segments(parts_dir: Path, out: Path) -> None:
    """Slå ihop delarna i playlist_downloaded.m3u8 till en mp4-fil."""
    lines = (parts_dir / "playlist_downloaded.m3u8").read_text(encoding="utf-8").splitlines()
    inits = {m.group(1) for line in lines if (m := re.search(r'#EXT-X-MAP:URI="([^"]+)"', line))}
    if len(inits) > 1:
        raise RuntimeError(f"VOD:en har {len(inits)} init-delar. Det stöds inte än.")
    segments = [parts_dir / line.strip() for line in lines if line.strip() and not line.startswith("#")]
    if not segments:
        raise RuntimeError(f"Inga nedladdade delar i {parts_dir}.")

    partial = out.with_name(f"{out.stem}.del.mp4")
    container = "mp4" if inits else "mpegts"  # fmp4-delar har en init-del, .ts-delar har ingen
    with tempfile.TemporaryFile() as errors:
        ffmpeg = subprocess.Popen(
            [need("ffmpeg"), "-v", "error", "-y", "-f", container, "-i", "pipe:0", "-c", "copy",
             "-f", "mp4", str(partial)],
            stdin=subprocess.PIPE, stderr=errors,
        )
        assert ffmpeg.stdin is not None
        try:
            for init in inits:
                with (parts_dir / init).open("rb") as part:
                    shutil.copyfileobj(part, ffmpeg.stdin)
            for segment in segments:
                with segment.open("rb") as part:
                    shutil.copyfileobj(part, ffmpeg.stdin)
                segment.unlink()
            ffmpeg.stdin.close()
        except BrokenPipeError:
            pass  # ffmpeg har stannat; felet står i errors
        code = ffmpeg.wait()
        errors.seek(0)
        message = errors.read().decode(errors="replace")
    if code != 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg kunde inte slå ihop delarna:\n{message[-1500:]}")
    partial.replace(out)


def download_vod(vod_id: str, work_dir: Path, quality: str = "1080p60") -> Path:
    """Ladda ner VOD:en till work/<id>/<id>.mp4. En fil som redan finns används direkt."""
    vod_dir = Path(work_dir) / vod_id
    out = vod_dir / f"{vod_id}.mp4"
    if out.exists():
        return out
    parts = vod_dir / "delar"
    subprocess.run(
        [sys.executable, "-m", "twitchdl", "download", vod_id,
         "--quality", _resolve_quality(vod_id, quality),
         "--no-join", "--cache-dir", str(parts)],
        check=True,
    )
    join_segments(parts, out)
    shutil.rmtree(parts, ignore_errors=True)
    return out
