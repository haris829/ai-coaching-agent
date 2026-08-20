"""Pass/fail determination, certificate gating and CPD synchronisation. The ordering is the design,
and it is the same one UC-03 uses for submission: **make the durable decision first, then attempt
the outward-facing effects.** 1. read the confirmed score (UC-04) and the attempt's own rules
(UC-03) 2. determine PASS / FAIL and the remaining-attempt arithmetic 3. persist the outcome,
plus a PENDING certificate request (on a pass) and a PENDING CPD record 4. commit -- from here
the quiz result and the outcome cannot be damaged by anything downstream 5. attempt certificate
issuance, then CPD synchronisation, each isolated Because step 4 commits before step 5, a
certificate service outage leaves a pending certificate and a correct, visible pass; a CPD outage
leaves a pending CPD record and, again, a correct, visible pass. Neither can alter a score or an
outcome, which is what the requirement asks for and what the transaction boundary -- not a
comment -- guarantees. Idempotency throughout: * determining twice returns the same outcome; the
outcome table permits one row per attempt and a trigger rejects edits to it; * issuing twice
returns the same certificate -- by the ``PENDING``-guarded update, by the partial unique index
over issued certificates per learner and quiz, and by the certificate service's own idempotency
key; * synchronising twice updates one CPD row rather than logging a second activity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.time import Clock, to_iso
from app.modules.certification.domain import errors
from app.modules.certification.domain.enums import (
    CertificateStatus,
    CpdSyncStatus,
    Outcome,
)
from app.modules.certification.domain.gating import Gate, attempts_remaining, gate
from app.modules.certification.integration.attempt_delivery.port import (
    AttemptPolicy,
    AttemptPolicyPort,
)
from app.modules.certification.integration.certificate.port import (
    CertificateRequest,
    CertificateServicePort,
    TransientCertificateError,
)
from app.modules.certification.integration.cpd.port import (
    CpdSyncPort,
    CpdSyncRecord,
    TransientCpdError,
)
from app.modules.certification.integration.formal_gate import (
    CertificateGatePort,
    UnrestrictedCertificateGate,
)
from app.modules.certification.integration.scoring.port import ConfirmedResult, ScoreResultPort
from app.modules.certification.models import AttemptOutcome, Certificate, CpdRecord
from app.modules.certification.repositories import CertificationRepository

logger = get_logger(__name__)

#: Failure codes stored on a certificate or CPD row. Stable, so an operator can filter on them.
TRANSIENT_CERTIFICATE_FAILURE = "CERTIFICATE_SERVICE_UNAVAILABLE"
PERMANENT_CERTIFICATE_FAILURE = "CERTIFICATE_SERVICE_REJECTED"
DUPLICATE_CERTIFICATE_PREVENTED = "CERTIFICATE_ALREADY_ISSUED"
TRANSIENT_CPD_FAILURE = "CPD_SERVICE_UNAVAILABLE"
PERMANENT_CPD_FAILURE = "CPD_SERVICE_REJECTED"


@dataclass(frozen=True, slots=True)
class OutcomeView:
    """Everything one attempt's gating amounts to, assembled for a caller."""

    outcome: AttemptOutcome
    certificate: Certificate | None
    cpd_record: CpdRecord | None
    #: Recomputed at read time from UC-03's live attempt count, unlike the audit copy on the row.
    attempts_used: int
    attempts_remaining: int | None
    max_attempts: int | None
    #: True when this call created the outcome rather than reading an existing one.
    created: bool = False

    @property
    def passed(self) -> bool:
        return self.outcome.outcome == str(Outcome.PASS)


