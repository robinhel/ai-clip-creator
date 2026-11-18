"""Hämta en kort del av en VOD som Twitch spelar in just nu.

twitch-dl 3.3.2 körs med --no-join: dess egen hopslagning väntar för evigt på en spellista
utan slutmarkering, och dess --start klipper inte början. Delarna slås i stället ihop med
join_segments, och delens verkliga start läses ur spellistan. Varje mapp har en egen
cache-mapp, så ett nytt försök i samma mapp hämtar inte om delar som redan finns.
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .download import _hms, join_segments

TIMEOUT_S = 300.0  # en del på två minuter tar normalt under en minut att hämta
# source finns alltid. Ett namn som 1080p60 måste först slås upp med ett anrop utan tidsgräns.
QUALITY = "source"
TRY_LATER = "Try again a bit later"  # twitch-dl säger så när en färsk VOD saknar access token
ENDLIST = "#EXT-X-ENDLIST"  # spellistan är klar: streamen är slut


class PartNotReady(Exception):
    """Slutet av delen finns inte i spellistan än, eller Twitch säger försök senare."""


class PartFailed(Exception):
    """Delen gick inte att hämta. Texten är kort och på svenska."""


def _run(cmd: list[str], timeout: float) -> tuple[int, str]:
    """Kör twitch-dl med en tidsgräns. Svarar med slutkoden och utskriften."""
    try:
        done = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace", timeout=timeout
        )
    except subprocess.TimeoutExpired as error:
        raise PartFailed(f"twitch-dl svarade inte på {timeout:.0f} sekunder.") from error
    return done.returncode, done.stdout + done.stderr


def _durations(playlist: Path) -> list[float]:
    """Längden på varje del i spellistan, i ordning."""
    text = playlist.read_text(encoding="utf-8")
    return [float(m) for m in re.findall(r"^#EXTINF:([0-9.]+)", text, flags=re.MULTILINE)]


def _first_index(downloaded: Path) -> int:
    """Platsen i spellistan för den första hämtade delen. twitch-dl döper delarna efter platsen, 00002.ts."""
    lines = downloaded.read_text(encoding="utf-8").splitlines()
    first = next((line for line in lines if line.strip() and not line.startswith("#")), None)
    if first is None:
        raise PartFailed("twitch-dl hämtade inga delar.")
    return int(Path(first.strip()).stem)


def _check_ready(output: str, playlist: Path, end_s: float) -> None:
    """PartNotReady när Twitch säger försök senare, eller när slutet inte är inspelat än.
    Har spellistan ENDLIST är streamen slut: då räcker det som finns, även om det slutar före end_s."""
    if TRY_LATER in output:
        raise PartNotReady("Twitch har inte VOD:en klar än.")
    if not playlist.exists() or ENDLIST in playlist.read_text(encoding="utf-8"):
        return
    if sum(_durations(playlist)) < end_s:
        raise PartNotReady("Slutet av delen är inte inspelat än.")


def _check_done(code: int, output: str, playlist: Path) -> None:
    """PartFailed när twitch-dl avslutade med fel eller inte skrev någon spellista."""
    if code != 0:
        last = output.strip().splitlines()[-1:] or ["inget svar"]
        raise PartFailed(f"Kunde inte hämta delen ur VOD:en: {last[0][:300]}")
    if not playlist.exists():
        raise PartFailed("twitch-dl skrev ingen spellista.")


def fetch_part(vod_id: str, start_s: float, end_s: float, folder: Path) -> float:
    """Hämta start_s till end_s ur VOD:en till folder/<mappnamn>.mp4.

    Svarar med delens verkliga start i VOD:en: början av den Twitch-del som start_s ligger i.
    Är streamen slut före end_s blir delen kortare: det som finns hämtas.
    PartNotReady: försök igen senare med samma mapp. PartFailed: något annat gick fel.
    """
    folder = Path(folder)
    cache = folder / "segments"
    for old in ("playlist.m3u8", "playlist_downloaded.m3u8"):  # ett gammalt försök får inte räknas
        (cache / old).unlink(missing_ok=True)
    cmd = [
        sys.executable, "-m", "twitchdl", "download", vod_id, "--quality", QUALITY,
        "--start", _hms(max(0, int(start_s))), "--end", _hms(math.ceil(end_s)),
        "--no-join", "--overwrite",
        "--cache-dir", str(cache).replace("{", "{{").replace("}", "}}"),  # twitch-dl kör .format på den
    ]  # fmt: skip
    code, output = _run(cmd, TIMEOUT_S)
    playlist = cache / "playlist.m3u8"
    _check_ready(output, playlist, end_s)
    _check_done(code, output, playlist)
    start = sum(_durations(playlist)[: _first_index(cache / "playlist_downloaded.m3u8")])
    try:
        join_segments(cache, folder / f"{folder.name}.mp4")
    except (RuntimeError, OSError) as error:
        raise PartFailed(f"Kunde inte slå ihop delarna: {str(error)[:300]}") from error
    shutil.rmtree(cache, ignore_errors=True)
    return start
