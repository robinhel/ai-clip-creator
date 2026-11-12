"""Vägar för kampanjer i appen: en kampanj i detalj, overlay-bilden, profilens YAML, ny kampanj,
facecam- och spelrutan och en bild från VOD:en att rita rutorna på.

Vägar som skriver kräver API-nyckeln. Fel ger detail på svenska.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from twitchdl import twitch

from clipsmith.api.deps import verify_api_key
from clipsmith.config.loaders import load_config

from . import profile_edit as edit
from .api import _describe_profile, _log, get_settings
from .config import Settings
from .media import need, probe, run
from .profile import Profile, load_profile

router = APIRouter(prefix="/campaign", tags=["campaign"])
_write_lock = threading.Lock()  # en ändring av kampanjfilerna i taget
_frame_lock = threading.Lock()
OVERLAY_MAX = 10 * 1024 * 1024
IMAGE_MAX = 10 * 1024 * 1024
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
    if not profile.overlay_file:
        return None
    target = (settings.root / str(profile.overlay_file)).resolve()
    if not target.is_relative_to((settings.root / "overlays").resolve()) or target.suffix.lower() != ".png":
        raise ValueError(f"overlay.file är {profile.overlay_file}. Den måste ligga under overlays/ och sluta på .png.")
    return target


def _config_path(profile: Profile, settings: Settings) -> Path:
    path = (settings.root / str(profile.clipsmith_config or "")).resolve()
    if not profile.clipsmith_config or not path.is_relative_to(settings.root.resolve()) or not path.is_file():
        raise HTTPException(400, f"Kampanjen saknar sin config-fil ({profile.clipsmith_config}).")
    return path


def _check_config(expected: dict | None = None) -> Callable[[Path], None]:
    """Kontroll av en ny config-fil innan den ersätter den gamla."""
    def check(tmp: Path) -> None:
        try:
            cfg = load_config(tmp)
        except Exception as error:  # yaml- eller pydantic-fel
            raise ValueError(f"Config-filen går inte att läsa efter ändringen: {error}") from error
        for key, value in (expected or {}).items():
            if getattr(cfg.reframe, key) != value:
                raise ValueError(f"reframe.{key} blev inte rätt i config-filen. Ändra den för hand.")
    return check


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
    out.update(
        overlay={"file": profile.overlay_file, "required": profile.overlay_required, "mode": profile.overlay_mode,
                 "exists": exists, "url": f"/campaign/profiles/{profile_id}/overlay",
                 "width": size[0] if size else None, "height": size[1] if size else None},
        on_screen_text={"text": profile.text, "position": profile.text_position, "required": profile.text_required},
        safe_zone={"top": profile.safe_top, "bottom": profile.safe_bottom},
        caption={"template": profile.caption_template, "hashtags": profile.hashtags,
                 "must_include": profile.caption_must_include},
        payment={key: profile.payment.get(key) for key in ("cpm_usd", "min_views", "max_payout_per_clip_usd")},
        clipsmith_config=profile.clipsmith_config,
    )
    return out


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
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    with _write_lock:
        edit.write_atomic(settings.profiles_dir / f".{profile_id}.yaml.bak", path.read_bytes())
        edit.write_atomic(path, body.yaml.encode("utf-8"))  # texten som den är, så kommentarerna finns kvar
    return _detail(profile_id, settings)


# ---------- ny kampanj ----------


class NewCampaign(BaseModel):
    channel: str
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
    formats: list[dict] = Field(default_factory=list)
    cpm_usd: float | None = None
    min_views: int | None = None
    max_payout_per_clip_usd: float | None = None
    engagement_min_percent: float | None = None


def _check_channel(login: str) -> None:
    """Finns kanalen på Twitch? Via twitch-dl, utan nycklar. login är redan kontrollerat mot edit.ID."""
    try:
        user = twitch.gql_query(f'{{ user(login: "{login}") {{ id login displayName }} }}')["data"]["user"]
    except Exception as error:  # twitch-dl kastar olika fel när Twitch inte svarar
        raise HTTPException(502, "Twitch svarar inte. Försök igen om en stund.") from error
    if not user:
        raise HTTPException(400, f"Twitch-kanalen {login} finns inte.")


@router.post("/profiles", status_code=201, summary="Ny kampanj", dependencies=[Depends(verify_api_key)])
def create_profile(body: NewCampaign, settings: Settings = Depends(get_settings)) -> dict:
    profile_id = body.channel.strip().lower()
    if not edit.ID.fullmatch(profile_id):
        raise HTTPException(400, "Kanalens namn får bara ha bokstäver a–z, siffror och _ (2–40 tecken).")
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
    template = settings.root / TEMPLATE_CONFIG
    if not template.is_file():
        raise HTTPException(500, f"Mallen {TEMPLATE_CONFIG} saknas. Den behövs för nya kampanjer.")
    _check_channel(profile_id)

    with _write_lock:
        if profile_path.exists() or config_path.exists():
            raise exists
        try:
            config_text = edit.new_config_text(template.read_bytes().decode("utf-8"), profile_id,
                                               body.clip_min_s, body.clip_max_s)
            edit.write_atomic(config_path, config_text.encode("utf-8"), check=_check_config())
            profile_text = edit.new_profile_text(profile_id, body.model_dump(), config_name)
            edit.check_profile_text(profile_text, profile_id, settings.root)
            edit.write_atomic(profile_path, profile_text.encode("utf-8"))
        except ValueError as error:
            config_path.unlink(missing_ok=True)
            raise HTTPException(400, str(error)) from error
    return _detail(profile_id, settings)


# ---------- facecam och spelruta ----------


class ReframeBody(BaseModel):
    webcam_rect: Any = None  # kontrolleras i check_rect, så att fel ger 400 med förklaring
    gameplay_rect: Any = None
    split_ratio: float


def _reframe(path: Path) -> dict:
    try:
        reframe = load_config(path).reframe
    except Exception as error:  # yaml- eller pydantic-fel
        raise HTTPException(400, f"Config-filen går inte att läsa: {error}") from error
    webcam_aspect, gameplay_aspect = edit.panel_aspects(reframe.split_ratio)
    return {"mode": reframe.mode, "split_ratio": reframe.split_ratio, "webcam_rect": reframe.webcam_rect,
            "gameplay_rect": reframe.gameplay_rect, "source_width": edit.SOURCE_W, "source_height": edit.SOURCE_H,
            "webcam_aspect": webcam_aspect, "gameplay_aspect": gameplay_aspect}


@router.get("/profiles/{profile_id}/reframe", summary="Facecam- och spelrutan")
def get_reframe(profile_id: str, settings: Settings = Depends(get_settings)) -> dict:
    return _reframe(_config_path(_load(profile_id, settings), settings))


@router.put("/profiles/{profile_id}/reframe", summary="Spara facecam- och spelrutan",
            dependencies=[Depends(verify_api_key)])
def put_reframe(profile_id: str, body: ReframeBody, settings: Settings = Depends(get_settings)) -> dict:
    path = _config_path(_load(profile_id, settings), settings)
    if not 0.1 <= body.split_ratio <= 0.9:
        raise HTTPException(400, "split_ratio måste ligga mellan 0.1 och 0.9.")
    webcam_aspect, gameplay_aspect = edit.panel_aspects(body.split_ratio)
    try:
        webcam = edit.check_rect(body.webcam_rect, webcam_aspect, "Facecam-rutan")
        gameplay = edit.check_rect(body.gameplay_rect, gameplay_aspect, "Spelrutan")
        with _write_lock:
            text = edit.reframe_text(path.read_bytes().decode("utf-8"), webcam, gameplay, body.split_ratio)
            edit.write_atomic(path, text.encode("utf-8"), check=_check_config(
                {"webcam_rect": webcam, "gameplay_rect": gameplay, "split_ratio": body.split_ratio}))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return _reframe(path)


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
    try:
        with http_client() as http:
            response = http.get(url)
    except httpx.HTTPError as error:
        raise HTTPException(502, "Twitch svarar inte. Försök igen om en stund.") from error
    if response.status_code != 200 or not response.content or len(response.content) > IMAGE_MAX:
        raise HTTPException(502, "Twitch skickade ingen bild.")
    src = frame.with_name(f".{frame.stem}.src.jpg")
    try:
        src.write_bytes(response.content)
        _ffmpeg(["-i", str(src)], frame)  # ffmpeg skalar och kontrollerar samtidigt att det är en bild
    finally:
        src.unlink(missing_ok=True)


@router.get("/profiles/{profile_id}/frame", summary="En bild ur VOD:en i 1920x1080 att rita rutorna på")
def get_frame(profile_id: str, vod_id: str | None = Query(default=None, pattern=r"^\d{6,15}$"),
              settings: Settings = Depends(get_settings)) -> FileResponse:
    _load(profile_id, settings)
    if vod_id is None:
        runs = [r for r in _log(settings).runs() if r["profile"] == profile_id]
        vod_id = runs[-1]["vod_id"] if runs else None
        if not vod_id or not VOD_ID.fullmatch(str(vod_id)):
            raise HTTPException(404, "Kampanjen har ingen körning än. Ange en VOD.")
    frame = settings.work_dir / "frames" / f"{vod_id}.jpg"
    with _frame_lock:
        if not frame.exists():
            frame.parent.mkdir(parents=True, exist_ok=True)
            mp4 = settings.work_dir / vod_id / f"{vod_id}.mp4"
            if mp4.exists():
                try:
                    seconds = probe(mp4)["duration"] * 0.25  # en bit in, förbi startskärmen
                except (RuntimeError, SystemExit) as error:
                    raise HTTPException(500, "VOD-filen går inte att läsa.") from error
                _ffmpeg(["-ss", f"{seconds:.2f}", "-i", str(mp4)], frame)
            else:
                _frame_from_twitch(vod_id, frame)
    return FileResponse(frame, media_type="image/jpeg")
