"""UC-09's ports, bound to the real capabilities in the merged application.

One file for six adapters, because each is small and they are read together: they are the whole of
"where does UC-09 get its facts from?".

    FormalAssessmentPolicyProvider  -> UC-01, through the version LOCKED to the attempt
    ScoringResultProvider           -> UC-04's qr_attempt_results
    PassFailResultProvider          -> UC-05's qg_attempt_outcomes
    CertificateWorkflow             -> UC-05's certificate service
    LearnerProfileProvider          -> the platform user directory (qa_users)
    AssessorDirectory               -> the platform user directory, assessor role

**The policy is read from the version the attempt locked, never from what is active now.** That is
the single most important line in this file. A quiz made formal tomorrow must not retroactively gate
a certificate somebody already earned; a quiz made informal tomorrow must not release one still
waiting on an assessor. UC-03 froze the three flags onto the attempt's configuration snapshot for
exactly this, so :meth:`FormalPolicyAdapter.get_policy_for_attempt` reads the snapshot and not
``qc_configuration_versions``.

**Nothing here writes to an attempt, an answer, a score, a result or a certificate.** The scoring
and pass/fail adapters are ``select`` statements. The certificate adapter asks UC-05 to run *its
own* workflow; it does not create a certificate row, and UC-09 has no model that could.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.async_db import offload
from app.core.logging import get_logger
from app.core.time import iso_or_none
from app.modules.attempt_delivery.models import QuizAttempt
from app.modules.certification.models import AttemptOutcome
from app.modules.formal_assessment.domain.errors import (
    AttemptDeliveryUnavailableError,
    CertificateWorkflowFailedError,
    LearnerProfileUnavailableError,
    ScoringUnavailableError,
)
from app.modules.formal_assessment.integration.assessors import Assessor
from app.modules.formal_assessment.integration.profiles import LearnerIdentityProfile
from app.modules.formal_assessment.integration.results import (
    AttemptScore,
    CertificateAcknowledgement,
    CertificateTrigger,
    PassFailResult,
)
from app.modules.formal_assessment.integration.uc01 import FormalAssessmentPolicy
from app.modules.identity.models import User
from app.modules.identity.principal import Role
from app.modules.quiz_configuration.models import ConfigurationVersion, Quiz
from app.modules.scoring.models import AttemptResult

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# UC-01 — is this quiz a formal assessment?
# ---------------------------------------------------------------------------


class FormalPolicyAdapter:
    """``FormalAssessmentPolicyProvider`` over UC-01."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    async def get_policy(self, quiz_id: str) -> FormalAssessmentPolicy | None:
        return await offload(self._get_policy, quiz_id)

    async def get_policy_for_attempt(self, attempt_id: str) -> FormalAssessmentPolicy | None:
        return await offload(self._get_policy_for_attempt, attempt_id)

    # ---- synchronous bodies ------------------------------------------------

    @staticmethod
    def _as_int(value: str) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _get_policy(self, quiz_id: str) -> FormalAssessmentPolicy | None:
        """The policy for a quiz about to be sat — read from the version active *now*.

        Correct here and only here: the learner has not started yet, so the version they are
        about to lock is the active one. Every later question about the same sitting goes through
        :meth:`get_policy_for_attempt`.
        """
        numeric = self._as_int(quiz_id)
        if numeric is None:
            return None
        try:
            quiz = self._session.get(Quiz, numeric)
            if quiz is None:
                return None
            version = (
                self._session.get(ConfigurationVersion, quiz.active_configuration_version_id)
                if quiz.active_configuration_version_id is not None
                else None
            )
        except SQLAlchemyError as exc:
            raise AttemptDeliveryUnavailableError() from exc

        if version is None:
            # A quiz with no active version exists but cannot be sat. Reported as unavailable
            # with a reason rather than as "not a formal assessment", which would quietly skip
            # the gate on a quiz nobody had finished configuring.
            return FormalAssessmentPolicy(
                quiz_id=str(quiz.id),
                course_id=str(quiz.course_id),
                is_formal_assessment=False,
                available=False,
                unavailable_reason="QUIZ_NOT_CONFIGURED",
                requires_human_review=True,
                requires_assessor_approval=True,
                quiz_title=quiz.title,
                course_name=quiz.course.title if quiz.course is not None else None,
            )

        return FormalAssessmentPolicy(
            quiz_id=str(quiz.id),
            course_id=str(quiz.course_id),
            is_formal_assessment=bool(version.is_formal_assessment),
            available=True,
            unavailable_reason=None,
            requires_human_review=bool(version.requires_human_review),
            requires_assessor_approval=bool(version.requires_assessor_approval),
            quiz_title=quiz.title,
            course_name=quiz.course.title if quiz.course is not None else None,
            configuration_version_id=str(version.id),
        )

    def _get_policy_for_attempt(self, attempt_id: str) -> FormalAssessmentPolicy | None:
        """The policy for an attempt that already exists — from the version it **locked**.

        Read out of the configuration snapshot UC-03 froze onto the attempt, not out of
        ``qc_configuration_versions``. See the module docstring: this is what stops a
        configuration change from retroactively releasing or withholding a certificate.
        """
        try:
            attempt = self._session.scalar(
                select(QuizAttempt).where(QuizAttempt.id == attempt_id)
            )
        except SQLAlchemyError as exc:
            raise AttemptDeliveryUnavailableError() from exc
        if attempt is None:
            return None

        snapshot: dict[str, Any] = attempt.configuration_snapshot or {}
        extra: dict[str, Any] = snapshot.get("extra") or {}

        # The attempt row's own flag is authoritative for "was this sitting supervised": UC-09 set
        # it when the attempt was created. The snapshot supplies the two review flags, which UC-03
        # never had an opinion about. Falling back to the snapshot flag keeps an attempt created
        # before UC-09 existed readable.
        is_formal = bool(
            attempt.is_formal_assessment or extra.get("isFormalAssessment", False)
        )
        return FormalAssessmentPolicy(
            quiz_id=attempt.quiz_id,
            course_id=attempt.course_id,
            is_formal_assessment=is_formal,
            available=True,
            unavailable_reason=None,
            # Defaulting to the stricter answer when the snapshot predates the flags: an
            # unlabelled formal assessment withholds a certificate rather than issuing one.
            requires_human_review=bool(extra.get("requiresHumanReview", True)),
            requires_assessor_approval=bool(extra.get("requiresAssessorApproval", True)),
            quiz_title=extra.get("quizTitle"),
            course_name=extra.get("courseTitle"),
            configuration_version_id=attempt.configuration_version_id,
        )


