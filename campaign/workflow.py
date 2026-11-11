"""Gör klippen från en clipsmith-körning kampanjklara och lägger dem i godkänn-kön."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .brain import Brain
from .budget import BudgetExceeded
from .caption import build_caption
from .cliplog import ClipLog
from .config import Settings
from .finish import finish_clip
from .media import probe
from .profile import Profile
from .review import review_clip
from .rules import check_clip, check_source


def load_picks(path: Path) -> list[dict]:
    """Läs clipsmiths picks.json. Klipp nummer N hör ihop med rad N i filen."""
    if not path.exists():
        return []
    picks = []
    for item in json.loads(path.read_text(encoding="utf-8")):
        pick = item.get("pick", {})
        picks.append(
            {
                "start": pick.get("start_offset_s"),
                "end": pick.get("end_offset_s"),
                "title": pick.get("title") or pick.get("title_es") or "",
                "format": pick.get("format"),
                "signals": item.get("candidate", {}).get("sources", []),
            }
        )
    return picks


def title_from_name(path: Path) -> str:
    """clip_03_too_close_call.mp4 blir 'Too close call'."""
    words = re.sub(r"^clip_\d+_?", "", path.stem).replace("_", " ").strip()
    return words.capitalize() or path.stem


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
    picks = load_picks(settings.work_dir / vod_id / "picks.json")
    done_files = {clip["file"] for clip in log.list() if clip["status"] != "nekad"}

    results: list[dict] = []
    for src in files:
        index = int(re.match(r"clip_(\d+)", src.name).group(1))
        pick = picks[index - 1] if 0 < index <= len(picks) else {}
        title = pick.get("title") or title_from_name(src)
        start, end = pick.get("start"), pick.get("end")
        out = clip_dir / "klara" / src.name

        if str(out) in done_files or str(src) in done_files:
            results.append({"file": src.name, "skipped": "finns redan i klipploggen"})
            continue
        if start is not None and end is not None and log.overlaps(vod_id, start, end):
            results.append({"file": src.name, "skipped": "samma del av VOD:en är redan klippt"})
            continue

        common = dict(
            vod_id=vod_id, profile=profile.name, title=title, start_s=start, end_s=end,
            clip_format=pick.get("format"), signals=pick.get("signals"),
        )
        source_problems = check_source(probe(src))
        if source_problems:
            clip_id = log.add(file=str(src), caption="", status="underkand",
                              problems=source_problems, **common)
            results.append({"id": clip_id, "file": src.name, "problems": source_problems})
            continue

        manifest = finish_clip(src, out, profile, settings.root, settings.encoder)
        caption = build_caption(title, profile, game)
        out.with_suffix(".txt").write_text(caption + "\n", encoding="utf-8")

        review_result, notes = None, []
        if review and brain is not None and brain.available:
            try:
                review_result = review_clip(
                    out, profile, brain, settings.review_model,
                    settings.work_dir / vod_id / "rutor",
                )
            except BudgetExceeded as error:
                notes.append(f"Bildgranskningen hoppades över: {error}")
            except Exception as error:  # nätverksfel m.m. ska inte stoppa hela körningen
                notes.append(f"Bildgranskningen misslyckades: {error}")

        problems, warnings = check_clip(
            probe(out), manifest, profile, caption, pick.get("format"), review_result
        )
        warnings += notes
        clip_id = log.add(
            file=str(out), caption=caption, status="underkand" if problems else "renderad",
            problems=problems, warnings=warnings, review=review_result, **common,
        )
        results.append(
            {"id": clip_id, "file": out.name, "title": title, "caption": caption,
             "problems": problems, "warnings": warnings}
        )
    return results
