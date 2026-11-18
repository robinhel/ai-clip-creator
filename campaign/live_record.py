"""Spela in en kanals levande ström i en rullande buffert, och klipp ut delar ur den.

Twitch sparar inte alltid en VOD av strömmen. Därför läser inspelaren kanalens mediaspellista var
annan sekund och sparar varje ny del i en mapp, med en rad per del i index.tsv. Delarnas tid är
spellistans PROGRAM-DATE-TIME, väggklockan, samma klocka som chattens tidsstämplar. Reklam som
Twitch lägger in i strömmen sparas inte. Inget token eller hel URL skrivs i loggen.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import random
import re
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import click
import httpx
import m3u8
from twitchdl import twitch
from twitchdl.playlists import parse_playlists, select_playlist_by_name

from .download import join_segments
from .live_part import PartFailed, PartNotReady

log = logging.getLogger(__name__)

USHER = "https://usher.ttvnw.net/api/channel/hls/{login}.m3u8"
LOGIN_RE = re.compile(r"[a-z0-9_]{1,25}")
QUALITY = "source"  # Robins val: källkvaliteten
TIMEOUT_S = 10.0
POLL_S = 2.0  # delarna är 2 s långa
FIRST_WAIT_S = 1.0  # väntan efter ett fel, dubblas upp till MAX_WAIT_S
MAX_WAIT_S = 60.0
MAX_HOLE = 0.3  # högst så stor del av fönstret får saknas (reklam, avbrott)
INDEX = "index.tsv"
AD_CLASS = "twitch-stitched-ad"
AD_ID = "stitched-ad-"
NEW_URL = (403, 404)  # token har gått ut eller spellistan finns inte längre

TOKEN_QUERY = """
{
  streamPlaybackAccessToken(
    channelName: "%s",
    params: {platform: "web", playerBackend: "mediaplayer", playerType: "site"}
  ) {
    value
    signature
  }
}
"""


@dataclass
class Seg:
    """En del i mediaspellistan. start_wall är delens PDT i sekunder sedan 1970."""

    seq: int
    start_wall: float
    dur: float
    url: str
    ad: bool


@dataclass(frozen=True)
class _Row:
    """En rad i index.tsv: en sparad del."""

    seq: int
    start: float
    dur: float
    name: str

    @property
    def end(self) -> float:
        return self.start + self.dur

    def line(self) -> str:
        return f"{self.seq}\t{self.start:.3f}\t{self.dur:.3f}\t{self.name}\n"


def _fetch(url: str) -> bytes:
    """Hämta en URL med tidsgräns. HTTPStatusError vid 4xx och 5xx."""
    response = httpx.get(url, timeout=TIMEOUT_S)
    response.raise_for_status()
    return response.content


def _status(error: httpx.HTTPError) -> int | None:
    return error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None


def _reason(error: Exception) -> str:
    """Felet utan URL: httpx skriver URL:en, med token, i sin text."""
    status = _status(error) if isinstance(error, httpx.HTTPError) else None
    return f"HTTP {status}" if status else type(error).__name__


def live_playlist(login: str) -> str | None:
    """URL till kanalens mediaspellista i källkvalitet. None när kanalen inte är live."""
    login = login.strip().lower()
    if not LOGIN_RE.fullmatch(login):
        raise ValueError(f"{login[:40]!r} är inget Twitch-namn.")
    token = twitch.gql_query(TOKEN_QUERY % login)["data"]["streamPlaybackAccessToken"]
    if token is None:
        return None
    params = {
        "sig": token["signature"],
        "token": token["value"],
        "allow_source": "true",
        "allow_audio_only": "true",
        "player_backend": "mediaplayer",
        "playlist_include_framerate": "true",
        "supported_codecs": "avc1",  # H.264 i .ts-delar, som join_segments slår ihop
        "p": str(random.randint(0, 9_999_999)),  # noqa: S311  # bara mot cache, ingen säkerhet
    }
    try:
        master = _fetch(str(httpx.URL(USHER.format(login=login), params=params)))
    except httpx.HTTPStatusError as error:
        if _status(error) == 404:
            return None
        raise
    playlists = parse_playlists(master.decode("utf-8", errors="replace"))
    return select_playlist_by_name(playlists, QUALITY).url


def _wall(value: str) -> float:
    return dt.datetime.fromisoformat(value).timestamp()


def _ad_ranges(document: m3u8.M3U8) -> list[tuple[float, float]]:
    """Reklamens tider, start och slut, ur spellistans DATERANGE."""
    ranges = (r for seg in document.segments for r in seg.dateranges)
    return [
        (_wall(r.start_date), _wall(r.start_date) + (r.duration or 0.0))
        for r in ranges
        if (r.class_ == AD_CLASS or (r.id or "").startswith(AD_ID)) and r.start_date
    ]


def parse(text: str) -> list[Seg]:
    """Delarna i en mediaspellista. Prefetch-delar tas inte med, och delar utan PDT kan inte placeras."""
    document = m3u8.loads(text)
    ads = _ad_ranges(document)
    first = document.media_sequence or 0
    segs = []
    for index, seg in enumerate(document.segments):
        if seg.current_program_date_time is None:
            continue
        start = seg.current_program_date_time.timestamp()
        segs.append(
            Seg(
                first + index,
                start,
                float(seg.duration or 0.0),
                seg.uri or "",
                _is_ad(seg, start, ads),
            )
        )
    return segs


def _is_ad(seg: m3u8.Segment, start: float, ads: list[tuple[float, float]]) -> bool:
    """Reklam: titeln säger Amazon, eller delen börjar inom en reklam-DATERANGE."""
    return "Amazon" in (seg.title or "") or any(a <= start < b for a, b in ads)


def _name(seg: Seg) -> str:
    """Filnamnet: PDT i millisekunder. Seq kan börja om på 0 i en ny session, tiden gör det inte."""
    return f"{round(seg.start_wall * 1000)}.ts"


def _read_index(folder: Path) -> list[_Row]:
    """Raderna i index.tsv. En rad som inte är färdigskriven, utan radslut, räknas inte."""
    try:
        text = (folder / INDEX).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    rows = []
    for line in text.split("\n")[:-1]:
        fields = line.split("\t")
        try:
            rows.append(_Row(int(fields[0]), float(fields[1]), float(fields[2]), fields[3]))
        except (IndexError, ValueError):
            continue
    return rows


def _write_index(folder: Path, rows: list[_Row]) -> None:
    """Skriv om index.tsv i ett steg, så att cut aldrig läser en halv fil."""
    partial = folder / f"{INDEX}.del"
    partial.write_text("".join(row.line() for row in rows), encoding="utf-8")
    partial.replace(folder / INDEX)


class Recorder:
    """Spelar in kanalens levande ström till folder: delarna som <PDT i ms>.ts och en rad per del i
    index.tsv. Delar äldre än keep_s städas bort. Testerna byter playlist, get, now och sleep."""

    def __init__(self, login: str, folder: Path, keep_s: float = 600.0) -> None:
        self.login = login
        self.folder = Path(folder)
        self.keep_s = keep_s
        self._stopped = threading.Event()
        self.playlist: Callable[[str], str | None] = live_playlist
        self.get: Callable[[str], bytes] = _fetch
        self.now: Callable[[], float] = lambda: dt.datetime.now(dt.UTC).timestamp()
        self.sleep: Callable[[float], object] = self._stopped.wait  # stop() avbryter väntan
        self._wait = FIRST_WAIT_S
        self._saved: set[str] = set()

    def stop(self) -> None:
        """Får run() att sluta inom ett varv."""
        self._stopped.set()

    def run(self) -> None:
        """Spela in tills kanalen inte är live längre eller stop() anropas."""
        self.folder.mkdir(parents=True, exist_ok=True)
        self._saved = {row.name for row in _read_index(self.folder)}  # förra sessionen ligger kvar
        url = self._resolve()
        while url is not None and not self._stopped.is_set():
            url = self._round(url)

    def _round(self, url: str) -> str | None:
        """Ett varv: städa, läs spellistan, spara nya delar. Svarar med URL:en till nästa varv."""
        started = self.now()
        self._clean()
        try:
            text = self.get(url).decode("utf-8", errors="replace")
        except httpx.HTTPError as error:
            return self._after_error(url, error)
        self._wait = FIRST_WAIT_S
        self._save(parse(text))
        self.sleep(max(0.0, POLL_S - (self.now() - started)))
        return url

    def _after_error(self, url: str, error: httpx.HTTPError) -> str | None:
        """Vänta längre för varje fel i rad. Vid 403 och 404 behövs en ny URL med nytt token."""
        log.warning("Spellistan för %s svarade inte (%s).", self.login, _reason(error))
        self._back_off()
        return self._resolve() if _status(error) in NEW_URL else url

    def _back_off(self) -> None:
        self.sleep(self._wait)
        self._wait = min(self._wait * 2, MAX_WAIT_S)

    def _resolve(self) -> str | None:
        """Ny URL till mediaspellistan. None när kanalen inte är live eller stop() har anropats."""
        while not self._stopped.is_set():
            try:
                return self.playlist(self.login)
            except (httpx.HTTPError, click.ClickException) as error:  # GQL-fel är ClickException
                log.warning(
                    "Kunde inte fråga Twitch om %s är live (%s).", self.login, _reason(error)
                )
                self._back_off()
        return None

    def _save(self, segs: list[Seg]) -> None:
        """Spara nya delar som inte är reklam. En del som inte gick att hämta prövas nästa varv."""
        for seg in segs:
            name = _name(seg)
            if seg.ad or name in self._saved:
                continue
            try:
                data = self.get(seg.url)
            except httpx.HTTPError as error:
                log.warning(
                    "Del %s från %s gick inte att hämta (%s).", seg.seq, self.login, _reason(error)
                )
                continue
            partial = self.folder / f"{name}.del"
            partial.write_bytes(data)
            partial.replace(self.folder / name)  # filen finns hel innan raden skrivs
            row = _Row(seg.seq, seg.start_wall, seg.dur, name)
            with (self.folder / INDEX).open("a", encoding="utf-8") as index:
                index.write(row.line())
            self._saved.add(name)

    def _clean(self) -> None:
        """Ta bort delar som slutade för mer än keep_s sedan, först raden och sedan filen."""
        rows = _read_index(self.folder)
        limit = self.now() - self.keep_s
        old = [row for row in rows if row.end < limit]
        if not old:
            return
        _write_index(self.folder, [row for row in rows if row.end >= limit])
        for row in old:
            (self.folder / row.name).unlink(missing_ok=True)
            self._saved.discard(row.name)


def _link(src: Path, dst: Path) -> None:
    """Hårdlänka delen, eller kopiera den när länk inte går. join_segments tar bort det den läst."""
    try:
        os.link(src, dst)
    except FileNotFoundError:
        raise
    except OSError:
        shutil.copy2(src, dst)


def _overlap(row: _Row, start_wall: float, end_wall: float) -> float:
    return min(row.end, end_wall) - max(row.start, start_wall)


def _window(rows: list[_Row], start_wall: float, end_wall: float) -> list[_Row]:
    """Delarna som täcker [start_wall, end_wall). PartFailed när för mycket saknas."""
    chosen = [row for row in rows if _overlap(row, start_wall, end_wall) > 0]
    length = end_wall - start_wall
    if not chosen:
        raise PartFailed("Inspelningen har inga delar i den tiden.")
    covered = sum(_overlap(row, start_wall, end_wall) for row in chosen)
    if length - covered > MAX_HOLE * length:
        raise PartFailed(
            f"Hålen i inspelningen (reklam eller avbrott) är {length - covered:.0f} s av {length:.0f} s."
        )
    return chosen


def _check(rows: list[_Row], start_wall: float, end_wall: float) -> None:
    """PartFailed när bufferten är tom eller början redan städad, PartNotReady när slutet inte finns än."""
    if not rows:
        raise PartFailed("Inspelningen har inga delar än.")
    if rows[0].start > start_wall:
        raise PartFailed(
            "Början av delen finns inte i inspelningen. Den är städad eller spelades aldrig in."
        )
    if rows[-1].end < end_wall:
        raise PartNotReady("Slutet av delen är inte inspelat än.")


def _join(folder: Path, chosen: list[_Row], out: Path) -> None:
    """Länka delarna till out/segments och slå ihop dem till out/<out.name>.mp4."""
    segments = out / "segments"
    shutil.rmtree(segments, ignore_errors=True)
    segments.mkdir(parents=True)
    try:
        for row in chosen:
            _link(folder / row.name, segments / row.name)
        playlist = "".join(f"#EXTINF:{row.dur:.3f},\n{row.name}\n" for row in chosen)
        (segments / "playlist_downloaded.m3u8").write_text("#EXTM3U\n" + playlist, encoding="utf-8")
        join_segments(segments, out / f"{out.name}.mp4")
    except FileNotFoundError as error:
        raise PartFailed("En del i tiden har redan städats bort ur inspelningen.") from error
    except (RuntimeError, OSError) as error:
        raise PartFailed(f"Kunde inte slå ihop delarna: {str(error)[:300]}") from error
    finally:
        shutil.rmtree(segments, ignore_errors=True)


def cut(folder: Path, start_wall: float, end_wall: float, out: Path) -> float:
    """Klipp ut [start_wall, end_wall) ur bufferten i folder till out/<out.name>.mp4.

    Svarar med väggklockan där den första delen börjar. PartNotReady: slutet är inte inspelat än,
    försök igen senare. PartFailed: delen går inte att göra, texten är kort och på svenska.
    """
    folder, out = Path(folder), Path(out)
    rows = sorted(_read_index(folder), key=lambda row: row.start)
    _check(rows, start_wall, end_wall)
    chosen = _window(rows, start_wall, end_wall)
    _join(folder, chosen, out)
    return chosen[0].start
