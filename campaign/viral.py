"""Virala klipp: hitta var i streamerns VOD ett viralt klipp kommer ifrån och jämför med appens egna klipp.

Andras klipp analyseras bara. Inget här postar eller renderar något från dem.

Källor: en mp4-fil, en mapp med mp4-filer, en Content Rewards-kampanjlänk (kampanjens toppklipp) eller en annan
länk (TikTok, Instagram, YouTube via yt-dlp). Varje klipp sparas i work/<VOD>/viral/<id>/ med video, meta.json
(källa, länk, visningar, datum) och transcript.json, så att en ny körning inte hämtar eller transkriberar igen.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml

from clipsmith.models.transcript import Transcript, Word

from . import examples
from .facit import compare
from .media import probe
from .workflow import load_picks

MIN_WORDS = 6  # så många ord i rad krävs för en bit; fyra-fem ord i rad finns i varje lång stream
GAP = 2  # så många felhörda eller extra ord i rad hoppas över utan att biten delas
MIN_SHARE = 0.4  # minst så stor del av klippets ord ska stämma; annars är det en annan stream med en vanlig fras
MAX_S = 600  # längre klipp än 10 minuter är inga korta klipp och transkriberas inte
MAX_BYTES = 300_000_000  # största fil som hämtas
Transcribe = Callable[[Path, Path], Transcript]


class DownloadFailed(Exception):
    """Ett klipp eller en källa gick inte att hämta. Körningen fortsätter med nästa."""


def _clean(text: object) -> str:
    """Text utan styrtecken, så att en titel inte kan ändra terminalen."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(text or ""))


def _norm(word: str) -> str:
    return re.sub(r"[^\w]", "", word.lower())


def _words(words: list[Word]) -> list[Word]:
    return [w for w in words if _norm(w.word)]


def _anchors(
    clip: list[str], vod: list[str], matcher: SequenceMatcher, lo: int, hi: int
) -> list[list[int]]:
    """Längsta gemensamma ordföljd i clip[lo:hi], sedan samma sak till vänster och höger om den."""
    if hi - lo < MIN_WORDS:
        return []
    i, j, size = matcher.find_longest_match(0, len(vod), lo, hi)
    if size < MIN_WORDS:
        return []
    left = _anchors(clip, vod, matcher, lo, j)
    right = _anchors(clip, vod, matcher, j + size, hi)
    return [
        *left,
        [j, j + size, i, i + size, size],
        *right,
    ]  # klipp från-till, VOD från-till, antal ord som stämmer


def _step(
    clip: list[str], vod: list[str], c: int, v: int, d: int, c_stop: int
) -> tuple[int, int] | None:
    """Hoppa över högst GAP ord i klippet och i VOD:en, åt hållet d. Ger nästa ställe där två ord i rad stämmer."""
    tries = sorted(((dc, dv) for dc in range(GAP + 1) for dv in range(GAP + 1)), key=sum)
    for dc, dv in tries:
        ci, vi = c + d * dc, v + d * dv
        pairs = [(ci, vi), (ci + d, vi + d)]
        if all(
            min(c_stop, c) <= a <= max(c_stop, c) and 0 <= b < len(vod) and clip[a] == vod[b]
            for a, b in pairs
        ):
            return ci, vi
    return None


def _extend(clip: list[str], vod: list[str], piece: list[int], lo: int, hi: int) -> None:
    """Väx biten framåt och bakåt förbi enstaka felhörda ord, inom klippets ord lo till hi."""
    while piece[1] < hi and (nxt := _step(clip, vod, piece[1], piece[3], 1, hi - 1)):
        c, v = nxt
        while c < hi and v < len(vod) and clip[c] == vod[v]:
            c, v, piece[4] = c + 1, v + 1, piece[4] + 1
        piece[1], piece[3] = c, v
    while piece[0] > lo and (prev := _step(clip, vod, piece[0] - 1, piece[2] - 1, -1, lo)):
        c, v = prev
        while c >= lo and v >= 0 and clip[c] == vod[v]:
            c, v, piece[4] = c - 1, v - 1, piece[4] + 1
        piece[0], piece[2] = c + 1, v + 1


