"""Kampanjprofil: en YAML-fil per kampanj med alla regler på ett ställe."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

PLATFORMS = ("tiktok", "youtube_shorts", "instagram_reels")


@dataclass
class Profile:
    name: str
    streamer: str
    language: str = "en"
    min_seconds: float = 7.0
    max_seconds: float = 60.0
    formats: list[dict] = field(default_factory=list)
    text: str = ""
    text_required: bool = False
    text_position: str = "top"  # top | bottom
    text_font_size: int = 46
    text_font_file: str | None = None
    # titeln överst de första sekunderna; None = på när text inte krävs
    hook_text: bool | None = None
    overlay_file: str | None = None
    overlay_required: bool = False
    overlay_mode: str = "full"  # full | top | bottom
    facecam_required: bool = False
    safe_top: float = 0.07
    safe_bottom: float = 0.20
    caption_tag_line: str = ""  # egen rad efter texten, t.ex. "#ad #blizzardpartner"; hashtags följer på raden
    caption_max_extra_hashtags: int | None = None  # högst så många hashtags utöver taggraden
    caption_must_include: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)
    game_hashtag: bool = True
    caption_template: str = "{title}"
    lufs: float = -14.0
    engagement: list[dict] = field(default_factory=list)
    payment: dict = field(default_factory=dict)
    clipsmith_config: str | None = None  # clipsmiths inställningar för kampanjen, t.ex. config.timmy.yaml
    preferred_games: list[str] = field(default_factory=list)  # spel som ger poäng i Senaste streams
    active: bool = True  # false = kampanjen syns bara under Kampanjer i appen
    raw: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.hook_text is None:
            self.hook_text = not self.text_required

    @property
    def format_ids(self) -> list[str]:
        return [f["id"] for f in self.formats]

    @property
    def platforms(self) -> list[str]:
        """Där kampanjen postas. Saknas listan eller är den tom: alla plattformar."""
        value = self.raw.get("platforms")
        return [str(p) for p in value] if isinstance(value, list) and value else list(PLATFORMS)

    @property
    def min_views(self) -> int:
        return int(self.payment.get("min_views", 0))

    def overlay_path(self, root: Path) -> Path | None:
        return (Path(root) / self.overlay_file) if self.overlay_file else None

    def engagement_min(self, views: int) -> float | None:
        """Lägsta engagemang i procent som kampanjen kräver vid ett visst antal visningar."""
        for step in self.engagement:
            limit = step.get("views_up_to")
            if limit is None or views < limit:
                return float(step["min"])
        return None

    def problems(self, root: Path) -> list[str]:
        """Det som saknas innan profilen går att använda fullt ut."""
        found: list[str] = []
        if self.text_required and not self.text:
            found.append("on_screen_text.text är tom men kampanjen kräver text på skärmen.")
        overlay = self.overlay_path(root)
        if self.overlay_required and (overlay is None or not overlay.exists()):
            found.append(
                f"Overlay-bilden saknas: {self.overlay_file}. "
                "Ladda ner den från kampanjen. Ladda upp den under Overlay."
            )
        if self.text_position not in ("top", "bottom"):
            found.append("on_screen_text.position måste vara top eller bottom.")
        if self.overlay_mode not in ("full", "top", "bottom"):
            found.append("overlay.mode måste vara full, top eller bottom.")
        if self.min_seconds >= self.max_seconds:
            found.append("clip.min_seconds måste vara mindre än clip.max_seconds.")
        return found


def _number(section: dict, key: str, default: float, label: str, kind: type = float):
    """Fältet som tal. Fel värde ger ett fel som nämner fältet, till exempel clip.min_seconds."""
    value = section.get(key, default)
    try:
        return kind(value)
    except (TypeError, ValueError):
        what = "ett heltal" if kind is int else "ett tal"
        raise SystemExit(f"{label} måste vara {what}, inte {value!r}.") from None


def load_profile(name_or_path: str | Path, profiles_dir: Path) -> Profile:
    """Läs en profil. Ange namnet (skiimasktimmy) eller en sökväg till YAML-filen."""
    path = Path(name_or_path)
    if not path.suffix:
        path = Path(profiles_dir) / f"{name_or_path}.yaml"
    if not path.exists():
        raise SystemExit(f"Hittar ingen profil: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for key in ("name", "streamer"):
        if not data.get(key):
            raise SystemExit(f"Profilen {path.name} saknar fältet {key!r}.")
    clip = data.get("clip") or {}
    text = data.get("on_screen_text") or {}
    overlay = data.get("overlay") or {}
    safe = data.get("safe_zone") or {}
    caption = data.get("caption") or {}
    return Profile(
        name=path.stem,  # filnamnet är kampanjens id; data["name"] är bara en etikett
        streamer=data["streamer"],
        language=data.get("language", "en"),
        min_seconds=_number(clip, "min_seconds", 7, "clip.min_seconds"),
        max_seconds=_number(clip, "max_seconds", 60, "clip.max_seconds"),
        formats=list(data.get("formats") or []),
        text=str(text.get("text") or ""),
        text_required=bool(text.get("required", False)),
        text_position=text.get("position", "top"),
        text_font_size=_number(text, "font_size", 46, "on_screen_text.font_size", int),
        text_font_file=text.get("font_file"),
        hook_text=None if text.get("hook_text") is None else bool(text["hook_text"]),
        overlay_file=overlay.get("file"),
        overlay_required=bool(overlay.get("required", False)),
        overlay_mode=overlay.get("mode", "full"),
        facecam_required=bool((data.get("facecam") or {}).get("required", False)),
        safe_top=_number(safe, "top", 0.07, "safe_zone.top"),
        safe_bottom=_number(safe, "bottom", 0.20, "safe_zone.bottom"),
        caption_tag_line=str(caption.get("tag_line") or ""),
        caption_max_extra_hashtags=None if caption.get("max_extra_hashtags") is None else _number(
            caption, "max_extra_hashtags", 0, "caption.max_extra_hashtags", int),
        caption_must_include=list(caption.get("must_include") or []),
        hashtags=list(caption.get("hashtags") or []),
        game_hashtag=bool(caption.get("game_hashtag", True)),
        caption_template=caption.get("template", "{title}"),
        lufs=_number(data.get("audio") or {}, "lufs", -14, "audio.lufs"),
        engagement=list(data.get("engagement_min_percent") or []),
        payment=dict(data.get("payment") or {}),
        clipsmith_config=data.get("clipsmith_config"),
        preferred_games=[str(g) for g in data.get("preferred_games") or []],
        active=data.get("active") is not False,
        raw=data,
    )
