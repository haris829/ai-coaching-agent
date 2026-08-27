"""Run the development server: ``python -m uc01``."""

from __future__ import annotations

import uvicorn

from .config import load_settings


def main() -> None:
    settings = load_settings()
    uvicorn.run(
        "uc01.api.asgi:app",
        host=settings.host,
        port=settings.port,
        reload=settings.dev_mode,
        log_config=None,
    )


if __name__ == "__main__":
    main()
