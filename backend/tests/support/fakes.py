"""In-memory test doubles for the boundaries UC-03 depends on.

Why fakes rather than the real adapters
--------------------------------------
UC-03's suite tests **UC-03's own logic** — timing, autosave, flags, submission, selection. Driving
it through the real UC-01 and UC-02 would test three capabilities at once and, worse, would make
several required behaviours untestable: UC-01 correctly *refuses* to publish an incoherent
configuration, so "UC-03 rejects a configuration it cannot deliver" could not be reached at all.

These fakes satisfy the same ``Protocol``s the real adapters do, which is exactly what the ports
exist for. The real adapters are covered separately by ``tests/integration/``, which drives UC-01,
UC-02 and UC-03 together over HTTP.

They replaced the ``ext_*`` database projections UC-03 shipped with. Those had to be a database
because they were also the demo data source; a test double does not, and holding the state in a dict
makes the fixtures faster and the failure modes (a withdrawn version, a retired question) trivial to
arrange.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from app.modules.attempt_delivery.domain import errors
from app.modules.attempt_delivery.integration.enrolment.port import Enrolment
from app.modules.attempt_delivery.integration.uc01.types import (
    QuizAvailability,
    QuizConfigurationVersion,
)
from app.modules.attempt_delivery.integration.uc02.types import (
    BankQuestion,
    DeliveredQuestionRef,
    QuestionQuery,
)
from app.modules.identity.enums import EnrolmentStatus


class FakeQuizConfigurationPort:
    """UC-01, in memory.

    Holds no configuration *rules* of its own — it stores whatever a test publishes and hands it
    back. That is deliberate: authoring rules belong to UC-01, and a fake that validated them would
    quietly become a second implementation of somebody else's domain.
    """

    def __init__(self) -> None:
        self.quizzes: dict[str, QuizAvailability] = {}
        self.versions: dict[str, QuizConfigurationVersion] = {}
        #: quiz id -> the configuration version id currently active for it
        self.active: dict[str, str] = {}

    # ---- QuizConfigurationPort -------------------------------------------

    def get_quiz_availability(self, quiz_id: str) -> QuizAvailability | None:
        return self.quizzes.get(quiz_id)

    def get_active_configuration(self, quiz_id: str) -> QuizConfigurationVersion | None:
        version_id = self.active.get(quiz_id)
        return None if version_id is None else self.versions.get(version_id)

    def get_configuration_version(
        self, configuration_version_id: str
    ) -> QuizConfigurationVersion | None:
        return self.versions.get(configuration_version_id)

    # ---- seeding ----------------------------------------------------------

    def upsert_quiz(
        self,
        *,
        quiz_id: str,
        course_id: str,
        title: str = "",
        available: bool = True,
        reason: str | None = None,
    ) -> None:
        self.quizzes[quiz_id] = QuizAvailability(
            quiz_id=quiz_id,
            course_id=course_id,
            available=available,
            reason=None if available else (reason or "QUIZ_UNAVAILABLE"),
        )

    def publish_version(
        self,
        *,
        configuration_version_id: str,
        quiz_id: str,
        course_id: str,
        version: int,
        activated_at: str,
        rules: dict,
        activate: bool = True,
    ) -> QuizConfigurationVersion:
        """Publish a version, superseding any previous active one.

        Mirrors what UC-01 does when an administrator saves a change, which is how the
        configuration-locking tests prove that superseding a version leaves an in-flight attempt
        undisturbed.
        """
        payload = {
            **rules,
            "configurationVersionId": configuration_version_id,
            "quizId": quiz_id,
            "courseId": course_id,
            "version": version,
            "activatedAt": activated_at,
        }
        try:
            resolved = QuizConfigurationVersion.from_dict(payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise errors.invalid_configuration(
                f"The stored quiz configuration is not usable: {exc}",
                configurationVersionId=configuration_version_id,
            ) from exc

        self.versions[configuration_version_id] = resolved
        if activate:
            self.active[quiz_id] = configuration_version_id
        return resolved

    def delete_version(self, configuration_version_id: str) -> None:
        """Withdraw a version entirely, as UC-01 might when a draft is deleted."""
        self.versions.pop(configuration_version_id, None)
        for quiz_id, active_id in list(self.active.items()):
            if active_id == configuration_version_id:
                del self.active[quiz_id]


class FakeQuestionBankPort:
    """UC-02, in memory. Read-only from UC-03's perspective; seeding is a test affordance."""

    def __init__(self) -> None:
        self.questions: dict[str, BankQuestion] = {}
        #: attempt ref -> what UC-03 reported as delivered.
        self.deliveries: dict[str, list[DeliveredQuestionRef]] = {}
        self.delivery_learners: dict[str, str | None] = {}

    # ---- QuestionBankPort -------------------------------------------------

    def find_eligible_questions(self, query: QuestionQuery) -> list[BankQuestion]:
        matches = [
            question
            for question in self.questions.values()
            if question.quiz_id == query.quiz_id
            and question.course_id == query.course_id
            and (not query.exclude_retired or not question.retired)
            and (not query.types or question.type in query.types)
            and (not query.topic_ids or question.topic_id in query.topic_ids)
        ]
        # Deterministic, so the pool handed to UC-03 is stable; shuffling is UC-03's decision,
        # driven by the attempt's persisted seed.
        return sorted(matches, key=lambda question: question.question_id)

    def get_questions_by_ids(self, question_ids: Sequence[str]) -> list[BankQuestion]:
        # Retired questions are returned deliberately, so an in-flight or historical attempt can
        # always be reconstructed.
        found = [self.questions[qid] for qid in question_ids if qid in self.questions]
        return sorted(found, key=lambda question: question.question_id)

    def record_delivery(
        self,
        attempt_ref: str,
        delivered: Sequence[DeliveredQuestionRef],
        learner_ref: str | None = None,
    ) -> None:
        """Remember what was delivered, so a test can assert the bank was told.

        Idempotent per attempt, matching the real adapter: a repeated creation must not double-
        count.
        """
        if attempt_ref in self.deliveries:
            return
        self.deliveries[attempt_ref] = list(delivered)
        self.delivery_learners[attempt_ref] = learner_ref

    # ---- seeding ----------------------------------------------------------

    def upsert_question(self, question: BankQuestion) -> None:
        if question.quiz_id is None or question.course_id is None:
            raise ValueError("A seeded question requires both quiz_id and course_id.")
        self.questions[question.question_id] = question

    def set_retired(self, question_id: str, retired: bool) -> None:
        """Mirror UC-02 retiring a question. Retired questions stay readable."""
        question = self.questions.get(question_id)
        if question is None:
            raise errors.question_unavailable(question_id)
        self.questions[question_id] = replace(question, retired=retired)


class FakeEnrolmentPort:
    """Course enrolment, in memory."""

    def __init__(self) -> None:
        self.enrolments: dict[tuple[str, str], Enrolment] = {}

    def get_enrolment(self, learner_id: str, course_id: str) -> Enrolment | None:
        return self.enrolments.get((learner_id, course_id))

    def upsert_enrolment(
        self,
        *,
        learner_id: str,
        course_id: str,
        status: EnrolmentStatus = EnrolmentStatus.ACTIVE,
        enrolled_at: str = "2026-01-01T00:00:00Z",
    ) -> None:
        self.enrolments[(learner_id, course_id)] = Enrolment(
            learner_id=learner_id,
            course_id=course_id,
            status=status,
            enrolled_at=enrolled_at,
        )
