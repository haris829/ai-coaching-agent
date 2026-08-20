"""Building a merged application around UC-10's in-memory repositories.

The same two adaptations UC-08's and UC-09's suites needed.

**The container.** The merged factory takes an :class:`AnalyticsAppContext`, which normally builds
the services per request from a database session. :class:`FixedContainerContext` returns the test's
already-built container instead, whatever session it is handed, which keeps the in-memory
repositories in play — these tests are about UC-10's calculations, and a calculation is best checked
against a dataset small enough to verify by hand. The real read-only projection over UC-03/UC-04's
rows is covered by ``tests/integration/test_analytics_chain.py``.

**The identity.** UC-10 authenticated with its own ``X-API-Key`` map. The merged application resolves
an administrator from a bearer token against ``qc_users``, and a UC-10 test has no users table and
should not need one to assert that a pass rate is 50%. So the administrator dependency is overridden
with a decoder that reads the identity out of the token.

That is a *test* seam, not a second authentication system: the endpoints still declare the real
dependency, an unauthenticated request is still refused, and the production guard is untouched.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, Header
from sqlalchemy.orm import Session

from app.core.errors import UnauthorizedError
from app.main import create_app
from app.modules.analytics.api.deps import ServiceContainer
from app.modules.analytics.container import AnalyticsAppContext
from app.modules.identity.security import require_admin

#: Prefix that makes a test token self-describing: ``Bearer admin:admin-1``.
ADMIN_PREFIX = "admin:"


class FixedContainerContext(AnalyticsAppContext):
    """An :class:`AnalyticsAppContext` that hands out one already-built container."""

    def __init__(self, container: ServiceContainer) -> None:  # noqa: D107 - see class docstring
        # Deliberately not calling super().__init__: every dependency it would resolve is already
        # resolved inside the container the test built, and constructing the real ports would bind
        # the database projection this suite exists to avoid.
        self._container = container

    def build(self, session: Session) -> ServiceContainer:  # noqa: ARG002 - unused by design
        return self._container


def admin_auth_headers(admin_id: str = "admin-1") -> dict[str, str]:
    """Credentials for one administrator, in the merged application's scheme."""
    return {"Authorization": f"Bearer {ADMIN_PREFIX}{admin_id}"}


def _admin_from_token(authorization: Annotated[str | None, Header()] = None) -> str:
    """Decode ``Bearer admin:<id>``, refusing anything else.

    Raises the application's own :class:`UnauthorizedError` so an unauthenticated request produces
    the standard error envelope the API tests assert on.
    """
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not token.startswith(ADMIN_PREFIX) or not token[len(ADMIN_PREFIX) :]:
        raise UnauthorizedError()
    return token[len(ADMIN_PREFIX) :]


def build_analytics_app(container: ServiceContainer) -> FastAPI:
    """The merged application, with UC-10 served by ``container`` and a test identity decoder."""
    app = create_app(analytics_context=FixedContainerContext(container))
    # Overridden through FastAPI's own mechanism, so the dependency graph the endpoints declare —
    # and therefore the refusal when no credential is supplied — is the real one.
    app.dependency_overrides[require_admin] = _admin_from_token
    return app
