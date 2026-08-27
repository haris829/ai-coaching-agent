"""FastAPI dependencies (DI without a framework).

Two rules are enforced here:

* the learner identity comes from the ``CurrentUserProvider`` only;
* the endpoints accept no input at all - no path parameter, no query parameter,
  no body - so a ``user_id`` can never be supplied by a caller.
"""

from __future__ import annotations

from fastapi import Depends, Request

from uc07.api.errors import UnknownRequestFields
from uc07.application.service import GapReportService
from uc07.composition import Container


def get_container(request: Request) -> Container:
    container = getattr(request.app.state, "container", None)
    if container is None:  # pragma: no cover - app is always built with a container
        raise RuntimeError("application container is not configured")
    return container


def get_service(container: Container = Depends(get_container)) -> GapReportService:
    return container.service


async def reject_request_input(request: Request) -> None:
    """Reject unknown request fields, including any attempt to pass a user id."""
    rejected = sorted(set(request.query_params.keys()))
    body = await request.body()
    if body:
        rejected.append("body")
    if rejected:
        raise UnknownRequestFields(rejected)


def get_current_user(
    request: Request, container: Container = Depends(get_container)
) -> str:
    """Resolve the learner server-side. Raises ``IdentityUnresolved`` if absent."""
    return container.current_user.resolve(request)
