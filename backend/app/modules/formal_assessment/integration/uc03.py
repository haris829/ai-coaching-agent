"""UC-03 (Quiz Attempt Delivery) — the contract UC-09 consumes.

UC-09 does not deliver quizzes, does not store answers, does not run the timer and does not score
anything. It supervises an attempt that UC-03 owns. So this port is the whole of the interaction,
and its shape is deliberately narrow:

======================================  ===================================================
UC-09 asks                               Because
======================================  ===================================================
``create_attempt``                       a formal attempt is an ordinary UC-03 attempt; the
                                         configuration lock, question selection, snapshot, delivery
                                         mode, timer and attempt numbering are all UC-03's, and
                                         rebuilding any of them here would be a second
                                         implementation that could drift.
``get_attempt``                          to check status, ownership and expiry from the
                                         authoritative source rather than from its own record.
``save_answers``                         autosave during a formal attempt goes through UC-09's
                                         session check and then straight into UC-03's existing
                                         autosave. No second autosave architecture (§6).
``get_latest_autosaved_state``           the disconnect path needs the latest valid autosaved
                                         state to submit (§5, §6).
``submit_attempt``                       submission — learner-confirmed or auto — is UC-03's
                                         existing, idempotent commit.
``get_attempt_responses``                the assessor's review payload shows what the learner
                                         answered (§10).
======================================  ===================================================

THREE DISTINCT THINGS, KEPT DISTINCT (§6)
-----------------------------------------
``AutosavedState`` — what the learner's client last saved, mutable until submission.
``SubmittedState`` — what was committed, immutable, with the reason it was committed.
``FormalResult`` (in ``domain.attempt``) — what UC-04/UC-05 decided about the submitted state.

A formal auto-submission is exactly "take the first, make it the second". Nothing in UC-09 blurs the
three, because an assessor asking "were those really the answers they gave?" needs each of them to
mean one thing.

SUBMISSION MUST BE IDEMPOTENT ON THE UC-03 SIDE TOO
---------------------------------------------------
``submit_attempt`` takes an ``idempotency_key`` derived from the attempt id. UC-03's own submission
record is unique per attempt, so a retried submit converges there as well as here. UC-09 does not
rely on that — its own state machine already prevents a second submission — but a boundary that can
be crossed twice should be idempotent on both sides of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.modules.formal_assessment.domain.enums import FormalSubmissionReason

#: UC-03's attempt statuses, as UC-09 reads them. Not redefined as an enum: they belong to UC-03,
#: and
#: a copy here would be a second declaration of someone else's vocabulary. Compared as strings.
ATTEMPT_STATUS_ACTIVE = "ACTIVE"
ATTEMPT_STATUS_SUBMISSION_PENDING = "SUBMISSION_PENDING"
ATTEMPT_STATUS_SUBMITTED = "SUBMITTED"

#: Statuses in which UC-03 still considers the attempt open.
OPEN_UPSTREAM_STATUSES: frozenset[str] = frozenset(
    {ATTEMPT_STATUS_ACTIVE, ATTEMPT_STATUS_SUBMISSION_PENDING}
)


@dataclass(frozen=True, slots=True)
class AttemptContext:
    """One attempt as recorded by UC-03."""

    attempt_id: str
    learner_id: str
    course_id: str
    quiz_id: str
    status: str
    attempt_number: int | None = None
    configuration_version_id: str | None = None
    started_at: str | None = None
    submitted_at: str | None = None
    expires_at: str | None = None
    total_questions: int | None = None
    answered_questions: int | None = None
    #: UC-03's submission reason, when it has one.
    submission_reason: str | None = None

    @property
    def submitted(self) -> bool:
        return self.status == ATTEMPT_STATUS_SUBMITTED

    @property
    def open(self) -> bool:
        return self.status in OPEN_UPSTREAM_STATUSES

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "learner_id": self.learner_id,
            "course_id": self.course_id,
            "quiz_id": self.quiz_id,
            "status": self.status,
            "attempt_number": self.attempt_number,
            "configuration_version_id": self.configuration_version_id,
            "started_at": self.started_at,
            "submitted_at": self.submitted_at,
            "expires_at": self.expires_at,
            "total_questions": self.total_questions,
            "answered_questions": self.answered_questions,
            "submission_reason": self.submission_reason,
        }


@dataclass(frozen=True, slots=True)
class CreateAttemptRequest:
    """Everything UC-03 needs to deliver a formal attempt, and nothing it does not.

    ``formal_assessment`` is passed so UC-03 can record the delivery as formal on its own attempt
    row — one boolean on an existing record, no new UC-03 concept. ``retake_of_attempt_id`` is
    present so a formal retake asked for by UC-08 keeps its lineage.
    """

    learner_id: str
    course_id: str
    quiz_id: str
    formal_assessment: bool = True
    retake_of_attempt_id: str | None = None
    #: Derived from the formal attempt, so a retried create converges upstream too.
    idempotency_key: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "learner_id": self.learner_id,
            "course_id": self.course_id,
            "quiz_id": self.quiz_id,
            "formal_assessment": self.formal_assessment,
            "retake_of_attempt_id": self.retake_of_attempt_id,
            "idempotency_key": self.idempotency_key,
        }


@dataclass(frozen=True, slots=True)
class AutosavedState:
    """The latest valid autosaved state of an in-flight attempt (§6).

    "Valid" is UC-03's word: its autosave endpoint validates every answer before writing, all or
    nothing, so anything readable here has already been accepted. UC-09 does not re-validate answers
    — it could only get that wrong differently.

    ``saved_at`` is the instant the state was persisted, which is what the disconnect record quotes
    so an assessor can see how much of the attempt was captured.
    """

    attempt_id: str
    saved_at: str | None = None
    answered_questions: int = 0
    total_questions: int | None = None
    #: The answered question ids, in delivery order. Enough for UC-09 to describe the state without
    #: holding the answers themselves.
    answered_question_ids: tuple[str, ...] = field(default_factory=tuple)
    #: True when UC-03 has some saved state for this attempt at all.
    exists: bool = True

    @property
    def complete(self) -> bool:
        """Whether every delivered question has an answer.

        Unknown totals count as incomplete: reporting "complete" on the strength of a missing number
        would be a claim UC-09 cannot support.
        """
        if self.total_questions is None:
            return False
        return self.answered_questions >= self.total_questions

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "exists": self.exists,
            "saved_at": self.saved_at,
            "answered_questions": self.answered_questions,
            "total_questions": self.total_questions,
            "complete": self.complete,
        }


@dataclass(frozen=True, slots=True)
class AnswerSubmission:
    """One answer being autosaved. The response payload is opaque to UC-09.

    Opaque on purpose: the five question types, their answer shapes and their validation rules are
    UC-02's and UC-03's. UC-09 checks who is saving and whether they may — the session, the state,
    the ownership — and hands the payload through unread. A formal assessment is not a reason to
    have a second answer validator.
    """

    question_id: str
    response: Any = None


@dataclass(frozen=True, slots=True)
class AutosaveResult:
    """What UC-03 reported after an autosave."""

    attempt_id: str
    saved_count: int = 0
    changed_count: int = 0
    persisted_at: str | None = None
    answered_questions: int | None = None
    total_questions: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "saved_count": self.saved_count,
            "changed_count": self.changed_count,
            "persisted_at": self.persisted_at,
            "answered_questions": self.answered_questions,
            "total_questions": self.total_questions,
        }


@dataclass(frozen=True, slots=True)
class SubmissionRequest:
    """A commit of an attempt, learner-confirmed or automatic."""

    attempt_id: str
    reason: FormalSubmissionReason
    idempotency_key: str
    #: For an auto-submission: the instant the disconnect was detected, so UC-03 can record why the
    #: submission happened when it did.
    disconnect_detected_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "reason": self.reason.value,
            "idempotency_key": self.idempotency_key,
            "disconnect_detected_at": self.disconnect_detected_at,
        }


@dataclass(frozen=True, slots=True)
class SubmittedState:
    """The committed, immutable state of the attempt (§6)."""

    attempt_id: str
    submitted_at: str
    #: UC-03's own reason string. Recorded verbatim; UC-09 does not translate it.
    submission_reason: str | None = None
    answered_questions: int | None = None
    total_questions: int | None = None
    #: True when this call committed the attempt, False when it was already submitted and this is a
    #: replay. The difference is what lets a duplicate submit return the existing submission rather
    #: than creating a second one.
    already_submitted: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "submitted_at": self.submitted_at,
            "submission_reason": self.submission_reason,
            "answered_questions": self.answered_questions,
            "total_questions": self.total_questions,
            "already_submitted": self.already_submitted,
        }


@dataclass(frozen=True, slots=True)
class QuestionResponse:
    """One delivered question and what the learner answered, for the assessor's review (§10)."""

    question_id: str
    position: int | None = None
    question_type: str | None = None
    prompt: str | None = None
    answered: bool = False
    #: The learner's answer as UC-03 renders it. Passed to the assessor unchanged.
    response: Any = None
    #: UC-04's verdict, when the caller also read the score.
    correct: bool | None = None
    marks_awarded: float | None = None
    marks_available: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "position": self.position,
            "question_type": self.question_type,
            "prompt": self.prompt,
            "answered": self.answered,
            "response": self.response,
            "correct": self.correct,
            "marks_awarded": self.marks_awarded,
            "marks_available": self.marks_available,
        }


