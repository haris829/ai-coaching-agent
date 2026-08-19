"""UC-06's error codes and failure factories."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from app.core.errors import AppError as SharedAppError


class ErrorCode(StrEnum):
    """Stable, machine-readable codes UC-06 returns."""

    ATTEMPT_NOT_FOUND = "ATTEMPT_NOT_FOUND"
    #: Feedback is generated from a confirmed score; a pending score has nothing to report.
    SCORE_NOT_CONFIRMED = "SCORE_NOT_CONFIRMED"
    FEEDBACK_NOT_FOUND = "FEEDBACK_NOT_FOUND"
    FEEDBACK_GENERATION_FAILED = "FEEDBACK_GENERATION_FAILED"
    PERSISTENCE_FAILED = "PERSISTENCE_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AppError(SharedAppError):
    """An expected UC-06 failure, safe to render to the client verbatim."""

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
            message, status_code=status, code=code, context=details or None, retryable=retryable
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


def score_not_confirmed(attempt_id: str, status: str | None = None) -> AppError:
    return AppError(
        409,
        ErrorCode.SCORE_NOT_CONFIRMED,
        "Feedback can only be generated from a confirmed score. The attempt is still awaiting "
        "scoring.",
        details={"attemptId": attempt_id, "resultStatus": status},
        retryable=True,
    )


def feedback_not_found(attempt_id: str) -> AppError:
    return AppError(
        404,
        ErrorCode.FEEDBACK_NOT_FOUND,
        "No feedback report has been generated for this attempt yet.",
        details={"attemptId": attempt_id},
    )


def generation_failed(attempt_id: str, message: str, **details: Any) -> AppError:
    return AppError(
        502,
        ErrorCode.FEEDBACK_GENERATION_FAILED,
        message,
        details={"attemptId": attempt_id, **details},
        retryable=True,
    )


def persistence_failed(operation: str) -> AppError:
    return AppError(
        503,
        ErrorCode.PERSISTENCE_FAILED,
        "The feedback report could not be saved because of a temporary problem. Nothing was saved "
        "-- please retry.",
        details={"operation": operation},
        retryable=True,
    )


def internal_error() -> AppError:
    return AppError(500, ErrorCode.INTERNAL_ERROR, "An unexpected error occurred.")
