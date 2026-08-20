"""UC-10's composition root, and the process-wide half of it.

The counterpart of ``ResultsAppContext``, ``CoachingAppContext``, ``RetakeAppContext`` and
``FormalAssessmentAppContext``. It holds what outlives a request — the analytics settings and the
clock — and builds the services per session on top of them.

**Why the settings are here rather than in ``app.core.config``.** UC-10's ``AnalyticsSettings`` is
not start-up configuration in the way the rest of the application's is: it is a *tunable* that an
administrator validates candidate values against through ``POST /config/validate``, and whose
dangerous values require explicit confirmation before they can be applied. That validation logic —
"a 0% threshold would flag every question" — is the requirement, so the object it validates stays
its own thing. The application seeds its defaults from the environment once, here.

**Both repositories read through a session, so both are per-request.** The assessment side is
read-only over other capabilities' rows; the review side owns UC-10's two tables. They are separate
classes on purpose: the read-only one has no mutating method to bind, which is what makes "analytics
cannot change an attempt" a property of the code rather than a rule someone has to remember.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.time import Clock, SystemClock
from app.modules.analytics.api.deps import ServiceContainer, build_container
from app.modules.analytics.config import AnalyticsSettings
from app.modules.analytics.integration.assessment_repository import (
    SqlAlchemyAnalyticsRepository,
)
from app.modules.analytics.repositories.base import AnalyticsRepository, ReviewRepository
from app.modules.analytics.repositories.sqlalchemy_review import SqlAlchemyReviewRepository


def analytics_settings_from(settings: Settings) -> AnalyticsSettings:
    """Seed UC-10's tunables from the application's environment configuration.

    Only the values a deployment genuinely sets per environment are threaded through. The rest keep
    UC-10's own defaults, which were chosen against its specification and are not the kind of thing
    an operator should have to restate.
    """
    return AnalyticsSettings(
        flag_wrong_answer_rate_threshold=settings.analytics_flag_threshold,
        flag_min_responses=settings.analytics_flag_min_responses,
        query_timeout_seconds=settings.analytics_query_timeout_seconds,
        repository_page_size=settings.analytics_page_size,
        max_scanned_records=settings.analytics_max_scanned_records,
        export_max_rows=settings.analytics_export_max_rows,
        # Set by the deployment that means it, and reported in the validation payload either way.
        allow_dangerous_configuration=settings.analytics_allow_dangerous_configuration,
    )


@dataclass(frozen=True, slots=True)
class AnalyticsPorts:
    """The two repositories, as per-session factories."""

    assessment: Callable[[Session], AnalyticsRepository]
    review: Callable[[Session], ReviewRepository]

    @classmethod
    def merged(cls) -> AnalyticsPorts:
        """The real bindings: the assessment projection, and UC-10's own ``qy_`` tables.

        This one call is the whole of the integration. Pointing analytics at a read replica, or at
        a warehouse, is a change to the line that names the assessment repository — and nothing
        else, because no service and no route knows which one it was given.
        """
        return cls(
            assessment=SqlAlchemyAnalyticsRepository,
            review=SqlAlchemyReviewRepository,
        )


class AnalyticsAppContext:
    """Process-wide dependencies for UC-10, and the factory for one request's services."""

    __slots__ = ("session_factory", "settings", "analytics_settings", "clock", "ports")

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        settings: Settings | None = None,
        analytics_settings: AnalyticsSettings | None = None,
        clock: Clock | None = None,
        ports: AnalyticsPorts | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings or default_settings
        self.analytics_settings = analytics_settings or analytics_settings_from(self.settings)
        self.clock = clock or SystemClock()
        self.ports = ports or AnalyticsPorts.merged()

    def build(self, session: Session) -> ServiceContainer:
        """Assemble UC-10's services for one session."""
        return build_container(
            analytics_repository=self.ports.assessment(session),
            review_repository=self.ports.review(session),
            settings=self.analytics_settings,
            clock=self.clock,
        )

    @contextmanager
    def unit_of_work(self) -> Iterator[ServiceContainer]:
        """A standalone unit of work, for scripts and tests."""
        session = self.session_factory()
        try:
            yield self.build(session)
        finally:
            session.close()
