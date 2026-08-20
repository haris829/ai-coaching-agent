"""Test doubles for every boundary UC-09 depends on.

``FakeAttemptModule`` is the interesting one. It is a stand-in for UC-03 that behaves like UC-03 at the points
UC-09 actually touches it:

* one open attempt per learner per quiz, refused rather than silently allowed;
* autosave is all-or-nothing and updates a *mutable* saved state;
* submission is idempotent and reports ``already_submitted`` on a repeat;
* a submitted attempt refuses further autosaves.

That matters because most of what UC-09 has to get right is what it does when a boundary behaves like a real
system rather than like a helpful mock: an autosave that lands *after* a disconnect, a submission that was
already made, an upstream attempt whose status disagrees with the formal record.

Every double can be told to fail, because the other half of what UC-09 has to get right is what it does when a
port does not answer: an unreachable profile source must not confirm an identity, an unreachable certificate
workflow must not lose an approval, and an unavailable queue must not lose a pending review.

None of these is a simulation of an upstream module's full behaviour. Each implements the narrow port UC-09
declares.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from app.core.errors import ProviderUnavailableError
from app.modules.formal_assessment.domain.errors import (
    AttemptDeliveryUnavailableError,
    CertificateWorkflowFailedError,
    LearnerProfileUnavailableError,
)
from app.modules.formal_assessment.domain.identity import LearnerIdentityProfile
from app.modules.formal_assessment.integration.assessors import Assessor
from app.modules.formal_assessment.integration.notification import (
    LearnerNotification,
    NotificationOutcome,
)
from app.modules.formal_assessment.integration.results import (
    AttemptScore,
    CertificateAcknowledgement,
    CertificateTrigger,
    PassFailResult,
)
from app.modules.formal_assessment.integration.uc01 import FormalAssessmentPolicy
from app.modules.formal_assessment.integration.uc03 import (
    AnswerSubmission,
    AttemptContext,
    AutosavedState,
    AutosaveResult,
    CreateAttemptRequest,
    QuestionResponse,
    SubmissionRequest,
    SubmittedState,
)

DEFAULT_LEARNER = "learner-alice"
DEFAULT_COURSE = "course-1"
DEFAULT_QUIZ = "quiz-formal-1"
DEFAULT_ASSESSOR = "assessor-jo"
DEFAULT_NAME = "John Smith"
DEFAULT_EMAIL = "john.smith@example.com"

#: A client_request_id long enough to satisfy the minimum in domain.idempotency.
CLIENT_REQUEST_ID = "client-request-token-0001"


# ---------------------------------------------------------------------------
# UC-01 — quiz configuration / formal assessment policy
# ---------------------------------------------------------------------------


class FakePolicyProvider:
    """Which quizzes are formal assessments, and whether they can be sat."""

    def __init__(self) -> None:
        self.policies: dict[str, FormalAssessmentPolicy] = {}
        #: quiz id per attempt id, so an attempt can be resolved to its policy.
        self.attempt_quizzes: dict[str, str] = {}
        self.fail_with: Exception | None = None

    def publish(
        self,
        quiz_id: str = DEFAULT_QUIZ,
        *,
        course_id: str = DEFAULT_COURSE,
        formal: bool = True,
        available: bool = True,
        requires_human_review: bool = True,
        requires_assessor_approval: bool = True,
    ) -> FormalAssessmentPolicy:
        policy = FormalAssessmentPolicy(
            quiz_id=quiz_id,
            course_id=course_id,
            is_formal_assessment=formal,
            available=available,
            unavailable_reason=None if available else "WITHDRAWN",
            requires_human_review=requires_human_review,
            requires_assessor_approval=requires_assessor_approval,
            course_name="Fire Safety",
            quiz_title="Fire Safety — Formal Assessment",
            configuration_version_id="cfg-v1",
        )
        self.policies[quiz_id] = policy
        return policy

    def withdraw(self, quiz_id: str = DEFAULT_QUIZ) -> None:
        current = self.policies[quiz_id]
        self.policies[quiz_id] = replace(current, available=False, unavailable_reason="WITHDRAWN")

    def link_attempt(self, attempt_id: str, quiz_id: str = DEFAULT_QUIZ) -> None:
        self.attempt_quizzes[attempt_id] = quiz_id

    async def get_policy(self, quiz_id: str) -> FormalAssessmentPolicy | None:
        self._maybe_fail()
        return self.policies.get(quiz_id)

    async def get_policy_for_attempt(self, attempt_id: str) -> FormalAssessmentPolicy | None:
        self._maybe_fail()
        quiz_id = self.attempt_quizzes.get(attempt_id)
        return self.policies.get(quiz_id) if quiz_id else None

    def _maybe_fail(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with


# ---------------------------------------------------------------------------
# UC-03 — attempt delivery, autosave, submission
# ---------------------------------------------------------------------------


@dataclass
class _StoredAttempt:
    context: AttemptContext
    #: The mutable autosaved state: question id -> response.
    saved: dict[str, Any] = field(default_factory=dict)
    saved_at: str | None = None
    submitted: bool = False
    submitted_at: str | None = None
    submission_reason: str | None = None
    #: The frozen answer set as it was at submission — what makes "submitted state" distinguishable from
    #: "latest autosaved state" in assertions.
    submitted_snapshot: dict[str, Any] = field(default_factory=dict)


class FakeAttemptModule:
    """UC-03, as UC-09 uses it."""

    def __init__(self, *, total_questions: int = 3, clock_iso: str = "2026-03-01T09:00:00.000Z") -> None:
        self.attempts: dict[str, _StoredAttempt] = {}
        self.total_questions = total_questions
        self.now = clock_iso
        self._counter = 0
        #: Raised by ``create_attempt`` when set.
        self.fail_create: Exception | None = None
        self.fail_submit: Exception | None = None
        self.fail_autosave: Exception | None = None
        self.fail_autosaved_state: Exception | None = None
        #: Every submission request received, so a test can prove there was exactly one.
        self.submissions: list[SubmissionRequest] = []
        self.created: list[CreateAttemptRequest] = []
        self.question_ids = tuple(f"q{index}" for index in range(1, total_questions + 1))

    # -- helpers used by tests ------------------------------------------

    def autosave_now(self, attempt_id: str, *, answered: int, saved_at: str | None = None) -> None:
        """Fill in ``answered`` answers directly, for a test that wants a state without HTTP calls."""
        stored = self.attempts[attempt_id]
        for question_id in self.question_ids[:answered]:
            stored.saved[question_id] = {"selectedOptionId": f"{question_id}-o1"}
        stored.saved_at = saved_at or self.now

    def force_status(self, attempt_id: str, status: str) -> None:
        """Move the upstream attempt's status without going through UC-09 — to test reconciliation."""
        stored = self.attempts[attempt_id]
        stored.context = replace(stored.context, status=status)

    def snapshot(self, attempt_id: str) -> dict[str, Any]:
        stored = self.attempts[attempt_id]
        return {
            "status": stored.context.status,
            "answered": len(stored.saved),
            "submitted": stored.submitted,
            "submission_reason": stored.submission_reason,
            "submitted_answers": len(stored.submitted_snapshot),
        }

    # -- the port ------------------------------------------------------

    async def get_attempt(self, attempt_id: str) -> AttemptContext | None:
        stored = self.attempts.get(attempt_id)
        if stored is None:
            return None
        return replace(
            stored.context,
            answered_questions=len(stored.saved),
            submitted_at=stored.submitted_at,
            submission_reason=stored.submission_reason,
        )

    async def find_open_attempt(self, learner_id: str, quiz_id: str) -> AttemptContext | None:
        for stored in self.attempts.values():
            if (
                stored.context.learner_id == learner_id
                and stored.context.quiz_id == quiz_id
                and not stored.submitted
            ):
                return stored.context
        return None

    async def create_attempt(self, request: CreateAttemptRequest) -> AttemptContext:
        if self.fail_create is not None:
            raise self.fail_create
        self.created.append(request)
        # UC-03's own rule: one open attempt per learner per quiz.
        existing = await self.find_open_attempt(request.learner_id, request.quiz_id)
        if existing is not None:
            raise AttemptDeliveryUnavailableError("An attempt is already in progress for this quiz.")

        self._counter += 1
        attempt_id = f"attempt-{self._counter:04d}"
        context = AttemptContext(
            attempt_id=attempt_id,
            learner_id=request.learner_id,
            course_id=request.course_id,
            quiz_id=request.quiz_id,
            status="ACTIVE",
            attempt_number=self._counter,
            configuration_version_id="cfg-v1",
            started_at=self.now,
            expires_at="2026-03-01T10:00:00.000Z",
            total_questions=self.total_questions,
            answered_questions=0,
        )
        self.attempts[attempt_id] = _StoredAttempt(context=context)
        return context

    async def get_latest_autosaved_state(self, attempt_id: str) -> AutosavedState | None:
        if self.fail_autosaved_state is not None:
            raise self.fail_autosaved_state
        stored = self.attempts.get(attempt_id)
        if stored is None:
            return None
        return AutosavedState(
            attempt_id=attempt_id,
            saved_at=stored.saved_at,
            answered_questions=len(stored.saved),
            total_questions=stored.context.total_questions,
            answered_question_ids=tuple(stored.saved),
            exists=bool(stored.saved),
        )

    async def save_answers(
        self, attempt_id: str, answers: tuple[AnswerSubmission, ...]
    ) -> AutosaveResult:
        if self.fail_autosave is not None:
            raise self.fail_autosave
        stored = self.attempts[attempt_id]
        if stored.submitted:
            # UC-03 refuses to change a submitted attempt. UC-09 should never get here — but if it did, the
            # double must not quietly accept it.
            raise ProviderUnavailableError("The attempt has already been submitted.")
        changed = 0
        for answer in answers:
            if stored.saved.get(answer.question_id) != answer.response:
                changed += 1
            stored.saved[answer.question_id] = answer.response
        stored.saved_at = self.now
        return AutosaveResult(
            attempt_id=attempt_id,
            saved_count=len(answers),
            changed_count=changed,
            persisted_at=self.now,
            answered_questions=len(stored.saved),
            total_questions=stored.context.total_questions,
        )

    async def submit_attempt(self, request: SubmissionRequest) -> SubmittedState:
        if self.fail_submit is not None:
            raise self.fail_submit
        self.submissions.append(request)
        stored = self.attempts[request.attempt_id]
        if stored.submitted:
            # Idempotent, as the port requires: report the existing submission rather than making a second.
            return SubmittedState(
                attempt_id=request.attempt_id,
                submitted_at=stored.submitted_at or self.now,
                submission_reason=stored.submission_reason,
                answered_questions=len(stored.submitted_snapshot),
                total_questions=stored.context.total_questions,
                already_submitted=True,
            )
        stored.submitted = True
        stored.submitted_at = self.now
        stored.submission_reason = request.reason.value
        stored.submitted_snapshot = dict(stored.saved)
        stored.context = replace(stored.context, status="SUBMITTED")
        return SubmittedState(
            attempt_id=request.attempt_id,
            submitted_at=stored.submitted_at,
            submission_reason=stored.submission_reason,
            answered_questions=len(stored.submitted_snapshot),
            total_questions=stored.context.total_questions,
            already_submitted=False,
        )

    async def get_attempt_responses(self, attempt_id: str) -> tuple[QuestionResponse, ...]:
        stored = self.attempts.get(attempt_id)
        if stored is None:
            return ()
        source = stored.submitted_snapshot if stored.submitted else stored.saved
        return tuple(
            QuestionResponse(
                question_id=question_id,
                position=index + 1,
                question_type="SINGLE_CHOICE",
                prompt=f"Question {index + 1}",
                answered=question_id in source,
                response=source.get(question_id),
                correct=True,
                marks_awarded=1.0,
                marks_available=1.0,
            )
            for index, question_id in enumerate(self.question_ids)
        )