# ---------------------------------------------------------------------------
# UC-04 / UC-05 — the result UC-09 waits for
# ---------------------------------------------------------------------------


class FormalScoringAdapter:
    """``ScoringResultProvider`` over UC-04's ``qr_attempt_results``."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    async def get_score(self, attempt_id: str) -> AttemptScore | None:
        return await offload(self._get_score, attempt_id)

    def _get_score(self, attempt_id: str) -> AttemptScore | None:
        try:
            row = self._session.scalar(
                select(AttemptResult).where(AttemptResult.attempt_id == attempt_id)
            )
        except SQLAlchemyError as exc:
            raise ScoringUnavailableError() from exc
        if row is None:
            return None
        # UC-04's status string is passed through: UC-09 compares it against CONFIRMED and defers
        # on anything else, so translating it here would only create a second vocabulary.
        confirmed = row.status == "SCORED"
        return AttemptScore(
            attempt_id=attempt_id,
            status="CONFIRMED" if confirmed else "PENDING",
            total_marks=row.total_marks if confirmed else None,
            maximum_marks=row.maximum_marks if confirmed else None,
            percentage=row.percentage if confirmed else None,
            scored_at=iso_or_none(row.scored_at) if confirmed else None,
        )


class FormalPassFailAdapter:
    """``PassFailResultProvider`` over UC-05's ``qg_attempt_outcomes``."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    async def get_result(self, attempt_id: str) -> PassFailResult | None:
        return await offload(self._get_result, attempt_id)

    def _get_result(self, attempt_id: str) -> PassFailResult | None:
        try:
            row = self._session.scalar(
                select(AttemptOutcome).where(AttemptOutcome.attempt_id == attempt_id)
            )
        except SQLAlchemyError as exc:
            raise ScoringUnavailableError() from exc
        if row is None:
            # No row means UC-05 has not decided yet, which defers. It is not PENDING: that is
            # UC-05's own state for a decision it made and could not complete, and inventing it
            # would put an attempt in a state UC-05 never assigned.
            return None
        return PassFailResult(
            attempt_id=attempt_id,
            status="PASSED" if row.outcome == "PASS" else "FAILED",
            result_id=row.result_id,
            percentage=row.percentage,
            pass_mark=row.pass_mark_percentage,
            determined_at=iso_or_none(row.determined_at),
        )


