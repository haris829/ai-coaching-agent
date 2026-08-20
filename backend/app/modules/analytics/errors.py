"""Structured error hierarchy.

Every failure that can reach an API boundary is expressed as an
:class:`AnalyticsError` carrying a stable machine-readable ``code`` and an
HTTP status. Internal details (stack traces, driver messages, SQL) are logged
server-side and never placed in ``message``/``details``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.errors import AppError as SharedAppError
from app.core.time import to_iso, utcnow

__all__ = [
    "AnalyticsError",
    "InvalidFilterError",
    "ConfigurationError",
    "InvalidThresholdError",
    "RepositoryUnavailableError",
    "UpstreamDataInvalidError",
    "CalculationError",
    "ExportError",
    "QueryTimeoutError",
    "DatasetTooLargeError",
    "QueryCancelledError",
    "NotFoundError",
    "ReviewConflictError",
    "AuthenticationError",
    "AuthorizationError",
]


class AnalyticsError(SharedAppError):
    """Base class for all UC-10 failures.

    Standalone, UC-10 carried its own exception hierarchy, its own status codes and its own
    ``to_payload``, because it was its own application with its own error envelope. In the merged
    system there is exactly one envelope — ``app.core.exception_handlers`` renders it and every
    capability's refusals go through it — so this class now extends the shared
    :class:`app.core.errors.AppError` and keeps only what is genuinely UC-10's:

    ``code`` / ``http_status``
        Declared per subclass, exactly as before, and mapped onto the shared base's ``code`` and
        ``status_code``. The wire codes a client branches on are unchanged.

    ``client_safe_message``
        The one behaviour worth keeping. A failure that wraps a driver error must not return its
        message, and this is the flag that says so — the shared base has no equivalent because no
        other capability wraps a third party the way the analytics provider does.
    """

    code: str = "ANALYTICS_ERROR"
    http_status: int = 500
    #: Safe to return verbatim to a client. Subclasses that wrap third-party failures set this to
    #: ``False`` and expose a generic message instead.
    client_safe_message: bool = True

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        public = message if self.client_safe_message else (
            "The analytics service could not complete the request."
        )
        super().__init__(
            public,
            status_code=self.http_status,
            code=self.code,
            context=dict(details) if details else None,
            retryable=getattr(self, "retryable", False),
            # The original message reaches the log and nothing else, which is the whole point of
            # ``client_safe_message``.
            log_context={"analytics_detail": message} if public != message else None,
        )
        self.message = public
        self.details: dict[str, Any] = dict(details or {})
        if cause is not None:
            self.__cause__ = cause

    def public_message(self) -> str:
        """Kept for callers that render a message without going through the envelope."""
        return self.message

    def to_response(self, *, request_id: str | None = None) -> dict[str, Any]:
        """Render into the application's error envelope.

        Overridden because UC-10's ``details`` is a *mapping* of contextual facts — the filter that
        was invalid, the record count that was too large — while the shared base's ``details`` is a
        list of field-level issues with an ``as_dict``. Both are called "details" and they are not
        the same shape, so the base's renderer cannot be used as-is: it would try to call
        ``as_dict`` on a dictionary key and fail.

        The envelope shape is the shared one; only the population of ``details`` differs.
        """
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "requestId": request_id,
                "timestamp": to_iso(utcnow()),
                **({"details": dict(self.details)} if self.details else {}),
            }
        }


class InvalidFilterError(AnalyticsError):
    """Filter values are structurally valid but semantically impossible."""

    code = "INVALID_FILTER"
    http_status = 422


class ConfigurationError(AnalyticsError):
    """Analytics configuration is invalid or requires explicit confirmation."""

    code = "INVALID_CONFIGURATION"
    http_status = 422


class InvalidThresholdError(ConfigurationError):
    """A threshold value is out of range or dangerous without confirmation."""

    code = "INVALID_THRESHOLD"
    http_status = 422


class RepositoryUnavailableError(AnalyticsError):
    """The external data provider failed or is unreachable.

    The underlying driver message is deliberately withheld from clients; it is
    attached as ``__cause__`` for server-side logging only.
    """

    code = "DATA_PROVIDER_UNAVAILABLE"
    http_status = 503
    client_safe_message = False


class UpstreamDataInvalidError(AnalyticsError):
    """The provider returned records that violate the repository contract.

    Distinct from :class:`RepositoryUnavailableError` because it is not
    transient: a score on a 0-1000 scale will fail identically on every retry.
    Reported as 502 so a caller knows the fault is upstream of them and that
    retrying is pointless; field names are surfaced to help the integrator, but
    never values, which may contain learner data.
    """

    code = "UPSTREAM_DATA_INVALID"
    http_status = 502
    client_safe_message = False


class CalculationError(AnalyticsError):
    """Aggregation could not be completed over the supplied data."""

    code = "CALCULATION_FAILED"
    http_status = 500
    client_safe_message = False


class ExportError(AnalyticsError):
    """CSV generation failed."""

    code = "EXPORT_FAILED"
    http_status = 500
    client_safe_message = False


class QueryTimeoutError(AnalyticsError):
    """The query exceeded its deadline.

    Clients are expected to refine their filters and retry.
    """

    code = "QUERY_TIMEOUT"
    http_status = 504


class DatasetTooLargeError(AnalyticsError):
    """The filtered dataset exceeds the configured scan limit.

    Raised instead of silently truncating: a partial scan would produce numbers
    that look authoritative but are wrong.
    """

    code = "DATASET_TOO_LARGE"
    http_status = 422


class QueryCancelledError(AnalyticsError):
    """The query was cancelled by the caller (client disconnect or refinement)."""

    code = "QUERY_CANCELLED"
    http_status = 499


class NotFoundError(AnalyticsError):
    """A referenced entity does not exist in the source system."""

    code = "NOT_FOUND"
    http_status = 404


class ReviewConflictError(AnalyticsError):
    """A review action conflicts with the current state of the question."""

    code = "REVIEW_CONFLICT"
    http_status = 409


class AuthenticationError(AnalyticsError):
    """Caller identity is missing or invalid."""

    code = "UNAUTHENTICATED"
    http_status = 401


class AuthorizationError(AnalyticsError):
    """Caller is known but not permitted to perform the operation."""

    code = "FORBIDDEN"
    http_status = 403
