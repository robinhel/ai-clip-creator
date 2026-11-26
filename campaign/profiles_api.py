"""Vägar för kampanjer i appen: en kampanj i detalj, overlay-bilden, profilens YAML, ny kampanj,
facecam- och spelrutan och en bild från VOD:en att rita rutorna på.

Vägar som skriver kräver API-nyckeln. Fel ger detail på svenska.
"""

from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from twitchdl import twitch

from clipsmith.api.deps import verify_api_key

from . import chat, examples, taste_api
from . import profile_edit as edit
from .api import _describe_profile, _log, _profile_names, get_settings
from .config import Settings
from .media import need, probe, run
from .profile import Profile, load_profile
from .tiktok import TikTok
from .youtube import YouTube

router = APIRouter(prefix="/campaign", tags=["campaign"])
router.include_router(taste_api.router)  # smaken från virala klipp
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
    if profile_id.startswith("_"):  # dolda kampanjer, som _ingen för liveklipp utan kampanj
        raise HTTPException(404, f"The campaign {profile_id} does not exist.")
    if not edit.ID.fullmatch(profile_id):
        raise HTTPException(400, "The campaign ID may only have lowercase letters, digits and _ (2–40 characters).")
    path = settings.profiles_dir / f"{profile_id}.yaml"
    if not path.exists():
        raise HTTPException(404, f"The campaign {profile_id} does not exist.")
    return path


def _load(profile_id: str, settings: Settings) -> Profile:
    _profile_path(profile_id, settings)
    try:
        return load_profile(profile_id, settings.profiles_dir)
    except (SystemExit, Exception) as error:  # SystemExit från load_profile, YAML-fel m.m.
        raise HTTPException(400, f"The campaign {profile_id} cannot be read: {error}") from error


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
                raise ValueError(f"reframe.{key} did not come out right in the config file. Change it by hand.")
    return check


def _check_answer(profile_id: str, settings: Settings) -> Callable[[Path], None]:
    """Kontroll av en ny profil innan den ersätter den gamla: svaret måste gå att räkna fram."""
    def check(tmp: Path) -> None:
        try:
            _describe_profile(profile_id, settings, path=tmp)
        except (SystemExit, Exception) as error:  # SystemExit från load_profile, typfel m.m.
            raise ValueError(f"The campaign cannot be read after the change: {error}") from None
    return check


def _latest_vod(profile_id: str, settings: Settings) -> str | None:
    runs = [r for r in _log(settings).runs() if r["profile"] == profile_id]
    vod_id = str(runs[-1]["vod_id"]) if runs else ""
    return vod_id if VOD_ID.fullmatch(vod_id) else None


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _as_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _only_texts(values: object) -> list[str]:
    return [v for v in values if isinstance(v, str)] if isinstance(values, list) else []


def _overlay_file(profile: Profile, settings: Settings) -> Path | None:
    """Overlay-bilden om den finns på disken."""
    try:
        target = _overlay_target(profile, settings)
    except ValueError:
        return None
    return target if target is not None and target.is_file() else None


def _overlay_info(profile: Profile, profile_id: str, settings: Settings) -> dict:
    target = _overlay_file(profile, settings)
    size = None
    if target is not None:
        with target.open("rb") as file:
            size = edit.png_size(file.read(24))
    width, height = size or (None, None)
    return {"file": profile.overlay_file, "required": profile.overlay_required, "mode": profile.overlay_mode,
            "exists": target is not None, "url": f"/campaign/profiles/{profile_id}/overlay",
            "width": width, "height": height}