# ---------------------------------------------------------------------------
# Learner profiles — the identity source
# ---------------------------------------------------------------------------


class FakeProfileProvider:
    def __init__(self) -> None:
        self.profiles: dict[str, LearnerIdentityProfile] = {}
        self.fail_with: Exception | None = None

    def add(
        self,
        learner_id: str = DEFAULT_LEARNER,
        *,
        full_name: str = DEFAULT_NAME,
        email: str = DEFAULT_EMAIL,
        email_confirmed: bool = True,
    ) -> LearnerIdentityProfile:
        profile = LearnerIdentityProfile(
            learner_id=learner_id,
            full_name=full_name,
            email=email,
            email_confirmed=email_confirmed,
        )
        self.profiles[learner_id] = profile
        return profile

    def unconfirm_email(self, learner_id: str = DEFAULT_LEARNER) -> None:
        self.profiles[learner_id] = replace(self.profiles[learner_id], email_confirmed=False)

    def break_provider(self, message: str = "profile source down") -> None:
        self.fail_with = LearnerProfileUnavailableError(message)

    async def get_profile(self, learner_id: str) -> LearnerIdentityProfile | None:
        if self.fail_with is not None:
            raise self.fail_with
        return self.profiles.get(learner_id)


# ---------------------------------------------------------------------------
# UC-04 / UC-05 — score, pass/fail, certificate workflow
# ---------------------------------------------------------------------------


