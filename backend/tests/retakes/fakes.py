"""Test doubles for every boundary UC-08 depends on.

``FakeAttemptModule`` is the interesting one. It is a stand-in for UC-03 that actually *behaves*
like UC-03's selector at the one point UC-08 interacts with it: it partitions the eligible pool
into unseen questions first and already-seen questions second, then applies the configured count
and per-type quotas to that ordering. That is exactly the additive change ``docs/INTEGRATION.md``
asks UC-03 for, so the tests exercise the real contract rather than a fake that helpfully returns
whatever UC-08 asked for.

It also enforces the two UC-03 invariants UC-08 relies on, so a test cannot pass by accident:

* an attempt number is unique per learner and quiz;
* a learner has at most one open attempt per quiz.

None of these doubles is a simulation of an upstream module's full behaviour. Each implements the
narrow port UC-08 declares, and each can be told to fail, because most of what UC-08 has to get
right is what it does when an upstream call does not succeed.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from app.core.errors import ProviderUnavailableError
from app.modules.retakes.integration.downstream import (
    AttemptScore,
    CoachingAvailability,
    FeedbackAvailability,
    PassFailResult,
    PassFailStatus,
)
from app.modules.retakes.integration.uc01 import (
    QuestionTypeQuota,
    QuizAvailability,
    QuizConfigurationVersion,
)
from app.modules.retakes.integration.uc02 import QuestionDescriptor, QuestionPoolQuery
from app.modules.retakes.integration.uc03 import (
    AttemptContext,
    AttemptStatus,
    DeliveredAttempt,
    RetakeAttemptRequest,
)

DEFAULT_QUIZ = "quiz-1"
DEFAULT_COURSE = "course-1"
DEFAULT_LEARNER = "learner-alice"


# ---------------------------------------------------------------------------
# UC-01
# ---------------------------------------------------------------------------


class FakeConfigurationProvider:
    """Quiz availability and immutable configuration versions."""

    def __init__(self) -> None:
        self.availability: dict[str, QuizAvailability] = {}
        self.versions: dict[str, QuizConfigurationVersion] = {}
        self.active: dict[str, str] = {}
        #: Set to raise instead of answering, to exercise the provider-failure paths.
        self.failure: Exception | None = None
        self.calls: list[str] = []

    # ---- test helpers ----------------------------------------------------

    def publish(
        self,
        *,
        configuration_version_id: str = "cfg-v1",
        version: int = 1,
        quiz_id: str = DEFAULT_QUIZ,
        course_id: str = DEFAULT_COURSE,
        question_count: int = 3,
        maximum_attempts: int | None = 2,
        quotas: tuple[tuple[str, int], ...] = (),
        allowed_types: tuple[str, ...] = (),
        randomise_question_order: bool = False,
        activate: bool = True,
        available: bool = True,
    ) -> QuizConfigurationVersion:
        config = QuizConfigurationVersion(
            configuration_version_id=configuration_version_id,
            quiz_id=quiz_id,
            course_id=course_id,
            version=version,
            question_count=question_count,
            maximum_attempts=maximum_attempts,
            question_type_quotas=tuple(
                QuestionTypeQuota(type=name, count=count) for name, count in quotas
            ),
            allowed_question_types=allowed_types,
            randomise_question_order=randomise_question_order,
        )
        self.versions[configuration_version_id] = config
        self.availability[quiz_id] = QuizAvailability(
            quiz_id=quiz_id, course_id=course_id, available=available
        )
        if activate:
            self.active[quiz_id] = configuration_version_id
        return config

    def withdraw_quiz(self, quiz_id: str = DEFAULT_QUIZ, reason: str = "ARCHIVED") -> None:
        current = self.availability[quiz_id]
        self.availability[quiz_id] = replace(current, available=False, reason=reason)

    def deactivate(self, quiz_id: str = DEFAULT_QUIZ) -> None:
        self.active.pop(quiz_id, None)

    # ---- port ------------------------------------------------------------

    async def get_quiz_availability(self, quiz_id: str) -> QuizAvailability | None:
        self._maybe_fail()
        return self.availability.get(quiz_id)

    async def get_active_configuration(self, quiz_id: str) -> QuizConfigurationVersion | None:
        self._maybe_fail()
        self.calls.append(f"active:{quiz_id}")
        version_id = self.active.get(quiz_id)
        return self.versions.get(version_id) if version_id else None

    async def get_locked_configuration(
        self, configuration_version_id: str
    ) -> QuizConfigurationVersion | None:
        self._maybe_fail()
        self.calls.append(f"locked:{configuration_version_id}")
        return self.versions.get(configuration_version_id)

    def _maybe_fail(self) -> None:
        if self.failure is not None:
            raise self.failure


# ---------------------------------------------------------------------------
# UC-02
# ---------------------------------------------------------------------------


class FakeQuestionBank:
    """An eligible-question pool. Retired questions are excluded, as the port requires."""

    def __init__(self) -> None:
        self.questions: list[QuestionDescriptor] = []
        self.failure: Exception | None = None
        self.queries: list[QuestionPoolQuery] = []

    def add(
        self,
        question_id: str,
        *,
        question_type: str = "SINGLE_CHOICE",
        topic_id: str | None = None,
        retired: bool = False,
    ) -> QuestionDescriptor:
        question = QuestionDescriptor(
            question_id=question_id,
            question_type=question_type,
            topic_id=topic_id,
            retired=retired,
        )
        self.questions.append(question)
        return question

    def add_many(
        self, count: int, *, prefix: str = "q", question_type: str = "SINGLE_CHOICE", start: int = 1
    ) -> list[QuestionDescriptor]:
        return [
            self.add(f"{prefix}{index}", question_type=question_type)
            for index in range(start, start + count)
        ]

    def retire(self, question_id: str) -> None:
        self.questions = [
            replace(question, retired=True) if question.question_id == question_id else question
            for question in self.questions
        ]

    async def find_eligible_questions(
        self, query: QuestionPoolQuery
    ) -> tuple[QuestionDescriptor, ...]:
        if self.failure is not None:
            raise self.failure
        self.queries.append(query)
        return tuple(
            question
            for question in self.questions
            if not (query.exclude_retired and question.retired)
            and (not query.types or question.question_type in query.types)
            and (not query.topic_ids or question.topic_id in query.topic_ids)
        )


# ---------------------------------------------------------------------------
# UC-03
# ---------------------------------------------------------------------------


@dataclass
class _StoredAttempt:
    context: AttemptContext
    question_ids: tuple[str, ...]


@dataclass
class FakeAttemptModule:
    """A stand-in for UC-03: attempt records plus retake attempt creation.

    See the module docstring for what it deliberately reproduces.
    """

    configurations: FakeConfigurationProvider
    bank: FakeQuestionBank
    attempts: dict[str, _StoredAttempt] = field(default_factory=dict)
    #: Raised by ``create_retake_attempt`` instead of creating an attempt.
    creation_failure: Exception | None = None
    #: Raised by ``get_delivered_question_ids`` for these attempt ids.
    unreadable_question_ids: set[str] = field(default_factory=set)
    #: Delivers exactly the previous paper regardless of the preference, to prove the difference
    #: check catches a selector that ignores it.
    ignore_exclusions: bool = False
    created_requests: list[RetakeAttemptRequest] = field(default_factory=list)
    _counter: int = 0

    # ---- test helpers ----------------------------------------------------

    def start_attempt(
        self,
        *,
        learner_id: str = DEFAULT_LEARNER,
        quiz_id: str = DEFAULT_QUIZ,
        course_id: str = DEFAULT_COURSE,
        configuration_version_id: str = "cfg-v1",
        question_ids: tuple[str, ...] = (),
        attempt_id: str | None = None,
        status: AttemptStatus = AttemptStatus.ACTIVE,
    ) -> AttemptContext:
        """Create a first attempt the way UC-03 would, without going through UC-08."""
        self._counter += 1
        identifier = attempt_id or f"attempt-{self._counter}"
        number = self._next_number(learner_id, quiz_id)
        config = self.configurations.versions.get(configuration_version_id)
        context = AttemptContext(
            attempt_id=identifier,
            learner_id=learner_id,
            course_id=course_id,
            quiz_id=quiz_id,
            attempt_number=number,
            status=status,
            configuration_version_id=configuration_version_id,
            configuration_version_number=config.version if config else 1,
            started_at="2026-01-01T09:00:00.000Z",
            total_questions=len(question_ids),
            course_name="Fire Safety",
        )
        self.attempts[identifier] = _StoredAttempt(context=context, question_ids=question_ids)
        return context

    def submit(self, attempt_id: str, *, submitted_at: str = "2026-01-01T09:30:00.000Z") -> None:
        stored = self.attempts[attempt_id]
        self.attempts[attempt_id] = _StoredAttempt(
            context=replace(
                stored.context, status=AttemptStatus.SUBMITTED, submitted_at=submitted_at
            ),
            question_ids=stored.question_ids,
        )

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """A deep-enough copy to assert that no historical attempt changed."""
        return {
            attempt_id: {
                "context": stored.context,
                "question_ids": stored.question_ids,
            }
            for attempt_id, stored in self.attempts.items()
        }

    # ---- port: reads -----------------------------------------------------

    async def get_attempt(self, attempt_id: str) -> AttemptContext | None:
        stored = self.attempts.get(attempt_id)
        return stored.context if stored else None

    async def list_attempts(self, learner_id: str, quiz_id: str) -> tuple[AttemptContext, ...]:
        return tuple(
            sorted(
                (
                    stored.context
                    for stored in self.attempts.values()
                    if stored.context.learner_id == learner_id
                    and stored.context.quiz_id == quiz_id
                ),
                key=lambda attempt: attempt.attempt_number,
            )
        )

    async def count_used_attempts(self, learner_id: str, course_id: str, quiz_id: str) -> int:
        return len(
            [
                stored
                for stored in self.attempts.values()
                if stored.context.learner_id == learner_id
                and stored.context.quiz_id == quiz_id
                and stored.context.course_id == course_id
            ]
        )

    async def find_open_attempt(self, learner_id: str, quiz_id: str) -> AttemptContext | None:
        return next(
            (
                stored.context
                for stored in self.attempts.values()
                if stored.context.learner_id == learner_id
                and stored.context.quiz_id == quiz_id
                and stored.context.open
            ),
            None,
        )

    async def get_delivered_question_ids(self, attempt_id: str) -> tuple[str, ...]:
        if attempt_id in self.unreadable_question_ids:
            raise ProviderUnavailableError("The delivered questions could not be read.")
        stored = self.attempts.get(attempt_id)
        return stored.question_ids if stored else ()

    # ---- port: the one write ---------------------------------------------

    async def create_retake_attempt(self, request: RetakeAttemptRequest) -> DeliveredAttempt:
        if self.creation_failure is not None:
            raise self.creation_failure

        self.created_requests.append(request)
        config = self.configurations.versions.get(request.configuration_version_id)
        if config is None:  # pragma: no cover - the service resolves a real version
            raise ProviderUnavailableError("Unknown configuration version.")

        # UC-03's invariants, enforced so a UC-08 test cannot pass by breaking them.
        if any(
            stored.context.learner_id == request.learner_id
            and stored.context.quiz_id == request.quiz_id
            and stored.context.attempt_number == request.attempt_number
            for stored in self.attempts.values()
        ):
            raise ProviderUnavailableError("Attempt number already used for this learner.")
        if await self.find_open_attempt(request.learner_id, request.quiz_id) is not None:
            raise ProviderUnavailableError("An attempt is already open for this learner.")

        selected = self._select(config, request)

        self._counter += 1
        attempt_id = f"attempt-{self._counter}"
        context = AttemptContext(
            attempt_id=attempt_id,
            learner_id=request.learner_id,
            course_id=request.course_id,
            quiz_id=request.quiz_id,
            attempt_number=request.attempt_number,
            status=AttemptStatus.ACTIVE,
            configuration_version_id=config.configuration_version_id,
            configuration_version_number=config.version,
            started_at="2026-01-02T09:00:00.000Z",
            total_questions=len(selected),
            course_name="Fire Safety",
        )
        self.attempts[attempt_id] = _StoredAttempt(context=context, question_ids=selected)

        return DeliveredAttempt(
            attempt_id=attempt_id,
            learner_id=request.learner_id,
            course_id=request.course_id,
            quiz_id=request.quiz_id,
            attempt_number=request.attempt_number,
            status=AttemptStatus.ACTIVE,
            configuration_version_id=config.configuration_version_id,
            configuration_version_number=config.version,
            delivered_question_ids=selected,
            started_at=context.started_at,
            delivery_mode="ALL_AT_ONCE",
        )

    # ---- selection -------------------------------------------------------

    def _select(
        self, config: QuizConfigurationVersion, request: RetakeAttemptRequest
    ) -> tuple[str, ...]:
        """Unseen questions first, then the rest — then UC-03's count and quota rules.

        The exclusion is a *preference*: when the unseen questions run out the remainder comes from
        the seen ones, and a retired question is never reached for.
        """
        pool = [question for question in self.bank.questions if not question.retired]
        permitted = {quota.type for quota in config.question_type_quotas if quota.count > 0} or set(
            config.allowed_question_types
        )
        if permitted:
            pool = [question for question in pool if question.question_type in permitted]

        deprioritised = set(() if self.ignore_exclusions else request.deprioritised_question_ids)
        ordered = [q for q in pool if q.question_id not in deprioritised] + [
            q for q in pool if q.question_id in deprioritised
        ]

        quotas = [quota for quota in config.question_type_quotas if quota.count > 0]
        if quotas:
            selected: list[str] = []
            for quota in quotas:
                matching = [q.question_id for q in ordered if q.question_type == quota.type]
                selected.extend(matching[: quota.count])
            return tuple(selected)
        return tuple(question.question_id for question in ordered[: config.question_count])

    def _next_number(self, learner_id: str, quiz_id: str) -> int:
        return (
            max(
                (
                    stored.context.attempt_number
                    for stored in self.attempts.values()
                    if stored.context.learner_id == learner_id
                    and stored.context.quiz_id == quiz_id
                ),
                default=0,
            )
            + 1
        )


# ---------------------------------------------------------------------------
# UC-04 / UC-05 / UC-06 / UC-07
# ---------------------------------------------------------------------------


class FakeScoringProvider:
    def __init__(self) -> None:
        self.scores: dict[str, AttemptScore] = {}
        self.failure: Exception | None = None

    def record(
        self,
        attempt_id: str,
        *,
        total: float,
        maximum: float,
        percentage: float,
        confirmed: bool = True,
    ) -> None:
        self.scores[attempt_id] = AttemptScore(
            attempt_id=attempt_id,
            confirmed=confirmed,
            total_marks=total,
            maximum_marks=maximum,
            percentage=percentage,
            scored_at="2026-01-01T09:31:00.000Z",
        )

    async def get_score(self, attempt_id: str) -> AttemptScore | None:
        if self.failure is not None:
            raise self.failure
        return self.scores.get(attempt_id)


class FakePassFailProvider:
    def __init__(self) -> None:
        self.results: dict[str, PassFailResult] = {}

    def record(
        self, attempt_id: str, *, status: PassFailStatus, pass_mark: float = 80.0
    ) -> None:
        self.results[attempt_id] = PassFailResult(
            attempt_id=attempt_id,
            status=status,
            pass_mark_percentage=pass_mark,
            determined_at="2026-01-01T09:32:00.000Z",
        )

    async def get_result(self, attempt_id: str) -> PassFailResult | None:
        return self.results.get(attempt_id)


class FakeFeedbackProvider:
    def __init__(self) -> None:
        self.available: set[str] = set()

    async def get_feedback_availability(self, attempt_id: str) -> FeedbackAvailability | None:
        if attempt_id not in self.available:
            return None
        return FeedbackAvailability(attempt_id=attempt_id, available=True, status="RELEASED")


class FakeCoachingProvider:
    def __init__(self) -> None:
        self.available: dict[str, int] = {}

    async def get_coaching_availability(self, attempt_id: str) -> CoachingAvailability | None:
        if attempt_id not in self.available:
            return None
        return CoachingAvailability(
            attempt_id=attempt_id,
            available=True,
            coachable_question_count=self.available[attempt_id],
        )


class RecordingAuditLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def record(self, event: str, **fields: Any) -> None:
        self.events.append((event, fields))

    def codes(self) -> list[str]:
        return [event for event, _ in self.events]


# ---------------------------------------------------------------------------
# Failure-injecting repositories
# ---------------------------------------------------------------------------


class FailingGrantRepository:
    """A grant store whose insert always fails, for §14's grant-failure path."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.inserts = 0

    async def get(self, grant_id: str) -> None:
        return None

    async def get_by_idempotency_key(self, idempotency_key: str) -> None:
        return None

    async def list_for_learner_quiz(self, learner_id: str, course_id: str, quiz_id: str) -> tuple:
        return ()

    async def insert(self, grant: Any) -> Any:
        self.inserts += 1
        raise self.error

    async def save(self, grant: Any) -> Any:  # pragma: no cover - never reached
        raise self.error
