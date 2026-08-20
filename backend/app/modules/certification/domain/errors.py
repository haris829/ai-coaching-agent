"""UC-05's error codes and failure factories. Same shape as UC-03's and UC-04's: one shared
exception hierarchy, one handler, one envelope."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from app.core.errors import AppError as SharedAppError


class ErrorCode(StrEnum):
    """Stable, machine-readable codes UC-05 returns."""

    ATTEMPT_NOT_FOUND = "ATTEMPT_NOT_FOUND"
    #: The attempt has no confirmed UC-04 result yet, so there is nothing to gate on.
    RESULT_NOT_CONFIRMED = "RESULT_NOT_CONFIRMED"
    OUTCOME_NOT_FOUND = "OUTCOME_NOT_FOUND"
    #: A determined outcome is a fact about an immutable score; it is never re-determined.
    OUTCOME_ALREADY_DETERMINED = "OUTCOME_ALREADY_DETERMINED"
    CERTIFICATE_NOT_APPLICABLE = "CERTIFICATE_NOT_APPLICABLE"
    CERTIFICATE_NOT_FOUND = "CERTIFICATE_NOT_FOUND"
    #: A passing formal assessment waiting on an assessor. Not a failure — see the helper below.
    CERTIFICATE_AWAITING_APPROVAL = "CERTIFICATE_AWAITING_APPROVAL"
    CERTIFICATE_UNAVAILABLE = "CERTIFICATE_UNAVAILABLE"
    CPD_SYNC_UNAVAILABLE = "CPD_SYNC_UNAVAILABLE"
    PERSISTENCE_FAILED = "PERSISTENCE_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AppError(SharedAppError):
    """An expected UC-05 failure, safe to render to the client verbatim."""

    def __init__(
        self,
        status: int,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(
            message,
            status_code=status,
            code=code,
            context=details or None,
            retryable=retryable,
        )

    @property
    def status(self) -> int:
        return self.status_code


def attempt_not_found(attempt_id: str) -> AppError:
    return AppError(
        404,
        ErrorCode.ATTEMPT_NOT_FOUND,
        "The attempt was not found, or it does not belong to this learner.",
        details={"attemptId": attempt_id},
    )


def result_not_confirmed(attempt_id: str, status: str | None = None) -> AppError:
    return AppError(
        409,
        ErrorCode.RESULT_NOT_CONFIRMED,
        "Pass or fail can only be determined from a confirmed score. The attempt is still "
        "awaiting scoring.",
        details={"attemptId": attempt_id, "resultStatus": status},
        retryable=True,
    )


def outcome_not_found(attempt_id: str) -> AppError:
    return AppError(
        404,
        ErrorCode.OUTCOME_NOT_FOUND,
        "No pass/fail outcome has been determined for this attempt yet.",
        details={"attemptId": attempt_id},
    )


def certificate_not_applicable(attempt_id: str, outcome: str) -> AppError:
    return AppError(
        409,
        ErrorCode.CERTIFICATE_NOT_APPLICABLE,
        "A certificate is only issued for a passing attempt.",
        details={"attemptId": attempt_id, "outcome": outcome},
    )


def certificate_awaiting_formal_approval(attempt_id: str, reason: str | None) -> AppError:
    """The learner passed a formal assessment and an assessor has not approved it yet (UC-09 §11).

    409 and explicitly **not an error state**: nothing failed, and nothing needs fixing. A retry
    endpoint has to be able to say "waiting for a person" rather than reporting a silent no-op,
    because an operator watching a queue of pending certificates needs to tell the two apart.
    """
    return AppError(
        409,
        ErrorCode.CERTIFICATE_AWAITING_APPROVAL,
        "This certificate is waiting for an assessor to approve the formal assessment.",
        details={"attemptId": attempt_id, "reason": reason},
        retryable=True,
    )


def certificate_not_found(attempt_id: str) -> AppError:
    return AppError(
        404,
        ErrorCode.CERTIFICATE_NOT_FOUND,
        "No certificate has been requested for this attempt.",
        details={"attemptId": attempt_id},
    )


def certificate_unavailable(attempt_id: str, message: str, **details: Any) -> AppError:
    return AppError(
        502,
        ErrorCode.CERTIFICATE_UNAVAILABLE,
        message,
        details={"attemptId": attempt_id, **details},
        retryable=True,
    )


def cpd_sync_unavailable(attempt_id: str, message: str, **details: Any) -> AppError:
    return AppError(
        502,
        ErrorCode.CPD_SYNC_UNAVAILABLE,
        message,
        details={"attemptId": attempt_id, **details},
        retryable=True,
    )


def persistence_failed(operation: str) -> AppError:
    return AppError(
        503,
        ErrorCode.PERSISTENCE_FAILED,
        "The outcome could not be saved because of a temporary problem. Nothing was saved -- "
        "please retry.",
        details={"operation": operation},
        retryable=True,
    )


def internal_error() -> AppError:
    return AppError(500, ErrorCode.INTERNAL_ERROR, "An unexpected error occurred.")