@runtime_checkable
class AttemptProvider(Protocol):
    """UC-03, as UC-09 uses it."""

    async def get_attempt(self, attempt_id: str) -> AttemptContext | None: ...

    async def find_open_attempt(self, learner_id: str, quiz_id: str) -> AttemptContext | None:
        """The learner's open attempt at this quiz, if any.

        UC-03 permits one. UC-09 reads it before starting so a learner with an attempt already
        running is told that, rather than being refused by an upstream constraint after a formal
        record has been built.
        """
        ...

    async def create_attempt(self, request: CreateAttemptRequest) -> AttemptContext:
        """Deliver the attempt. Must be all-or-nothing.

        A partially created attempt would leave UC-09's formal record and UC-03's attempt
        disagreeing about whether the learner is sitting an assessment. Transient failures should
        raise ``ProviderUnavailableError``; a refusal by UC-03's own rules should raise the matching
        UC-09 error so the caller sees one taxonomy.
        """
        ...

    async def get_latest_autosaved_state(self, attempt_id: str) -> AutosavedState | None:
        """The latest valid autosaved state (§6). ``None`` when UC-03 has no state for the attempt.
        """
        ...

    async def save_answers(
        self, attempt_id: str, answers: tuple[AnswerSubmission, ...]
    ) -> AutosaveResult:
        """UC-03's existing autosave, all-or-nothing (§6)."""
        ...

    async def submit_attempt(self, request: SubmissionRequest) -> SubmittedState:
        """Commit the attempt. Must be idempotent on ``request.idempotency_key``.

        An implementation that finds the attempt already submitted must return the existing
        submission with ``already_submitted=True`` rather than raising — that is what turns a
        duplicate submit into a replay instead of an error the learner has to interpret.
        """
        ...

    async def get_attempt_responses(self, attempt_id: str) -> tuple[QuestionResponse, ...]:
        """Delivered questions and the learner's answers, for the assessor's review payload (§10).
        """
        ...
