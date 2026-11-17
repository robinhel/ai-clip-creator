"""Vägar för kampanjer i appen: en kampanj i detalj, overlay-bilden, profilens YAML, ny kampanj,
facecam- och spelrutan och en bild från VOD:en att rita rutorna på.

Vägar som skriver kräver API-nyckeln. Fel ger detail på svenska.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from twitchdl import twitch

from clipsmith.api.deps import verify_api_key

from . import chat, examples
from . import profile_edit as edit
from .api import _describe_profile, _log, _profile_names, get_settings
from .config import Settings
from .media import need, probe, run
from .profile import Profile, load_profile
from .tiktok import TikTok
from .youtube import YouTube

router = APIRouter(prefix="/campaign", tags=["campaign"])
_write_lock = threading.Lock()  # en ändring av kampanjfilerna i taget
_frame_lock = threading.Lock()
OVERLAY_MAX = 10 * 1024 * 1024
IMAGE_MAX = 5 * 1024 * 1024  # Twitchs bild i 1920x1080 är cirka 250 kB
TEMPLATE_CONFIG = "config.timmy.yaml"  # mallen för nya kampanjers config-fil
THUMB_HOST = "static-cdn.jtvnw.net"  # bilder hämtas bara härifrån
VOD_ID = re.compile(r"\d{6,15}")


def http_client() -> httpx.Client:
    return httpx.Client(timeout=30.0)  # följer inte omdirigeringar, så bilden kommer från THUMB_HOST


# ---------- hjälpare ----------


def _profile_path(profile_id: str, settings: Settings) -> Path:
    if not edit.ID.fullmatch(profile_id):
        raise HTTPException(400, "Kampanjens id får bara ha små bokstäver, siffror och _ (2–40 tecken).")
    path = settings.profiles_dir / f"{profile_id}.yaml"
    if not path.exists():
        raise HTTPException(404, f"Kampanjen {profile_id} finns inte.")
    return path


def _load(profile_id: str, settings: Settings) -> Profile:
    _profile_path(profile_id, settings)
    try:
        return load_profile(profile_id, settings.profiles_dir)
    except (SystemExit, Exception) as error:  # SystemExit från load_profile, YAML-fel m.m.
        raise HTTPException(400, f"Kampanjen {profile_id} går inte att läsa: {error}") from error


def _overlay_target(profile: Profile, settings: Settings) -> Path | None:
    """Overlay-bildens sökväg. Den måste ligga under overlays/ och vara en .png."""
    return edit.overlay_target(settings.root, profile.overlay_file) if profile.overlay_file else None


def _config_path(profile: Profile, settings: Settings) -> Path:
    try:
        return edit.config_file(settings.root, profile.clipsmith_config)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


def _check_config(expected: dict | None = None) -> Callable[[Path], None]:
    """Kontroll av en ny config-fil innan den ersätter den gamla."""
    def check(tmp: Path) -> None:
        cfg = edit.read_config(tmp)
        for key, value in (expected or {}).items():
            if getattr(cfg.reframe, key) != value:
                raise ValueError(f"reframe.{key} blev inte rätt i config-filen. Ändra den för hand.")
    return check


def _check_answer(profile_id: str, settings: Settings) -> Callable[[Path], None]:
    """Kontroll av en ny profil innan den ersätter den gamla: svaret måste gå att räkna fram."""
    def check(tmp: Path) -> None:
        try:
            _describe_profile(profile_id, settings, path=tmp)
        except (SystemExit, Exception) as error:  # SystemExit från load_profile, typfel m.m.
            raise ValueError(f"Kampanjen går inte att läsa efter ändringen: {error}") from None
    return check


def _latest_vod(profile_id: str, settings: Settings) -> str | None:
    runs = [r for r in _log(settings).runs() if r["profile"] == profile_id]
    vod_id = str(runs[-1]["vod_id"]) if runs else ""
    return vod_id if VOD_ID.fullmatch(vod_id) else None


def _detail(profile_id: str, settings: Settings) -> dict:
    profile = _load(profile_id, settings)
    try:
        out = _describe_profile(profile_id, settings)
    except (SystemExit, Exception) as error:
        raise HTTPException(400, f"Kampanjen {profile_id} går inte att läsa: {error}") from error
    try:
        target = _overlay_target(profile, settings)
    except ValueError:
        target = None
    exists = target is not None and target.is_file()
    size = None
    if target is not None and exists:
        with target.open("rb") as file:
            size = edit.png_size(file.read(24))
    links = profile.raw.get("links") if isinstance(profile.raw.get("links"), dict) else {}
    source = profile.raw.get("source") if isinstance(profile.raw.get("source"), dict) else {}
    connected = _connected(settings)
    out.update(
        overlay={"file": profile.overlay_file, "required": profile.overlay_required, "mode": profile.overlay_mode,
                 "exists": exists, "url": f"/campaign/profiles/{profile_id}/overlay",
                 "width": size[0] if size else None, "height": size[1] if size else None},
        on_screen_text={"text": profile.text, "position": profile.text_position, "required": profile.text_required},
        safe_zone={"top": profile.safe_top, "bottom": profile.safe_bottom},
        caption={"template": profile.caption_template, "hashtags": profile.hashtags,
                 "must_include": profile.caption_must_include, "tag_line": profile.caption_tag_line or None,
                 "max_extra_hashtags": profile.caption_max_extra_hashtags},
        payment={key: profile.payment.get(key) for key in ("cpm_usd", "min_views", "max_payout_per_clip_usd")},
        clipsmith_config=profile.clipsmith_config,
        search_default_scope=profile.search_default_scope,
        rules=[r for r in profile.raw.get("other_rules") or [] if isinstance(r, str)],
        approval=profile.raw.get("approval") if isinstance(profile.raw.get("approval"), str) else None,
        links={key: _https(links.get(key)) for key in ("content_rewards", "analytics_form")},
        material={"allowed": source.get("allowed") if isinstance(source.get("allowed"), str) else None,
                  "banned": [b for b in source.get("banned") or [] if isinstance(b, str)]},
        platforms=profile.platforms,
        accounts={platform: [_resolve(entry, connected[platform]) for entry in profile.accounts.get(platform, [])]
                  for platform in ACCOUNT_PLATFORMS},
        posting={"max_per_day": profile.max_posts_per_day, "min_gap_minutes": profile.min_gap_minutes},
        engagement=profile.engagement,
        # Samma kontroll som chatten gör före anropet till Claude.
        chat_problem=chat.blocker(chat.brain(settings).available, chat.material(profile_id, settings, _log(settings)),
                                  out["source"] == "twitch"),
    )
    return out


# ---------- konton ----------

ACCOUNT_PLATFORMS = ("tiktok", "youtube_shorts")  # plattformar där appen kopplar konton


def _connected(settings: Settings) -> dict[str, list[dict]]:
    """De kopplade kontona per plattform, med namn. names: id och namn som profilen kan använda, små bokstäver."""
    def entry(account_id: str, name: str, names: tuple, connected: bool) -> dict:
        return {"id": account_id, "name": name, "connected": connected,
                "names": {str(n).lstrip("@").lower() for n in (account_id, *names) if n}}

    return {
        "tiktok": [entry(key, t.get("display_name") or t.get("username") or "TikTok-konto",
                         (t.get("username"), t.get("display_name")), TikTok.connected(t))
                   for key, t in TikTok(settings).accounts().items()],
        "youtube_shorts": [entry(key, a.get("title") or a.get("handle") or "YouTube-kanal",
                                 (a.get("handle"), a.get("title")), YouTube.connected(a))
                           for key, a in YouTube(settings).accounts().items()],
    }


def _resolve(entry: str, accounts: list[dict]) -> dict:
    """Ett konto i profilen (id, användarnamn eller handle) som id och namn. Okänt konto: inget namn."""
    found = next((a for a in accounts if entry.lstrip("@").lower() in a["names"]), None)
    return {"id": found["id"], "name": found["name"]} if found else {"id": entry, "name": None}


@router.get("/accounts", summary="Kopplade TikTok-konton och YouTube-kanaler, och kampanjerna som postar från dem")
def get_accounts(settings: Settings = Depends(get_settings)) -> dict:
    connected = _connected(settings)
    used: dict[tuple[str, str], list[dict]] = {}
    for name in _profile_names(settings):
        try:
            profile = load_profile(name, settings.profiles_dir)
        except (SystemExit, Exception):  # en trasig profil syns redan under Kampanjer
            continue
        for platform in ACCOUNT_PLATFORMS:
            for entry in profile.accounts.get(platform, []):
                account = _resolve(entry, connected[platform])["id"]
                used.setdefault((platform, account), []).append({"name": name, "streamer": profile.streamer})
    return {platform: [{"id": a["id"], "name": a["name"], "connected": a["connected"],
                        "used_by": used.get((platform, a["id"]), [])} for a in accounts]
            for platform, accounts in connected.items()}


def _https(value: object) -> str | None:
    """Länken om den är en https-adress, annars None. Profilen får inte lägga in en javascript:-länk."""
    return value if isinstance(value, str) and urlparse(value).scheme == "https" and urlparse(value).netloc else None


# ---------- kampanjen ----------


@router.get("/profiles/{profile_id}", summary="En kampanj med overlay, text, caption och betalning")
def get_profile(profile_id: str, settings: Settings = Depends(get_settings)) -> dict:
    return _detail(profile_id, settings)


# ---------- overlay ----------


@router.put("/profiles/{profile_id}/overlay", summary="Ladda upp overlay-bilden (rå PNG, högst 10 MB)",
            dependencies=[Depends(verify_api_key)])
async def put_overlay(profile_id: str, request: Request, settings: Settings = Depends(get_settings)) -> dict:
    profile = _load(profile_id, settings)
    too_big = HTTPException(400, "Bilden är större än 10 MB.")
    if int(request.headers.get("content-length") or 0) > OVERLAY_MAX:
        raise too_big
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > OVERLAY_MAX:
            raise too_big
    path = settings.profiles_dir / f"{profile_id}.yaml"
    try:
        edit.check_overlay(bytes(data), profile.overlay_mode)
        size = edit.png_size(bytes(data[:24]))
        if profile.overlay_mode == "full" and size != (edit.PANEL_W, edit.PANEL_H):  # en banner
            split = edit.read_config(_config_path(profile, settings)).reframe.split_ratio
            data = bytearray(edit.banner_to_full(bytes(data), round(split * edit.PANEL_H)))
        with _write_lock:
            target = _overlay_target(profile, settings)
            if target is None:  # profilen saknar overlay.file: lägg till en rad, resten av filen är orörd
                text = edit.set_keys(path.read_bytes().decode("utf-8"), "overlay",
                                     {"file": f"overlays/{profile_id}.png"})
                profile = edit.check_profile_text(text, profile_id, settings.root)
                target = _overlay_target(profile, settings)
                assert target is not None  # overlay.file sattes nyss
                edit.write_atomic(path, text.encode("utf-8"))
            edit.write_atomic(target, bytes(data))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return _detail(profile_id, settings)


@router.get("/profiles/{profile_id}/overlay", summary="Overlay-bilden")
def get_overlay(profile_id: str, settings: Settings = Depends(get_settings)) -> FileResponse:
    profile = _load(profile_id, settings)
    try:
        target = _overlay_target(profile, settings)
    except ValueError:
        target = None
    if target is None or not target.is_file():
        raise HTTPException(404, "Kampanjen har ingen overlay-bild än.")
    return FileResponse(target, media_type="image/png")


@router.delete("/profiles/{profile_id}/overlay", summary="Ta bort overlay-bilden. Filens namn står kvar i profilen",
               dependencies=[Depends(verify_api_key)])
def delete_overlay(profile_id: str, settings: Settings = Depends(get_settings)) -> dict:
    profile = _load(profile_id, settings)
    try:
        target = _overlay_target(profile, settings)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    with _write_lock:
        if target is None or not target.is_file():
            raise HTTPException(404, "Kampanjen har ingen overlay-bild.")
        target.unlink()
    return _detail(profile_id, settings)


# ---------- exempelklipp ----------


@router.get("/profiles/{profile_id}/examples", summary="Kampanjens bästa klipp och klippare från Content Rewards")
def get_examples(profile_id: str, settings: Settings = Depends(get_settings)) -> dict:
    url = _load(profile_id, settings).content_rewards_url
    problem = examples.link_problem(url)
    if problem:
        return {"source": url, "problem": problem, "clips": [], "earners": []}
    try:
        data = examples.fetch(url)
    except RuntimeError as error:
        raise HTTPException(502, str(error)) from error
    return {"source": url, "problem": None, **data}


# ---------- profilens YAML ----------


class YamlBody(BaseModel):
    yaml: str = Field(max_length=200_000)


@router.get("/profiles/{profile_id}/yaml", summary="Profilen som text, med kommentarer")
def get_yaml(profile_id: str, settings: Settings = Depends(get_settings)) -> dict:
    return {"yaml": _profile_path(profile_id, settings).read_bytes().decode("utf-8")}


@router.put("/profiles/{profile_id}/yaml", summary="Spara profilen som text",
            dependencies=[Depends(verify_api_key)])
def put_yaml(profile_id: str, body: YamlBody, settings: Settings = Depends(get_settings)) -> dict:
    path = _profile_path(profile_id, settings)
    try:
        edit.check_profile_text(body.yaml, profile_id, settings.root)
        with _write_lock:
            old = path.read_bytes()
            # Texten som den är, så att kommentarerna finns kvar. Tmp-filen kontrolleras som svaret innan bytet,
            # så att en 400 aldrig lämnar en ändrad fil.
            edit.write_atomic(path, body.yaml.encode("utf-8"), check=_check_answer(profile_id, settings))
            edit.write_atomic(settings.profiles_dir / f".{profile_id}.yaml.bak", old)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return _detail(profile_id, settings)


class ActiveBody(BaseModel):
    active: bool


@router.put("/profiles/{profile_id}/active", summary="Slå på eller stäng av kampanjen i appen",
            dependencies=[Depends(verify_api_key)])
def put_active(profile_id: str, body: ActiveBody, settings: Settings = Depends(get_settings)) -> dict:
    path = _profile_path(profile_id, settings)
    try:
        with _write_lock:
            text = edit.set_active(path.read_bytes().decode("utf-8"), body.active)
            edit.check_profile_text(text, profile_id, settings.root)
            edit.write_atomic(path, text.encode("utf-8"))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return _detail(profile_id, settings)


# ---------- ny kampanj ----------


class NewFormat(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)


def _texts(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _texts(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _texts(item)


PlatformName = Literal["tiktok", "youtube_shorts", "instagram_reels"]
ACCOUNT_LIMIT = 20


def _cr_problem(url: str | None) -> str | None:
    """Länken till kampanjen i Content Rewards måste vara en https-adress på contentrewards.com."""
    if url and urlparse(url).scheme == "https" and urlparse(url).hostname in examples.HOSTS:
        return None
    return ("Länken till kampanjen ska börja med https://contentrewards.com/, till exempel "
            "https://contentrewards.com/c/campaigns/… Kopiera den från kampanjsidan.") if url else None


def _form_problem(url: str | None) -> str | None:
    return "Länken till analytics-formuläret måste vara en hel adress som börjar med https://." \
        if url and not _https(url) else None


class NoControl(BaseModel):
    @model_validator(mode="after")
    def _no_control_characters(self) -> NoControl:
        """En radbrytning kan annars lägga in egna fält i profilens YAML eller i kommentaren överst."""
        if any(edit.CONTROL.search(text) for text in _texts(self.model_dump())):
            raise ValueError("Texten får inte ha radbrytningar, tabbar eller andra styrtecken.")
        return self


class NewCampaign(NoControl):
    # source twitch: kanalen blir kampanjens id. source local: bara egna videor, name blir id:t.
    source: Literal["twitch", "local"] = "twitch"
    channel: str = ""
    name: str = ""
    streamer: str = Field(min_length=1, max_length=100)
    campaign: str | None = None
    language: str = "en"
    clip_min_s: int = 15
    clip_max_s: int = 45
    on_screen_text: str | None = None
    text_position: Literal["top", "bottom"] = "top"
    overlay_required: bool = True
    overlay_mode: Literal["full", "top", "bottom"] = "full"
    facecam_required: bool = True
    must_include: list[str] | None = None
    hashtags: list[str] | None = None
    caption_template: str | None = None
    formats: list[NewFormat] = Field(default_factory=list)
    cpm_usd: float | None = None
    min_views: int | None = None
    max_payout_per_clip_usd: float | None = None
    engagement_min_percent: float | None = None
    content_rewards_url: str | None = Field(default=None, max_length=500)
    analytics_form_url: str | None = Field(default=None, max_length=500)
    platforms: list[PlatformName] | None = None
    scout_ref: str | None = Field(default=None, max_length=200)  # kampanjen i Bevakning, så att den visas som inlagd


def _check_channel(login: str) -> str | None:
    """Finns kanalen på Twitch? Via twitch-dl, utan nycklar. login är redan kontrollerat mot edit.ID.
    Svarar inte Twitch blir svaret en varning, och kampanjen sparas ändå."""
    try:
        user = twitch.gql_query(f'{{ user(login: "{login}") {{ id login displayName }} }}')["data"]["user"]
    except Exception:  # twitch-dl kastar olika fel när Twitch inte svarar
        return (f"Twitch svarar inte, så appen kunde inte kontrollera att kanalen {login} finns. Kontrollera "
                f"stavningen på twitch.tv/{login}.")
    if not user:
        raise HTTPException(400, f"Twitch-kanalen {login} finns inte.")
    return None


@router.post("/profiles", status_code=201, summary="Ny kampanj", dependencies=[Depends(verify_api_key)])
def create_profile(body: NewCampaign, settings: Settings = Depends(get_settings)) -> dict:
    local = body.source == "local"
    profile_id = (body.name if local else body.channel).strip().lower()
    if not edit.ID.fullmatch(profile_id):
        raise HTTPException(400, "Kampanjens id får bara ha bokstäver a–z, siffror och _ (2–40 tecken)." if local
                            else "Kanalens namn får bara ha bokstäver a–z, siffror och _ (2–40 tecken).")
    profile_path = settings.profiles_dir / f"{profile_id}.yaml"
    config_name = f"config.{profile_id}.yaml"
    config_path = settings.root / config_name
    exists = HTTPException(409, f"Kampanjen {profile_id} finns redan.")
    if profile_path.exists() or config_path.exists():
        raise exists
    if not 1 <= body.clip_min_s < body.clip_max_s:
        raise HTTPException(400, "Klippens kortaste längd måste vara minst 1 sekund och kortare än den längsta.")
    try:
        (body.caption_template or "{title}").format(title="x")
    except (KeyError, IndexError, ValueError) as error:
        raise HTTPException(400, "Captionens mall får bara ha {title} inom klamrar.") from error
    if problem := _cr_problem(body.content_rewards_url) or _form_problem(body.analytics_form_url):
        raise HTTPException(400, problem)
    if body.platforms is not None and not body.platforms:
        raise HTTPException(400, "Välj minst en plattform.")
    template = settings.root / TEMPLATE_CONFIG
    if not template.is_file():
        raise HTTPException(500, f"Mallen {TEMPLATE_CONFIG} saknas. Den behövs för nya kampanjer.")
    warning = None if local else _check_channel(profile_id)

    with _write_lock:
        if profile_path.exists() or config_path.exists():
            raise exists
        try:
            config_text = edit.new_config_text(template.read_bytes().decode("utf-8"), profile_id,
                                               body.clip_min_s, body.clip_max_s, local=local)
            edit.write_atomic(config_path, config_text.encode("utf-8"), check=_check_config())
            profile_text = edit.new_profile_text(profile_id, body.model_dump(), config_name)
            edit.check_profile_text(profile_text, profile_id, settings.root)
            edit.write_atomic(profile_path, profile_text.encode("utf-8"))
        except ValueError as error:
            config_path.unlink(missing_ok=True)
            raise HTTPException(400, str(error)) from error
    return {**_detail(profile_id, settings), "warning": warning}


# ---------- Ställ in: formuläret för en kampanj som finns ----------


class SettingsFormat(BaseModel):
    id: str | None = Field(default=None, max_length=60)
    name: str = Field(max_length=100)
    description: str = Field(default="", max_length=1000)


class Links(BaseModel):
    content_rewards: str | None = Field(default=None, max_length=500)
    analytics_form: str | None = Field(default=None, max_length=500)


class CampaignSettings(NoControl):
    """Fälten i Ställ in. Fält som inte skickas rörs inte."""
    streamer: str | None = Field(default=None, max_length=100)
    campaign: str | None = Field(default=None, max_length=300)
    language: str | None = Field(default=None, max_length=20)
    links: Links | None = None
    caption_template: str | None = Field(default=None, max_length=500)
    hashtags: list[str] | None = None
    must_include: list[str] | None = None
    on_screen_text: str | None = Field(default=None, max_length=200)
    text_position: Literal["top", "bottom"] | None = None
    allowed_min_s: int | None = None  # det kampanjen tillåter (profilens clip)
    allowed_max_s: int | None = None
    clip_min_s: int | None = None  # det appen klipper (config-filens clip)
    clip_max_s: int | None = None
    cpm_usd: float | None = None
    min_views: int | None = None
    max_payout_per_clip_usd: float | None = None
    formats: list[SettingsFormat] | None = None
    platforms: list[PlatformName] | None = None
    accounts: dict[Literal["tiktok", "youtube_shorts"], list[str]] | None = None


def _settings_changes(body: CampaignSettings, data: dict, sent: set[str]) -> dict[str, object]:
    """Ändringarna i profilen, som sökväg -> värde. Bara värden som skiljer sig från filen."""
    wanted: dict[str, object] = {}
    for key in ("streamer", "campaign", "language"):
        if key in sent:
            value = (getattr(body, key) or "").strip()
            if key == "streamer" and not value:
                raise ValueError("Skriv streamerns namn.")
            wanted[key] = value or edit.DELETE
    if body.links is not None:
        for key in ("content_rewards", "analytics_form"):
            wanted[f"links.{key}"] = (getattr(body.links, key) or "").strip() or None
    for key, path in (("caption_template", "caption.template"), ("hashtags", "caption.hashtags"),
                      ("must_include", "caption.must_include"), ("on_screen_text", "on_screen_text.text"),
                      ("text_position", "on_screen_text.position"), ("allowed_min_s", "clip.min_seconds"),
                      ("allowed_max_s", "clip.max_seconds")):
        if key in sent:
            value = getattr(body, key)
            if isinstance(value, list):
                value = [v.strip() for v in value if v.strip()]
            wanted[path] = value
    for key in ("cpm_usd", "min_views", "max_payout_per_clip_usd"):
        if key in sent:
            value = getattr(body, key)
            wanted[f"payment.{key}"] = edit.DELETE if value is None else value
    if body.formats is not None:
        wanted["formats"] = edit.format_ids([f.model_dump() for f in body.formats])
    if body.platforms is not None:
        wanted["platforms"] = list(dict.fromkeys(body.platforms))
    if body.accounts is not None:
        old = data.get("accounts") if isinstance(data.get("accounts"), dict) else {}
        new = {**old, **{p: list(dict.fromkeys(a.strip() for a in names if a.strip()))
                         for p, names in body.accounts.items()}}
        new = {p: names for p, names in new.items() if names}
        if new != old:
            if not new:
                wanted["accounts"] = edit.DELETE
            else:
                for platform in dict.fromkeys([*old, *new]):
                    wanted[f"accounts.{platform}"] = new.get(platform, edit.DELETE)

    def current(path: str) -> object:
        top, _, sub = path.partition(".")
        value = data.get(top, edit.DELETE)
        if sub:
            value = value.get(sub, edit.DELETE) if isinstance(value, dict) else edit.DELETE
        return value

    def same(a: object, b: object) -> bool:  # null och ett fält som saknas betyder samma sak
        return a == b or (a is None or a is edit.DELETE) and (b is None or b is edit.DELETE)

    return {path: value for path, value in wanted.items() if not same(current(path), value)}


def _check_settings(body: CampaignSettings, profile: Profile, cfg_clip: tuple[int, int]) -> None:
    if body.links is not None and (problem := _cr_problem(body.links.content_rewards)
                                   or _form_problem(body.links.analytics_form)):
        raise ValueError(problem)
    if body.platforms is not None and not body.platforms:
        raise ValueError("Välj minst en plattform där kampanjen postas.")
    if body.formats is not None and any(not f.name.strip() for f in body.formats):
        raise ValueError("Ge varje format ett namn, eller ta bort raden.")
    if body.caption_template is not None:
        try:
            body.caption_template.format(title="x")
        except (KeyError, IndexError, ValueError) as error:
            raise ValueError("Captionens mall får bara ha {title} inom klamrar.") from error
        if "{title}" not in body.caption_template:
            raise ValueError("Captionens mall måste innehålla {title}, där klippets rubrik hamnar.")
    for value, label in ((body.cpm_usd, "Betalningen per 1 000 visningar"), (body.min_views, "Minsta visningar"),
                         (body.max_payout_per_clip_usd, "Taket per klipp")):
        if value is not None and value < 0:
            raise ValueError(f"{label} får inte vara mindre än 0.")
    lengths = (body.allowed_min_s, body.allowed_max_s, body.clip_min_s, body.clip_max_s)
    if any(v is not None for v in lengths):
        low = body.allowed_min_s if body.allowed_min_s is not None else profile.min_seconds
        high = body.allowed_max_s if body.allowed_max_s is not None else profile.max_seconds
        clip_low = body.clip_min_s if body.clip_min_s is not None else cfg_clip[0]
        clip_high = body.clip_max_s if body.clip_max_s is not None else cfg_clip[1]
        if not 1 <= low < high:
            raise ValueError("Kampanjens kortaste längd måste vara minst 1 sekund och kortare än den längsta.")
        if not 1 <= clip_low < clip_high:
            raise ValueError("Appens kortaste klipp måste vara minst 1 sekund och kortare än det längsta.")
        if not (low <= clip_low and clip_high <= high):
            raise ValueError(f"Appen ska klippa inom det kampanjen tillåter: {_seconds(low)}–{_seconds(high)} s.")


def _seconds(value: float) -> str:
    return f"{value:g}".replace(".", ",")


@router.put("/profiles/{profile_id}/settings", summary="Spara formuläret Ställ in. Resten av profilen är orörd",
            dependencies=[Depends(verify_api_key)])
def put_settings(profile_id: str, body: CampaignSettings, settings: Settings = Depends(get_settings)) -> dict:
    path = _profile_path(profile_id, settings)
    profile = _load(profile_id, settings)
    sent = body.model_fields_set
    try:
        with _write_lock:
            text = path.read_bytes().decode("utf-8")
            lengths_sent = {"allowed_min_s", "allowed_max_s", "clip_min_s", "clip_max_s"} & sent
            config = _config_path(profile, settings) if lengths_sent else None
            cfg = edit.read_config(config) if config else None
            _check_settings(body, profile, (cfg.clip.min_seconds, cfg.clip.max_seconds) if cfg else (0, 10**6))
            changes = _settings_changes(body, edit.parse_yaml(text), sent)
            new_text = edit.update_profile(text, changes) if changes else text
            edit.check_profile_text(new_text, profile_id, settings.root)
            old_config = config.read_bytes() if config else None
            if config and cfg:
                lengths = {"min_seconds": body.clip_min_s if body.clip_min_s is not None else cfg.clip.min_seconds,
                           "max_seconds": body.clip_max_s if body.clip_max_s is not None else cfg.clip.max_seconds}
                if (lengths["min_seconds"], lengths["max_seconds"]) != (cfg.clip.min_seconds, cfg.clip.max_seconds):
                    config_text = edit.set_keys(old_config.decode("utf-8"), "clip",
                                                {k: str(v) for k, v in lengths.items()})
                    edit.write_atomic(config, config_text.encode("utf-8"), check=_check_config())
            if new_text != text:
                try:
                    edit.write_atomic(path, new_text.encode("utf-8"), check=_check_answer(profile_id, settings))
                except ValueError:
                    if config and old_config is not None:
                        edit.write_atomic(config, old_config)  # inget halvt sparat
                    raise
                edit.write_atomic(settings.profiles_dir / f".{profile_id}.yaml.bak", text.encode("utf-8"))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return _detail(profile_id, settings)


# ---------- facecam och spelruta ----------


class ReframeBody(BaseModel):
    webcam_rect: Any = None  # kontrolleras i check_rect, så att fel ger 400 med förklaring
    webcam_rects: Any = None  # flera kamerapositioner; går före webcam_rect
    gameplay_rect: Any = None
    split_ratio: float


def _local_mp4(profile_id: str, settings: Settings, vod_id: str | None) -> Path | None:
    vod_id = vod_id or _latest_vod(profile_id, settings)
    mp4 = settings.work_dir / vod_id / f"{vod_id}.mp4" if vod_id else None
    return mp4 if mp4 is not None and mp4.is_file() else None


def _reframe(path: Path, mp4: Path | None) -> dict:
    """Rutorna och formerna. Finns VOD:en på disken visas dess verkliga storlek, med en varning om den
    inte är 1920x1080, eftersom rutorna mäts i en bild i 1920x1080."""
    try:
        reframe = edit.read_config(path).reframe
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    webcam_aspect, gameplay_aspect = edit.panel_aspects(reframe.split_ratio)
    width, height, warning = edit.SOURCE_W, edit.SOURCE_H, None
    if mp4 is not None:
        try:
            info = probe(mp4)
            width, height = info["width"], info["height"]
        except (RuntimeError, SystemExit):
            pass  # filen går inte att läsa: visa standardstorleken
    if (width, height) != (edit.SOURCE_W, edit.SOURCE_H):
        warning = (f"VOD:en är {width}x{height}, inte {edit.SOURCE_W}x{edit.SOURCE_H}. Rutorna mäts i "
                   f"{edit.SOURCE_W}x{edit.SOURCE_H} och hamnar fel i den här VOD:en. Ladda ner den i 1080p.")
    rects = reframe.webcam_rects or ([reframe.webcam_rect] if reframe.webcam_rect else [])
    return {"mode": reframe.mode, "split_ratio": reframe.split_ratio, "webcam_rect": reframe.webcam_rect,
            "webcam_rects": rects, "gameplay_rect": reframe.gameplay_rect, "source_width": width, "source_height": height,
            "webcam_aspect": webcam_aspect, "gameplay_aspect": gameplay_aspect, "warning": warning}


@router.get("/profiles/{profile_id}/reframe", summary="Facecam- och spelrutan")
def get_reframe(profile_id: str, vod_id: str | None = Query(default=None, pattern=r"^\d{6,15}$"),
                settings: Settings = Depends(get_settings)) -> dict:
    path = _config_path(_load(profile_id, settings), settings)
    return _reframe(path, _local_mp4(profile_id, settings, vod_id))


@router.put("/profiles/{profile_id}/reframe", summary="Spara facecam- och spelrutan",
            dependencies=[Depends(verify_api_key)])
def put_reframe(profile_id: str, body: ReframeBody, settings: Settings = Depends(get_settings)) -> dict:
    path = _config_path(_load(profile_id, settings), settings)
    if not 0.1 <= body.split_ratio <= 0.9:
        raise HTTPException(400, "split_ratio måste ligga mellan 0.1 och 0.9.")
    webcam_aspect, gameplay_aspect = edit.panel_aspects(body.split_ratio)
    try:
        if body.webcam_rects is not None:
            webcams = edit.check_rects(body.webcam_rects, webcam_aspect)
        else:
            webcams = [edit.check_rect(body.webcam_rect, webcam_aspect, "Facecam-rutan")]
        gameplay = edit.check_rect(body.gameplay_rect, gameplay_aspect, "Spelrutan")
        with _write_lock:
            text = edit.reframe_text(path.read_bytes().decode("utf-8"), webcams, gameplay, body.split_ratio)
            edit.write_atomic(path, text.encode("utf-8"), check=_check_config(
                {"mode": "stacked", "webcam_rect": webcams[0], "webcam_rects": webcams, "gameplay_rect": gameplay,
                 "split_ratio": body.split_ratio}))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return _reframe(path, _local_mp4(profile_id, settings, None))


# ---------- bild från VOD:en ----------


def _ffmpeg(inputs: list[str], frame: Path) -> None:
    """En bildruta i 1920x1080, samma storlek som rutorna mäts i. Skrivs via en tmp-fil."""
    tmp = frame.with_name(f".{frame.stem}.tmp.jpg")
    try:
        run([need("ffmpeg"), "-y", "-v", "error", *inputs, "-frames:v", "1",
             "-vf", f"scale={edit.SOURCE_W}:{edit.SOURCE_H}", "-q:v", "3", str(tmp)])
        os.replace(tmp, frame)
    except (RuntimeError, SystemExit) as error:  # SystemExit från need() när ffmpeg saknas
        raise HTTPException(500, "Kunde inte göra en bild av VOD:en med ffmpeg.") from error
    finally:
        tmp.unlink(missing_ok=True)


def _frame_from_twitch(vod_id: str, frame: Path) -> None:
    try:
        video = twitch.gql_query(
            f'{{ video(id: "{vod_id}") {{ previewThumbnailURL(width: 1920, height: 1080) }} }}')["data"]["video"]
    except Exception as error:  # twitch-dl kastar olika fel när Twitch inte svarar
        raise HTTPException(502, "Twitch svarar inte. Försök igen om en stund.") from error
    url = str((video or {}).get("previewThumbnailURL") or "")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != THUMB_HOST:
        raise HTTPException(404, f"Twitch har ingen bild för VOD {vod_id}.")
    too_big = HTTPException(502, "Bilden från Twitch är större än 5 MB.")
    data = bytearray()
    try:
        with http_client() as http, http.stream("GET", url) as response:
            if response.status_code != 200:
                raise HTTPException(502, "Twitch skickade ingen bild.")
            if int(response.headers.get("content-length") or 0) > IMAGE_MAX:
                raise too_big
            for chunk in response.iter_bytes():  # storleken kontrolleras under hämtningen
                data += chunk
                if len(data) > IMAGE_MAX:
                    raise too_big
    except httpx.HTTPError as error:
        raise HTTPException(502, "Twitch svarar inte. Försök igen om en stund.") from error
    if not data:
        raise HTTPException(502, "Twitch skickade ingen bild.")
    src = frame.with_name(f".{frame.stem}.src.jpg")
    try:
        src.write_bytes(bytes(data))
        # image2 läser filen bara som en bild, aldrig som till exempel en spellista med andra adresser.
        _ffmpeg(["-f", "image2", "-pattern_type", "none", "-i", str(src)], frame)
    finally:
        src.unlink(missing_ok=True)


@router.get("/profiles/{profile_id}/frame", summary="En bild ur VOD:en i 1920x1080 att rita rutorna på")
def get_frame(profile_id: str, vod_id: str | None = Query(default=None, pattern=r"^\d{6,15}$"),
              settings: Settings = Depends(get_settings)) -> FileResponse:
    _load(profile_id, settings)
    vod_id = vod_id or _latest_vod(profile_id, settings)
    if not vod_id:
        raise HTTPException(404, "Kampanjen har inget projekt än. Välj en stream eller skriv ett VOD-id.")
    frames = settings.work_dir / "frames"
    from_vod, from_twitch = frames / f"{vod_id}.vod.jpg", frames / f"{vod_id}.twitch.jpg"
    mp4 = settings.work_dir / vod_id / f"{vod_id}.mp4"
    with _frame_lock:
        frames.mkdir(parents=True, exist_ok=True)
        if mp4.is_file() and not from_vod.exists():  # VOD:en på disken går före Twitchs bild
            try:
                seconds = probe(mp4)["duration"] * 0.25  # en bit in, förbi startskärmen
            except (RuntimeError, SystemExit) as error:
                raise HTTPException(500, "VOD-filen går inte att läsa.") from error
            _ffmpeg(["-ss", f"{seconds:.2f}", "-i", str(mp4)], from_vod)
        if from_vod.exists():
            return FileResponse(from_vod, media_type="image/jpeg")
        if not from_twitch.exists():
            _frame_from_twitch(vod_id, from_twitch)
    return FileResponse(from_twitch, media_type="image/jpeg")