def find(clip_words: list[Word], vod_words: list[Word]) -> list[dict]:
    """Bitarna i VOD:en som klippet består av, i klippets ordning: start, slut (sekunder i VOD:en) och hits (ord som
    stämmer). Tom lista när inget stämmer i minst MIN_WORDS ord i rad, eller när färre än MIN_SHARE av klippets ord
    stämmer: då gissas inget."""
    cw, vw = _words(clip_words), _words(vod_words)
    clip, vod = [_norm(w.word) for w in cw], [_norm(w.word) for w in vw]
    matcher = SequenceMatcher(None, vod, clip, autojunk=False)
    pieces = _anchors(clip, vod, matcher, 0, len(clip))
    for n, piece in enumerate(pieces):
        lo = pieces[n - 1][1] if n else 0
        hi = pieces[n + 1][0] if n + 1 < len(pieces) else len(clip)
        _extend(clip, vod, piece, lo, hi)
    merged: list[list[int]] = []
    for piece in pieces:  # två bitar som följer på varandra i både klippet och VOD:en är en bit
        last = merged[-1] if merged else None
        if last and piece[0] - last[1] <= GAP and 0 <= piece[2] - last[3] <= GAP:
            last[1], last[3], last[4] = piece[1], piece[3], last[4] + piece[4]
        else:
            merged.append(piece)
    if sum(p[4] for p in merged) < MIN_SHARE * len(clip):
        return []
    return [{"start": vw[p[2]].start, "end": vw[p[3] - 1].end, "hits": p[4]} for p in merged]


def cold_open(pieces: list[dict]) -> bool:
    """Klippet visar något senare i VOD:en före något tidigare, oftast slutet först."""
    return any(b["start"] < a["start"] for a, b in zip(pieces, pieces[1:], strict=False))


def clock(seconds: float) -> str:
    minutes, s = divmod(int(seconds), 60)
    return f"{minutes // 60}:{minutes % 60:02d}:{s:02d}"


def _moment(result: dict) -> dict:
    """Hela spannet i VOD:en, och huvudbiten (den med flest ord som stämmer). time är mitten av huvudbiten."""
    main = max(result["pieces"], key=lambda p: p["hits"])
    start = min(p["start"] for p in result["pieces"])
    end = max(p["end"] for p in result["pieces"])
    return {
        "time": (main["start"] + main["end"]) / 2,
        "start": start,
        "end": end,
        "main_start": main["start"],
        "main_end": main["end"],
    }


def _diff(seconds: float, verb: str) -> str:
    if seconds == 0:
        return f"{verb} samtidigt"
    when = "tidigare" if seconds < 0 else "senare"
    return f"{verb} {abs(seconds):.1f} s {when}".replace(".", ",")


def report(results: list[dict], picks: list[dict] | None) -> list[str]:
    """Rader att skriva ut per viralt klipp. Med picks: hittat eller inte och skillnaden i start och slut."""
    lines: list[str] = []
    clips = [(p["start"], p["end"]) for p in picks or [] if p.get("start") is not None]
    found = total = elsewhere = 0
    for n, result in enumerate(results, 1):
        views = (
            ", " + f"{result['views']:,}".replace(",", " ") + " visningar"
            if result.get("views")
            else ""
        )
        title = _clean(result.get("title")).strip() or "Utan titel"
        lines.append(f"{n}. {title}{views}  {_clean(result.get('url'))}".rstrip())
        if not result["pieces"]:
            elsewhere += 1
            lines.append(
                "   Ingen träff i VOD:en. Klippet kommer nog från en annan stream, eller är för kort."
            )
            continue
        where = " + ".join(f"{clock(p['start'])}–{clock(p['end'])}" for p in result["pieces"])
        lines.append(
            f"   I VOD:en: {where}"
            + ("  (cold open: slutet visas först)" if cold_open(result["pieces"]) else "")
        )
        if picks is None:
            continue
        total += 1
        hit = compare([_moment(result)], clips)[0]
        if hit["found"]:
            found += 1
            start, end = hit["clip"]
            lines.append(
                f"   Hittat: appens klipp {clock(start)}–{clock(end)} "
                f"{_diff(hit['start_diff'], 'börjar')} och {_diff(hit['end_diff'], 'slutar')}."
            )
        else:
            lines.append("   Appen missade det.")
    if picks is not None:
        tail = f" från den här VOD:en ({elsewhere} fanns inte i den)" if elsewhere else ""
        lines.append(f"Appen hittade {found} av {total} virala klipp{tail}.")
    return lines


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:30] or "klipp"


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:8]


