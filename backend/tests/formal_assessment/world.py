"""Building a merged application around UC-09's test container.

The same two adaptations UC-08's suite needed, for the same two reasons.

**The container.** The merged factory takes a :class:`FormalAssessmentAppContext`, which normally
builds a container per request from a database session. :class:`FixedContainerContext` returns the
test's container instead, whatever session it is handed, which keeps the fakes in play — these
tests are about UC-09's own rules, and the real adapters are covered by
``tests/integration/test_formal_assessment_chain.py``.

**The identities.** The merged application resolves a learner, an assessor and a system caller
from credentials against ``qc_users``. A UC-09 test has no users table and should not need one to
assert that a second device is refused, so the three identity dependencies are overridden with
decoders that read the identifier straight out of the token. That is a *test* seam, not a second
authentication system: the endpoints still declare the real dependencies, an unauthenticated
request is still refused, and the production resolvers are untouched.

What is being asserted stays true. A learner sees only their own formal attempt because the
services re-read it scoped to the resolved learner; an assessor may review only what the assessor
directory authorises, which is a separate check UC-09 makes on every operation and which no token
can satisfy.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, Header
from sqlalchemy.orm import Session

from app.core.errors import UnauthorizedError
from app.main import create_app
from app.modules.formal_assessment.container import Container, FormalAssessmentAppContext
from app.modules.identity.security import (
    require_assessor_id,
    require_learner_id,
    require_system_actor,
)

#: Prefixes that make a test token self-describing: ``Bearer learner:learner-1``.
LEARNER_PREFIX = "learner:"
ASSESSOR_PREFIX = "assessor:"
SYSTEM_PREFIX = "system:"


class FixedContainerContext(FormalAssessmentAppContext):
    """A :class:`FormalAssessmentAppContext` that hands out one already-built container."""

    def __init__(self, container: Container) -> None:  # noqa: D107 - see class docstring
        # Deliberately not calling super().__init__: every dependency it would resolve is already
        # resolved inside the container the test built, and constructing the real ports would bind
        # the database adapters this suite exists to avoid.
        self._container = container

    def build(self, session: Session) -> Container:  # noqa: ARG002 - unused by design
        return self._container


def learner_auth_headers(learner_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {LEARNER_PREFIX}{learner_id}"}


def assessor_auth_headers(assessor_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {ASSESSOR_PREFIX}{assessor_id}"}


def system_auth_headers(actor: str = "session-monitor") -> dict[str, str]:
    return {"Authorization": f"Bearer {SYSTEM_PREFIX}{actor}"}


def _decode(authorization: str | None, prefix: str) -> str:
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not token.startswith(prefix) or not token[len(prefix) :]:
        raise UnauthorizedError()
    return token[len(prefix) :]


def _learner_from_token(authorization: Annotated[str | None, Header()] = None) -> str:
    return _decode(authorization, LEARNER_PREFIX)


def _assessor_from_token(authorization: Annotated[str | None, Header()] = None) -> str:
    return _decode(authorization, ASSESSOR_PREFIX)


def _system_from_token(authorization: Annotated[str | None, Header()] = None) -> str:
    return _decode(authorization, SYSTEM_PREFIX)


def build_formal_app(container: Container) -> FastAPI:
    """The merged application, with UC-09 served by ``container`` and test identity decoders."""
    app = create_app(formal_context=FixedContainerContext(container))
    # Overridden through FastAPI's own mechanism, so the dependency graph the endpoints declare —
    # and therefore the refusal when the wrong kind of credential is presented — is the real one.
    app.dependency_overrides[require_learner_id] = _learner_from_token
    app.dependency_overrides[require_assessor_id] = _assessor_from_token
    app.dependency_overrides[require_system_actor] = _system_from_token
    return app
