"""Attempt lifecycle and question delivery.

Creation performs every eligibility and configuration check *before* touching the
database, then persists the attempt and its entire frozen question set in a single
transaction. A partially created attempt is therefore not representable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.attempt_delivery.domain import errors
from app.modules.attempt_delivery.domain.enums import (
    ATTEMPT_ELIGIBLE_ENROLMENT_STATUSES,
    AttemptStatus,
    QuestionPresentation,
)
from app.modules.attempt_delivery.ids import new_id
from app.modules.attempt_delivery.integration.enrolment.port import EnrolmentPort
from app.modules.attempt_delivery.integration.uc01.port import QuizConfigurationPort
from app.modules.attempt_delivery.integration.uc01.types import QuizConfigurationVersion
from app.modules.attempt_delivery.integration.uc02.port import QuestionBankPort
from app.modules.attempt_delivery.integration.uc02.types import (
    BankQuestion,
    DeliveredQuestionRef,
    QuestionQuery,
)
from app.modules.attempt_delivery.models import AttemptQuestion, QuizAttempt
from app.modules.attempt_delivery.repositories.attempt_question_repository import (
    AttemptQuestionRepository,
)
from app.modules.attempt_delivery.repositories.attempt_repository import AttemptRepository
from app.modules.attempt_delivery.services.attempt_access_service import AttemptAccessService
from app.modules.attempt_delivery.services.configuration_lock import lock_configuration
from app.modules.attempt_delivery.services.question_selection_service import (
    QuestionSelectionService,
)
from app.modules.attempt_delivery.services.submission_service import SubmissionService
from app.modules.attempt_delivery.services.timing_service import TimingService

#: Marks awarded when UC-02 does not specify a value for a question.
DEFAULT_QUESTION_POINTS = 1.0


@dataclass(frozen=True, slots=True)
class EligibilityReport:
    """Whether the learner may start a new attempt, and why not if they may not."""

    quiz_id: str
    course_id: str | None
    learner_id: str
    eligible: bool
    reasons: list[dict[str, str]]
    enrolled: bool
    enrolment_status: str | None
    attempts_used: int
    max_attempts: int | None
    #: ``None`` when the configuration permits unlimited attempts.
    attempts_remaining: int | None
    open_attempt_id: str | None
    active_configuration_version_id: str | None
    active_configuration_version: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "quizId": self.quiz_id,
            "courseId": self.course_id,
            "learnerId": self.learner_id,
            "eligible": self.eligible,
            "reasons": self.reasons,
            "enrolled": self.enrolled,
            "enrolmentStatus": self.enrolment_status,
            "attemptsUsed": self.attempts_used,
            "maxAttempts": self.max_attempts,
            "attemptsRemaining": self.attempts_remaining,
            "openAttemptId": self.open_attempt_id,
            "activeConfigurationVersionId": self.active_configuration_version_id,
            "activeConfigurationVersion": self.active_configuration_version,
        }


@dataclass(frozen=True, slots=True)
class RetakeDirective:
    """What UC-08 has already decided about a retake, handed to UC-03 to deliver.

    UC-03 creates every attempt, including retakes: a second creation path would be a second
    attempt lifecycle, and the two would drift. What a retake changes is narrow and stated
    here rather than inferred, so a reader can see exactly which of UC-03's rules UC-08
    overrides and why:

    ``configuration_version_id``
        The version UC-08 resolved. Re-reading the active version here could lock a
        *different* one if an administrator published between the eligibility check and this
        call — the retake would then run under a configuration nobody checked the allowance
        against.

    ``attempt_number``
        The slot UC-08 reserved. ``next_attempt_number`` would recompute it from rows UC-03
        can see, which excludes another request's in-flight reservation.

    ``deprioritised_question_ids``
        What the learner has already been shown. A preference for the selector, never a
        filter — see ``QuestionSelectionService.select``.

    The maximum-attempts check is skipped, and only for a retake: UC-08 is the authority on
    the allowance because it is the only module that can see administrator grants and
    in-flight reservations. Every other rule — enrolment, availability, one open attempt at a
    time, the configuration lock, the frozen snapshot, the timer, and both unique
    constraints — applies to a retake exactly as it does to a first attempt.
    """

    previous_attempt_id: str
    configuration_version_id: str
    attempt_number: int
    deprioritised_question_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AttemptCreationResult:
    attempt: QuizAttempt
    questions: Sequence[AttemptQuestion]
    type_counts: dict[str, int]
    #: Deprioritised questions the bank was too small to avoid re-delivering (UC-08 §17).
    reused_question_ids: tuple[str, ...] = ()


class AttemptService:
    """Creates attempts and delivers their questions."""

    __slots__ = (
        "_session",
        "_attempts",
        "_attempt_questions",
        "_configurations",
        "_question_bank",
        "_enrolments",
        "_selection",
        "_access",
        "_submissions",
        "_timing",
        "_clock",
    )

    def __init__(
        self,
        *,
        session: Session,
        attempts: AttemptRepository,
        attempt_questions: AttemptQuestionRepository,
        configurations: QuizConfigurationPort,
        question_bank: QuestionBankPort,
        enrolments: EnrolmentPort,
        selection: QuestionSelectionService,
        access: AttemptAccessService,
        submissions: SubmissionService,
        timing: TimingService,
        clock: Any,
    ) -> None:
        self._session = session
        self._attempts = attempts
        self._attempt_questions = attempt_questions
        self._configurations = configurations
        self._question_bank = question_bank
        self._enrolments = enrolments
        self._selection = selection
        self._access = access
        self._submissions = submissions
        self._timing = timing
        self._clock = clock

    # -------------------------------------------------------- eligibility

    def check_eligibility(self, learner_id: str, quiz_id: str) -> EligibilityReport:
        """Report whether the learner may start a new attempt, without creating one.

        Lets a client show "2 of 3 attempts remaining", or explain a refusal, before
        the learner commits — using exactly the checks :meth:`create_attempt` applies.
        """
        reasons: list[dict[str, str]] = []

        availability = self._configurations.get_quiz_availability(quiz_id)
        if availability is None:
            raise errors.quiz_not_found(quiz_id)

        course_id = availability.course_id
        if not availability.available:
            reasons.append(
                {
                    "code": "QUIZ_NOT_AVAILABLE",
                    "message": (
                        "The quiz is not available for attempts "
                        f"({availability.reason or 'unspecified'})."
                    ),
                }
            )

        enrolment = self._enrolments.get_enrolment(learner_id, course_id)
        if enrolment is None:
            reasons.append(
                {
                    "code": "LEARNER_NOT_ENROLLED",
                    "message": "The learner is not enrolled in this course.",
                }
            )
        elif enrolment.status not in ATTEMPT_ELIGIBLE_ENROLMENT_STATUSES:
            reasons.append(
                {
                    "code": "ENROLMENT_NOT_ACTIVE",
                    "message": f"The learner enrolment is {enrolment.status}.",
                }
            )

        config = self._configurations.get_active_configuration(quiz_id)
        if config is None:
            reasons.append(
                {
                    "code": "CONFIGURATION_VERSION_UNAVAILABLE",
                    "message": "The quiz has no active configuration version.",
                }
            )

        attempts_used = self._attempts.count_for_learner_and_quiz(learner_id, quiz_id)
        max_attempts = config.max_attempts if config else None
        attempts_remaining = None if max_attempts is None else max(0, max_attempts - attempts_used)

        if max_attempts is not None and attempts_used >= max_attempts:
            reasons.append(
                {
                    "code": "MAX_ATTEMPTS_REACHED",
                    "message": f"The maximum of {max_attempts} attempt(s) has been reached.",
                }
            )

        open_attempt = self._attempts.find_open(learner_id, quiz_id)
        if open_attempt is not None:
            reasons.append(
                {
                    "code": "ACTIVE_ATTEMPT_EXISTS",
                    "message": "An attempt is already in progress. Resume or submit it first.",
                }
            )

        return EligibilityReport(
            quiz_id=quiz_id,
            course_id=course_id,
            learner_id=learner_id,
            eligible=not reasons,
            reasons=reasons,
            enrolled=enrolment is not None,
            enrolment_status=str(enrolment.status) if enrolment else None,
            attempts_used=attempts_used,
            max_attempts=max_attempts,
            attempts_remaining=attempts_remaining,
            open_attempt_id=open_attempt.id if open_attempt else None,
            active_configuration_version_id=config.configuration_version_id if config else None,
            active_configuration_version=config.version if config else None,
        )

    # ------------------------------------------------------------ creation

    def create_attempt(
        self,
        learner_id: str,
        quiz_id: str,
        *,
        retake: RetakeDirective | None = None,
        formal_assessment: bool = False,
    ) -> AttemptCreationResult:
        """Create an attempt.

        The order is deliberate:
        availability → enrolment → active configuration → configuration lock →
        remaining attempts → no open attempt → question selection → persist.

        Everything before ``persist`` is validation and reads across the UC-01/UC-02
        boundaries. Only once all of it has passed is anything written, and then the
        attempt row and its complete question set are committed together.

        ``retake`` (UC-08) and ``formal_assessment`` (UC-09) are additive: with both at their
        defaults this is the method as it was, step for step. See :class:`RetakeDirective` for
        exactly which rules a retake overrides.
        """
        # ---- 1. The quiz itself must be attemptable. ----------------------
        availability = self._configurations.get_quiz_availability(quiz_id)
        if availability is None:
            raise errors.quiz_not_found(quiz_id)
        if not availability.available:
            raise errors.quiz_not_available(quiz_id, availability.reason or "QUIZ_UNAVAILABLE")
        course_id = availability.course_id

        # ---- 2. Enrolment. -----------------------------------------------
        enrolment = self._enrolments.get_enrolment(learner_id, course_id)
        if enrolment is None:
            raise errors.learner_not_enrolled(learner_id, course_id)
        if enrolment.status not in ATTEMPT_ELIGIBLE_ENROLMENT_STATUSES:
            raise errors.enrolment_not_active(learner_id, course_id, str(enrolment.status))

        # ---- 3. Read the configuration exactly once, and lock it. --------
        if retake is None:
            active = self._configurations.get_active_configuration(quiz_id)
        else:
            # The version UC-08 resolved, not whatever is active now — see RetakeDirective.
            active = self._configurations.get_configuration_version(
                retake.configuration_version_id
            )
        if active is None:
            raise errors.configuration_version_unavailable(quiz_id)
        configuration = lock_configuration(active)

        # ---- 4. Remaining attempts. --------------------------------------
        # Skipped for a retake: UC-08 holds the allowance, because only UC-08 can see
        # administrator grants and another request's reservation. It is skipped rather than
        # relaxed — UC-08 has already refused the retake if no attempt remained.
        if retake is None:
            attempts_used = self._attempts.count_for_learner_and_quiz(learner_id, quiz_id)
            if (
                configuration.max_attempts is not None
                and attempts_used >= configuration.max_attempts
            ):
                raise errors.max_attempts_reached(attempts_used, configuration.max_attempts)

        # ---- 5. Only one attempt may be open at a time. ------------------
        open_attempt = self._attempts.find_open(learner_id, quiz_id)
        if open_attempt is not None:
            raise errors.active_attempt_exists(open_attempt.id)

        # ---- 6. Select the questions (UC-02 boundary). --------------------
        pool = self._question_bank.find_eligible_questions(
            QuestionQuery(
                quiz_id=quiz_id,
                course_id=course_id,
                topic_ids=configuration.topic_ids,
                exclude_retired=True,
            )
        )

        attempt_id = new_id()
        # The attempt id doubles as the randomisation seed, so the selection is
        # reproducible from the persisted row alone.
        selection = self._selection.select(
            configuration,
            pool,
            attempt_id,
            deprioritised_question_ids=retake.deprioritised_question_ids if retake else (),
        )

        # ---- 7. Persist atomically. --------------------------------------
        started_at = self._clock.now()
        expires_at = self._timing.compute_expiry(started_at, configuration.time_limit_seconds)
        attempt_number = (
            retake.attempt_number
            if retake is not None
            else self._attempts.next_attempt_number(learner_id, quiz_id)
        )

        attempt = QuizAttempt(
            id=attempt_id,
            learner_id=learner_id,
            course_id=course_id,
            quiz_id=quiz_id,
            configuration_version_id=configuration.configuration_version_id,
            configuration_version_number=configuration.version,
            configuration_snapshot=configuration.to_dict(),
            attempt_number=attempt_number,
            status=str(AttemptStatus.ACTIVE),
            question_presentation=str(configuration.question_presentation),
            retake_of_attempt_id=retake.previous_attempt_id if retake else None,
            is_formal_assessment=formal_assessment,
            selection_seed=attempt_id,
            total_questions=len(selection.questions),
            current_position=1,
            time_limit_seconds=configuration.time_limit_seconds,
            started_at=started_at,
            expires_at=expires_at,
            submitted_at=None,
            finalised_at=None,
            submission_reason=None,
            last_activity_at=started_at,
            created_at=started_at,
            updated_at=started_at,
        )

        try:
            self._attempts.add(attempt)
            self._attempt_questions.add_all(
                [
                    AttemptQuestion(
                        id=new_id(),
                        attempt_id=attempt_id,
                        question_id=question.question_id,
                        question_version=question.version,
                        question_type=str(question.type),
                        position=index,
                        points=question.points or DEFAULT_QUESTION_POINTS,
                        question_snapshot=question.to_dict(),
                        created_at=started_at,
                    )
                    for index, question in enumerate(selection.questions, start=1)
                ]
            )
            # Tell the bank what it just handed over, in this same transaction. UC-02's usage
            # counts, its refusal to hard-delete a question that has been used, and its historical
            # attempt report all depend on this record; an attempt that skipped it would look
            # correct here and leave UC-02's history with a hole in it.
            self._question_bank.record_delivery(
                attempt_id,
                [
                    DeliveredQuestionRef(
                        question_id=question.question_id,
                        question_version=question.version,
                        position=index,
                    )
                    for index, question in enumerate(selection.questions, start=1)
                ],
                learner_ref=learner_id,
            )
            self._session.commit()
        except IntegrityError as exc:
            # `ux_attempt_single_open` / `ux_attempt_number` turn a lost creation race
            # into a clear conflict rather than a second concurrent attempt.
            self._session.rollback()
            existing = self._attempts.find_open(learner_id, quiz_id)
            raise errors.active_attempt_exists(existing.id if existing else attempt_id) from exc

        created = self._attempts.get(attempt_id)
        if created is None:  # pragma: no cover - defensive
            raise errors.internal_error()

        return AttemptCreationResult(
            attempt=created,
            questions=self._attempt_questions.list_for_attempt(attempt_id),
            type_counts=selection.type_counts,
            reused_question_ids=selection.reused_question_ids,
        )

    # ----------------------------------------------------------- retrieval

    def get_attempt(self, attempt_id: str, learner_id: str) -> QuizAttempt:
        return self._access.load(attempt_id, learner_id).attempt

    def get_open_attempt(self, learner_id: str, quiz_id: str) -> QuizAttempt:
        """The learner's attempt currently in progress for a quiz.

        This is the reload/reconnection entry point: a client calls it after a refresh
        and gets the authoritative attempt, then rebuilds answers and flags from the
        state endpoints rather than from its own memory.
        """
        open_attempt = self._attempts.find_open(learner_id, quiz_id)
        if open_attempt is None:
            raise errors.no_active_attempt(quiz_id)
        # Routed through the access layer so an elapsed time limit is settled here too.
        return self._access.load(open_attempt.id, learner_id).attempt

    def list_attempts(self, learner_id: str, quiz_id: str) -> Sequence[QuizAttempt]:
        return self._attempts.list_for_learner_and_quiz(learner_id, quiz_id)

    # ---------------------------------------------------- question delivery

    def get_all_questions(
        self, attempt_id: str, learner_id: str
    ) -> tuple[QuizAttempt, Sequence[AttemptQuestion]]:
        """The attempt's full question set.

        Permitted only when the locked configuration's delivery mode is ALL_AT_ONCE.
        Under ONE_AT_A_TIME the whole paper is not handed out; the client uses the
        single-question endpoints instead. Enforcing this server-side makes the
        delivery mode a real constraint rather than a frontend convention.
        """
        attempt = self._access.load(attempt_id, learner_id).attempt
        if attempt.question_presentation != str(QuestionPresentation.ALL_AT_ONCE):
            raise errors.question_presentation_violation(
                "This attempt is delivered one question at a time; request questions individually.",
                attemptId=attempt_id,
                questionPresentation=attempt.question_presentation,
                currentPosition=attempt.current_position,
            )
        return attempt, self._attempt_questions.list_for_attempt(attempt_id)

    def get_question(
        self, attempt_id: str, learner_id: str, question_id: str
    ) -> tuple[QuizAttempt, AttemptQuestion]:
        """A single delivered question. Valid in both delivery modes."""
        attempt = self._access.load(attempt_id, learner_id).attempt
        question = self._attempt_questions.find_by_question_id(attempt_id, question_id)
        if question is None:
            raise errors.question_unavailable(question_id)
        return attempt, question

    def get_question_at_position(
        self, attempt_id: str, learner_id: str, position: int
    ) -> tuple[QuizAttempt, AttemptQuestion]:
        attempt = self._access.load(attempt_id, learner_id).attempt
        question = self._attempt_questions.find_by_position(attempt_id, position)
        if question is None:
            raise errors.validation_error(
                f"No question at position {position} for this attempt.",
                attemptId=attempt_id,
                position=position,
                totalQuestions=attempt.total_questions,
            )
        return attempt, question

    def get_current_question(
        self, attempt_id: str, learner_id: str
    ) -> tuple[QuizAttempt, AttemptQuestion]:
        """The question at the attempt's persisted cursor.

        The cursor lives on the attempt row, so a learner who reconnects resumes at the
        question they were on rather than at the start.
        """
        attempt = self._access.load(attempt_id, learner_id).attempt
        question = self._attempt_questions.find_by_position(
            attempt_id, attempt.current_position
        ) or self._attempt_questions.find_by_position(attempt_id, 1)
        if question is None:  # pragma: no cover - an attempt always has questions
            raise errors.internal_error()
        return attempt, question

    def set_cursor(self, attempt_id: str, learner_id: str, position: int) -> QuizAttempt:
        """Move the persisted navigation cursor. Allowed only while the attempt is open."""
        attempt = self._access.load_for_write(attempt_id, learner_id)
        if position < 1 or position > attempt.total_questions:
            raise errors.validation_error(
                '"position" must be within the attempt question range.',
                position=position,
                totalQuestions=attempt.total_questions,
            )
        self._attempts.update_cursor(attempt_id, position, self._clock.now())
        self._session.commit()
        updated = self._attempts.get(attempt_id)
        if updated is None:  # pragma: no cover - defensive
            raise errors.attempt_not_found(attempt_id)
        return updated

    # --------------------------------------------------- navigation state

    def get_navigation_state(self, attempt_id: str, learner_id: str) -> dict[str, Any]:
        """Everything a client needs to render navigation.

        Per-question answered, complete and flagged state, plus authoritative timing.
        """
        attempt = self._access.load(attempt_id, learner_id).attempt
        outline = self._submissions.outline(attempt_id)
        summary = self._submissions.summarise(attempt_id)

        return {
            "attemptId": attempt.id,
            "status": attempt.status,
            "questionPresentation": attempt.question_presentation,
            "currentPosition": attempt.current_position,
            **summary.to_dict(),
            "flaggedCount": sum(1 for entry in outline if entry.flagged),
            "questions": [entry.to_dict() for entry in outline],
            "timing": self._timing.compute(attempt).to_dict(),
        }

    # ------------------------------------------------------------ helpers

    def find_expired_attempts(self, limit: int = 100) -> Sequence[QuizAttempt]:
        """Exposed for the expiry sweep and for diagnostics."""
        return self._attempts.find_expired(self._clock.now(), limit)

    @staticmethod
    def locked_configuration(attempt: QuizAttempt) -> QuizConfigurationVersion:
        """Rehydrate the configuration this attempt is locked to."""
        return QuizConfigurationVersion.from_dict(attempt.configuration_snapshot)

    @staticmethod
    def snapshot_question(question: AttemptQuestion) -> BankQuestion:
        """Rehydrate the exact question structure the learner was shown."""
        return BankQuestion.from_dict(question.question_snapshot)
