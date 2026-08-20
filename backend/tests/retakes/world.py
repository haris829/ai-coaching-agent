"""Building a merged application around UC-08's test container.

Standalone, UC-08's ``create_app`` took a :class:`Container` directly, and authentication was a
seam that read ``X-Learner-Id``. Neither is true here, and this module is where the difference is
absorbed so the suite itself stays about retakes:

**The container.** The merged factory takes a :class:`RetakeAppContext`, which normally builds a
container per request from a database session. :class:`FixedContainerContext` returns the test's
container instead, whatever session it is handed. That keeps the fakes in play — these tests are
about UC-08's own rules, and the real adapters are covered in ``tests/integration/``.

**The identity.** The merged application resolves a learner from a bearer token against
``qc_users``. A UC-08 test has no users table and should not need one to assert a retake rule, so
the learner-identity dependency is overridden with a decoder that reads the learner id straight
out of the token. That is a *test* seam, not a second authentication system: the endpoints still
go through ``require_learner_id``, still reject a request with no credential, and the production
resolver is untouched. What is asserted stays true — a learner sees only their own retakes —
because the services re-check ownership against whatever id is resolved.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, Header
from sqlalchemy.orm import Session

from app.core.errors import UnauthorizedError
from app.main import create_app
from app.modules.identity.security import require_learner_id
from app.modules.retakes.container import Container, RetakeAppContext

#: Prefix that makes a test token self-describing: ``Bearer learner:learner-alice``.
TOKEN_PREFIX = "learner:"


class SequentialIdGenerator:
    """Deterministic ids for tests: ``uc08-0001``, ``uc08-0002``, …

    Kept in the test package rather than imported from the module: an id generator that exists to
    make assertions readable belongs with the assertions.
    """

    def __init__(self, prefix: str = "id") -> None:
        self._prefix = prefix
        self._counter = 0

    def __call__(self) -> str:
        self._counter += 1
        return f"{self._prefix}-{self._counter:04d}"


class FixedContainerContext(RetakeAppContext):
    """A :class:`RetakeAppContext` that hands out one already-built container."""

    def __init__(self, container: Container) -> None:  # noqa: D107 - see class docstring
        # Deliberately not calling super().__init__: every dependency it would resolve is already
        # resolved inside the container the test built, and constructing the real ports would
        # bind the database adapters this suite exists to avoid.
        self._container = container

    def build(self, session: Session) -> Container:  # noqa: ARG002 - session is unused by design
        return self._container


def learner_auth_headers(learner_id: str) -> dict[str, str]:
    """Credentials for one learner, in the merged application's scheme."""
    return {"Authorization": f"Bearer {TOKEN_PREFIX}{learner_id}"}


def _learner_from_token(authorization: Annotated[str | None, Header()] = None) -> str:
    """Decode ``Bearer learner:<id>``, refusing anything else.

    Raises the application's own :class:`UnauthorizedError` rather than an ``HTTPException`` so an
    unauthenticated request produces the standard error envelope the API tests assert on.
    """
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not token.startswith(TOKEN_PREFIX) or not token[len(TOKEN_PREFIX) :]:
        raise UnauthorizedError()
    return token[len(TOKEN_PREFIX) :]


def build_retake_app(container: Container) -> FastAPI:
    """The merged application, with UC-08 served by ``container`` and a test identity decoder."""
    app = create_app(retake_context=FixedContainerContext(container))
    # Overridden through FastAPI's own mechanism, so the dependency graph the endpoints declare —
    # and therefore the refusal when no credential is supplied — is the real one.
    app.dependency_overrides[require_learner_id] = _learner_from_token
    return app
