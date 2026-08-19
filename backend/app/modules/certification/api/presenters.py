"""Serialisation for UC-05.

An allow-list per row. The certificate presenter is the one to read carefully: it exposes the
certificate *number* and its document reference, which is what a learner needs, and nothing about
how the certificate service was called.
"""

from __future__ import annotations

from typing import Any

from app.core.time import iso_or_none
from app.modules.certification.domain.enums import OUTCOME_LABELS, Outcome
from app.modules.certification.models import AttemptOutcome, Certificate, CpdRecord
from app.modules.certification.services.certification_service import OutcomeView


def outcome_label(outcome: str) -> str:
    try:
        return OUTCOME_LABELS[Outcome(outcome)]
    except ValueError:  # pragma: no cover - the column has a CHECK constraint
        return outcome


def present_outcome(outcome: AttemptOutcome) -> dict[str, Any]:
    return {
        "outcomeId": outcome.id,
        "attemptId": outcome.attempt_id,
        "resultId": outcome.result_id,
        "learnerId": outcome.learner_id,
        "courseId": outcome.course_id,
        "quizId": outcome.quiz_id,
        "attemptNumber": outcome.attempt_number,
        "outcome": outcome.outcome,
        "outcomeLabel": outcome_label(outcome.outcome),
        "passed": outcome.outcome == str(Outcome.PASS),
        "percentage": outcome.percentage,
        # The pass mark of the attempt's own configuration version.
        "passMarkPercentage": outcome.pass_mark_percentage,
        "totalMarks": outcome.total_marks,
        "maximumMarks": outcome.maximum_marks,
        "configurationVersionId": outcome.configuration_version_id,
        "certificateRequired": bool(outcome.certificate_required),
        "determinedAt": iso_or_none(outcome.determined_at),
        # The audit copy from determination time; the live figures are on the response root.
        "attemptsUsedAtOutcome": outcome.attempts_used_at_outcome,
        "attemptsRemainingAtOutcome": outcome.attempts_remaining_at_outcome,
        "maxAttempts": outcome.max_attempts,
    }


def present_certificate(certificate: Certificate | None) -> dict[str, Any] | None:
    if certificate is None:
        return None
    return {
        "certificateId": certificate.id,
        "attemptId": certificate.attempt_id,
        "status": certificate.status,
        "certificateNumber": certificate.certificate_number,
        "documentReference": certificate.document_reference,
        "courseName": certificate.course_name,
        "quizTitle": certificate.quiz_title,
        "percentage": certificate.percentage,
        "generationAttemptCount": certificate.generation_attempt_count,
        "failureCode": certificate.failure_code,
        "failureMessage": certificate.failure_message,
        "requestedAt": iso_or_none(certificate.requested_at),
        "lastAttemptedAt": iso_or_none(certificate.last_attempted_at),
        "issuedAt": iso_or_none(certificate.issued_at),
    }


def present_cpd(record: CpdRecord | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "cpdRecordId": record.id,
        "attemptId": record.attempt_id,
        "status": record.status,
        # The four facts the CPD system is given.
        "attemptDate": iso_or_none(record.attempt_date),
        "scorePercentage": record.score_percentage,
        "passed": bool(record.passed),
        "courseName": record.course_name,
        "externalReference": record.external_reference,
        "syncAttemptCount": record.sync_attempt_count,
        "failureCode": record.failure_code,
        "failureMessage": record.failure_message,
        "synchronisedAt": iso_or_none(record.synchronised_at),
    }


def present_view(view: OutcomeView) -> dict[str, Any]:
    """The whole gating picture for one attempt."""
    return {
        "outcome": present_outcome(view.outcome),
        "certificate": present_certificate(view.certificate),
        "cpd": present_cpd(view.cpd_record),
        # Live, recomputed from UC-03's attempt count rather than read off the outcome row.
        "attemptsUsed": view.attempts_used,
        "attemptsRemaining": view.attempts_remaining,
        "maxAttempts": view.max_attempts,
        "mayReattempt": view.attempts_remaining is None or view.attempts_remaining > 0,
    }
