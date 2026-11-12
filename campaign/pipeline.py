"""En körning från appen: hämta VOD, transkribera via Berget, låt clipsmith välja och klippa,
och gör sedan klippen kampanjklara. Allt med den valda kampanjens inställningar."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from clipsmith.api.worker import _emit, _harvest_clips
from clipsmith.config.loaders import load_config, load_secrets
from clipsmith.db.models import Run, RunStatus
from clipsmith.db.session import get_session
from clipsmith.models.twitch import Video
from clipsmith.pipeline import process_vod

from .brain import Brain
from .budget import budget_from_settings
from .cliplog import ClipLog
from .config import Settings
from .download import download_vod
from .profile import Profile, load_profile
from .transcribe import berget_post, transcribe_vod
from .workflow import finish_vod

Emit = Callable[[str, float, str], None]
log = logging.getLogger(__name__)


def clipsmith_config(profile: Profile, settings: Settings):
    """clipsmiths inställningar för kampanjen. Saknas filen stoppar körningen."""
    if not profile.clipsmith_config:
        raise ValueError(f"Kampanjen {profile.name} saknar clipsmith_config i sin profil.")
    path = settings.root / profile.clipsmith_config
    if not path.exists():
        raise ValueError(f"Kampanjen {profile.name} pekar på {profile.clipsmith_config}, som saknas.")
    return load_config(path)


def finish(vod_id: str, profile: Profile, settings: Settings, game: str | None) -> list[dict]:
    log = ClipLog(settings.data_dir / "klipp.db")
    brain = Brain(settings, budget_from_settings(settings))
    return finish_vod(vod_id, profile, settings, log, brain=brain, game=game, review=True)


def make_clips(
    vod_id: str,
    profile: Profile,
    settings: Settings,
    *,
    game: str | None,
    emit: Emit,
    start_s: float = 0.0,
    end_s: float = 0.0,
    focus: str = "",
    min_s: int | None = None,
    max_s: int | None = None,
):
    """Hela kedjan för en VOD. Returnerar clipsmiths inställningar, som behövs efteråt."""
    cfg = clipsmith_config(profile, settings)
    if min_s or max_s:  # körningens längd i en kopia, kampanjens fil ändras inte
        clip = cfg.clip.model_copy(update={"min_seconds": min_s or cfg.clip.min_seconds,
                                           "max_seconds": max_s or cfg.clip.max_seconds})
        cfg = cfg.model_copy(update={"clip": clip})
    channel = cfg.channels[0] if cfg.channels else profile.streamer.lower()

    emit("download", 5.0, "Hämtar VOD:en")
    mp4 = download_vod(vod_id, cfg.work_dir.expanduser())

    transcript = mp4.parent / "transcript.json"
    if not transcript.exists():
        if not settings.berget_api_key:
            raise ValueError("BERGET_API_KEY saknas i .env. Den behövs för transkriberingen.")
        emit("transcribe", 15.0, f"Transkriberar via Berget ({settings.transcribe_model})")
        transcribe_vod(mp4, vod_id, transcript, post=berget_post(settings.berget_api_key),
                       model=settings.transcribe_model, language=profile.language,
                       on_part=lambda n, total: emit("transcribe", 15.0 + 25.0 * (n - 1) / total,
                                                     f"Del {n} av {total}"),
                       budget=budget_from_settings(settings))

    video = Video(id=vod_id, user_id="", user_login=channel, title=vod_id, created_at="",
                  published_at="", url=f"https://www.twitch.tv/videos/{vod_id}", duration="",
                  type="archive")
    process_vod(
        video, cfg, load_secrets(),
        skip_download=True,
        skip_transcribe=True,
        skip_chat=(mp4.parent / "chat.json").exists(),
        on_stage=lambda stage, pct: emit(stage, pct, ""),
        start_s=start_s,
        end_s=end_s,
        focus=focus,
    )

    emit("finish", 90.0, "Lägger på overlay, text och caption och kontrollerar reglerna")
    finish(vod_id, profile, settings, game)
    return cfg


def _fail(db, run_id: int, error: BaseException) -> None:
    """Markera körningen som misslyckad, så att sidan slutar vänta."""
    log.error("kampanjkörning %d misslyckades", run_id, exc_info=error)
    try:
        db.rollback()  # ett fel vid commit lämnar sessionen trasig
        run = db.get(Run, run_id)
        if run:
            run.status = RunStatus.failed
            run.error = str(error) or type(error).__name__
            run.updated_at = datetime.now(UTC)
            db.commit()
    except Exception:
        log.exception("kunde inte spara att körning %d misslyckades", run_id)


def run_campaign_vod(
    run_id: int,
    vod_id: str,
    profile_name: str,
    game: str | None,
    settings: Settings,
    *,
    start_s: float = 0.0,
    end_s: float = 0.0,
    focus: str = "",
    min_s: int | None = None,
    max_s: int | None = None,
    app: Any = None,
) -> None:
    """Bakgrundsjobb för POST /campaign/runs. Skriver förloppet som clipsmiths händelser."""
    db = get_session()
    try:
        profile = load_profile(profile_name, settings.profiles_dir)
        cfg = make_clips(vod_id, profile, settings, game=game, start_s=start_s, end_s=end_s,
                         focus=focus, min_s=min_s, max_s=max_s,
                         emit=lambda stage, pct, msg: _emit(db, run_id, stage, pct, msg))
        _harvest_clips(db, run_id, vod_id, cfg)
        _emit(db, run_id, "done", 100.0, "Klart", status=RunStatus.done)
    except BaseException as error:  # SystemExit från kampanjlagret ska också synas i appen
        _fail(db, run_id, error)
        if not isinstance(error, Exception | SystemExit):
            raise
    finally:
        db.close()
        if app is not None:
            app.state.active_run_id = None


def finish_summary(results: list[dict]) -> str:
    """Vad Gör klippen kampanjklara igen gjorde. Blir körningens meddelande."""
    done = sum(1 for result in results if "id" in result)
    skipped = Counter(result["skipped"] for result in results if "skipped" in result)
    text = f"{done} klipp gjordes om." if done else "Inga klipp gjordes om."
    if skipped:
        reasons = ", ".join(f"{reason}: {count}" for reason, count in skipped.most_common())
        text += f" {sum(skipped.values())} hoppades över ({reasons})."
    return text


def finish_run(run_id: int, vod_id: str, profile_name: str, game: str | None, settings: Settings,
               app: Any = None) -> None:
    """Bakgrundsjobb för POST /campaign/runs/{id}/finish: gör klippen kampanjklara igen."""
    db = get_session()
    try:
        _emit(db, run_id, "finish", 90.0, "Gör klippen kampanjklara igen")
        results = finish(vod_id, load_profile(profile_name, settings.profiles_dir), settings, game)
        _emit(db, run_id, "done", 100.0, finish_summary(results), status=RunStatus.done)
    except BaseException as error:
        _fail(db, run_id, error)
        if not isinstance(error, Exception | SystemExit):
            raise
    finally:
        db.close()
        if app is not None:
            app.state.active_run_id = None