def _save(folder: Path, meta: dict) -> dict:
    """meta.json skrivs sist: finns den är klippet helt hämtat."""
    (folder / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {**meta, "dir": folder}


def _cached(folder: Path) -> dict | None:
    meta = folder / "meta.json"
    return (
        {**json.loads(meta.read_text(encoding="utf-8")), "dir": folder} if meta.exists() else None
    )


def _local(path: Path, viral_dir: Path) -> list[dict]:
    files = sorted(path.glob("*.mp4")) if path.is_dir() else [path]
    if not files:
        raise SystemExit(f"Inga mp4-filer i {path}.")
    clips = []
    for file in files:
        folder = viral_dir / f"fil-{_slug(file.stem)}-{_hash(str(file.resolve()))}"
        if not (meta := _cached(folder)):
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, folder / "video.mp4")
            meta = _save(
                folder,
                {
                    "source": "fil",
                    "url": str(file.resolve()),
                    "title": file.stem,
                    "views": None,
                    "date": None,
                    "file": "video.mp4",
                },
            )
        clips.append(meta)
    return clips


def _download(url: str, file: Path) -> None:
    """Hämta en mp4 från Content Rewards CDN. Avbryter över MAX_BYTES."""
    size = 0
    try:
        with examples.http_client() as http, http.stream("GET", url) as response:
            if response.status_code != 200:
                raise DownloadFailed(f"Content Rewards svarade {response.status_code}.")
            with file.open("wb") as out:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise DownloadFailed("filen är större än 300 MB.")
                    out.write(chunk)
    except (httpx.HTTPError, DownloadFailed) as error:
        file.unlink(missing_ok=True)
        if isinstance(error, DownloadFailed):
            raise
        raise DownloadFailed(f"nätverksfel ({type(error).__name__}).") from error


def _content_rewards(url: str, viral_dir: Path) -> list[dict]:
    try:
        found = examples.fetch(url)["clips"]
    except RuntimeError as error:
        raise DownloadFailed(str(error)) from error
    clips = []
    for clip in found:
        if not clip["video_url"]:
            continue
        # post_url är stabil; CDN-länken kan vara signerad och ändras
        folder = viral_dir / f"cr-{_hash(clip['post_url'] or clip['video_url'])}"
        meta = {
            "source": "contentrewards",
            "campaign": url,
            "url": clip["post_url"],
            "video_url": clip["video_url"],
            "platform": clip["platform"],
            "title": clip["title"],
            "views": clip["views"],
            "date": None,
            "file": "video.mp4",
        }
        if not _cached(folder):
            folder.mkdir(parents=True, exist_ok=True)
            print(f"  hämtar {_clean(clip['post_url'] or clip['video_url'])}", flush=True)
            try:
                _download(clip["video_url"], folder / "video.mp4")
            except DownloadFailed as error:
                print(f"  Kunde inte hämta klippet, hoppar över det: {error}", flush=True)
                continue
        clips.append(_save(folder, meta))  # visningarna uppdateras vid varje körning
    return clips


def _date(info: dict) -> str | None:
    if info.get("timestamp"):
        return datetime.fromtimestamp(info["timestamp"], UTC).date().isoformat()
    raw = info.get("upload_date")
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}" if raw else None


class _Silent:
    def debug(self, msg: str) -> None:
        pass

    warning = error = debug


def _ytdlp(url: str, viral_dir: Path) -> list[dict]:
    folder = viral_dir / f"{_slug(urlparse(url).hostname or 'lank')}-{_hash(url)}"
    if meta := _cached(folder):
        return [meta]
    try:
        import yt_dlp
    except ImportError:
        raise SystemExit('yt-dlp saknas. Installera det med: pip install -e ".[viral]"') from None
    folder.mkdir(parents=True, exist_ok=True)
    options = {
        "outtmpl": str(folder / "video.%(ext)s"),
        "format": "b[ext=mp4]/bv*+ba/b",
        "merge_output_format": "mp4",
        "quiet": True,
        "noprogress": True,
        "noplaylist": True,
        "logger": _Silent(),  # felet blir en kort rad från oss i stället
        "max_filesize": MAX_BYTES,
        "match_filter": yt_dlp.utils.match_filter_func(f"duration <? {MAX_S}"),
    }
    print(f"  hämtar {_clean(url)}", flush=True)
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            if ydl.extract_info(url, download=False, process=False).get("_type") == "playlist":
                raise DownloadFailed(
                    "länken är en spellista eller kanal. Ange länken till ett enda klipp."
                )
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as error:
        raise DownloadFailed(
            _clean(error).replace("ERROR: ", "").split(" (caused by")[0][:200]
        ) from error
    downloads = info.get("requested_downloads") or []
    if (
        info.get("_type") == "playlist"
        or not downloads
        or not Path(downloads[0]["filepath"]).exists()
    ):
        raise DownloadFailed(
            "klippet hämtades inte. Det är längre än 10 minuter, större än 300 MB eller en spellista."
        )
    file = Path(downloads[0]["filepath"])
    return [
        _save(
            folder,
            {
                "source": info.get("extractor_key") or "länk",
                "url": info.get("webpage_url") or url,
                "title": info.get("title") or "",
                "views": info.get("view_count"),
                "date": _date(info),
                "file": file.name,
            },
        )
    ]


