"""Inlägg per plattform: samma klipp postat på TikTok, Reels och Shorts. Varje inlägg skickas in, följs
och betalas för sig. Svaren är hela klippet, så att webben kan visa klippet och alla dess inlägg.

Vägarna registreras på campaign.api.router, som importerar den här modulen sist.
"""

from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import Depends, HTTPException, Response
from pydantic import BaseModel, Field

from clipsmith.api.deps import verify_api_key

from .api import (
    NEXT_STATUS,
    _check_min_views,
    _get,
    _link_problem,
    _log,
    _profile,
    _public,
    get_settings,
    router,
)
from .cliplog import ClipLog
from .config import Settings

Platform = Literal["tiktok", "youtube_shorts", "instagram_reels"]
PostStatus = Literal["postad", "inskickad", "analytics_inskickad", "betald"]


class NewPost(BaseModel):
    platform: Platform
    url: str = Field(max_length=500)


class PostPatch(BaseModel):
    status: PostStatus | None = None
    url: str | None = Field(default=None, max_length=500)
    views: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    shares: int | None = Field(default=None, ge=0)
    paid_usd: float | None = Field(default=None, ge=0)


def _get_post(log: ClipLog, post_id: int) -> dict:
    try:
        return log.get_post(post_id)
    except SystemExit as error:
        raise HTTPException(404, "Inlägget finns inte.") from error


@router.post("/clips/{clip_id}/posts", status_code=201, summary="Lägg till ett inlägg på en plattform",
             dependencies=[Depends(verify_api_key)])
def add_post(clip_id: int, body: NewPost, response: Response, settings: Settings = Depends(get_settings)) -> dict:
    log = _log(settings)
    clip = _get(log, clip_id)
    if clip["status"] not in ("godkand", "postad", "inskickad", "analytics_inskickad", "betald"):
        raise HTTPException(400, "Klippet måste vara godkänt innan det postas.")
    if clip["problems"]:
        raise HTTPException(400, "Klippet har problem. Det får inte postas.")
    url = body.url.strip()
    problem = _link_problem(url, body.platform)
    if problem:
        raise HTTPException(400, problem)
    posts = [p for p in log.posts(clip_id) if p["platform"] == body.platform]
    if any(p["url"] == url for p in posts):
        response.status_code = 200  # samma inlägg igen, till exempel lagt in av hjälpen efter uppladdningen
        return _public(log.get(clip_id), settings)
    taken = "Klippet har redan ett inlägg på den plattformen. Ändra länken i stället."
    if posts and not _profile(clip["profile"], settings).multi(body.platform):  # ett konto: ett inlägg
        raise HTTPException(409, taken)
    try:
        log.add_post(clip_id, body.platform, url)  # för hand: inget konto
    except sqlite3.IntegrityError as error:
        raise HTTPException(409, taken) from error
    return _public(log.get(clip_id), settings)


@router.patch("/posts/{post_id}", summary="Ändra ett inlägg: status, länk, statistik eller utbetalning",
              dependencies=[Depends(verify_api_key)])
def patch_post(post_id: int, body: PostPatch, settings: Settings = Depends(get_settings)) -> dict:
    log = _log(settings)
    post = _get_post(log, post_id)
    fields = body.model_dump(exclude_unset=True)
    if "status" in fields and fields["status"] is None:
        raise HTTPException(400, "Status får inte vara tom.")
    if "url" in fields:
        problem = _link_problem((fields["url"] or "").strip(), post["platform"])
        if problem:
            raise HTTPException(400, problem)
        fields["url"] = fields["url"].strip()
    new = fields.get("status")
    if new and new != post["status"]:
        if new not in NEXT_STATUS[post["status"]]:
            raise HTTPException(400, f"Ett inlägg med status {post['status']} kan inte bli {new}.")
        if new == "analytics_inskickad":
            clip = _get(log, post["clip_id"])
            _check_min_views(fields.get("views", post["views"]), _profile(clip["profile"], settings))
    log.update_post(post_id, **fields)
    return _public(log.get(post["clip_id"]), settings)


@router.delete("/posts/{post_id}", summary="Ta bort ett inlägg", dependencies=[Depends(verify_api_key)])
def delete_post(post_id: int, settings: Settings = Depends(get_settings)) -> dict:
    log = _log(settings)
    post = _get_post(log, post_id)
    log.delete_post(post_id)
    return _public(log.get(post["clip_id"]), settings)
