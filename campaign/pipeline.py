"""En körning från appen: hämta VOD, transkribera via Berget, låt clipsmith välja och klippa,
och gör sedan klippen kampanjklara. Allt med den valda kampanjens inställningar."""

from __future__ import annotations

import logging
import shutil
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from clipsmith.api.worker import _emit, _harvest_clips
from clipsmith.candidates.builder import load_candidates
from clipsmith.config.loaders import load_config, load_secrets
from clipsmith.db.models import Run, RunStatus
from clipsmith.db.session import get_session
from clipsmith.llm import get_provider
from clipsmith.llm.prompts import build_stream_context
from clipsmith.models.transcript import Transcript
from clipsmith.models.twitch import Video
from clipsmith.pipeline import process_vod, save_picks
from clipsmith.rendering.clipper import cut_all_clips
from clipsmith.selection.selector import PickResult, select_clips
from clipsmith.twitch.client import TwitchClient

from .brain import Brain
from .budget import budget_from_settings
from .cliplog import ClipLog
from .config import Settings
from .download import download_vod
from .profile import Profile, load_profile
from .transcribe import berget_post, transcribe_vod
from .vodinfo import vod_facts
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


def finish(vod_id: str, profile: Profile, settings: Settings, game: str | None,
           run_id: int | None = None) -> list[dict]:
    log = ClipLog(settings.data_dir / "klipp.db")
    brain = Brain(settings, budget_from_settings(settings))
    subdir = f"run_{run_id}"
    if not (settings.out_dir / vod_id / subdir).is_dir():
        subdir = None  # en körning från före mapparna per körning har klippen direkt i out/<vod>/
    return finish_vod(vod_id, profile, settings, log, brain=brain, game=game, review=True, subdir=subdir)


