"""ASGI entrypoint.

    uvicorn uc01.api.asgi:app --reload
"""

from __future__ import annotations

from .app import create_app

app = create_app()

__all__ = ["app"]
