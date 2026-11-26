"""Vägar för smaken från virala klipp: smaken och klippen, hämta virala klipp, en egen länk och analysen.

Ingår i profiles_api.router (samma prefix /campaign). Vägar som skriver kräver API-nyckeln. Fel ger detail på engelska.
campaign/viral.py och campaign/taste.py importeras inne i funktionerna.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from clipsmith.api.deps import verify_api_key

from .api import get_settings
from .brain import Brain
from .budget import BudgetExceeded, budget_from_settings
from .config import Settings
from .profile import Profile

router = APIRouter()


class ViralLink(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    views: int | None = Field(default=None, ge=0)


def _load(profile_id: str, settings: Settings) -> Profile:
    from .profiles_api import _load as load  # profiles_api tar in den här routern

    return load(profile_id, settings)


def _state(profile: Profile) -> dict:
    from . import taste, viral

    return {"taste": taste.load_taste(profile), "clips": viral.load(profile)}


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
    return _state(_load(profile_id, settings))


@router.post("/profiles/{profile_id}/viral/collect", summary="Fetch the campaign's viral clips and find the outliers",
             dependencies=[Depends(verify_api_key)])
def collect_viral(profile_id: str, settings: Settings = Depends(get_settings)) -> dict:
    from . import viral

    profile = _load(profile_id, settings)
    with _errors():
        viral.collect(profile)
    return _state(profile)


@router.post("/profiles/{profile_id}/viral/link", summary="Add a viral clip from a link",
             dependencies=[Depends(verify_api_key)])
def add_viral_link(profile_id: str, body: ViralLink, settings: Settings = Depends(get_settings)) -> dict:
    from . import viral

    profile = _load(profile_id, settings)
    with _errors():
        viral.add_link(profile, body.url.strip(), views=body.views)
    return _state(profile)


@router.post("/profiles/{profile_id}/taste/analyse", summary="Ask Claude how the top outliers were made",
             dependencies=[Depends(verify_api_key)])
def analyse_taste(profile_id: str, settings: Settings = Depends(get_settings)) -> dict:
    from . import taste

    profile = _load(profile_id, settings)
    brain = Brain(settings, budget_from_settings(settings))
    if not brain.available:
        raise HTTPException(400, "The Claude key is missing. Add it under Settings, then analyse again.")
    with _errors():
        taste.analyse(profile, brain=brain)
    return _state(profile)