class FormalCertificateWorkflowAdapter:
    """``CertificateWorkflow`` over UC-05's certificate service.

    The direction matters. UC-05 asks UC-09 "may I generate?" through the certificate gate; this
    adapter is the other direction — "you may now, an assessor approved it." The two are not a
    cycle: one is a question about state, the other is an instruction that follows a human
    decision.

    UC-09 does not create a certificate row. It calls UC-05's own service, which owns the
    certificate lifecycle, the duplicate prevention and the CPD hand-off.
    """

    __slots__ = ("_session", "_certificates")

    def __init__(self, session: Session, certificates: Any) -> None:
        self._session = session
        #: UC-05's certificate service, built by the results chain's composition root.
        self._certificates = certificates

    async def trigger(self, request: CertificateTrigger) -> CertificateAcknowledgement:
        return await offload(self._trigger, request)

    def _trigger(self, request: CertificateTrigger) -> CertificateAcknowledgement:
        try:
            issued = self._certificates.issue_for_attempt(
                request.attempt_id,
                idempotency_key=request.idempotency_key,
                approved_by=request.approved_by,
            )
        except Exception as exc:  # noqa: BLE001 - translated to one retryable failure below
            # A certificate that could not be requested must leave the approval standing and the
            # learner un-notified, so the retry produces the certificate rather than a second
            # approval. Retryable, and nothing about the review changed.
            logger.warning(
                "formal.certificate_workflow_failed",
                extra={"attempt_id": request.attempt_id, "cause": str(exc)[:300]},
            )
            raise CertificateWorkflowFailedError(request.attempt_id) from exc

        return CertificateAcknowledgement(
            accepted=True,
            reference=getattr(issued, "certificate_reference", None) or getattr(issued, "id", None),
            status=getattr(issued, "status", None),
            already_requested=bool(getattr(issued, "already_requested", False)),
        )


# ---------------------------------------------------------------------------
# The platform directory — learners and assessors
# ---------------------------------------------------------------------------


class PlatformLearnerProfileAdapter:
    """``LearnerProfileProvider`` over the platform user directory.

    UC-09 never *sets* ``email_confirmed``; it reads the account's own flag. A module that could
    mark an email confirmed in order to let a learner past its own identity step would be checking
    nothing.
    """

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    async def get_profile(self, learner_id: str) -> LearnerIdentityProfile | None:
        return await offload(self._get_profile, learner_id)

    def _get_profile(self, learner_id: str) -> LearnerIdentityProfile | None:
        try:
            numeric = int(learner_id)
        except (TypeError, ValueError):
            return None
        try:
            user = self._session.get(User, numeric)
        except SQLAlchemyError as exc:
            # Unreadable must never degrade into "identity confirmed": the whole point of the
            # step is that somebody proved who they are.
            raise LearnerProfileUnavailableError() from exc
        if user is None or user.role != Role.LEARNER.value:
            return None
        return LearnerIdentityProfile(
            learner_id=str(user.id),
            full_name=user.display_name,
            email=user.email,
            # The placeholder directory has no confirmation column, so an account that exists is
            # treated as confirmed. The company IdP replaces this adapter and supplies the real
            # flag; the domain rule that reads it does not change.
            email_confirmed=True,
        )


class PlatformAssessorDirectory:
    """``AssessorDirectory`` over the platform user directory.

    **Authentication is not authorisation.** The identity seam establishes that a caller holds the
    assessor role; this directory answers whether *that* assessor may review *that* course, and
    UC-09 asks it on every review operation. Binding a real staff directory does not remove the
    need for this — it replaces the implementation behind it.

    The placeholder authorises an active assessor for all courses, because the merged system has
    no per-assessor course register yet. That is stated on :attr:`Assessor.all_courses` rather than
    hidden: an operator reading a review record can see the scope was "all courses", and the
    company's register replaces this one class.
    """

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    async def get_assessor(self, assessor_id: str) -> Assessor | None:
        return await offload(self._get_assessor, assessor_id)

    async def list_authorised_course_ids(self, assessor_id: str) -> tuple[str, ...]:
        return await offload(self._list_authorised_course_ids, assessor_id)

    def _get_assessor(self, assessor_id: str) -> Assessor | None:
        try:
            numeric = int(assessor_id)
        except (TypeError, ValueError):
            return None
        try:
            user = self._session.get(User, numeric)
        except SQLAlchemyError as exc:
            raise LearnerProfileUnavailableError() from exc
        if user is None or user.role != Role.ASSESSOR.value:
            # Not an assessor is not an error — it is "this person may not review", which is the
            # answer the review service needs.
            return None
        return Assessor(
            assessor_id=str(user.id),
            active=True,
            authorised_course_ids=frozenset(),
            all_courses=True,
            display_name=user.display_name,
            role=user.role,
        )

    def _list_authorised_course_ids(self, assessor_id: str) -> tuple[str, ...]:
        assessor = self._get_assessor(assessor_id)
        # An empty tuple means "authorised for nothing" and an unrestricted assessor is signalled
        # by all_courses, so the two cases stay distinguishable — the review repository treats an
        # empty scope as an empty queue rather than the whole queue.
        return () if assessor is None else tuple(sorted(assessor.authorised_course_ids))