class FakeScoringProvider:
    def __init__(self) -> None:
        self.scores: dict[str, AttemptScore] = {}
        self.fail_with: Exception | None = None

    def record(
        self,
        attempt_id: str,
        *,
        status: str = "CONFIRMED",
        percentage: float = 90.0,
        total_marks: float = 9.0,
        maximum_marks: float = 10.0,
    ) -> None:
        self.scores[attempt_id] = AttemptScore(
            attempt_id=attempt_id,
            status=status,
            total_marks=total_marks,
            maximum_marks=maximum_marks,
            percentage=percentage,
            scored_at="2026-03-01T09:35:00.000Z",
        )

    async def get_score(self, attempt_id: str) -> AttemptScore | None:
        if self.fail_with is not None:
            raise self.fail_with
        return self.scores.get(attempt_id)


class FakePassFailProvider:
    def __init__(self) -> None:
        self.results: dict[str, PassFailResult] = {}
        self.fail_with: Exception | None = None

    def record(
        self,
        attempt_id: str,
        *,
        status: str = "PASSED",
        percentage: float = 90.0,
        pass_mark: float = 80.0,
    ) -> None:
        self.results[attempt_id] = PassFailResult(
            attempt_id=attempt_id,
            status=status,
            result_id=f"result-{attempt_id}",
            percentage=percentage,
            pass_mark=pass_mark,
            determined_at="2026-03-01T09:36:00.000Z",
        )

    async def get_result(self, attempt_id: str) -> PassFailResult | None:
        if self.fail_with is not None:
            raise self.fail_with
        return self.results.get(attempt_id)


