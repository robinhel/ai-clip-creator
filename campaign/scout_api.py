"""Vägar för kampanjbevakningen: listan med betyg, Kontrollera nu, dölj eller visa en kampanj, och Robins land."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from clipsmith.api.deps import verify_api_key

from . import preferences
from .api import get_settings
from .config import Settings
from .scout import Scout

router = APIRouter(prefix="/campaign", tags=["campaign"])


@router.get("/scout", summary="New clipping campaigns from ClippingAlpha with scores")
def scout_overview(settings: Settings = Depends(get_settings)) -> dict:
    return Scout(settings).overview()


@router.post("/scout/refresh", status_code=202, summary="Kontrollera nu", dependencies=[Depends(verify_api_key)])
def scout_refresh(tasks: BackgroundTasks, settings: Settings = Depends(get_settings)) -> dict:
    scout = Scout(settings)
    if scout.ran_recently():  # sparar anrop: gränsen är 300 per dygn
        raise HTTPException(429, "Wait a moment. The last check was just now.")
    tasks.add_task(scout.run_once)
    return {"started": True}


class CountryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    country: str


def _country(settings: Settings) -> dict:
    return {"country": preferences.country(settings),
            "choices": [{"code": code, "name": name} for code, name in preferences.COUNTRIES.items()]}


@router.get("/scout/country", summary="Robin's country for the campaign score, and the countries to choose from")
def scout_country(settings: Settings = Depends(get_settings)) -> dict:
    return _country(settings)


@router.put("/scout/country", summary="Change country and recalculate the scores", dependencies=[Depends(verify_api_key)])
def scout_set_country(body: CountryBody, settings: Settings = Depends(get_settings)) -> dict:
    try:
        preferences.save_country(settings, body.country)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    Scout(settings).rescore()  # ur sparad data, inga anrop till ClippingAlpha
    return _country(settings)


def _dismiss(ref: str, dismissed: bool, settings: Settings) -> dict:
    if not Scout(settings).dismiss(ref, dismissed):
        raise HTTPException(404, "The campaign is not in the scout.")
    return {"ref": ref, "dismissed": dismissed}


@router.post("/scout/{ref:path}/dismiss", summary="Hide a campaign", dependencies=[Depends(verify_api_key)])
def scout_dismiss(ref: str, settings: Settings = Depends(get_settings)) -> dict:
    return _dismiss(ref, True, settings)


@router.post("/scout/{ref:path}/undismiss", summary="Show a hidden campaign again",
             dependencies=[Depends(verify_api_key)])
def scout_undismiss(ref: str, settings: Settings = Depends(get_settings)) -> dict:
    return _dismiss(ref, False, settings)