class CertificationService:
    """Determine pass/fail, gate the certificate, and keep CPD in step."""

    __slots__ = (
        "_session",
        "_repository",
        "_results",
        "_attempts",
        "_certificates",
        "_cpd",
        "_clock",
        "_formal_gate",
    )

    def __init__(
        self,
        *,
        session: Session,
        repository: CertificationRepository,
        results: ScoreResultPort,
        attempts: AttemptPolicyPort,
        certificates: CertificateServicePort,
        cpd: CpdSyncPort,
        clock: Clock,
        formal_gate: CertificateGatePort | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._results = results
        self._attempts = attempts
        self._certificates = certificates
        self._cpd = cpd
        self._clock = clock
        # UC-09's certificate gate. Defaulted to "allow", which is the truth for a deployment with
        # no formal assessments — see ``integration/formal_gate.py`` for why that default is safe
        # and why an *unreadable* gate is a different case that raises.
        self._formal_gate = formal_gate or UnrestrictedCertificateGate()

    # ------------------------------------------------------------------ read

    def find_outcome(self, attempt_id: str, *, learner_id: str | None = None) -> OutcomeView:
        """The stored outcome for an attempt, scoped to its owner."""
        policy = self._attempts.get_policy(attempt_id, learner_id=learner_id)
        if policy is None:
            raise errors.attempt_not_found(attempt_id)
        outcome = self._repository.get_outcome(attempt_id)
        if outcome is None:
            raise errors.outcome_not_found(attempt_id)
        return self._view(outcome, policy)

    def list_outcomes(self, learner_id: str, *, quiz_id: str | None = None) -> list[AttemptOutcome]:
        return self._repository.list_outcomes(learner_id, quiz_id=quiz_id)

    # ------------------------------------------------------------- determine

    def determine(self, attempt_id: str, *, learner_id: str | None = None) -> OutcomeView:
        """Determine pass/fail for a scored attempt, then drive the downstream effects. Safe to call
        repeatedly. An attempt that already has an outcome keeps it -- the verdict is never
        recomputed, because the score it was based on is immutable -- but any certificate or CPD
        record still ``PENDING`` is driven again, which is what makes this the natural retry
        entry point as well as the first-time path."""
        policy = self._attempts.get_policy(attempt_id, learner_id=learner_id)
        if policy is None:
            raise errors.attempt_not_found(attempt_id)

        result = self._results.get_result(attempt_id)
        if result is None or not result.confirmed:
            raise errors.result_not_confirmed(attempt_id, result.status if result else None)

        existing = self._repository.get_outcome(attempt_id)
        if existing is None:
            outcome, created = self._record_outcome(policy, result)
        else:
            outcome, created = existing, False

        # Downstream effects, each isolated. Neither can raise here: this method's contract is that
        # the outcome is returned whatever the certificate and CPD services are doing.
        if outcome.outcome == str(Outcome.PASS):
            self._issue_certificate(outcome, policy, raise_on_failure=False)
        self._synchronise_cpd(outcome, policy, raise_on_failure=False)

        stored = self._repository.get_outcome(attempt_id)
        if stored is None:
            # pragma: no cover - defensive
            raise errors.internal_error()
        view = self._view(stored, policy)
        return OutcomeView(
            outcome=view.outcome,
            certificate=view.certificate,
            cpd_record=view.cpd_record,
            attempts_used=view.attempts_used,
            attempts_remaining=view.attempts_remaining,
            max_attempts=view.max_attempts,
            created=created,
        )

    # ---------------------------------------------------------------- retries

    def retry_certificate(self, attempt_id: str, *, learner_id: str | None = None) -> OutcomeView:
        """Drive a pending certificate again. Raises when the service is still unavailable."""
        policy = self._attempts.get_policy(attempt_id, learner_id=learner_id)
        if policy is None:
            raise errors.attempt_not_found(attempt_id)
        outcome = self._repository.get_outcome(attempt_id)
        if outcome is None:
            raise errors.outcome_not_found(attempt_id)
        if outcome.outcome != str(Outcome.PASS):
            raise errors.certificate_not_applicable(attempt_id, outcome.outcome)

        self._issue_certificate(outcome, policy, raise_on_failure=True)
        return self._view(outcome, policy)

    def issue_after_formal_approval(
        self, attempt_id: str, *, approved_by: str, idempotency_key: str | None = None
    ) -> OutcomeView:
        """Generate the certificate for a formal assessment an assessor has just approved (UC-09).

        The one caller that bypasses the gate, and it does so because it *is* the event the gate
        was waiting for — asking UC-09 again here would be asking it to confirm the decision it
        has just made, against a record it may not have committed yet.

        Everything else is UC-05's ordinary path: the same PENDING row, the same duplicate
        prevention, the same certificate service, the same retry semantics. UC-09 does not create
        a certificate; it removes the reason one was being withheld.
        """
        policy = self._attempts.get_policy(attempt_id)
        if policy is None:
            raise errors.attempt_not_found(attempt_id)
        outcome = self._repository.get_outcome(attempt_id)
        if outcome is None:
            raise errors.outcome_not_found(attempt_id)
        if outcome.outcome != str(Outcome.PASS):
            # An approval on a failed attempt is a defect upstream, refused rather than honoured:
            # a certificate for a fail is worse than a missing certificate for a pass.
            raise errors.certificate_not_applicable(attempt_id, outcome.outcome)

        logger.info(
            "certification.certificate_released_by_approval",
            extra={
                "attemptId": attempt_id,
                "approvedBy": approved_by,
                "idempotencyKey": idempotency_key,
            },
        )
        self._issue_certificate(
            outcome, policy, raise_on_failure=True, formal_approval_granted=True
        )
        return self._view(outcome, policy)

    def retry_cpd(self, attempt_id: str, *, learner_id: str | None = None) -> OutcomeView:
        """Drive a pending CPD synchronisation again. Raises when it still fails."""
        policy = self._attempts.get_policy(attempt_id, learner_id=learner_id)
        if policy is None:
            raise errors.attempt_not_found(attempt_id)
        outcome = self._repository.get_outcome(attempt_id)
        if outcome is None:
            raise errors.outcome_not_found(attempt_id)

        self._synchronise_cpd(outcome, policy, raise_on_failure=True)
        return self._view(outcome, policy)

    # -------------------------------------------------------------- internals

    def _record_outcome(
        self, policy: AttemptPolicy, result: ConfirmedResult
    ) -> tuple[AttemptOutcome, bool]:
        """Persist the verdict, the certificate obligation and the CPD obligation together."""
        now = self._clock.now()

        # The bar is the attempt's own. UC-04 copied the same value onto the result; a disagreement
        # would mean one of them read the wrong configuration version, so it is worth surfacing.
        pass_mark = policy.pass_mark_percentage
        if abs(pass_mark - result.pass_mark_percentage) > 1e-9:
            logger.warning(
                "certification.pass_mark_disagreement",
                extra={
                    "attemptId": policy.attempt_id,
                    "attemptPassMark": pass_mark,
                    "resultPassMark": result.pass_mark_percentage,
                },
            )

        decision = gate(
            percentage=result.percentage,
            pass_mark_percentage=pass_mark,
            attempts_used=policy.attempts_used,
            max_attempts=policy.max_attempts,
        )
        attempt_date = policy.submitted_at or result.submitted_at or now

        try:
            with self._session.begin_nested():
                outcome = self._repository.insert_outcome(
                    attempt_id=policy.attempt_id,
                    result_id=result.result_id,
                    learner_id=policy.learner_id,
                    course_id=policy.course_id,
                    quiz_id=policy.quiz_id,
                    attempt_number=policy.attempt_number,
                    configuration_version_id=policy.configuration_version_id,
                    outcome=str(decision.outcome),
                    percentage=result.percentage,
                    pass_mark_percentage=pass_mark,
                    total_marks=result.total_marks,
                    maximum_marks=result.maximum_marks,
                    attempts_used_at_outcome=decision.attempts_used,
                    max_attempts=decision.max_attempts,
                    attempts_remaining_at_outcome=decision.attempts_remaining,
                    certificate_required=decision.certificate_due,
                    determined_at=now,
                    created_at=now,
                )
        except IntegrityError:
            # A concurrent determination won. Its verdict stands; ours is discarded rather than
            # written a second time.
            self._session.rollback()
            winner = self._repository.get_outcome(policy.attempt_id)
            if winner is None:  # pragma: no cover - the unique index is the only cause
                raise
            return winner, False

        try:
            self._ensure_obligations(outcome, policy, decision, attempt_date, now)
            self._session.commit()
        except SQLAlchemyError as exc:
            self._session.rollback()
            logger.error(
                "certification.persistence_failed",
                extra={"attemptId": policy.attempt_id},
                exc_info=exc,
            )
            raise errors.persistence_failed("determine_outcome") from exc

        stored = self._repository.get_outcome(policy.attempt_id)
        if stored is None:
            # pragma: no cover - defensive
            raise errors.internal_error()
        return stored, True

    def _ensure_obligations(
        self,
        outcome: AttemptOutcome,
        policy: AttemptPolicy,
        decision: Gate,
        attempt_date: datetime,
        now: datetime,
    ) -> None:
        """Create the PENDING certificate (on a pass) and the PENDING CPD record. Both rows exist
        before either service is called. That is what "asynchronous and retryable" means in
        practice: the obligation is durable, so the work can be picked up again later by anything
        -- a retry endpoint, a sweep, an operator."""
        if (
            decision.certificate_due
            and self._repository.get_certificate(outcome.attempt_id) is None
        ):
            already_held = self._repository.find_issued_certificate_for_quiz(
                policy.learner_id, policy.quiz_id
            )
            if already_held is not None:
                # Duplicate prevention, decided before anything is requested: a learner who passes a
                # second time keeps the certificate they already hold, and no second request is
                # recorded. The partial unique index would refuse the issue anyway; refusing to ask
                # is
                # what keeps a passing attempt from carrying a FAILED certificate it never needed.
                logger.info(
                    "certification.certificate_already_held",
                    extra={
                        "attemptId": outcome.attempt_id,
                        "heldCertificateId": already_held.id,
                        "heldForAttemptId": already_held.attempt_id,
                    },
                )
            else:
                self._repository.insert_certificate(
                    attempt_id=outcome.attempt_id,
                    outcome_id=outcome.id,
                    learner_id=policy.learner_id,
                    course_id=policy.course_id,
                    quiz_id=policy.quiz_id,
                    course_name=policy.course_name,
                    quiz_title=policy.quiz_title,
                    percentage=outcome.percentage,
                    requested_at=now,
                    created_at=now,
                    updated_at=now,
                )

        if self._repository.get_cpd_record(outcome.attempt_id) is None:
            self._repository.insert_cpd_record(
                attempt_id=outcome.attempt_id,
                outcome_id=outcome.id,
                learner_id=policy.learner_id,
                course_id=policy.course_id,
                quiz_id=policy.quiz_id,
                attempt_date=attempt_date,
                score_percentage=outcome.percentage,
                passed=decision.passed,
                course_name=policy.course_name,
                total_marks=outcome.total_marks,
                maximum_marks=outcome.maximum_marks,
                requested_at=now,
                created_at=now,
                updated_at=now,
            )

    # ---- certificate -------------------------------------------------------

    def _issue_certificate(
        self,
        outcome: AttemptOutcome,
        policy: AttemptPolicy,
        *,
        raise_on_failure: bool,
        formal_approval_granted: bool = False,
    ) -> Certificate | None:
        """Generate the certificate for a passing attempt, unless something says not yet.

        ``formal_approval_granted`` is passed only by :meth:`issue_after_formal_approval`, which
        is the call UC-09 makes *because* an assessor has just approved. Everything else — the
        submission pipeline, the retry endpoint, an operator sweep — goes through the gate.
        """
        # UC-09 §11. Asked before anything is requested and at the single point a certificate can
        # be generated, so there is no route to one that skips it. A blocked formal assessment
        # leaves the PENDING certificate row in place: the obligation stays durable and visible,
        # and the approval later drives it rather than creating it from nothing.
        if not formal_approval_granted:
            gate = self._formal_gate.check_attempt(outcome.attempt_id)
            if not gate.certificate_allowed:
                logger.info(
                    "certification.certificate_withheld_pending_review",
                    extra={
                        "attemptId": outcome.attempt_id,
                        "reason": gate.reason,
                        "reviewId": gate.review_id,
                    },
                )
                if raise_on_failure:
                    # A retry endpoint must say why rather than reporting a silent no-op: the
                    # certificate is not failing, it is waiting for a person.
                    raise errors.certificate_awaiting_formal_approval(
                        outcome.attempt_id, gate.reason
                    )
                return None

        certificate = self._repository.get_certificate(outcome.attempt_id)
        if certificate is None:
            # A pass with no certificate row: only reachable if the obligation was never written.
            # Create it now rather than silently skipping what the learner is owed.
            now = self._clock.now()
            self._repository.insert_certificate(
                attempt_id=outcome.attempt_id,
                outcome_id=outcome.id,
                learner_id=policy.learner_id,
                course_id=policy.course_id,
                quiz_id=policy.quiz_id,
                course_name=policy.course_name,
                quiz_title=policy.quiz_title,
                percentage=outcome.percentage,
                requested_at=now,
                created_at=now,
                updated_at=now,
            )
            self._session.commit()
            certificate = self._repository.get_certificate(outcome.attempt_id)
            if certificate is None:
                # pragma: no cover - defensive
                raise errors.internal_error()

        if certificate.status == str(CertificateStatus.ISSUED):
            # Already issued: nothing is sent and nothing is written. This is the duplicate guard
            # doing its job on the happy path.
            return certificate

        # A learner who already holds a certificate for this quiz does not get a second one. The
        # partial unique index would refuse it anyway; refusing here means the reason is recorded
        # rather than surfacing as a constraint error.
        held = self._repository.find_issued_certificate_for_quiz(policy.learner_id, policy.quiz_id)
        if held is not None:
            self._repository.mark_certificate_failure(
                certificate.id,
                status=CertificateStatus.FAILED,
                failure_code=DUPLICATE_CERTIFICATE_PREVENTED,
                failure_message=(
                    "This learner already holds certificate "
                    f"{held.certificate_number} for this quiz, so no second one was issued."
                ),
                now=self._clock.now(),
            )
            self._session.commit()
            logger.info(
                "certificate.duplicate_prevented",
                extra={"attemptId": outcome.attempt_id, "heldCertificateId": held.id},
            )
            return self._repository.get_certificate(outcome.attempt_id)

        now = self._clock.now()
        self._repository.record_certificate_run(certificate.id, now)
        self._session.commit()

        request = CertificateRequest(
            attempt_id=certificate.attempt_id,
            learner_id=certificate.learner_id,
            course_id=certificate.course_id,
            quiz_id=certificate.quiz_id,
            course_name=certificate.course_name,
            quiz_title=certificate.quiz_title,
            percentage=certificate.percentage,
            total_marks=outcome.total_marks,
            maximum_marks=outcome.maximum_marks,
            attempt_date=to_iso(policy.submitted_at or now),
            # Stable per attempt: a retry the service already received returns the same document.
            idempotency_key=f"certificate:{certificate.attempt_id}",
        )

        try:
            issued = self._certificates.issue(request)
        except TransientCertificateError as exc:
            message = str(exc) or exc.__class__.__name__
            self._repository.mark_certificate_failure(
                certificate.id,
                status=CertificateStatus.PENDING,
                failure_code=TRANSIENT_CERTIFICATE_FAILURE,
                failure_message=message,
                now=self._clock.now(),
            )
            self._session.commit()
            logger.warning(
                "certificate.transient_failure",
                extra={"attemptId": outcome.attempt_id, "reason": message},
            )
            if raise_on_failure:
                raise errors.certificate_unavailable(
                    outcome.attempt_id,
                    "The certificate could not be issued yet. The quiz result and the pass are "
                    "unchanged, and the certificate remains pending -- retry it.",
                    certificateStatus=str(CertificateStatus.PENDING),
                    reason=message,
                ) from exc
            return self._repository.get_certificate(outcome.attempt_id)
        except Exception as exc:
            # noqa: BLE001 - classified as permanent, deliberately
            message = str(exc) or exc.__class__.__name__
            self._repository.mark_certificate_failure(
                certificate.id,
                status=CertificateStatus.FAILED,
                failure_code=PERMANENT_CERTIFICATE_FAILURE,
                failure_message=message,
                now=self._clock.now(),
            )
            self._session.commit()
            logger.error(
                "certificate.permanent_failure",
                extra={"attemptId": outcome.attempt_id, "reason": message},
            )
            if raise_on_failure:
                raise errors.certificate_unavailable(
                    outcome.attempt_id,
                    "The certificate service rejected the request. The quiz result and the pass "
                    "are unchanged.",
                    certificateStatus=str(CertificateStatus.FAILED),
                    reason=message,
                ) from exc
            return self._repository.get_certificate(outcome.attempt_id)

        try:
            self._repository.mark_certificate_issued(
                certificate.id,
                certificate_number=issued.certificate_number,
                document_reference=issued.document_reference,
                metadata=dict(issued.metadata) or None,
                now=self._clock.now(),
            )
            self._session.commit()
        except IntegrityError:
            # The partial unique index refused a second issued certificate. The learner already has
            # one; that is the guarantee holding, not an error to propagate.
            self._session.rollback()
            logger.info(
                "certificate.duplicate_refused_by_database",
                extra={"attemptId": outcome.attempt_id},
            )
        return self._repository.get_certificate(outcome.attempt_id)

    # ---- CPD ---------------------------------------------------------------

    def _synchronise_cpd(
        self, outcome: AttemptOutcome, policy: AttemptPolicy, *, raise_on_failure: bool
    ) -> CpdRecord | None:
        record = self._repository.get_cpd_record(outcome.attempt_id)
        if record is None:
            now = self._clock.now()
            self._repository.insert_cpd_record(
                attempt_id=outcome.attempt_id,
                outcome_id=outcome.id,
                learner_id=policy.learner_id,
                course_id=policy.course_id,
                quiz_id=policy.quiz_id,
                attempt_date=policy.submitted_at or now,
                score_percentage=outcome.percentage,
                passed=outcome.outcome == str(Outcome.PASS),
                course_name=policy.course_name,
                total_marks=outcome.total_marks,
                maximum_marks=outcome.maximum_marks,
                requested_at=now,
                created_at=now,
                updated_at=now,
            )
            self._session.commit()
            record = self._repository.get_cpd_record(outcome.attempt_id)
            if record is None:
                # pragma: no cover - defensive
                raise errors.internal_error()

        if record.status == str(CpdSyncStatus.SYNCHRONISED):
            return record

        now = self._clock.now()
        self._repository.record_cpd_run(record.id, now)
        self._session.commit()

        payload = CpdSyncRecord(
            attempt_id=record.attempt_id,
            learner_id=record.learner_id,
            course_id=record.course_id,
            attempt_date=to_iso(record.attempt_date),
            score_percentage=record.score_percentage,
            passed=bool(record.passed),
            course_name=record.course_name,
            total_marks=record.total_marks,
            maximum_marks=record.maximum_marks,
            idempotency_key=f"cpd:{record.attempt_id}",
        )

        try:
            ack = self._cpd.synchronise(payload)
        except TransientCpdError as exc:
            message = str(exc) or exc.__class__.__name__
            self._repository.mark_cpd_failure(
                record.id,
                status=CpdSyncStatus.PENDING,
                failure_code=TRANSIENT_CPD_FAILURE,
                failure_message=message,
                now=self._clock.now(),
            )
            self._session.commit()
            logger.warning(
                "cpd.transient_failure",
                extra={"attemptId": outcome.attempt_id, "reason": message},
            )
            if raise_on_failure:
                raise errors.cpd_sync_unavailable(
                    outcome.attempt_id,
                    "The CPD record could not be synchronised yet. The quiz result and the "
                    "pass/fail outcome are unchanged -- retry it.",
                    cpdStatus=str(CpdSyncStatus.PENDING),
                    reason=message,
                ) from exc
            return self._repository.get_cpd_record(outcome.attempt_id)
        except Exception as exc:
            # noqa: BLE001 - classified as permanent, deliberately
            message = str(exc) or exc.__class__.__name__
            self._repository.mark_cpd_failure(
                record.id,
                status=CpdSyncStatus.FAILED,
                failure_code=PERMANENT_CPD_FAILURE,
                failure_message=message,
                now=self._clock.now(),
            )
            self._session.commit()
            logger.error(
                "cpd.permanent_failure",
                extra={"attemptId": outcome.attempt_id, "reason": message},
            )
            if raise_on_failure:
                raise errors.cpd_sync_unavailable(
                    outcome.attempt_id,
                    "The CPD service rejected the record. The quiz result and the pass/fail "
                    "outcome are unchanged.",
                    cpdStatus=str(CpdSyncStatus.FAILED),
                    reason=message,
                ) from exc
            return self._repository.get_cpd_record(outcome.attempt_id)

        self._repository.mark_cpd_synchronised(
            record.id, external_reference=ack.external_reference, now=self._clock.now()
        )
        self._session.commit()
        return self._repository.get_cpd_record(outcome.attempt_id)

    # ---- assembly ----------------------------------------------------------

    def _view(self, outcome: AttemptOutcome, policy: AttemptPolicy) -> OutcomeView:
        """Assemble the read model, recomputing the remaining attempts live. The row carries what
        the learner was told at determination time; this recomputes from UC-03's current attempt
        count, because "how many attempts do I have left" has to answer for *now*."""
        return OutcomeView(
            outcome=outcome,
            certificate=self._repository.get_certificate(outcome.attempt_id),
            cpd_record=self._repository.get_cpd_record(outcome.attempt_id),
            attempts_used=policy.attempts_used,
            attempts_remaining=attempts_remaining(policy.max_attempts, policy.attempts_used),
            max_attempts=policy.max_attempts,
        )
