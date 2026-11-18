"""Kampanjprofil: en YAML-fil per kampanj med alla regler på ett ställe."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

PLATFORMS = ("tiktok", "youtube_shorts", "instagram_reels")
HOOK_STYLES = ("sans", "serif")


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
    # titeln överst hela klippet; None = på när text inte krävs
    hook_text: bool | None = None
    hook_style: str = "sans"  # rubrikens stil: sans | serif (versaler)
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
    audience_countries: str | None = None  # länderna vars visningar betalas, som de visas i Statistik
    clipsmith_config: str | None = None  # clipsmiths inställningar för kampanjen, t.ex. config.timmy.yaml
    preferred_games: list[str] = field(default_factory=list)  # spel som ger poäng i Senaste streams
    active: bool = True  # false = kampanjen syns bara under Kampanjer i appen
    # Kontona kampanjen postar från, per plattform (tiktok, youtube_shorts): id, användarnamn eller handle.
    # Saknas plattformen får alla kopplade konton användas.
    accounts: dict[str, list[str]] = field(default_factory=dict)
    max_posts_per_day: int = 3  # per konto och svensk dag
    min_gap_minutes: int = 120  # schemat varnar när två inlägg på samma konto ligger närmare
    # finish: ta bort tystnad och utfyllnadsord, och visa hooken först (bara klipp med ordfil)
    cut_silence: bool = True
    cut_fillers: bool = True
    cold_open: bool = True
    # Kontots fasta stil för en ny version: konto (id eller namn som i accounts) -> font, overlay, sticker,
    # start_cut_s. Saknas kontot: standardvärden.
    accounts_style: dict[str, dict] = field(default_factory=dict)
    search_default_scope: str = "latest"  # chatten letar i: latest = senaste materialet, all = allt
    raw: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.hook_text is None:
            self.hook_text = not self.text_required
        self.hook_style = self.hook_style or "sans"  # hook_style: null i YAML

    @property
    def format_ids(self) -> list[str]:
        return [f["id"] for f in self.formats]

    @property
    def platforms(self) -> list[str]:
        """Där kampanjen postas. Saknas listan eller är den tom: alla plattformar."""
        value = self.raw.get("platforms")
        return [str(p) for p in value] if isinstance(value, list) and value else list(PLATFORMS)

    def multi(self, platform: str) -> bool:
        """Kampanjen postar från flera konton på plattformen (tiktok, youtube_shorts). Annars gäller ett inlägg
        per klipp och plattform."""
        return len(self.accounts.get(platform) or []) >= 2

    @property
    def content_rewards_url(self) -> str | None:
        """Kampanjen i Content Rewards, bara en https-adress."""
        links = self.raw.get("links")
        url = links.get("content_rewards") if isinstance(links, dict) else None
        return url if isinstance(url, str) and url.startswith("https://") else None

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
        choices = ((self.text_position, ("top", "bottom"), "on_screen_text.position måste vara top eller bottom."),
                   (self.hook_style, HOOK_STYLES, "on_screen_text.hook_style måste vara sans eller serif."),
                   (self.overlay_mode, ("full", "top", "bottom"), "overlay.mode måste vara full, top eller bottom."))
        found += [message for value, allowed, message in choices if value not in allowed]
        if self.min_seconds >= self.max_seconds:
            found.append("clip.min_seconds måste vara mindre än clip.max_seconds.")
        if self.max_posts_per_day < 1 or self.min_gap_minutes < 0:
            found.append("posting.max_per_day måste vara minst 1, och posting.min_gap_minutes får inte vara negativ.")
        return found


def _number(section: dict, key: str, default: float, label: str, kind: type = float):
    """Fältet som tal. Fel värde ger ett fel som nämner fältet, till exempel clip.min_seconds."""
    value = section.get(key, default)
    try:
        return kind(value)
    except (TypeError, ValueError):
        what = "ett heltal" if kind is int else "ett tal"
        raise SystemExit(f"{label} måste vara {what}, inte {value!r}.") from None


def _accounts(data: dict) -> dict[str, list[str]]:
    value = data.get("accounts") or {}
    if not (isinstance(value, dict) and all(isinstance(v, list) for v in value.values())):
        raise SystemExit("accounts måste vara en karta med en lista per plattform, till exempel tiktok: [konto].")
    return {str(key): [str(a).strip().lstrip("@") for a in names if str(a).strip()] for key, names in value.items()}


def _edit(data: dict) -> dict[str, bool]:
    value = data.get("edit") or {}
    if not isinstance(value, dict):
        raise SystemExit("edit måste vara en karta, till exempel edit: {cut_silence: true}.")
    flags = {key: value.get(key, True) for key in ("cut_silence", "cut_fillers", "cold_open")}
    for key, flag in flags.items():
        if not isinstance(flag, bool):
            raise SystemExit(f"edit.{key} måste vara true eller false, inte {flag!r}.")
    return flags


def _accounts_style(data: dict) -> dict[str, dict]:
    value = data.get("accounts_style") or {}
    if not isinstance(value, dict) or not all(isinstance(style, dict) for style in value.values()):
        raise SystemExit("accounts_style måste vara en karta med en stil per konto, till exempel "
                         "accounts_style: {kontot: {font: Impact}}.")
    styles = {}
    for account, style in value.items():
        where = f"accounts_style.{account}"
        for key, kinds in (("font", (str,)), ("overlay", (str,)), ("sticker", (dict,))):
            if style.get(key) is not None and not isinstance(style[key], kinds):
                raise SystemExit(f"{where}.{key} har fel typ: {style[key]!r}.")
        styles[str(account)] = {**style, "start_cut_s": _number(style, "start_cut_s", 0, f"{where}.start_cut_s")}
    return styles


def _search_scope(data: dict) -> str:
    search = data.get("search") or {}
    if not isinstance(search, dict):
        raise SystemExit("search måste vara en karta, till exempel search: {default_scope: all}.")
    scope = search.get("default_scope", "latest")
    if scope not in ("latest", "all"):
        raise SystemExit(f"search.default_scope måste vara latest eller all, inte {scope!r}.")
    return scope


def load_profile(name_or_path: str | Path, profiles_dir: Path) -> Profile:
    """Läs en profil. Ange namnet (skiimasktimmy) eller en sökväg till YAML-filen."""
    path = Path(name_or_path)
    if not path.suffix:
        path = Path(profiles_dir) / f"{name_or_path}.yaml"
    if not path.exists():
        raise SystemExit(f"Hittar ingen kampanj: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for key in ("name", "streamer"):
        if not data.get(key):
            raise SystemExit(f"Kampanjens fil {path.name} saknar fältet {key!r}.")
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
        hook_style=text.get("hook_style", "sans"),
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
        audience_countries=str(data["audience_countries"]) if data.get("audience_countries") else None,
        clipsmith_config=data.get("clipsmith_config"),
        preferred_games=[str(g) for g in data.get("preferred_games") or []],
        active=data.get("active") is not False,
        accounts=_accounts(data),
        max_posts_per_day=_number(data.get("posting") or {}, "max_per_day", 3, "posting.max_per_day", int),
        min_gap_minutes=_number(data.get("posting") or {}, "min_gap_minutes", 120, "posting.min_gap_minutes", int),
        **_edit(data),
        accounts_style=_accounts_style(data),
        search_default_scope=_search_scope(data),
        raw=data,
    )
