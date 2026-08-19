"""Composition root.

Wiring lives here and nowhere else, which is what keeps the UC-01/UC-02 boundaries
swappable: services depend on the port protocols, and the decision about which
adapter satisfies them is made once, at this single point.

UC-01 and UC-02 are now merged in-process; :class:`Ports.merged` is the single place that
says so. Moving either behind HTTP later changes this one method and nothing else.

Two scopes exist:

* :class:`AppContext` — process-wide: settings, engine, session factory, clock and the
  port *factories*. Built once at startup.
* :class:`RequestContext` — per request (or per unit of work): a session and the
  repositories and services bound to it. SQLAlchemy sessions are not thread-safe, so
  each request gets its own.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.time import Clock, SystemClock
from app.db.session import (
    SessionLocal as default_session_factory,
)
from app.db.session import (
    create_schema,
    create_session_factory,
)
from app.db.session import (
    engine as default_engine,
)
from app.modules.attempt_delivery.integration.enrolment.platform_adapter import (
    PlatformEnrolmentAdapter,
)
from app.modules.attempt_delivery.integration.enrolment.port import EnrolmentPort
from app.modules.attempt_delivery.integration.submission_dispatch.port import (
    NoopSubmissionDispatch,
    SubmissionDispatchPort,
)
from app.modules.attempt_delivery.integration.uc01.configuration_adapter import (
    Uc01ConfigurationAdapter,
)
from app.modules.attempt_delivery.integration.uc01.port import QuizConfigurationPort
from app.modules.attempt_delivery.integration.uc02.port import QuestionBankPort
from app.modules.attempt_delivery.integration.uc02.question_bank_adapter import (
    Uc02QuestionBankAdapter,
)
from app.modules.attempt_delivery.repositories.answer_repository import AnswerRepository
from app.modules.attempt_delivery.repositories.attempt_question_repository import (
    AttemptQuestionRepository,
)
from app.modules.attempt_delivery.repositories.attempt_repository import AttemptRepository
from app.modules.attempt_delivery.repositories.flag_repository import FlagRepository
from app.modules.attempt_delivery.repositories.submission_repository import SubmissionRepository
from app.modules.attempt_delivery.services.answer_service import AnswerService
from app.modules.attempt_delivery.services.attempt_access_service import AttemptAccessService
from app.modules.attempt_delivery.services.attempt_service import AttemptService
from app.modules.attempt_delivery.services.flag_service import FlagService
from app.modules.attempt_delivery.services.question_selection_service import (
    QuestionSelectionService,
)
from app.modules.attempt_delivery.services.submission_service import SubmissionService
from app.modules.attempt_delivery.services.timing_service import TimingService

#: A port factory takes the request's session and returns an adapter bound to it.
#: Adapters that talk to a remote UC-01/UC-02 will simply ignore the session.
PortFactory = Callable[[Session], object]


@dataclass(frozen=True, slots=True)
class Ports:
    """The four boundaries UC-03 depends on, as per-session factories."""

    configurations: Callable[[Session], QuizConfigurationPort]
    question_bank: Callable[[Session], QuestionBankPort]
    enrolments: Callable[[Session], EnrolmentPort]
    #: The dispatcher has no session of its own; it is a remote call.
    dispatcher: SubmissionDispatchPort

    @classmethod
    def merged(cls, dispatcher: SubmissionDispatchPort | None = None) -> Ports:
        """The real adapters: UC-01 and UC-02 in-process, enrolment from the platform table.

        This replaced ``Ports.local()``, which wired provisional adapters over ``ext_*`` projections
        while UC-01 and UC-02 were separate workspaces. Those tables and adapters are gone; the
        ports they existed to satisfy are unchanged, which is the whole point of having had them.
        """
        return cls(
            configurations=Uc01ConfigurationAdapter,
            question_bank=Uc02QuestionBankAdapter,
            enrolments=PlatformEnrolmentAdapter,
            dispatcher=dispatcher or NoopSubmissionDispatch(),
        )


@dataclass(slots=True)
class RequestContext:
    """Repositories and services bound to one session."""

    settings: Settings
    session: Session
    configurations: QuizConfigurationPort
    question_bank: QuestionBankPort
    enrolments: EnrolmentPort
    attempts_repo: AttemptRepository
    attempt_questions_repo: AttemptQuestionRepository
    answers_repo: AnswerRepository
    flags_repo: FlagRepository
    submissions_repo: SubmissionRepository
    timing: TimingService
    selection: QuestionSelectionService
    submissions: SubmissionService
    access: AttemptAccessService
    attempts: AttemptService
    answers: AnswerService
    flags: FlagService

    # Convenience handles for seeding. Present only when the ports are test doubles that expose
    # seeding helpers; asking for one against a real adapter is a programming error, not a silent
    # no-op, which is why these raise.
    @property
    def seedable_configurations(self) -> Any:
        return _seedable(self.configurations, "publish_version")

    @property
    def seedable_question_bank(self) -> Any:
        return _seedable(self.question_bank, "upsert_question")

    @property
    def seedable_enrolments(self) -> Any:
        return _seedable(self.enrolments, "upsert_enrolment")


def _seedable(port: object, method: str) -> Any:
    if not hasattr(port, method):
        raise TypeError(
            f"{type(port).__name__} has no seeding helper {method!r}; "
            f"it is a real adapter, so seed "
            "through UC-01/UC-02's own APIs instead."
        )
    return port


class AppContext:
    """Process-wide dependencies."""

    __slots__ = ("settings", "engine", "session_factory", "clock", "ports")

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        engine: Engine | None = None,
        session_factory: sessionmaker[Session] | None = None,
        clock: Clock | None = None,
        ports: Ports | None = None,
    ) -> None:
        self.settings = settings or default_settings
        # Default to the application's shared engine so every module uses one connection pool.
        # Passing an engine gives a test its own isolated database.
        self.engine = engine or default_engine
        self.session_factory = session_factory or (
            default_session_factory if engine is None else create_session_factory(self.engine)
        )
        self.clock = clock or SystemClock()
        self.ports = ports or Ports.merged()

    def create_schema(self) -> None:
        """Create the schema directly from the models (tests and local bootstrap)."""
        create_schema(self.engine)

    def build(self, session: Session) -> RequestContext:
        """Assemble the services for one session.

        The wiring order reflects the dependency graph: timing has no dependencies;
        the submission service settles expiry; the access layer sits between requests
        and the repositories, using the submission service; and the remaining services
        gate every attempt read/write through the access layer.
        """
        configurations = self.ports.configurations(session)
        question_bank = self.ports.question_bank(session)
        enrolments = self.ports.enrolments(session)

        attempts_repo = AttemptRepository(session)
        attempt_questions_repo = AttemptQuestionRepository(session)
        answers_repo = AnswerRepository(session)
        flags_repo = FlagRepository(session)
        submissions_repo = SubmissionRepository(session)

        timing = TimingService(self.clock, self.settings)
        selection = QuestionSelectionService()

        submissions = SubmissionService(
            session=session,
            attempts=attempts_repo,
            attempt_questions=attempt_questions_repo,
            answers=answers_repo,
            flags=flags_repo,
            submissions=submissions_repo,
            dispatcher=self.ports.dispatcher,
            timing=timing,
            clock=self.clock,
        )

        access = AttemptAccessService(
            attempts=attempts_repo, submissions=submissions, timing=timing
        )

        attempts = AttemptService(
            session=session,
            attempts=attempts_repo,
            attempt_questions=attempt_questions_repo,
            configurations=configurations,
            question_bank=question_bank,
            enrolments=enrolments,
            selection=selection,
            access=access,
            submissions=submissions,
            timing=timing,
            clock=self.clock,
        )

        answers = AnswerService(
            session=session,
            attempts=attempts_repo,
            attempt_questions=attempt_questions_repo,
            answers=answers_repo,
            access=access,
            timing=timing,
            clock=self.clock,
        )

        flags = FlagService(
            session=session,
            attempts=attempts_repo,
            attempt_questions=attempt_questions_repo,
            flags=flags_repo,
            access=access,
            timing=timing,
            clock=self.clock,
        )

        return RequestContext(
            settings=self.settings,
            session=session,
            configurations=configurations,
            question_bank=question_bank,
            enrolments=enrolments,
            attempts_repo=attempts_repo,
            attempt_questions_repo=attempt_questions_repo,
            answers_repo=answers_repo,
            flags_repo=flags_repo,
            submissions_repo=submissions_repo,
            timing=timing,
            selection=selection,
            submissions=submissions,
            access=access,
            attempts=attempts,
            answers=answers,
            flags=flags,
        )

    @contextmanager
    def unit_of_work(self) -> Iterator[RequestContext]:
        """A standalone unit of work, for seeding, scripts and tests."""
        session = self.session_factory()
        try:
            yield self.build(session)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()
