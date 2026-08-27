"""FastAPI application assembly."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from ..application.logging_config import configure_logging, log_event
from ..composition import get_container, verify_configuration
from ..config import Settings, load_settings
from ..domain.errors import UC05Error
from .errors import (
    uc05_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from .routes import router


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or load_settings()
    configure_logging(resolved.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Fail fast: a provider key with no implementation stops the service
        # here, at boot, rather than surfacing as a 500 on a learner's first
        # question -- and never as a silent fallback to a mock.
        verify_configuration(resolved)
        container = get_container()
        log_event("service.started", count=len(container.describe()))
        yield

    app = FastAPI(
        title="UC-05 Socratic Method Coaching",
        version="1.0.0",
        description=(
            "Guiding-question coaching with an explicit, persisted dialogue "
            "state machine. Exposes the state a frontend needs; builds no UI."
        ),
        lifespan=lifespan,
    )

    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(UC05Error, uc05_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
    app.include_router(router)
    return app


app = create_app()