def collect(source: str, viral_dir: Path) -> list[dict]:
    """Klippen från en källa, hämtade till viral_dir. Redan hämtade klipp hämtas inte igen."""
    if source.startswith(("http://", "https://")):
        if urlparse(source).hostname in examples.HOSTS:
            return _content_rewards(source, viral_dir)
        return _ytdlp(source, viral_dir)
    path = Path(source)
    if not path.exists():
        raise SystemExit(f"Hittar inte {source}. Ange en mp4-fil, en mapp eller en länk.")
    if path.suffix == ".txt":  # en lista med källor, till exempel utvalda.txt från samla lista
        clips = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not (line := line.strip()) or line.startswith("#"):
                continue
            if not line.startswith(("http://", "https://")) and Path(line).suffix == ".txt":
                print(f"  Hoppar över {_clean(line)}: en .txt-lista i en .txt-lista läses inte.", flush=True)
                continue
            try:
                clips += collect(line, viral_dir)
            except (DownloadFailed, SystemExit) as error:  # SystemExit: sökvägen finns inte
                print(f"  Kunde inte hämta {_clean(line)}, hoppar över den: {error}", flush=True)
        return clips
    return _local(path, viral_dir)


def _all_words(transcript: Transcript) -> list[Word]:
    return [w for s in transcript.segments for w in s.words]


def run(vod_id: str, sources: list[str], work_dir: Path, transcribe: Transcribe) -> int:
    vod_dir = work_dir / vod_id
    path = vod_dir / "transcript.json"
    if not path.exists():
        raise SystemExit(
            f"{path} saknas. Hämta och transkribera VOD:en först, till exempel med:\n"
            f"  clipsmith run-vod {vod_id} --config config.timmy.yaml"
        )
    vod = _all_words(Transcript.from_json(path.read_text(encoding="utf-8")))
    viral_dir = vod_dir / "viral"
    viral_dir.mkdir(parents=True, exist_ok=True)

    for source in sources:
        try:
            clips = collect(source, viral_dir)
        except DownloadFailed as error:
            print(f"  Kunde inte hämta {_clean(source)}, hoppar över den: {error}", flush=True)
            continue
        for clip in clips:
            out = clip["dir"] / "transcript.json"
            if out.exists():
                continue
            mp4 = clip["dir"] / clip["file"]
            try:
                seconds = probe(mp4)["duration"]
            except RuntimeError as error:  # TikTok-bildspel kommer som mp3 eller m4a
                if "ingen videoström" not in str(error):
                    raise
                print(
                    f"  {clip['dir'].name}: hoppar över, filen har ingen video (bildspel eller bara ljud).",
                    flush=True,
                )
                continue
            if seconds > MAX_S:
                print(
                    f"  Hoppar över {clip['dir'].name}: {seconds / 60:.0f} minuter är längre än 10 minuter.",
                    flush=True,
                )
                continue
            print(f"  transkriberar {clip['dir'].name}", flush=True)
            transcribe(mp4, out)

    results = []  # alla klipp som någon körning har hämtat och transkriberat för den här VOD:en
    for meta in sorted(viral_dir.glob("*/meta.json")):
        out = meta.parent / "transcript.json"
        if out.exists():
            transcript = Transcript.from_json(out.read_text(encoding="utf-8"))
            results.append({**_cached(meta.parent), "pieces": find(_all_words(transcript), vod)})

    moments = [
        {
            "time": clock(m["time"]),
            "start": round(m["start"], 1),
            "end": round(m["end"], 1),
            "note": " ".join(
                str(x)
                for x in (
                    _clean(r.get("title")).strip(),
                    r.get("views") and f"({r['views']} visningar)",
                    r.get("url"),
                )
                if x
            ),
        }
        for r in results
        if r["pieces"]
        for m in [_moment(r)]
    ]
    facit = viral_dir / "facit.yaml"
    facit.write_text(
        yaml.safe_dump({"vod": vod_id, "moments": moments}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    picks_path = vod_dir / "picks.json"
    picks = load_picks(picks_path) if picks_path.exists() else None
    if picks is None:
        print(
            f"{picks_path} saknas, så det finns inga klipp från appen att jämföra med. Här kommer klippen ifrån:"
        )
    for line in report(results, picks):
        print(line)
    print(f"Facit sparat i {facit}")
    return 0
