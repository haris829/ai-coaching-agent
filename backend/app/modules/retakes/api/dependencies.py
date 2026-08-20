"""Request-scoped access to UC-08's services.

UC-08 shipped with the learner in the path — ``/learners/{id}/quizzes/{q}/…`` — because it had no
identity layer to consult, and with an ``ensure_learner_scope`` helper to check the path against
an authenticated header. Neither survives here. The merged application has one authentication
seam, so the learner comes from the bearer token exactly as it does for UC-03, UC-04, UC-05,
UC-06 and UC-07, and the path segment that could disagree with it is simply gone.

That is a removal of a duplicate check, not of a guarantee. The ownership rule is unchanged and
still enforced where it always was — in the services, which re-read every retake and every attempt
scoped to the learner they resolved, because "a token resolved" and "this retake is theirs" are
different claims.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request

from app.core.deps import DbSession
from app.modules.identity.security import Actor, LearnerIdentity
from app.modules.retakes.container import Container, RetakeAppContext


def get_retake_app_context(request: Request) -> RetakeAppContext:
    context = getattr(request.app.state, "retakes", None)
    if context is None:  # pragma: no cover - a wiring mistake, not a runtime condition
        raise RuntimeError("The application was created without a RetakeAppContext.")
    return context


def get_retake_container(request: Request, db: DbSession) -> Iterator[Container]:
    """UC-08's services, bound to this request's session."""
    yield get_retake_app_context(request).build(db)


RetakeCtx = Annotated[Container, Depends(get_retake_container)]

#: The learner every retake operation is scoped to, resolved from the bearer token.
RetakeLearner = LearnerIdentity

#: The administrator a grant is attributed to. Resolved by the same guard UC-02's admin endpoints
#: use, so there is one answer to "who is allowed to do this?" in the application.
RetakeAdmin = Actor
