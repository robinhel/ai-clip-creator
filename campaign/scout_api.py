"""Vägar för kampanjbevakningen: listan med betyg, Kontrollera nu, och dölj eller visa en kampanj."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from clipsmith.api.deps import verify_api_key

from .api import get_settings
from .config import Settings
from .scout import Scout

router = APIRouter(prefix="/campaign", tags=["campaign"])


@router.get("/scout", summary="Nya clipping-kampanjer från ClippingAlpha med betyg")
def scout_overview(settings: Settings = Depends(get_settings)) -> dict:
    return Scout(settings).overview()


@router.post("/scout/refresh", status_code=202, summary="Kontrollera nu", dependencies=[Depends(verify_api_key)])
def scout_refresh(tasks: BackgroundTasks, settings: Settings = Depends(get_settings)) -> dict:
    scout = Scout(settings)
    if scout.ran_recently():  # sparar anrop: gränsen är 300 per dygn
        raise HTTPException(429, "Vänta en stund. Senaste kontrollen var nyss.")
    tasks.add_task(scout.run_once)
    return {"started": True}


def _dismiss(ref: str, dismissed: bool, settings: Settings) -> dict:
    if not Scout(settings).dismiss(ref, dismissed):
        raise HTTPException(404, "Kampanjen finns inte i bevakningen.")
    return {"ref": ref, "dismissed": dismissed}


@router.post("/scout/{ref:path}/dismiss", summary="Dölj en kampanj", dependencies=[Depends(verify_api_key)])
def scout_dismiss(ref: str, settings: Settings = Depends(get_settings)) -> dict:
    return _dismiss(ref, True, settings)


@router.post("/scout/{ref:path}/undismiss", summary="Visa en dold kampanj igen",
             dependencies=[Depends(verify_api_key)])
def scout_undismiss(ref: str, settings: Settings = Depends(get_settings)) -> dict:
    return _dismiss(ref, False, settings)
