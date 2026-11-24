"""FastAPI application factory."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, RedirectResponse

from .routes import analytics, clips, files, health, publish, runs, stream
from ..db.models import Run, RunStatus
from ..db.session import get_session, init_db
from ..settings import load_config
from ..telemetry import tracer  # noqa: F401 — side-effect: initialises OTel SDK


def _fail_interrupted_runs() -> None:
    """Background runs die with the server, so a run still marked as going can never finish."""
    db = get_session()
    try:
        db.query(Run).filter(Run.status.in_([RunStatus.pending, RunStatus.running])).update(
            {"status": RunStatus.failed, "error": "The server restarted in the middle of the project. Press Run again."},
            synchronize_session=False,
        )
        db.commit()
    finally:
        db.close()


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    cfg = load_config(Path("config.yaml"))
    db_path = cfg.work_dir.expanduser() / "clipsmith.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path)
    _fail_interrupted_runs()
    app.state.active_run_id = None  # int | None; cleared by worker on completion
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except ImportError:
        pass
    from campaign.live import start as start_live  # Bevaka live: liveklipp när chatten exploderar
    from campaign.schedule import start as start_schedule  # schemalagda uppladdningar
    from campaign.scout import start as start_scout  # kampanjbevakningen, var 10:e minut

    start_scout()
    start_schedule()
    start_live(app)
    yield


_TAGS: list[dict] = [
    {
        "name": "runs",
        "description": "Pipeline run lifecycle — create, list, and inspect VOD processing runs.",
    },
    {
        "name": "clips",
        "description": "Clip review — list clips produced by a run and approve/reject them.",
    },
    {
        "name": "analytics",
        "description": (
            "Signal and prompt analytics — compare which signals drive approvals "
            "and which prompt version performs best."
        ),
    },
    {
        "name": "stream",
        "description": "Server-Sent Events stream of live pipeline progress for a run.",
    },
    {
        "name": "publish",
        "description": "Publish approved clips to YouTube Shorts.",
    },
    {
        "name": "files",
        "description": "Download the raw MP4 file for a clip.",
    },
    {
        "name": "system",
        "description": "Health check and run-count metrics.",
    },
]

app = FastAPI(
    title="clipsmith API",
    description=(
        "REST API for the **clipsmith** AI clip pipeline.\n\n"
        "clipsmith converts Twitch VODs into vertical 9:16 short-form clips using a "
        "6-stage pipeline (download → transcribe → chat → candidates → LLM selection → render). "
        "This API drives the Next.js dashboard and exposes analytics for the ML feedback loop.\n\n"
        "## Authentication\n"
        "Mutating endpoints (`POST /runs`, `PATCH /clips`, `POST /clips/{id}/publish`, "
        "`POST /analytics/runs/{id}/calibrate`) require an `X-Api-Key` header when "
        "`CLIPSMITH_API_KEY` is set in the server environment. Read endpoints are open.\n\n"
        "Every request must use a local Host (`localhost`, `127.0.0.1`, `[::1]`, or a name in "
        "`ALLOWED_HOSTS`), and a write with an `Origin` header must come from `CORS_ORIGINS`.\n\n"
        "## Quick start\n"
        "```bash\n"
        "# Start a pipeline run\n"
        "curl -X POST http://localhost:8000/runs \\\n"
        '  -H "X-Api-Key: $CLIPSMITH_API_KEY" \\\n'
        '  -H "Content-Type: application/json" \\\n'
        '  -d \'{"vod_id":"2341234567","channel":"xqc","prompt_version":"v1"}\'\n'
        "```"
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=_TAGS,
    contact={"name": "Ricardo García", "url": "https://github.com/ricardogr07/clipsmith"},
    license_info={"name": "MIT"},
    lifespan=_lifespan,
)

_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Api-Key"],
)

app.include_router(runs.router)
app.include_router(clips.router)
app.include_router(stream.router)
app.include_router(files.router)
app.include_router(health.router)
app.include_router(publish.router)
app.include_router(analytics.router)

# Robins kampanjlager (campaign/): kampanjval, kö och klipplogg i webbappen.
from campaign.api import router as campaign_router
from campaign.editor_api import router as editor_router
from campaign.keys import LOCAL_HOSTS, host_name
from campaign.keys import router as keys_router
from campaign.live_api import router as live_router
from campaign.profiles_api import router as profiles_router
from campaign.schedule_api import router as schedule_router
from campaign.scout_api import router as scout_router
from campaign.tiktok_api import router as tiktok_router
from campaign.timeline_api import router as timeline_router
from campaign.youtube_api import router as youtube_router

app.include_router(campaign_router)
app.include_router(tiktok_router)
app.include_router(youtube_router)
app.include_router(scout_router)
app.include_router(profiles_router)
app.include_router(editor_router)
app.include_router(keys_router)
app.include_router(schedule_router)
app.include_router(live_router)
app.include_router(timeline_router)

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@app.middleware("http")
async def _local_guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """The app runs on your own computer, so no other web page may drive it.

    Host must be a local name (or one in ALLOWED_HOSTS, e.g. ``api`` in Docker): this stops DNS rebinding,
    where a web page's own name suddenly points at 127.0.0.1. A write that carries an Origin must come from
    the dashboard (CORS_ORIGINS): this stops a form or fetch on another page. Tools without Origin (curl) work.
    Starlette's TrustedHostMiddleware is not used: it cannot read ``[::1]:8000``.
    """
    extra = {host_name(h) for h in os.getenv("ALLOWED_HOSTS", "").split(",") if h.strip()}
    if host_name(request.headers.get("host", "")) not in LOCAL_HOSTS | extra:
        return PlainTextResponse("Forbidden: this server only answers requests to localhost.", status_code=403)
    origin = request.headers.get("origin")
    if request.method not in _SAFE_METHODS and origin is not None and origin not in _cors_origins:
        return PlainTextResponse("Forbidden: this origin may not change anything here.", status_code=403)
    return await call_next(request)


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse("/docs")
