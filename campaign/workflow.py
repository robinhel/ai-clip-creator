"""Gör klippen från en clipsmith-körning kampanjklara och lägger dem i godkänn-kön."""

from __future__ import annotations

import json
import re
from pathlib import Path

from clipsmith.llm.base import SUBSCORES
from clipsmith.selection.selector import SIGNALS_REASON

from .brain import Brain
from .budget import BudgetExceeded
from .caption import PLACEHOLDER, build_caption
from .cliplog import ClipLog
from .config import Settings
from .editor import edit_meta, source_path
from .finish import finish_clip
from .media import probe
from .profile import Profile
from .review import review_clip, stacked_split
from .rules import check_clip, check_source
from .subtitles import load_words
from .timemap import prepare

FORCED_REASON = "Claude valde inte klippet. Det togs med för att nå min_picks."


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
    picked = {key: common[key] for key in ("title", "start_s", "end_s", "score", "reason", "subscores")}
    if common.get("clip_format"):
        picked["format"] = common["clip_format"]
    log.update(redo["id"], **picked, **fields)
    return redo["id"]


def finish_vod(
    vod_id: str,
    profile: Profile,
    settings: Settings,
    log: ClipLog,
    brain: Brain | None = None,
    game: str | None = None,
    review: bool = True,
    subdir: str | None = None,
) -> list[dict]:
    """Gå igenom out/<vod_id>/clip_*.mp4 och gör varje klipp kampanjklart."""
    clip_dir = settings.out_dir / vod_id
    if subdir:
        clip_dir = clip_dir / subdir
    files = sorted(clip_dir.glob("clip_*.mp4"))
    if not files:
        raise SystemExit(f"Hittar inga klipp i {clip_dir}. Kör clipsmith först.")
    own = clip_dir / "picks.json"  # en körning i out/<vod>/run_<id>/ har sina egna val
    picks = load_picks(own if own.exists() else settings.work_dir / vod_id / "picks.json")
    existing = {clip["file"]: clip for clip in log.list()}

    results: list[dict] = []
    for src in files:
        index = int(re.match(r"clip_(\d+)", src.name).group(1))
        pick = picks[index - 1] if 0 < index <= len(picks) else {}
        title = pick.get("title") or title_from_name(src)
        hook = pick.get("title") or ""  # filnamnet blir ingen hook-text
        hook = "" if PLACEHOLDER.search(hook) else hook  # snabblägets "Moment h:mm:ss" blir ingen hook-text
        start, end = pick.get("start"), pick.get("end")
        out = clip_dir / "klara" / src.name

        before = existing.get(str(out)) or existing.get(str(src))
        redo = before if before and before["status"] == "underkand" else None
        if before and not redo:
            reason = "du har nekat klippet" if before["status"] == "nekad" else "finns redan i klipploggen"
            results.append({"file": src.name, "skipped": reason})
            continue
        if redo and edit_meta(src):
            # Klippet är redigerat i appen: loggens rubrik och tider gäller, inte picks.json. edit_meta kollar
            # råklippets storlek och ändringstid, så ett nytt råklipp från clipsmith får picks.json igen.
            title, start, end = redo["title"], redo["start_s"], redo["end_s"]
            hook = "" if title == title_from_name(src) else title  # filnamnsreserven blir ingen hook
        if not redo and start is not None and end is not None and log.overlaps(vod_id, start, end):
            results.append({"file": src.name, "skipped": "samma del av VOD:en är redan klippt"})
            continue

        common = dict(
            vod_id=vod_id, profile=profile.name, title=title, start_s=start, end_s=end,
            clip_format=pick.get("format"), signals=pick.get("signals"),
            score=pick.get("score"), reason=pick.get("reason", ""), subscores=pick.get("subscores"),
        )

        source_problems = check_source(probe(src))
        if source_problems:
            clip_id = _save(log, redo, common, file=str(src), caption="", status="underkand",
                           problems=source_problems, warnings=[], review=None)
            if clip_id is None:
                results.append({"file": src.name, "skipped": "klippet ändrades under tiden"})
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
                notes.append(f"Bildgranskningen hoppades över: {error}")
            except Exception as error:  # nätverksfel m.m. ska inte stoppa hela körningen
                notes.append(f"Bildgranskningen misslyckades: {error}")

        problems, warnings = check_clip(
            probe(out), manifest, profile, caption, pick.get("format"), review_result
        )
        warnings += notes
        clip_id = _save(
            log, redo, common, file=str(out), caption=caption, status="underkand" if problems else "renderad",
            problems=problems, warnings=warnings, review=review_result,
        )
        if clip_id is None:
            results.append({"file": src.name, "skipped": "klippet ändrades under tiden"})
            continue
        results.append(
            {"id": clip_id, "file": out.name, "title": title, "caption": caption,
             "problems": problems, "warnings": warnings}
        )
    return results
