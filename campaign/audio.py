"""Ljudet i egna videor: Behåll (som förut), Tyst eller Musik ur kampanjens mapp musik/<kampanj>/.

Ljudet byts i ett eget ffmpeg-pass efter finish_clip. Bilden kopieras som den är, så bildrutorna blir
desamma, och klippet får samma längd som med Behåll. Med Musik blandas klippets eget ljud (redan i rätt
hastighet) med musiken, som spelas i normal hastighet från vald start.

Fel ger VersionError med svensk text.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from contextlib import suppress
from pathlib import Path
from typing import BinaryIO, NoReturn, Protocol

from .media import need, probe, run
from .profile import Profile

MODES = ("keep", "mute", "music")
SOUND_TYPES = (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac")
MUSIC_TYPES = (*SOUND_TYPES, ".mp4", ".mov", ".m4v", ".webm", ".mkv")  # en video: dess ljud
FOLDER = "musik"
FADE_IN_S, FADE_OUT_S = 0.5, 1.0
RULE_WORDS = re.compile(r"musik|ljud|music|audio", re.IGNORECASE)
NO_SOUND = "The file {} has no sound. Choose an audio file or a video with sound."


def _refuse(message: str) -> NoReturn:
    from .versions import VersionError  # här: versions importerar finish, som importerar den här modulen

    raise VersionError(message)


def folder(root: Path, profile: str) -> Path:
    return root / FOLDER / profile


def sound_info(path: Path) -> dict | None:
    """Längden i sekunder, eller None om filen inte har något ljud som går att läsa."""
    out = subprocess.run([need("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                          str(path)], capture_output=True, text=True)
    try:
        data = json.loads(out.stdout) if out.returncode == 0 else {}
    except ValueError:
        return None
    if not any(s.get("codec_type") == "audio" for s in data.get("streams", [])):
        return None
    return {"duration_s": round(float(data.get("format", {}).get("duration", 0.0)), 3)}


def _listed(path: Path) -> bool:
    """En musikfil, inte en uppladdning som pågår (punkt först)."""
    return path.suffix.lower() in MUSIC_TYPES and not path.name.startswith(".")


def _inside(base: Path, target: Path) -> bool:
    return target.is_relative_to(base) and _listed(target) and target.is_file()


def music_path(root: Path, profile: str, file: object) -> Path:
    """En ljudfil eller video i kampanjens musikmapp. Ingen sökväg utanför mappen, som klistermärkena."""
    base = folder(root, profile).resolve()
    named = isinstance(file, str) and "\x00" not in file  # NUL: resolve kastar ValueError
    target = (base / str(file)).resolve() if named else None
    if target is None or not _inside(base, target):
        _refuse(f"The music {file} does not exist. Choose a file in {FOLDER}/{profile}/.")
    return target


def music_list(root: Path, profile: str) -> list[dict]:
    """Kampanjens musik med längden. Tom lista om mappen saknas."""
    found = []
    for path in sorted(folder(root, profile).glob("*")):
        info = sound_info(path) if _listed(path) and path.is_file() else None
        if info:
            found.append({"file": path.name, **info})
    return found


def rules(profile: Profile) -> list[str]:
    """Kampanjens regler som handlar om ljud eller musik."""
    return [str(r) for r in profile.raw.get("other_rules") or [] if RULE_WORDS.search(str(r))]


class SoundChoices(Protocol):
    """Ljudvalen i drafts.Choices."""

    profile: str
    audio: str
    music: str
    music_start_s: float
    original_volume: float


def choose(root: Path, choices: SoundChoices) -> dict:
    """Ljudvalet kontrollerat, klart för replace_audio."""
    mode, music, start_s, volume = choices.audio, choices.music, choices.music_start_s, choices.original_volume
    if mode not in MODES:
        _refuse("That sound mode does not exist. Choose Keep, Mute or Music.")
    if mode != "music":
        return {"mode": mode}
    if not music:
        _refuse("Choose a music file, or choose Keep or Mute.")
    if not 0 <= volume <= 1:
        _refuse("The original sound can be 0–100 %.")
    path = music_path(root, choices.profile, music)
    info = sound_info(path)
    if info is None:
        _refuse(NO_SOUND.format(music))
    if not start_s >= 0:  # också NaN
        _refuse("The music starts at 0 s or later.")
    if start_s >= info["duration_s"]:
        shown = f"{round(info['duration_s'], 1):g}"
        _refuse(f"The music is {shown} s long. Choose a start before the end.")
    return {"mode": mode, "path": path, "start_s": start_s, "volume": volume, "duration_s": info["duration_s"]}


def _music_graph(audio: dict, length: float, has_audio: bool, lufs: float) -> str:
    """Musiken från sin start, lika stark som originalet (loudnorm), tonad in och ut mot klippets slut (eller
    musikens, om den tar slut först), blandad med klippets eget ljud. loudnorm också på det blandade."""
    played = min(length, audio["duration_s"] - audio["start_s"])
    fmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    norm = f"loudnorm=I={lufs}:TP=-1.5:LRA=11"
    parts = [f"[1:a]{norm},{fmt},afade=t=in:d={FADE_IN_S},afade=t=out:st={max(played - FADE_OUT_S, 0):.3f}:d={FADE_OUT_S},"
             "apad[mus]"]
    if has_audio and audio["volume"] > 0:
        parts.append(f"[0:a]{fmt},volume={audio['volume']:.3f},apad[orig]")
        parts.append("[orig][mus]amix=inputs=2:duration=longest:normalize=0[mixed]")
        last = "mixed"
    else:
        last = "mus"
    # atrim också efter loudnorm: annars blev det blandade ljudet upp till 0,1 s längre än med Behåll.
    trim = f"atrim=end={length:.3f}"
    parts.append(f"[{last}]{trim},{norm},{trim}[aout]")
    return ";".join(parts)


def replace_audio(out: Path, profile: Profile, audio: dict | None) -> dict:
    """Byt ljudet i det färdiga klippet efter valet (från choose). audio None är ett Twitch-klipp: inget
    ändras. Returnerar manifestets del om ljudet."""
    mode = (audio or {}).get("mode", "keep")
    if mode != "keep":
        info = probe(out)
        length = info["duration"]
        if mode == "mute":
            inputs, graph = [], f"anullsrc=r=48000:cl=stereo,atrim=end={length:.3f}[aout]"
        else:
            inputs = ["-ss", f"{audio['start_s']:.3f}", "-t", f"{length:.3f}", "-i", str(audio["path"])]
            graph = _music_graph(audio, length, info["has_audio"], profile.lufs)
        tmp = out.with_name(f".{out.stem}.ljud.mp4")
        try:
            run([need("ffmpeg"), "-y", "-v", "error", "-i", str(out), *inputs, "-filter_complex", graph,
                 "-map", "0:v:0", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
                 "-movflags", "+faststart", str(tmp)])
            os.replace(tmp, out)
        finally:
            tmp.unlink(missing_ok=True)
    heard = {"original_volume": audio["volume"]} if mode == "music" else {}  # regelkontrollen varnar vid 0
    return {"audio": mode, "own_video": audio is not None, **heard}


def _free_name(base: Path, name: str) -> Path:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(name).stem).strip("_")[:60] or "musik"
    suffix = Path(name).suffix.lower()
    target, n = base / f"{stem}{suffix}", 2
    while target.exists():
        target, n = base / f"{stem}-{n}{suffix}", n + 1
    return target


def _link_free(tmp: Path, base: Path, name: str) -> Path:
    """Länka under ett fritt namn. os.link skriver aldrig över: en uppladdning med samma namn samtidigt ger
    FileExistsError, och då tas nästa namn."""
    while True:
        target = _free_name(base, name)
        with suppress(FileExistsError):
            os.link(tmp, target)
            return target


def add_music(root: Path, profile: str, file: BinaryIO, name: str) -> dict:
    """Spara en ljudfil eller video i kampanjens musikmapp. Ett namn som finns får ett tillägg."""
    suffix = Path(name).suffix.lower()
    if suffix not in MUSIC_TYPES:
        _refuse(f"The file type {suffix or '(none)'} is not supported. Choose {', '.join(MUSIC_TYPES)}.")
    base = folder(root, profile)
    base.mkdir(parents=True, exist_ok=True)
    tmp = base / f".uppladdning-{uuid.uuid4().hex}{suffix}"  # punkt först: listas inte
    try:
        with tmp.open("wb") as out:
            shutil.copyfileobj(file, out)
        info = sound_info(tmp)
        if info is None:
            _refuse(NO_SOUND.format(name))
        target = _link_free(tmp, base, name)
    finally:
        tmp.unlink(missing_ok=True)
    return {"file": target.name, **info}
