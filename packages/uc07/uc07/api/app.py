"""FastAPI application factory.

The composition root runs once here. Configuration problems (an unknown provider
name, a missing topic-description registry) raise at startup rather than
degrading silently at request time.
"""

from __future__ import annotations

from fastapi import FastAPI

from uc07 import ANALYSIS_VERSION
from uc07.api.errors import install_error_handlers
from uc07.api.routes import router
from uc07.composition import Container, build_container
from uc07.observability import configure_logging


def create_app(container: Container | None = None) -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="UC-07 Progress & Knowledge Gap Identification",
        version=ANALYSIS_VERSION,
        description=(
            "Read-only aggregator: derives a deterministic, evidence-backed "
            "knowledge-gap report from a learner's coaching history. It writes "
            "nothing upstream and persists only the report it generates."
        ),
    )
    app.state.container = container or build_container()
    install_error_handlers(app)
    app.include_router(router)
    return app


# Run with:  uvicorn "uc07.api.app:create_app" --factory
