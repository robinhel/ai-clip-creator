"""Gör klippen från en clipsmith-körning kampanjklara och lägger dem i godkänn-kön."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from clipsmith.llm.base import SUBSCORES
from clipsmith.selection.selector import SIGNALS_REASON

from .brain import Brain
from .budget import BudgetExceeded
from .caption import PLACEHOLDER, build_caption, headline_text
from .cliplog import ClipLog
from .config import Settings
from .editor import edit_meta, source_path
from .finish import finish_clip, hook_clean
from .media import probe
from .profile import Profile
from .review import review_clip, stacked_split
from .rules import check_clip, check_source
from .subtitles import load_words
from .timemap import prepare

FORCED_REASON = "Claude valde inte klippet. Det togs med för att nå min_picks."


class RunCancelled(Exception):
    """Robin tryckte Avbryt. Körningen stoppar mellan två steg, så att inga halva filer blir kvar."""


def load_picks(path: Path) -> list[dict]:
    """Läs clipsmiths picks.json. Klipp nummer N hör ihop med rad N i filen."""
    if not path.exists():
        return []
    picks = []
    for item in json.loads(path.read_text(encoding="utf-8")):
        pick = item.get("pick", {})
        reason = pick.get("reason") or ""
        forced = reason.startswith("Force-accepted")  # clipsmiths min_picks, inte Claudes val
        picks.append(
            {
                "start": pick.get("start_offset_s"),
                "end": pick.get("end_offset_s"),
                "title": pick.get("title") or pick.get("title_es") or "",
                # kort rad för bilden (val v2); tom i äldre picks
                "headline": pick.get("headline", ""),
                "format": pick.get("format"),
                "signals": item.get("candidate", {}).get("sources", []),
                # None för picks från före betyget och för snabbläget: inget betyg från Claude att visa
                "score": None if forced or reason.startswith(SIGNALS_REASON) else pick.get("score"),
                "reason": FORCED_REASON if forced else reason,
                # Claudes delpoäng 0-25 (val v2). None när svaret saknade dem: då sparade clipsmith nollor.
                "subscores": None if forced or not any(pick.get(key) for key in SUBSCORES)
                else {key: pick.get(key) or 0 for key in SUBSCORES},
            }
        )
    return picks


TITLE_WORDS = 8


def spoken_title(transcript: Path, start: float | None, end: float | None) -> str:
    """Snabbläget har ingen rubrik från Claude. De första orden som sägs i klippet blir rubriken, så att klippet
    går att granska. Tomt när inget sägs eller transkriptet saknas."""
    if start is None or end is None or not transcript.exists():
        return ""
    try:
        segments = json.loads(transcript.read_text(encoding="utf-8")).get("segments", [])
    except ValueError:
        return ""
    said = [w["word"].strip() for s in segments for w in s.get("words") or []
            if start <= w["start"] < end and w["word"].strip()]
    if not said:
        return ""
    title = " ".join(said[:TITLE_WORDS])
    if len(said) > TITLE_WORDS:
        title = title.rstrip(",;:") + "…"
    return title[0].upper() + title[1:]


def title_from_name(path: Path) -> str:
    """clip_03_too_close_call.mp4 blir 'Too close call'."""
    words = re.sub(r"^clip_\d+_?", "", path.stem).replace("_", " ").strip()
    return words.capitalize() or path.stem


def _save(log: ClipLog, redo: dict | None, common: dict, **fields) -> int | None:
    """Spara klippet i loggen. Ett underkänt klipp som görs om behåller sin rad.

    Returnerar None om klippet har ändrats under tiden, t.ex. nekats i kön.
    """
    if redo is None:
        return log.add(**common, **fields)
    if log.get(redo["id"])["status"] != "underkand":
        return None
    picked = {key: common[key] for key in ("title", "headline", "start_s", "end_s", "score", "reason", "subscores")}
    if common.get("clip_format"):
        picked["format"] = common["clip_format"]
    log.update(redo["id"], **picked, **fields)
    return redo["id"]


def _raw_clips(vod_id: str, settings: Settings, subdir: str | None) -> tuple[Path, list[Path], list[dict]]:
    """Mappen med råklippen, råklippen och valen från clipsmith."""
    clip_dir = settings.out_dir / vod_id / subdir if subdir else settings.out_dir / vod_id
    files = sorted(clip_dir.glob("clip_*.mp4"))
    if not files:
        raise SystemExit(f"No clips found in {clip_dir}. Run clipsmith first.")
    own = clip_dir / "picks.json"  # en körning i out/<vod>/run_<id>/ har sina egna val
    return clip_dir, files, load_picks(own if own.exists() else settings.work_dir / vod_id / "picks.json")


def finish_vod(
    vod_id: str,
    profile: Profile,
    settings: Settings,
    log: ClipLog,
    brain: Brain | None = None,
    game: str | None = None,
    review: bool = True,
    subdir: str | None = None,
    stop: Callable[[], None] = lambda: None,  # kastar RunCancelled när Robin har avbrutit
) -> list[dict]:
    """Gå igenom out/<vod_id>/clip_*.mp4 och gör varje klipp kampanjklart."""
    clip_dir, files, picks = _raw_clips(vod_id, settings, subdir)
    existing = {clip["file"]: clip for clip in log.list()}

    results: list[dict] = []
    for src in files:
        stop()  # före varje klipp: klara klipp är sparade, och inget nytt klipp eller anrop till Claude börjar
        index = int(re.match(r"clip_(\d+)", src.name).group(1))
        pick = picks[index - 1] if 0 < index <= len(picks) else {}
        title = pick.get("title") or title_from_name(src)
        headline = hook_clean(pick.get("headline") or "")  # "" utan headline
        hook = headline_text(pick)  # captionen byggs av title nedan, aldrig av headline
        start, end = pick.get("start"), pick.get("end")
        if PLACEHOLDER.search(title):  # rubrik och caption utan platshållaren, men ingen hook-text av talet
            title = spoken_title(settings.work_dir / vod_id / "transcript.json", start, end) or title
        out = clip_dir / "klara" / src.name

        before = existing.get(str(out)) or existing.get(str(src))
        redo = before if before and before["status"] == "underkand" else None
        if before and not redo:
            reason = "you rejected the clip" if before["status"] == "nekad" else "already in the clip log"
            results.append({"file": src.name, "skipped": reason})
            continue
        if redo and edit_meta(src):
            # Klippet är redigerat i appen: loggens rubrik och tider gäller, inte picks.json. edit_meta kollar
            # råklippets storlek och ändringstid, så ett nytt råklipp från clipsmith får picks.json igen.
            title, headline, start, end = redo["title"], redo["headline"], redo["start_s"], redo["end_s"]
            # Filnamnsreserven blir ingen text i bilden, men en headline blir det.
            hook = headline_text({"headline": headline, "title": "" if title == title_from_name(src) else title})
        if not redo and start is not None and end is not None and log.overlaps(vod_id, start, end):
            results.append({"file": src.name, "skipped": "the same part of the VOD is already clipped"})
            continue

        common = dict(
            vod_id=vod_id, profile=profile.name, title=title, headline=headline, start_s=start, end_s=end,
            clip_format=pick.get("format"), signals=pick.get("signals"),
            score=pick.get("score"), reason=pick.get("reason", ""), subscores=pick.get("subscores"),
        )

        source_problems = check_source(probe(src))
        if source_problems:
            clip_id = _save(log, redo, common, file=str(src), caption="", status="underkand",
                           problems=source_problems, warnings=[], review=None)
            if clip_id is None:
                results.append({"file": src.name, "skipped": "the clip changed in the meantime"})
                continue
            results.append({"id": clip_id, "file": src.name, "problems": source_problems})
            continue

        # Ordfilen finns bara när appen skar klippet utan undertext. Saknas den, eller hör den till ett annat
        # råklipp, bränns ingen undertext: den kan redan vara inbränd.
        words = load_words(src, source_path(src))
        ass: Path | None = None
        try:
            plan = None
            if words:
                ass = src.with_name(f".{src.stem}.ass")
                meta = edit_meta(src)
                in_s, out_s = (meta["in_s"], meta["out_s"]) if meta else (0.0, probe(src)["duration"])
                plan = prepare(src, words, in_s, out_s, profile, ass)  # tystnad och ehm bort; samma som redigeraren
            manifest = finish_clip(src, out, profile, settings.root, settings.encoder, title=hook, subtitles=ass,
                                   plan=plan, frame=words["reframe"]["mode"] if words else "")
        finally:
            if ass:
                ass.unlink(missing_ok=True)
        caption = build_caption(title, profile, game)
        out.with_suffix(".txt").write_text(caption + "\n", encoding="utf-8")

        review_result, notes = None, []
        if review and brain is not None and brain.available:
            try:
                review_result = review_clip(
                    out, profile, brain, settings.review_model,
                    settings.work_dir / vod_id / "rutor",
                    split_ratio=stacked_split(profile, settings.root),
                )
            except BudgetExceeded as error:
                notes.append(f"The image review was skipped: {error}")
            except Exception as error:  # nätverksfel m.m. ska inte stoppa hela körningen
                notes.append(f"The image review failed: {error}")

        problems, warnings = check_clip(
            probe(out), manifest, profile, caption, pick.get("format"), review_result
        )
        warnings += notes
        clip_id = _save(
            log, redo, common, file=str(out), caption=caption, status="underkand" if problems else "renderad",
            problems=problems, warnings=warnings, review=review_result,
        )
        if clip_id is None:
            results.append({"file": src.name, "skipped": "the clip changed in the meantime"})
            continue
        results.append(
            {"id": clip_id, "file": out.name, "title": title, "caption": caption,
             "problems": problems, "warnings": warnings}
        )
    return results
