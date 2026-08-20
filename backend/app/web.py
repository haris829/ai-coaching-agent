"""Serving the built frontend from the API process.

WHY THIS EXISTS
---------------
Locally the browser only ever makes same-origin ``/api/...`` requests, because Vite's dev server
proxies them. A deployment has to reproduce that, and there are two ways:

1. Two services and a CORS allow-list naming the frontend's generated domain.
2. One service that answers ``/api/...`` itself and serves the built assets for everything else.

The second is what this module does, and it is the better trade for this system. CORS stops being
part of the deployment at all — not "configured correctly", *absent* — which removes a class of
misconfiguration whose failure mode is a browser-only error nobody sees in a server log. It is also
one service to deploy, one URL to hand a reviewer, and one origin for a cookie or a bearer token to
be scoped to if the company's identity provider later wants either.

Nothing here is required: with no built frontend present the API serves only ``/api``, exactly as it
does today.

THE SPA FALLBACK, AND WHAT IT MUST NOT SWALLOW
---------------------------------------------
The UI uses client-side routing, so ``GET /attempt`` is a real URL a reviewer can bookmark and
reload, but not a file on disk. It has to return ``index.html`` and let the router sort it out.

That single requirement is where this kind of catch-all usually goes wrong: a fallback that answers
*everything* with ``index.html`` turns a mistyped API path into an HTML page and a ``200``. A client
then parses ``<!doctype html>`` as JSON and reports something unrelated to what actually happened.
So ``/api`` — and the docs and OpenAPI paths under it — are excluded explicitly, and a request for a
missing *asset* (anything that looks like a file) gets a real 404 rather than the page.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Where a build lands when nobody says otherwise: ``frontend/dist`` beside the backend directory.
#: Matches ``vite.config.ts``'s ``build.outDir``, so ``npm run build:web`` is enough locally.
DEFAULT_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def resolve_dist() -> Path | None:
    """The directory of built assets to serve, or ``None`` when there is nothing to serve.

    An explicitly configured directory that does not exist is a **configuration error**, not a
    reason to quietly serve an API-only application: someone who set ``FRONTEND_DIST`` meant a UI,
    and a deployment that silently starts without one produces a blank page and no explanation. It
    is logged as an error and skipped, so the API still comes up — a reviewer with a broken UI and a
    working ``/api/docs`` is better off than one with neither.
    """
    if settings.frontend_dist:
        configured = Path(settings.frontend_dist).expanduser()
        if (configured / "index.html").is_file():
            return configured
        logger.error(
            "web.frontend_dist_missing",
            extra={"configured": str(configured)},
        )
        return None

    if (DEFAULT_DIST / "index.html").is_file():
        return DEFAULT_DIST
    return None


def mount_frontend(app: FastAPI, dist: Path) -> None:
    """Serve ``dist`` at ``/``, with a fallback for the SPA's client-side routes."""
    index = dist / "index.html"

    # Vite emits hashed filenames under assets/, so these are safe to cache hard. index.html is
    # not: it names the current hashes, and a cached copy would keep pointing at assets that a
    # redeploy has already replaced.
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def index_page() -> FileResponse:
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    # ``response_model=None`` because the return type is a union of two response classes, and
    # FastAPI otherwise tries to derive a Pydantic response model from it and refuses at start-up.
    # There is no schema to derive here: the route returns a file or an error body, never a model.
    @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
    def spa_fallback(full_path: str, request: Request) -> FileResponse | JSONResponse:
        # An /api path that reached here does not exist. Answering it with the SPA shell would give
        # a client HTML with a 200 where it expected JSON with a 404 — the error would be reported
        # as a parse failure somewhere unrelated to the actual mistake.
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content={
                    "error": {
                        "code": "NOT_FOUND",
                        "message": f"No API route matches /{full_path}.",
                        "retryable": False,
                    }
                },
            )

        # A real file that exists — favicon, robots.txt, a logo — is served as itself.
        candidate = (dist / full_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)

        # A request that looks like an asset and is not there is a 404, not the shell. Returning
        # index.html for a missing .js would be served as HTML and fail to parse in the browser,
        # which reads as a mysterious runtime error rather than a missing file.
        if "." in Path(full_path).name:
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "NOT_FOUND", "message": "Not found."}},
            )

        # Everything else is a client-side route: /attempt, /analytics, /reports/abc.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    logger.info("web.frontend_mounted", extra={"dist": str(dist)})
