"""Request-scoped access to UC-07's services.

Two dependencies, and the split between them is the point.

:data:`CoachingCtx` builds the coaching services against **this request's** database session, so the
adapters onto UC-03, UC-04, UC-06 and the ``qk_`` tables all read and write through one session —
the same contract ``app.composition.ResultsCtx`` gives UC-04/05/06. The process-wide half (the
clock, the id generator, the sanitiser, the bound AI provider) comes from ``app.state.coaching``,
built once at start-up.

:data:`CoachingLearner` resolves *who is asking*. Every coaching operation is scoped to a learner,
and that learner comes from the bearer token — never from the URL. UC-07 shipped with the learner id
in its paths because it had no identity layer of its own to consult; this application has exactly
one (``app.modules.identity``), and routing round it would mean two places that decide who a caller
is.

The ownership check has not moved, though. ``CoachingAuthorizer`` still re-derives, on every single
operation, that the attempt and the session belong to the learner it was handed — because "the
framework resolved a token" and "this attempt is theirs" are different claims, and §9 is about the
second one.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request

from app.core.deps import DbSession
from app.modules.coaching.container import CoachingAppContext, Container
from app.modules.identity.security import LearnerIdentity


def get_coaching_app_context(request: Request) -> CoachingAppContext:
    context = getattr(request.app.state, "coaching", None)
    if context is None:  # pragma: no cover - a wiring mistake, not a runtime condition
        raise RuntimeError("The application was created without a CoachingAppContext.")
    return context


def get_coaching_container(request: Request, db: DbSession) -> Iterator[Container]:
    """UC-07's services, bound to this request's session."""
    yield get_coaching_app_context(request).build(db)


CoachingCtx = Annotated[Container, Depends(get_coaching_container)]

#: The learner every coaching operation is scoped to, resolved from the bearer token.
CoachingLearner = LearnerIdentity