def _twitch_ids(vod_id: str, channel: str, secrets) -> tuple[str, str]:
    """Kanalens id och VOD:ens start. clipsmith behöver båda för att hämta tittarnas klipp (+100).
    Utan nycklar, eller när Twitch inte svarar, går körningen ändå, men utan tittarklipp."""
    if not (secrets.twitch_client_id and secrets.twitch_client_secret):
        log.warning("Twitch-nycklarna saknas: tittarnas klipp räknas inte i körningen för %s", vod_id)
        return "", ""
    try:
        with TwitchClient(secrets.twitch_client_id, secrets.twitch_client_secret) as client:
            user_id = client.get_user_id(channel)
        created_at = vod_facts(vod_id).get("created_at") or ""
    except Exception as error:  # nätverk eller nycklar: körningen går vidare utan tittarklipp
        log.warning("Twitch svarade inte (%s): tittarnas klipp räknas inte för %s", type(error).__name__, vod_id)
        return "", ""
    return user_id, created_at


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
    run_id: int | None = None,
):
    """Hela kedjan för en VOD. Returnerar clipsmiths inställningar, som behövs efteråt, och en förklaring
    när körningen inte gav några nya klipp (annars None).

    Körningens råklipp och val hamnar i out/<vod>/run_<id>/, så att två körningar på samma VOD inte blandas.
    Utan run_id hamnar de direkt i out/<vod>/, som före mapparna per körning.
    """
    cfg = clipsmith_config(profile, settings)
    if min_s or max_s:  # körningens längd i en kopia, kampanjens fil ändras inte
        clip = cfg.clip.model_copy(update={"min_seconds": min_s or cfg.clip.min_seconds,
                                           "max_seconds": max_s or cfg.clip.max_seconds})
        cfg = cfg.model_copy(update={"clip": clip})
    channel = cfg.channels[0] if cfg.channels else profile.streamer.lower()
    meta = (ClipLog(settings.data_dir / "klipp.db").run(run_id) if run_id is not None else None) or {}
    title = meta.get("title") or vod_id  # VOD-fakta sparades när körningen startade
    duration = _duration(meta["duration_s"]) if meta.get("duration_s") else ""

    shown: set[int] = set()

    def downloading(done: int, total: int) -> None:
        percent = round(100 * done / max(total, 1))
        if percent not in shown:  # en händelse per procent, inte en per del (1 400 delar på 4 timmar)
            shown.add(percent)
            emit("download", 5.0 + percent / 10, f"Hämtar: {percent} %")

    emit("download", 5.0, "Hämtar VOD:en")
    mp4 = download_vod(vod_id, cfg.work_dir.expanduser(), on_progress=downloading)

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

    secrets = load_secrets()
    user_id, created_at = _twitch_ids(vod_id, channel, secrets)
    video = Video(id=vod_id, user_id=user_id, user_login=channel, title=title, created_at=created_at,
                  published_at="", url=f"https://www.twitch.tv/videos/{vod_id}", duration=duration,
                  type="archive")
    # clipsmith hittar kandidaterna. Valet och klippningen görs här, med kampanjens format och språk.
    process_vod(
        video, cfg, secrets,
        skip_download=True,
        skip_transcribe=True,
        skip_chat=(mp4.parent / "chat.json").exists(),
        skip_select=True,
        on_stage=lambda stage, pct: emit(stage, pct, ""),
        start_s=start_s,
        end_s=end_s,
    )
    candidates = load_candidates(mp4.parent / "candidates.json")
    run_dir = settings.out_dir / vod_id
    if run_id is not None:
        run_dir /= f"run_{run_id}"
    picks: list[PickResult] = []
    asked = 0
    if candidates:
        emit("select", 60.0, "")
        transcript = Transcript.from_json((mp4.parent / "transcript.json").read_text(encoding="utf-8"))
        transcript.segments = [s for s in transcript.segments  # samma del av VOD:en som kandidaterna
                               if s.start >= start_s and (not end_s or s.start <= end_s)]
        picker = get_provider(cfg, secrets, video=mp4)

        def ask(*args):
            nonlocal asked
            asked += 1
            return picker.pick(*args)

        context = build_stream_context(
            channel, title, duration, focus=focus, clip_range=(cfg.clip.min_seconds, cfg.clip.max_seconds),
            game=game or "", language=profile.language, formats=profile.formats)
        picks = select_clips(candidates, transcript, SimpleNamespace(pick=ask), context, cfg.clip)
        for result in picks:
            if result.pick.format not in profile.format_ids:
                result.pick.format = None  # ett id som kampanjen inte har blir inget format
        if picks:
            emit("clip", 85.0, "")
            cut_all_clips(mp4, transcript, picks, run_dir, cfg)  # väljer webbkamera per klipp i picks
    legacy = settings.out_dir / vod_id / "picks.json"
    if not legacy.exists() and (mp4.parent / "picks.json").exists() and any(legacy.parent.glob("clip_*.mp4")):
        shutil.copy(mp4.parent / "picks.json", legacy)  # gamla klipp i out/<vod>/ behåller sina val
    run_dir.mkdir(parents=True, exist_ok=True)
    for path in (run_dir / "picks.json", mp4.parent / "picks.json"):  # work/<vod>/: senaste körningen, för facit
        save_picks(picks, path)
    if not picks:
        return cfg, f"Claude valde inga av {asked} kandidater." if asked else "Inga kandidater hittades."

    emit("finish", 90.0, "Lägger på overlay, text och caption och kontrollerar reglerna")
    results = finish(vod_id, profile, settings, game, run_id)
    if not any("id" in result for result in results):
        reasons = ", ".join(dict.fromkeys(result["skipped"] for result in results if "skipped" in result))
        return cfg, f"Inga nya klipp. Claude valde {len(picks)}, men {reasons}."
    return cfg, None


def _duration(seconds: int) -> str:
    """13950 blir 3h52m30s, som i Twitchs API."""
    hours, rest = divmod(int(seconds), 3600)
    return f"{hours}h{rest // 60}m{rest % 60}s"


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
        cfg, why_none = make_clips(vod_id, profile, settings, game=game, start_s=start_s, end_s=end_s,
                                   focus=focus, min_s=min_s, max_s=max_s, run_id=run_id,
                                   emit=lambda stage, pct, msg: _emit(db, run_id, stage, pct, msg))
        _harvest_clips(db, run_id, vod_id, cfg)
        _emit(db, run_id, "done", 100.0, why_none or "Klart", status=RunStatus.done)
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
        results = finish(vod_id, load_profile(profile_name, settings.profiles_dir), settings, game, run_id)
        _emit(db, run_id, "done", 100.0, finish_summary(results), status=RunStatus.done)
    except BaseException as error:
        _fail(db, run_id, error)
        if not isinstance(error, Exception | SystemExit):
            raise
    finally:
        db.close()
        if app is not None:
            app.state.active_run_id = None
