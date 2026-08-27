"""Development entry point: ``python -m uvicorn uc04.main:app``."""

from __future__ import annotations

from .api.app import create_app

app = create_app()
