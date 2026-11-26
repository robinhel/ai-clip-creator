"""Smak ur virala klipp: mät hur klippen är gjorda och spara det som Claude ska använda när den väljer.

measure kör reverse-videos measure.py (campaign/vendor/reverse_video) på en lokal video och behåller bara några
siffror. analyse mäter de största avvikarna i klippdata/viral/<profil>.json, jämför med stället i strömmen och
ber Haiku om regler. Resultatet sparas i klippdata/smak/<profil>.json. write_skills gör två Amp-skills av det.
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import yaml

from .brain import Brain
from .budget import budget_from_settings
from .config import load_settings

MEASURE = Path(__file__).resolve().parent / "vendor" / "reverse_video" / "measure.py"
HOOK_S = 3.0  # text på bilden före så här många sekunder räknas som hook-text
HOOK_KINDS = ("title", "caption", "text")
TEXT_MAX = 600

SYSTEM = (
    "You study measurements of viral gaming clips (short vertical videos cut from Twitch streams). "
    "Write short, concrete rules a clipper can follow: which moments to pick and how to cut them "
    "(length, first cut, hook, captions). Base every rule on the numbers. Answer in English."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "rules": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "summary_en": {"type": "string"},
    },
    "required": ["rules", "summary_en"],
}


def _name(profile) -> str:
    return str(getattr(profile, "name", profile))


def _path(profile) -> Path:
    return load_settings().data_dir / "smak" / f"{_name(profile)}.json"


def _position(y_pct: float | None) -> str | None:
    if y_pct is None:
        return None
    return "top" if y_pct < 40 else "middle" if y_pct < 60 else "bottom"


def _hook_text(events: list[dict]) -> bool:
    return any(e.get("kind") in HOOK_KINDS and e.get("start", HOOK_S) < HOOK_S for e in events)


def _has_face(faces: dict | None) -> bool | None:
    return None if faces is None else faces.get("on_screen_pct", 0) >= 10


def pick(m: dict) -> dict:
    """Det lilla urvalet ur measures.json som sparas per klipp."""
    shots = m.get("shots") or {}
    captions = (m.get("text") or {}).get("captions") or {}
    return {
        "length_s": (m.get("format") or {}).get("duration"),
        "first_cut_s": shots.get("first_cut_at"),
        "cuts_per_min": shots.get("cuts_per_min"),
        "caption_words": captions.get("words_per_caption_median"),
        "caption_position": _position(captions.get("y_centre_pct")),
        "hook_text": _hook_text(m.get("text_events") or []),
        "has_face": _has_face(m.get("faces")),
        "words_per_min": (m.get("speech") or {}).get("wpm_overall"),
    }


def measure(video_path: Path | str) -> dict:
    """Kör measure.py på en lokal video. Arbetsmappen (som fetch.py gör den) raderas efteråt."""
    video = Path(video_path).resolve()
    with tempfile.TemporaryDirectory(prefix="smak-") as tmp:
        wd = Path(tmp)
        try:
            os.link(video, wd / "video.mp4")
        except OSError:
            shutil.copy2(video, wd / "video.mp4")
        meta = {"source": str(video), "platform": "file", "title": video.stem}
        (wd / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        cmd = [
            sys.executable,
            "-I",
            str(MEASURE),
            str(wd),
            "--stems",
            "off",
            "--model",
            load_settings().local_whisper_model,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        raw = wd / "measures.json"
        if result.returncode or not raw.exists():
            raise RuntimeError(
                f"measure.py misslyckades: {(result.stderr or result.stdout)[-600:]}"
            )
        return pick(json.loads(raw.read_text(encoding="utf-8")))


def _payoff(location: dict | None, work_dir: Path) -> float | None:
    """Sekunder in i klippet till det starkaste ögonblicket (clipsmiths kandidater) inom klippet."""
    if not location:
        return None
    path = work_dir / str(location.get("vod_id")) / "candidates.json"
    if not path.exists():
        return None
    start, end = float(location["start_s"]), float(location["end_s"])
    inside = [
        c for c in json.loads(path.read_text(encoding="utf-8")) if start <= c["t_center"] <= end
    ]
    if not inside:
        return None
    return max(inside, key=lambda c: c["score"])["t_center"] - start


def _mean(values: list) -> float | None:
    values = [float(v) for v in values if v is not None]
    return round(statistics.mean(values), 2) if values else None


def _numbers(clips: list[dict], work_dir: Path) -> dict:
    tastes = [c["taste"] for c in clips]
    hooks = [t["hook_text"] for t in tastes if t.get("hook_text") is not None]
    return {
        "length_s": _mean([t.get("length_s") for t in tastes]),
        "first_cut_s": _mean([t.get("first_cut_s") for t in tastes]),
        "cuts_per_min": _mean([t.get("cuts_per_min") for t in tastes]),
        "caption_words": _mean([t.get("caption_words") for t in tastes]),
        "hook_text_share": round(sum(hooks) / len(hooks), 2) if hooks else None,
        "payoff_s": _mean([_payoff(c.get("location"), work_dir) for c in clips]),
    }


def _measure_missing(clips: list[dict]) -> bool:
    """Mät de klipp som har en lokal video och inte är mätta. True om något mättes."""
    todo = [c for c in clips if _needs_measure(c)]
    for clip in todo:
        clip["taste"], clip["measured"] = measure(clip["video_path"]), True
    return bool(todo)


def _needs_measure(clip: dict) -> bool:
    if clip.get("measured") or not clip.get("video_path"):
        return False
    return Path(clip["video_path"]).exists()


def _top(clips: list[dict], top_n: int) -> list[dict]:
    ranked = [c for c in clips if c.get("outlier") is not None]
    return sorted(ranked, key=lambda c: -c["outlier"])[:top_n]


def _ask(brain: Brain, numbers: dict, measured: list[dict]) -> dict:
    """Haikus regler och sammanfattning ur siffrorna."""
    clips = [{"title": c.get("title"), "outlier": c["outlier"], **c["taste"]} for c in measured]
    content = json.dumps({"average": numbers, "clips": clips}, ensure_ascii=False)
    return brain.ask(
        purpose="smak",
        model=brain.settings.review_model,
        system=SYSTEM,
        content=content,
        schema=SCHEMA,
    )


def analyse(profile, top_n: int = 5, brain: Brain | None = None) -> dict:
    """Bygg smakfilen av de top_n största avvikarna. Kastar ValueError när inget klipp är mätt."""
    from . import viral

    settings = load_settings()
    clips = viral.load(profile)
    top = _top(clips, top_n)
    if _measure_missing(top):
        viral.save(profile, clips)
    measured = [c for c in top if c.get("taste")]
    if not measured:
        raise ValueError("Inget av de största virala klippen är mätt än.")
    numbers = _numbers(measured, settings.work_dir)
    answer = _ask(brain or Brain(settings, budget_from_settings(settings)), numbers, measured)
    result = {
        "updated": datetime.now(UTC).isoformat(timespec="seconds"),
        "profile": _name(profile),
        "clip_ids": [c["id"] for c in measured],
        "numbers": numbers,
        **_answer(answer),
    }
    _save_taste(profile, result)
    return result


def _answer(answer: dict) -> dict:
    return {
        "rules": [str(r) for r in answer.get("rules") or []],
        "summary_en": str(answer.get("summary_en") or ""),
    }


def _save_taste(profile, result: dict) -> None:
    path = _path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def load_taste(profile) -> dict | None:
    path = _path(profile)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _number_lines(numbers: dict) -> list[str]:
    labels = {
        "length_s": "length {:g} s",
        "first_cut_s": "first cut at {:g} s",
        "cuts_per_min": "{:g} cuts per minute",
        "caption_words": "{:g} words per caption",
        "hook_text_share": "text hook in first 3 s in {:.0%} of clips",
        "payoff_s": "payoff {:g} s in",
    }
    return [
        label.format(numbers[key]) for key, label in labels.items() if numbers.get(key) is not None
    ]


def taste_text(profile) -> str:
    """Smaken som kort engelsk text för Claudes val. Tom sträng utan smakfil."""
    data = load_taste(profile)
    if not data:
        return ""
    text = f"Viral clips taste: {data.get('summary_en', '')}".strip()
    typical = ", ".join(_number_lines(data.get("numbers") or {}))
    if typical:
        text += f"\nTypical: {typical}."
    for rule in data.get("rules") or []:
        line = f"\n- {rule}"
        if len(text) + len(line) > TEXT_MAX:
            break
        text += line
    return text[:TEXT_MAX]


def _skill(name: str, description: str, title: str, body: list[str]) -> str:
    front = yaml.safe_dump(
        {"name": name, "description": description}, allow_unicode=True, sort_keys=False
    )
    return f"---\n{front}---\n\n# {title}\n\n" + "\n".join(body) + "\n"


def write_skills(profile, root: Path = Path(".agents/skills")) -> list[Path]:
    """Skriv <profil>-moment-scout och <profil>-clip-editor från smakfilen. Kastar ValueError utan smakfil."""
    data = load_taste(profile)
    if not data:
        raise ValueError("Smakfilen saknas. Kör analyse först.")
    name = _name(profile)
    numbers = data.get("numbers") or {}
    rules = [f"- {r}" for r in data.get("rules") or []]
    summary = data.get("summary_en", "")
    payoff = numbers.get("payoff_s")
    skills = {
        f"{name}-moment-scout": _skill(
            f"{name}-moment-scout",
            f"Picks which moments in {name} streams tend to go viral, learned from measured viral clips. "
            f"Use when choosing moments or candidates for {name} clips.",
            f"Moment scout for {name}",
            [
                summary,
                "",
                f"In viral clips the strongest moment comes about {payoff:g} s after the clip starts."
                if payoff is not None
                else "",
                "",
                "## Rules",
                "",
                *rules,
            ],
        ),
        f"{name}-clip-editor": _skill(
            f"{name}-clip-editor",
            f"Describes how viral {name} clips are cut: length, hook and captions, from measured viral clips. "
            f"Use when cutting, trimming or captioning {name} clips.",
            f"Clip editor for {name}",
            [
                summary,
                "",
                "## Numbers",
                "",
                *[f"- {line}" for line in _number_lines(numbers)],
                "",
                "## Rules",
                "",
                *rules,
            ],
        ),
    }
    paths = []
    for folder, text in skills.items():
        path = Path(root) / folder / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        paths.append(path)
    return paths
