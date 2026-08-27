"""Application factory."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError

from uc10.api.deps import Container, build_container
from uc10.api.errors import http_exception_handler, validation_exception_handler
from uc10.api.routes import router
from uc10.config import Settings, get_settings
from uc10.logging_setup import configure_logging, get_logger

log = get_logger("uc10.app")


def create_app(
    *, settings: Settings | None = None, container: Container | None = None
) -> FastAPI:
    """Build the ASGI app.

    Provider selection happens here, once, through the registry.  A configured provider
    with no registered implementation raises before the app is returned -- the service
    refuses to start rather than quietly running on mock data.
    """
    settings = settings or (container.settings if container else get_settings())
    configure_logging(settings.log_level)
    container = container or build_container(settings=settings)

    app = FastAPI(
        title="UC-10 Feedback & Improvement",
        version="0.1.0",
        summary="Rating capture and content review flagging.",
    )
    app.state.container = container
    app.include_router(router)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    log.info(
        "component_started",
        interaction_provider=settings.interaction_provider,
        interaction_adapter=type(container.interactions).__name__,
        flag_window_days=settings.flag_window_days,
        historical_rating_window_hours=settings.historical_rating_window_hours,
        dev_session_minting=settings.allow_dev_session_minting,
    )
    return app


app = create_app  # explicit factory; uvicorn: uvicorn uc10.api.app:create_app --factory