class FakeCertificateWorkflow:
    """UC-05's certificate workflow, idempotent on the trigger key."""

    def __init__(self) -> None:
        self.triggers: list[CertificateTrigger] = []
        #: key -> reference, so a repeated trigger reports the same certificate.
        self.issued: dict[str, str] = {}
        self.fail_with: Exception | None = None
        self.accept: bool = True

    def break_workflow(self, message: str = "certificate workflow down") -> None:
        self.fail_with = CertificateWorkflowFailedError(message)

    def repair(self) -> None:
        self.fail_with = None

    async def trigger(self, request: CertificateTrigger) -> CertificateAcknowledgement:
        if self.fail_with is not None:
            raise self.fail_with
        self.triggers.append(request)
        if not self.accept:
            return CertificateAcknowledgement(accepted=False, status="REJECTED")
        existing = self.issued.get(request.idempotency_key)
        if existing is not None:
            return CertificateAcknowledgement(
                accepted=True,
                reference=existing,
                status="ACCEPTED",
                already_requested=True,
            )
        reference = f"cert-{len(self.issued) + 1:04d}"
        self.issued[request.idempotency_key] = reference
        return CertificateAcknowledgement(accepted=True, reference=reference, status="ACCEPTED")

    @property
    def certificate_count(self) -> int:
        """Distinct certificates issued. The number a duplicate-trigger test asserts on."""
        return len(self.issued)


