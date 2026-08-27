"""FastAPI application factory for the standalone UC-01 project."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import Settings, load_settings
from ..contracts.repository import SessionRepository
from ..logging_setup import configure_logging
from .container import AppContainer
from .errors import register_error_handlers
from .routes import router

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

DESCRIPTION = """
Standalone implementation of **UC-01 — Coaching Session Initiation**.

This project is independent: it assumes no company repository, database, auth system or
API exists. All four external dependencies (NARIC, Courses Agent, Case Prep / Case
Files, Profile) sit behind internal contracts with **development mock adapters**.

* Architecture and rationale: `docs/DESIGN.md`
* Replacing a mock with a real integration: `docs/ADAPTER_REPLACEMENT.md`

Authentication in this standalone project is a development stand-in: send
`Authorization: Bearer dev-alice` (or `dev-bob`, `dev-carol`).
""".strip()


def create_app(
    settings: Settings | None = None,
    *,
    repository: SessionRepository | None = None,
    configure_logs: bool = True,
) -> FastAPI:
    """Build the application.

    ``settings`` and ``repository`` are injectable so tests can run against an isolated
    database and arbitrary adapter scenarios without touching the environment.
    """
    resolved = settings or load_settings()
    if configure_logs:
        configure_logging(resolved.log_level, resolved.log_format)

    container = AppContainer(resolved, repository=repository)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info(
            "startup",
            extra={
                "uc01": {
                    "environment": resolved.environment,
                    "adapters": dict(resolved.describe_adapters()),
                    "dev_mode": resolved.dev_mode,
                }
            },
        )
        try:
            yield
        finally:
            container.close()
            logger.info("shutdown")

    app = FastAPI(
        title="UC-01 Coaching Session Initiation",
        version="1.0.0",
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.state.container = container
    register_error_handlers(app)
    app.include_router(router)

    if resolved.serve_frontend and WEB_DIR.exists():
        app.mount(
            "/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static"
        )

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(str(WEB_DIR / "index.html"))

    @app.get("/healthz", include_in_schema=False)
    def root_health() -> JSONResponse:
        """Convenience alias for infrastructure probes."""
        return JSONResponse({"status": "ok", "use_case": "UC-01"})

    return app


__all__ = ["WEB_DIR", "create_app"]
