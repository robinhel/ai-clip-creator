"""Smak ur virala klipp: mät hur klippen är gjorda och spara det som Claude ska använda när den väljer.

measure kör reverse-videos measure.py (campaign/vendor/reverse_video) på en lokal video och behåller bara några
siffror. analyse mäter de största avvikarna i klippdata/viral/<profil>.json (Content Rewards-videor laddas ner
från deras CDN och raderas efter mätningen), jämför med stället i strömmen och ber Haiku om regler. Twitch-klipp
är oredigerade och mäts inte; deras titlar och ord i strömmen visar Haiku vilka ögonblick som blir virala. Resultatet sparas i klippdata/smak/<profil>.json. write_skills gör två Amp-skills av det.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml
from clipsmith.models.transcript import Transcript

from . import locate
from .brain import Brain
from .budget import budget_from_settings
from .config import load_settings

log = logging.getLogger(__name__)

MEASURE = Path(__file__).resolve().parent / "vendor" / "reverse_video" / "measure.py"
SKILLS_DIR = Path(__file__).resolve().parents[1] / ".agents" / "skills"
CDN_HOST = "cdn.contentrewards.com"  # den enda värden som analyse laddar ner från
MOMENT_WORDS = 60  # så många ord ur strömmen per Twitch-klipp till Haiku
HOOK_S = 3.0  # text på bilden före så här många sekunder räknas som hook-text
HOOK_KINDS = ("title", "caption", "text")
TEXT_MAX = 600

SYSTEM = (
    "You study measurements of viral gaming clips (short vertical videos cut from Twitch streams). "
    "Write short, concrete rules a clipper can follow: which moments to pick and how to cut them "
    "(length, first cut, hook, captions). Base every rule on the numbers. "
    "The moments are raw, unedited Twitch clips that went viral, with the words said in the stream: "
    "use them for rules about which moments to pick. Answer in English."
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
    """Mät de klipp som har en lokal video eller en CDN-video och inte är mätta. True om något mättes.
    En CDN-video som inte går att hämta hoppas över."""
    done = False
    for clip in [c for c in clips if _needs_measure(c)]:
        try:
            _measure_one(clip)
        except httpx.HTTPError as error:
            log.warning(
                "Could not download %s (%s): not measured", clip["id"], type(error).__name__
            )
            continue
        done = True
    return done


def _needs_measure(clip: dict) -> bool:
    if clip.get("measured"):
        return False
    if clip.get("video_path"):
        return Path(clip["video_path"]).exists()
    return _cdn_url(clip) is not None


def _cdn_url(clip: dict) -> str | None:
    """video_url när den ligger på Content Rewards CDN över https, annars None."""
    url = clip.get("video_url") or ""
    parts = urlparse(url)
    return url if parts.scheme == "https" and parts.hostname == CDN_HOST else None


def _measure_one(clip: dict) -> None:
    if clip.get("video_path"):
        clip["taste"] = measure(clip["video_path"])
    else:
        _measure_download(clip)
    clip["measured"] = True


def _measure_download(clip: dict) -> None:
    """Ladda ner CDN-videon, mät den, leta stället i strömmen och radera videon."""
    with tempfile.TemporaryDirectory(prefix="smak-cdn-") as tmp:  # videon raderas med mappen
        video = Path(tmp) / "video.mp4"
        _download(str(_cdn_url(clip)), video)
        clip["taste"] = measure(video)
        place = _try_locate({**clip, "path": str(video)})
        if place:
            clip["location"] = place


def _try_locate(clip: dict) -> dict | None:
    """locate.locate, men ett fel (till exempel i whisper) ger en varning och None i stället för att avbryta."""
    try:
        return locate.locate(clip)
    except Exception as error:  # platsen är en bonus; mätningen ska sparas ändå
        log.warning("Could not locate %s in the stream (%s)", clip.get("id"), type(error).__name__)
        return None


def _download(url: str, path: Path) -> None:
    with httpx.stream(
        "GET", url, timeout=120
    ) as response:  # följer inte omdirigeringar till andra värdar
        response.raise_for_status()
        with path.open("wb") as file:
            for chunk in response.iter_bytes():
                file.write(chunk)


def _top(clips: list[dict], top_n: int) -> list[dict]:
    ranked = [c for c in clips if c.get("outlier") is not None]
    return sorted(ranked, key=lambda c: -c["outlier"])[:top_n]


def _moments(clips: list[dict], top_n: int, work_dir: Path) -> list[dict]:
    """De största Twitch-avvikarna: titel, visningar per dag och orden i strömmen när transkriptet finns."""
    twitch = _top([c for c in clips if c.get("source") == "twitch"], top_n)
    return [_moment(c, work_dir) for c in twitch]


def _moment(clip: dict, work_dir: Path) -> dict:
    moment = {"title": clip.get("title"), "views_per_day": clip.get("views_per_day")}
    words = _span_words(clip.get("location"), work_dir)
    if words:
        moment["words"] = words
    return moment


def _span_words(location: dict | None, work_dir: Path) -> str:
    """Högst MOMENT_WORDS ord ur work/<vod>/transcript.json inom klippets tidsspann."""
    if not location:
        return ""
    path = work_dir / str(location.get("vod_id")) / "transcript.json"
    if not path.exists():
        return ""
    transcript = Transcript.from_json(path.read_text(encoding="utf-8"))
    start, end = float(location["start_s"]), float(location["end_s"])
    words = [
        w.word.strip() for seg in transcript.segments for w in seg.words if start <= w.start <= end
    ]
    return " ".join(words[:MOMENT_WORDS])


def _ask(brain: Brain, numbers: dict, measured: list[dict], moments: list[dict]) -> dict:
    """Haikus regler och sammanfattning ur siffrorna och Twitch-ögonblicken."""
    clips = [{"title": c.get("title"), "outlier": c["outlier"], **c["taste"]} for c in measured]
    content = json.dumps(
        {"average": numbers, "clips": clips, "moments": moments}, ensure_ascii=False
    )
    return brain.ask(
        purpose="smak",
        model=brain.settings.review_model,
        system=SYSTEM,
        content=content,
        schema=SCHEMA,
    )


def analyse(profile, top_n: int = 5, brain: Brain | None = None) -> dict:
    """Bygg smakfilen av de top_n största avvikarna som inte är Twitch-klipp, med de top_n största
    Twitch-avvikarna som ögonblick. Utan mätta klipp blir mätvärdena None och reglerna kommer från
    ögonblicken. Kastar ValueError när varken mätta klipp eller ögonblick finns."""
    from . import viral

    settings = load_settings()
    clips = viral.load(profile)
    measured = _measured_top(profile, clips, top_n)
    moments = _moments(clips, top_n, settings.work_dir)
    if not measured and not moments:
        raise ValueError(
            "Inget av de största virala klippen är mätt än, och inga Twitch-klipp finns."
        )
    numbers = _numbers(measured, settings.work_dir)
    brain = brain or Brain(settings, budget_from_settings(settings))
    answer = _ask(brain, numbers, measured, moments)
    result = {
        "updated": datetime.now(UTC).isoformat(timespec="seconds"),
        "profile": _name(profile),
        "clip_ids": [c["id"] for c in measured],
        "numbers": numbers,
        **_answer(answer),
    }
    _save_taste(profile, result)
    return result


def _measured_top(profile, clips: list[dict], top_n: int) -> list[dict]:
    """De top_n största avvikarna som inte är Twitch-klipp, mätta om det går. Bara de mätta returneras."""
    from . import viral

    top = _top([c for c in clips if c.get("source") != "twitch"], top_n)
    if _measure_missing(top):
        viral.save(profile, clips)
    return [c for c in top if c.get("taste")]


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


def write_skills(profile, root: Path | None = None) -> list[Path]:
    """Skriv <profil>-moment-scout och <profil>-clip-editor från smakfilen, som standard i repots
    .agents/skills. Kastar ValueError utan smakfil."""
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
                *(_number_lines_or_missing(numbers)),
                "",
                "## Rules",
                "",
                *rules,
            ],
        ),
    }
    return _write(skills, Path(root or SKILLS_DIR))


def _number_lines_or_missing(numbers: dict) -> list[str]:
    lines = [f"- {line}" for line in _number_lines(numbers)]
    return lines or ["No editing data yet: no edited viral clip has been measured."]


def _write(skills: dict[str, str], root: Path) -> list[Path]:
    paths = []
    for folder, text in skills.items():
        path = root / folder / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        paths.append(path)
    return paths