# ---------------------------------------------------------------------------
# Assessors
# ---------------------------------------------------------------------------


class FakeAssessorDirectory:
    """The assessor register. Authorises nobody until a test says otherwise."""

    def __init__(self) -> None:
        self.assessors: dict[str, Assessor] = {}
        self.fail_with: Exception | None = None

    def add(
        self,
        assessor_id: str = DEFAULT_ASSESSOR,
        *,
        courses: tuple[str, ...] = (DEFAULT_COURSE,),
        all_courses: bool = False,
        active: bool = True,
    ) -> Assessor:
        assessor = Assessor(
            assessor_id=assessor_id,
            active=active,
            authorised_course_ids=frozenset(courses),
            all_courses=all_courses,
            display_name=assessor_id.replace("-", " ").title(),
            role="Assessor",
        )
        self.assessors[assessor_id] = assessor
        return assessor

    def deactivate(self, assessor_id: str = DEFAULT_ASSESSOR) -> None:
        self.assessors[assessor_id] = replace(self.assessors[assessor_id], active=False)

    async def get_assessor(self, assessor_id: str) -> Assessor | None:
        if self.fail_with is not None:
            raise self.fail_with
        return self.assessors.get(assessor_id)

    async def list_authorised_course_ids(self, assessor_id: str) -> tuple[str, ...]:
        assessor = self.assessors.get(assessor_id)
        if assessor is None:
            return ()
        return tuple(sorted(assessor.authorised_course_ids))


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[LearnerNotification] = []
        self.fail_with: Exception | None = None
        self.refuse: bool = False

    def break_notifier(self, message: str = "notification channel down") -> None:
        self.fail_with = RuntimeError(message)

    async def notify(self, notification: LearnerNotification) -> NotificationOutcome:
        if self.fail_with is not None:
            raise self.fail_with
        if self.refuse:
            return NotificationOutcome(delivered=False, error="REFUSED")
        self.sent.append(notification)
        return NotificationOutcome(delivered=True, reference=f"note-{len(self.sent):04d}")

    def events(self) -> list[str]:
        return [item.event.value for item in self.sent]


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class RecordingAuditLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def record(self, event: str, /, **fields: Any) -> None:
        self.events.append((event, fields))

    def codes(self) -> list[str]:
        return [event for event, _ in self.events]

    def fields_for(self, event: str) -> list[dict[str, Any]]:
        return [fields for name, fields in self.events if name == event]

    def count(self, event: str) -> int:
        return sum(1 for name, _ in self.events if name == event)


# ---------------------------------------------------------------------------
# Repositories that fail, for the concurrency and failure paths
# ---------------------------------------------------------------------------


class FailingOnceRepository:
    """Wraps a repository and makes the first call to one method raise.

    Used to drive the compare-and-set recovery paths deterministically: the service must re-read and converge
    rather than losing an operation or performing it twice.
    """

    def __init__(self, wrapped: Any, method: str, error: Exception) -> None:
        self._wrapped = wrapped
        self._method = method
        self._error: Exception | None = error

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._wrapped, name)
        if name != self._method:
            return attribute

        async def _guarded(*args: Any, **kwargs: Any) -> Any:
            if self._error is not None:
                error, self._error = self._error, None
                raise error
            return await attribute(*args, **kwargs)

        return _guarded