def _detail(profile_id: str, settings: Settings) -> dict:
    profile = _load(profile_id, settings)
    try:
        out = _describe_profile(profile_id, settings)
    except (SystemExit, Exception) as error:
        raise HTTPException(400, f"The campaign {profile_id} cannot be read: {error}") from error
    links, source = _as_dict(profile.raw.get("links")), _as_dict(profile.raw.get("source"))
    connected = _connected(settings)
    out.update(
        overlay=_overlay_info(profile, profile_id, settings),
        on_screen_text={"text": profile.text, "position": profile.text_position, "required": profile.text_required,
                        "hook_style": profile.hook_style},
        safe_zone={"top": profile.safe_top, "bottom": profile.safe_bottom},
        caption={"template": profile.caption_template, "hashtags": profile.hashtags,
                 "must_include": profile.caption_must_include, "tag_line": profile.caption_tag_line or None,
                 "max_extra_hashtags": profile.caption_max_extra_hashtags},
        payment={key: profile.payment.get(key) for key in ("cpm_usd", "min_views", "max_payout_per_clip_usd")},
        clipsmith_config=profile.clipsmith_config,
        search_default_scope=profile.search_default_scope,
        rules=_only_texts(profile.raw.get("other_rules")),
        approval=_as_text(profile.raw.get("approval")),
        links={key: _https(links.get(key)) for key in ("content_rewards", "analytics_form")},
        material={"allowed": _as_text(source.get("allowed")), "banned": _only_texts(source.get("banned"))},
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
        "tiktok": [entry(key, t.get("display_name") or t.get("username") or "TikTok account",
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


def _readable_profiles(settings: Settings) -> Iterator[tuple[str, Profile]]:
    for name in _profile_names(settings):
        try:
            yield name, load_profile(name, settings.profiles_dir)
        except (SystemExit, Exception):  # en trasig profil syns redan under Kampanjer
            continue


def _chosen_accounts(profile: Profile) -> Iterator[tuple[str, str]]:
    """Kontona som profilen har valt, som (plattform, konto)."""
    return ((platform, entry) for platform in ACCOUNT_PLATFORMS for entry in profile.accounts.get(platform, []))


@router.get("/accounts", summary="Connected TikTok accounts and YouTube channels, and the campaigns that post from them")
def get_accounts(settings: Settings = Depends(get_settings)) -> dict:
    connected = _connected(settings)
    used: dict[tuple[str, str], list[dict]] = {}
    for name, profile in _readable_profiles(settings):
        for platform, entry in _chosen_accounts(profile):
            account = _resolve(entry, connected[platform])["id"]
            used.setdefault((platform, account), []).append({"name": name, "streamer": profile.streamer})
    return {platform: [{"id": a["id"], "name": a["name"], "connected": a["connected"],
                        "used_by": used.get((platform, a["id"]), [])} for a in accounts]
            for platform, accounts in connected.items()}


def _https(value: object) -> str | None:
    """Länken om den är en https-adress, annars None. Profilen får inte lägga in en javascript:-länk."""
    return value if isinstance(value, str) and urlparse(value).scheme == "https" and urlparse(value).netloc else None


# ---------- kampanjen ----------


@router.get("/profiles/{profile_id}", summary="A campaign with overlay, text, caption and payment")
def get_profile(profile_id: str, settings: Settings = Depends(get_settings)) -> dict:
    return _detail(profile_id, settings)


# ---------- overlay ----------


@router.put("/profiles/{profile_id}/overlay", summary="Upload the overlay image (raw PNG, at most 10 MB)",
            dependencies=[Depends(verify_api_key)])
async def put_overlay(profile_id: str, request: Request, settings: Settings = Depends(get_settings)) -> dict:
    profile = _load(profile_id, settings)
    too_big = HTTPException(400, "The image is larger than 10 MB.")
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
        raise HTTPException(404, "The campaign has no overlay image yet.")
    return FileResponse(target, media_type="image/png")


@router.delete("/profiles/{profile_id}/overlay", summary="Remove the overlay image. The file name stays in the profile",
               dependencies=[Depends(verify_api_key)])
def delete_overlay(profile_id: str, settings: Settings = Depends(get_settings)) -> dict:
    profile = _load(profile_id, settings)
    try:
        target = _overlay_target(profile, settings)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    with _write_lock:
        if target is None or not target.is_file():
            raise HTTPException(404, "The campaign has no overlay image.")
        target.unlink()
    return _detail(profile_id, settings)


# ---------- exempelklipp ----------


@router.get("/profiles/{profile_id}/examples", summary="The campaign's best clips and clippers from Content Rewards")
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


@router.get("/profiles/{profile_id}/yaml", summary="The profile as text, with comments")
def get_yaml(profile_id: str, settings: Settings = Depends(get_settings)) -> dict:
    return {"yaml": _profile_path(profile_id, settings).read_bytes().decode("utf-8")}


@router.put("/profiles/{profile_id}/yaml", summary="Save the profile as text",
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


@router.put("/profiles/{profile_id}/active", summary="Turn the campaign on or off in the app",
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


def _children(value: object) -> Iterable[object]:
    if isinstance(value, dict):
        return value.values()
    return value if isinstance(value, list | tuple) else ()


def _texts(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    for item in _children(value):
        yield from _texts(item)


PlatformName = Literal["tiktok", "youtube_shorts", "instagram_reels"]
ACCOUNT_LIMIT = 20


def _cr_problem(url: str | None) -> str | None:
    """Länken till kampanjen i Content Rewards måste vara en https-adress på contentrewards.com."""
    if _https(url) and urlparse(url).hostname in examples.HOSTS:
        return None
    return ("The link to the campaign must start with https://contentrewards.com/, for example "
            "https://contentrewards.com/c/campaigns/… Copy it from the campaign page.") if url else None


def _form_problem(url: str | None) -> str | None:
    return "The link to the analytics form must be a full address starting with https://." \
        if url and not _https(url) else None


class NoControl(BaseModel):
    @model_validator(mode="after")
    def _no_control_characters(self) -> NoControl:
        """En radbrytning kan annars lägga in egna fält i profilens YAML eller i kommentaren överst."""
        if any(edit.CONTROL.search(text) for text in _texts(self.model_dump())):
            raise ValueError("The text cannot have line breaks, tabs or other control characters.")
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
        return (f"Twitch is not responding, so the app could not check that the channel {login} exists. Check "
                f"the spelling at twitch.tv/{login}.")
    if not user:
        raise HTTPException(400, f"The Twitch channel {login} does not exist.")
    return None


def _check_template(template: str) -> None:
    try:
        template.format(title="x")
    except (KeyError, IndexError, ValueError) as error:
        raise ValueError("The caption template may only have {title} inside braces.") from error


def _check_links(content_rewards: str | None, analytics_form: str | None) -> None:
    problem = _cr_problem(content_rewards) or _form_problem(analytics_form)
    if problem:
        raise ValueError(problem)


def _check_platforms(platforms: Sequence[str] | None) -> None:
    if platforms is not None and not platforms:
        raise ValueError("Choose at least one platform where the campaign is posted.")


def _check_new(body: NewCampaign) -> None:
    """Fälten i Ny kampanj som servern kan kontrollera innan något skrivs."""
    if not 1 <= body.clip_min_s < body.clip_max_s:
        raise ValueError("The clips' shortest length must be at least 1 second and shorter than the longest.")
    _check_template(body.caption_template or "{title}")
    _check_links(body.content_rewards_url, body.analytics_form_url)
    _check_platforms(body.platforms)


@contextmanager
def _bad_request() -> Iterator[None]:
    """Ett ValueError blir ett svar med 400 och felets text."""
    try:
        yield
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


def _no_files(files: tuple[Path, ...], profile_id: str) -> None:
    if any(f.exists() for f in files):
        raise HTTPException(409, f"The campaign {profile_id} already exists.")


def _template(settings: Settings) -> Path:
    template = settings.root / TEMPLATE_CONFIG
    if not template.is_file():
        raise HTTPException(500, f"The template {TEMPLATE_CONFIG} is missing. It is needed for new campaigns.")
    return template


def _new_id(body: NewCampaign) -> str:
    local = body.source == "local"
    profile_id = (body.name if local else body.channel).strip().lower()
    if not edit.ID.fullmatch(profile_id):
        what = "The campaign ID" if local else "The channel name"
        raise HTTPException(400, f"{what} may only have letters a–z, digits and _ (2–40 characters).")
    return profile_id


def _write_new(profile_id: str, body: NewCampaign, template: Path, settings: Settings) -> None:
    """Config-filen först och sedan profilen. Går profilen inte att skriva tas config-filen bort."""
    config_name = f"config.{profile_id}.yaml"
    config_path = settings.root / config_name
    try:
        config_text = edit.new_config_text(template.read_bytes().decode("utf-8"), profile_id,
                                           (body.clip_min_s, body.clip_max_s), local=body.source == "local")
        edit.write_atomic(config_path, config_text.encode("utf-8"), check=_check_config())
        profile_text = edit.new_profile_text(profile_id, body.model_dump(), config_name)
        edit.check_profile_text(profile_text, profile_id, settings.root)
        edit.write_atomic(settings.profiles_dir / f"{profile_id}.yaml", profile_text.encode("utf-8"))
    except ValueError:
        config_path.unlink(missing_ok=True)
        raise


@router.post("/profiles", status_code=201, summary="New campaign", dependencies=[Depends(verify_api_key)])
def create_profile(body: NewCampaign, settings: Settings = Depends(get_settings)) -> dict:
    profile_id = _new_id(body)
    files = (settings.profiles_dir / f"{profile_id}.yaml", settings.root / f"config.{profile_id}.yaml")
    _no_files(files, profile_id)
    with _bad_request():
        _check_new(body)
    template = _template(settings)
    warning = None if body.source == "local" else _check_channel(profile_id)
    with _write_lock:
        _no_files(files, profile_id)
        with _bad_request():
            _write_new(profile_id, body, template, settings)
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
    hook_style: Literal["sans", "serif"] | None = None
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


_TEXT_FIELDS = ("streamer", "campaign", "language")
_PLAIN_FIELDS = (("caption_template", "caption.template"), ("hashtags", "caption.hashtags"),
                 ("must_include", "caption.must_include"), ("on_screen_text", "on_screen_text.text"),
                 ("text_position", "on_screen_text.position"), ("hook_style", "on_screen_text.hook_style"),
                 ("allowed_min_s", "clip.min_seconds"), ("allowed_max_s", "clip.max_seconds"))
_PAYMENT_FIELDS = ("cpm_usd", "min_views", "max_payout_per_clip_usd")


def _clean(value: object) -> object:
    """Listor utan tomma rader och mellanslag runt texten."""
    return [v.strip() for v in value if v.strip()] if isinstance(value, list) else value


def _text_changes(body: CampaignSettings, sent: set[str]) -> dict[str, object]:
    blank_streamer = not (body.streamer or "").strip()
    if "streamer" in sent and blank_streamer:
        raise ValueError("Enter the streamer's name.")
    return {key: (getattr(body, key) or "").strip() or edit.DELETE for key in _TEXT_FIELDS if key in sent}


def _payment_changes(body: CampaignSettings, sent: set[str]) -> dict[str, object]:
    """Ett tomt fält tar bort raden i profilen."""
    values = {key: getattr(body, key) for key in _PAYMENT_FIELDS if key in sent}
    return {f"payment.{key}": edit.DELETE if value is None else value for key, value in values.items()}


def _link_changes(links: Links) -> dict[str, object]:
    return {f"links.{key}": (getattr(links, key) or "").strip() or None for key in ("content_rewards", "analytics_form")}


def _field_changes(body: CampaignSettings, sent: set[str]) -> dict[str, object]:
    wanted: dict[str, object] = {path: _clean(getattr(body, key)) for key, path in _PLAIN_FIELDS if key in sent}
    wanted.update(_payment_changes(body, sent))
    if body.links is not None:
        wanted.update(_link_changes(body.links))
    if body.formats is not None:
        wanted["formats"] = edit.format_ids([f.model_dump() for f in body.formats])
    if body.platforms is not None:
        wanted["platforms"] = list(dict.fromkeys(body.platforms))
    return wanted


def _account_changes(accounts: Mapping[Any, list[str]], data: dict) -> dict[str, object]:
    """Kontona per plattform. En plattform utan konton tas bort, och accounts tas bort när inget är kvar."""
    old = _as_dict(data.get("accounts"))
    chosen = {p: list(dict.fromkeys(a.strip() for a in names if a.strip())) for p, names in accounts.items()}
    new = {p: names for p, names in {**old, **chosen}.items() if names}
    if new == old:
        return {}
    if not new:
        return {"accounts": edit.DELETE}
    return {f"accounts.{platform}": new.get(platform, edit.DELETE) for platform in dict.fromkeys([*old, *new])}


def _current(data: dict, path: str) -> object:
    top, _, sub = path.partition(".")
    value = data.get(top, edit.DELETE)
    if not sub:
        return value
    return value.get(sub, edit.DELETE) if isinstance(value, dict) else edit.DELETE


def _same(a: object, b: object) -> bool:
    """null och ett fält som saknas betyder samma sak."""
    missing = (None, edit.DELETE)
    return a == b or (a in missing and b in missing)


def _settings_changes(body: CampaignSettings, data: dict, sent: set[str]) -> dict[str, object]:
    """Ändringarna i profilen, som sökväg -> värde. Bara värden som skiljer sig från filen."""
    wanted = {**_text_changes(body, sent), **_field_changes(body, sent)}
    if body.accounts is not None:
        wanted.update(_account_changes(body.accounts, data))
    return {path: value for path, value in wanted.items() if not _same(_current(data, path), value)}


def _check_caption(template: str | None) -> None:
    if template is None:
        return
    _check_template(template)
    if "{title}" not in template:
        raise ValueError("The caption template must contain {title}, where the clip's headline goes.")


def _check_lists(body: CampaignSettings) -> None:
    if body.links is not None:
        _check_links(body.links.content_rewards, body.links.analytics_form)
    _check_platforms(body.platforms)
    if body.formats is not None and any(not f.name.strip() for f in body.formats):
        raise ValueError("Give each format a name, or remove the row.")


def _check_payment(body: CampaignSettings) -> None:
    for value, label in ((body.cpm_usd, "The payment per 1,000 views"), (body.min_views, "Minimum views"),
                         (body.max_payout_per_clip_usd, "The cap per clip")):
        if value is not None and value < 0:
            raise ValueError(f"{label} cannot be less than 0.")


def _pick(value: int | None, fallback: float) -> float:
    return fallback if value is None else value


def _check_lengths(body: CampaignSettings, profile: Profile, cfg_clip: tuple[int, int]) -> None:
    """Appens längd (config-filen) ska ligga inom kampanjens (profilen)."""
    low, high = _pick(body.allowed_min_s, profile.min_seconds), _pick(body.allowed_max_s, profile.max_seconds)
    clip_low, clip_high = _pick(body.clip_min_s, cfg_clip[0]), _pick(body.clip_max_s, cfg_clip[1])
    if not 1 <= low < high:
        raise ValueError("The campaign's shortest length must be at least 1 second and shorter than the longest.")
    if not 1 <= clip_low < clip_high:
        raise ValueError("The app's shortest clip must be at least 1 second and shorter than the longest.")
    if not (low <= clip_low and clip_high <= high):
        raise ValueError(f"The app must clip within what the campaign allows: {_seconds(low)}–{_seconds(high)} s.")


_LENGTHS = ("allowed_min_s", "allowed_max_s", "clip_min_s", "clip_max_s")


def _check_settings(body: CampaignSettings, profile: Profile, cfg_clip: tuple[int, int]) -> None:
    _check_lists(body)
    _check_caption(body.caption_template)
    _check_payment(body)
    if any(getattr(body, key) is not None for key in _LENGTHS):
        _check_lengths(body, profile, cfg_clip)


def _seconds(value: float) -> str:
    return f"{value:g}"


def _clip_of(config: Path) -> tuple[int, int]:
    """Appens klipplängd i config-filen."""
    cfg = edit.read_config(config)
    return cfg.clip.min_seconds, cfg.clip.max_seconds


def _new_lengths(body: CampaignSettings, old: tuple[int, int]) -> dict[str, int] | None:
    """Appens nya klipplängd för config-filen, eller None när den inte ändras."""
    new = (old[0] if body.clip_min_s is None else body.clip_min_s, old[1] if body.clip_max_s is None else body.clip_max_s)
    return None if new == old else {"min_seconds": new[0], "max_seconds": new[1]}


def _write_lengths(config: Path, lengths: dict[str, int]) -> bytes:
    """Skriver klipplängden i config-filen. Svaret är filen som den var, så att den kan skrivas tillbaka."""
    old = config.read_bytes()
    text = edit.set_keys(old.decode("utf-8"), "clip", {k: str(v) for k, v in lengths.items()})
    edit.write_atomic(config, text.encode("utf-8"), check=_check_config())
    return old


def _save_settings(profile_id: str, body: CampaignSettings, profile: Profile, settings: Settings) -> None:
    """Sparar båda filerna. Går profilen inte att spara skrivs config-filen tillbaka, så att inget blir halvt."""
    path = _profile_path(profile_id, settings)
    text = path.read_bytes().decode("utf-8")
    config = _config_path(profile, settings) if set(_LENGTHS) & body.model_fields_set else None
    old_clip = _clip_of(config) if config else None
    _check_settings(body, profile, old_clip or (0, 10**6))
    changes = _settings_changes(body, _as_dict(edit.parse_yaml(text)), body.model_fields_set)
    new_text = edit.update_profile(text, changes) if changes else text
    edit.check_profile_text(new_text, profile_id, settings.root)
    lengths = _new_lengths(body, old_clip) if old_clip else None
    restore = (config, _write_lengths(config, lengths)) if config and lengths else None
    if new_text == text:
        return
    try:
        edit.write_atomic(path, new_text.encode("utf-8"), check=_check_answer(profile_id, settings))
    except ValueError:
        if restore:
            edit.write_atomic(*restore)  # inget halvt sparat
        raise
    edit.write_atomic(settings.profiles_dir / f".{profile_id}.yaml.bak", text.encode("utf-8"))


@router.put("/profiles/{profile_id}/settings", summary="Save the Set up form. The rest of the profile is untouched",
            dependencies=[Depends(verify_api_key)])
def put_settings(profile_id: str, body: CampaignSettings, settings: Settings = Depends(get_settings)) -> dict:
    profile = _load(profile_id, settings)
    try:
        with _write_lock:
            _save_settings(profile_id, body, profile, settings)
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
        warning = (f"The VOD is {width}x{height}, not {edit.SOURCE_W}x{edit.SOURCE_H}. The boxes are measured in "
                   f"{edit.SOURCE_W}x{edit.SOURCE_H} and end up wrong in this VOD. Download it in 1080p.")
    rects = reframe.webcam_rects or ([reframe.webcam_rect] if reframe.webcam_rect else [])
    return {"mode": reframe.mode, "split_ratio": reframe.split_ratio, "webcam_rect": reframe.webcam_rect,
            "webcam_rects": rects, "gameplay_rect": reframe.gameplay_rect, "source_width": width, "source_height": height,
            "webcam_aspect": webcam_aspect, "gameplay_aspect": gameplay_aspect, "warning": warning}


@router.get("/profiles/{profile_id}/reframe", summary="Facecam and game boxes")
def get_reframe(profile_id: str, vod_id: str | None = Query(default=None, pattern=r"^\d{6,15}$"),
                settings: Settings = Depends(get_settings)) -> dict:
    path = _config_path(_load(profile_id, settings), settings)
    return _reframe(path, _local_mp4(profile_id, settings, vod_id))


@router.put("/profiles/{profile_id}/reframe", summary="Save the facecam and game boxes",
            dependencies=[Depends(verify_api_key)])
def put_reframe(profile_id: str, body: ReframeBody, settings: Settings = Depends(get_settings)) -> dict:
    path = _config_path(_load(profile_id, settings), settings)
    if not 0.1 <= body.split_ratio <= 0.9:
        raise HTTPException(400, "split_ratio must be between 0.1 and 0.9.")
    webcam_aspect, gameplay_aspect = edit.panel_aspects(body.split_ratio)
    try:
        if body.webcam_rects is not None:
            webcams = edit.check_rects(body.webcam_rects, webcam_aspect)
        else:
            webcams = [edit.check_rect(body.webcam_rect, webcam_aspect, "The facecam box")]
        gameplay = edit.check_rect(body.gameplay_rect, gameplay_aspect, "The game box")
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
        raise HTTPException(500, "Could not make a frame of the VOD with ffmpeg.") from error
    finally:
        tmp.unlink(missing_ok=True)


def _frame_from_twitch(vod_id: str, frame: Path) -> None:
    try:
        video = twitch.gql_query(
            f'{{ video(id: "{vod_id}") {{ previewThumbnailURL(width: 1920, height: 1080) }} }}')["data"]["video"]
    except Exception as error:  # twitch-dl kastar olika fel när Twitch inte svarar
        raise HTTPException(502, "Twitch is not responding. Try again in a while.") from error
    url = str((video or {}).get("previewThumbnailURL") or "")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != THUMB_HOST:
        raise HTTPException(404, f"Twitch has no image for VOD {vod_id}.")
    too_big = HTTPException(502, "The image from Twitch is larger than 5 MB.")
    data = bytearray()
    try:
        with http_client() as http, http.stream("GET", url) as response:
            if response.status_code != 200:
                raise HTTPException(502, "Twitch sent no image.")
            if int(response.headers.get("content-length") or 0) > IMAGE_MAX:
                raise too_big
            for chunk in response.iter_bytes():  # storleken kontrolleras under hämtningen
                data += chunk
                if len(data) > IMAGE_MAX:
                    raise too_big
    except httpx.HTTPError as error:
        raise HTTPException(502, "Twitch is not responding. Try again in a while.") from error
    if not data:
        raise HTTPException(502, "Twitch sent no image.")
    src = frame.with_name(f".{frame.stem}.src.jpg")
    try:
        src.write_bytes(bytes(data))
        # image2 läser filen bara som en bild, aldrig som till exempel en spellista med andra adresser.
        _ffmpeg(["-f", "image2", "-pattern_type", "none", "-i", str(src)], frame)
    finally:
        src.unlink(missing_ok=True)


@router.get("/profiles/{profile_id}/frame", summary="A frame from the VOD in 1920x1080 to draw the boxes on")
def get_frame(profile_id: str, vod_id: str | None = Query(default=None, pattern=r"^\d{6,15}$"),
              settings: Settings = Depends(get_settings)) -> FileResponse:
    _load(profile_id, settings)
    vod_id = vod_id or _latest_vod(profile_id, settings)
    if not vod_id:
        raise HTTPException(404, "The campaign has no project yet. Choose a stream or enter a VOD ID.")
    frames = settings.work_dir / "frames"
    from_vod, from_twitch = frames / f"{vod_id}.vod.jpg", frames / f"{vod_id}.twitch.jpg"
    mp4 = settings.work_dir / vod_id / f"{vod_id}.mp4"
    with _frame_lock:
        frames.mkdir(parents=True, exist_ok=True)
        if mp4.is_file() and not from_vod.exists():  # VOD:en på disken går före Twitchs bild
            try:
                seconds = probe(mp4)["duration"] * 0.25  # en bit in, förbi startskärmen
            except (RuntimeError, SystemExit) as error:
                raise HTTPException(500, "The VOD file cannot be read.") from error
            _ffmpeg(["-ss", f"{seconds:.2f}", "-i", str(mp4)], from_vod)
        if from_vod.exists():
            return FileResponse(from_vod, media_type="image/jpeg")
        if not from_twitch.exists():
            _frame_from_twitch(vod_id, from_twitch)
    return FileResponse(from_twitch, media_type="image/jpeg")
