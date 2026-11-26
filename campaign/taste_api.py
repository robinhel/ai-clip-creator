"""Vägar för smaken från virala klipp: smaken och klippen, hämta virala klipp, en egen länk och analysen.

Ingår i profiles_api.router (samma prefix /campaign). Vägar som skriver kräver API-nyckeln. Fel ger detail på engelska.
campaign/viral.py och campaign/taste.py importeras inne i funktionerna.

En egen länk och analysen tar minuter (nedladdning, measure.py, whisper) och körs som bakgrundsjobb: svaret är 202,
och GET /taste visar running och error tills jobbet är klart. Ett jobb per kampanj i taget; läget finns bara i minnet.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from clipsmith.api.deps import verify_api_key

from .api import get_settings
from .brain import Brain
from .budget import BudgetExceeded, budget_from_settings
from .config import Settings
from .profile import Profile

router = APIRouter()
log = logging.getLogger(__name__)
JOBS: dict[str, dict] = {}  # kampanjens namn -> {"running": bool, "error": str | None}
_jobs_lock = threading.Lock()
IDLE = {"running": False, "error": None}


class ViralLink(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    views: int | None = Field(default=None, ge=0)


def _load(profile_id: str, settings: Settings) -> Profile:
    from .profiles_api import _load as load  # profiles_api tar in den här routern

    return load(profile_id, settings)


def _data(profile: Profile) -> dict:
    from . import taste, viral

    return {"taste": taste.load_taste(profile), "clips": viral.load(profile)}


def _state(profile: Profile, settings: Settings) -> dict:
    """Smaken, klippen, texten Claude får och läget för kampanjens bakgrundsjobb."""
    from . import taste

    return {**_data(profile), "text": taste.taste_text(profile, settings.data_dir),
            **JOBS.get(profile.name, IDLE)}


def _start(profile: Profile, tasks: BackgroundTasks, work: Callable[[], object]) -> dict:
    """Starta work som bakgrundsjobb. 409 när kampanjen redan har ett jobb igång."""
    state = _data(profile)  # före låset: kastar den här står jobbet inte kvar som running
    with _jobs_lock:
        if JOBS.get(profile.name, IDLE)["running"]:
            raise HTTPException(409, "A job is already running for this campaign. Wait until it is done.")
        JOBS[profile.name] = {"running": True, "error": None}
    tasks.add_task(_run, profile.name, work)
    return {**state, "running": True}


def _run(name: str, work: Callable[[], object]) -> None:
    """Kör jobbet och spara hur det gick. Jobbet lämnas aldrig som running."""
    error = None
    try:
        work()
    except BudgetExceeded as exc:
        error = f"{exc} Nothing was saved."
    except Exception as exc:  # felet visas i appen; jobbet får inte stå kvar som running
        log.warning("Taste job for %s failed (%s)", name, type(exc).__name__)
        error = str(exc) or type(exc).__name__
    JOBS[name] = {"running": False, "error": error}


@contextmanager
def _errors() -> Iterator[None]:
    """Felen från viral.py och taste.py som svar: fel i det skickade 400, budgettaket 402, nätet 502."""
    try:
        yield
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except BudgetExceeded as error:
        raise HTTPException(402, f"{error} Nothing was saved.") from error
    except RuntimeError as error:
        raise HTTPException(502, str(error)) from error


@router.get("/profiles/{profile_id}/taste", summary="The taste from viral clips, and the clips it comes from")
def get_taste(profile_id: str, settings: Settings = Depends(get_settings)) -> dict:
    return _state(_load(profile_id, settings), settings)


@router.post("/profiles/{profile_id}/viral/collect", summary="Fetch the campaign's viral clips and find the outliers",
             dependencies=[Depends(verify_api_key)])
def collect_viral(profile_id: str, settings: Settings = Depends(get_settings)) -> dict:
    from . import viral

    profile = _load(profile_id, settings)
    with _errors():
        viral.collect(profile)
    return _state(profile, settings)


@router.post("/profiles/{profile_id}/viral/link", status_code=202, summary="Add a viral clip from a link",
             dependencies=[Depends(verify_api_key)])
def add_viral_link(profile_id: str, body: ViralLink, tasks: BackgroundTasks,
                   settings: Settings = Depends(get_settings)) -> dict:
    from . import viral

    profile = _load(profile_id, settings)
    url = body.url.strip()
    with _errors():
        viral.check_link(url)
    return _start(profile, tasks, lambda: viral.add_link(profile, url, views=body.views))


@router.post("/profiles/{profile_id}/taste/analyse", status_code=202,
             summary="Ask Claude how the top outliers were made", dependencies=[Depends(verify_api_key)])
def analyse_taste(profile_id: str, tasks: BackgroundTasks, settings: Settings = Depends(get_settings)) -> dict:
    from . import taste

    profile = _load(profile_id, settings)
    brain = Brain(settings, budget_from_settings(settings))
    if not brain.available:
        raise HTTPException(400, "The Claude key is missing. Add it under Settings, then analyse again.")
    return _start(profile, tasks, lambda: taste.analyse(profile, brain=brain))
