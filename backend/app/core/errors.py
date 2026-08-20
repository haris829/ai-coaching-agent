"""Application error taxonomy — shared by every module.

Everything the API returns to a client passes through one of these classes, so the response
envelope, the HTTP status and the machine-readable ``code`` stay consistent across UC-01 and
UC-02. Stack traces and driver messages are never serialised — they are logged server-side by
the handlers in ``app/core/exception_handlers.py``.

The envelope is always::

    {"error": {
        "code": "...", "message": "...",
        "retryable": bool, "requestId": "...", "timestamp": "...",
        "details": [{field, code, message}],   # field-level problems, when any
        "context": {...},                      # structured, machine-readable context
        ...                                    # code-specific extras, e.g. `capacity`
    }}

Two distinct slots, deliberately kept apart so a client never has to type-check a key:

* ``details`` — always a **list** of field-level problems, whichever route the payload arrived
  through (JSON API, CSV row, quiz-configuration form, or attempt answer).
* ``context`` — always a **dict** of structured context about the failure itself: which option ids
  were valid, how many selections were expected, which attempt is already open.

``retryable`` tells a client whether repeating the identical request may succeed. ``requestId``
correlates the response with the server log line.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.core.time import to_iso, utcnow


@dataclass(frozen=True, slots=True)
class FieldIssue:
    """A single field-level problem.

    Shared by the question JSON validator, the CSV row validator and the quiz-configuration
    validator, so an administrator sees identically shaped messages everywhere.
    """

    field: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


class AppError(Exception):
    """Base class for every error that is safe to show a client."""

    status_code: int = 500
    code: str = "INTERNAL_ERROR"

    #: True when repeating the identical request may succeed later.
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        details: list[FieldIssue] | None = None,
        context: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
        retryable: bool | None = None,
        log_context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        self.details = details or []
        #: Structured context *about the failure*, as a dict.
        self.context = context or {}
        #: Code-specific keys merged directly into the error object (e.g. ``capacity``).
        self.extra = extra or {}
        if retryable is not None:
            self.retryable = retryable
        #: Extra context for the log line only — never serialised to the client.
        self.log_context = log_context or {}

    def to_response(self, *, request_id: str | None = None) -> dict[str, Any]:
        error: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "requestId": request_id,
            "timestamp": to_iso(utcnow()),
        }
        if self.details:
            error["details"] = [issue.as_dict() for issue in self.details]
        if self.context:
            error["context"] = self.context
        error.update(self.extra)
        return {"error": error}


class BadRequestError(AppError):
    """400 — the request itself is malformed (bad JSON, bad params, unparseable CSV)."""

    status_code = 400
    code = "BAD_REQUEST"

    def __init__(self, message: str, details: list[FieldIssue] | None = None) -> None:
        super().__init__(message, details=details)


class ValidationError(AppError):
    """422 — the request was understood but what it describes is not valid."""

    status_code = 422
    code = "VALIDATION_FAILED"

    def __init__(
        self,
        message: str = "The submitted data is not valid.",
        details: list[FieldIssue] | None = None,
        *,
        code: str | None = None,
        context: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        # ``context`` forwarded because ``AppError`` has always accepted it and renders it; the
        # capabilities that describe *which* configuration or question set was invalid need it,
        # and the alternative was each of them defining a near-identical 422 of its own.
        super().__init__(message, code=code, details=details, context=context, extra=extra)


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"

    def __init__(
        self,
        resource: str,
        identifier: str | None = None,
        *,
        code: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        message = (
            f"{resource} '{identifier}' was not found."
            if identifier
            else f"{resource} was not found."
        )
        # ``code`` lets a capability name *which* thing was missing — ``ATTEMPT_NOT_FOUND`` rather
        # than a bare ``NOT_FOUND`` — without inventing a second 404 class per resource.
        super().__init__(message, code=code, context=context)


class ConflictError(AppError):
    """409 — the request conflicts with the resource's current lifecycle state."""

    status_code = 409
    code = "CONFLICT"

    def __init__(
        self,
        message: str,
        *,
        code: str = "CONFLICT",
        details: list[FieldIssue] | None = None,
        context: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        # Most conflicts are permanent — the state genuinely disagrees with the request. A few
        # are not: "no configuration version is active *yet*" is a conflict now and may resolve
        # on its own, and a client needs to be able to tell those apart to decide whether a
        # retry button makes sense.
        super().__init__(
            message,
            code=code,
            details=details,
            context=context,
            extra=extra,
            retryable=retryable,
        )


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "PAYLOAD_TOO_LARGE"


class UnauthorizedError(AppError):
    status_code = 401
    code = "UNAUTHORIZED"

    def __init__(self, message: str = "Authentication is required for this operation.") -> None:
        super().__init__(message)


class ForbiddenError(AppError):
    status_code = 403
    code = "FORBIDDEN"

    def __init__(
        self,
        message: str = "You do not have permission to perform this action.",
        *,
        code: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        # ``context`` names *which* resource was refused, which an operator needs when a learner
        # reports being locked out of something they believe is theirs. It never contains the
        # requester's credentials — only the identifiers already in the request.
        super().__init__(message, code=code, context=context)


class DatabaseError(AppError):
    """500 — a persistence failure. The underlying driver error is logged, not returned."""

    status_code = 500
    code = "DATABASE_ERROR"

    def __init__(self, message: str = "The request could not be persisted.") -> None:
        super().__init__(message)


class PersistenceFailedError(AppError):
    """503 — a write failed and *nothing was applied*, so the caller may safely retry.

    Distinct from :class:`DatabaseError`: this promises the previous state is intact, which is
    what lets the admin UI offer a "Retry save" button without risking a duplicate version.
    """

    status_code = 503
    code = "PERSISTENCE_FAILED"

    def __init__(
        self,
        operation: str,
        cause: BaseException | None = None,
        message: str = (
            "The change could not be saved because of a temporary problem. "
            "Nothing was saved — please try again."
        ),
    ) -> None:
        super().__init__(
            message,
            retryable=True,
            log_context={"operation": operation, "cause": str(cause) if cause else None},
        )


#: Every error code the shared kernel itself can produce, independent of any capability's taxonomy.
#:
#: A capability owns the codes for its *domain* failures (``MAX_ATTEMPTS_REACHED``,
#: ``DUPLICATE_QUESTION``…), but a request can also fail before or beneath the domain: a malformed
#: body, an unknown route, an integrity violation. Those codes are the platform's, and they are
#: listed here so a capability's error-taxonomy test can assert that everything its API returns is
#: either one of its own codes or one of these. Without that, an unhandled failure quietly
#: introduces an undocumented code and no test notices.
PLATFORM_ERROR_CODES: frozenset[str] = frozenset(
    {
        # Raised deliberately, by the classes above.
        BadRequestError.code,
        ValidationError.code,
        NotFoundError.code,
        ConflictError.code,
        PayloadTooLargeError.code,
        UnauthorizedError.code,
        ForbiddenError.code,
        DatabaseError.code,
        PersistenceFailedError.code,
        # Produced only by the exception handlers, for failures nothing raises on purpose.
        "METHOD_NOT_ALLOWED",
        "UNSUPPORTED_MEDIA_TYPE",
        "HTTP_ERROR",
        "INTEGRITY_CONFLICT",
        "INTERNAL_ERROR",
    }
)


class ProviderUnavailableError(AppError):
    """503 — a module or external system this request depends on could not be reached.

    Added with UC-08/09/10, which are the first capabilities whose work genuinely spans
    several modules at request time: a retake reads UC-01, UC-02, UC-03, UC-04 and UC-05
    before it can answer, and one of them being down is not the same failure as a bad request
    or a broken database. Retryable, and nothing was written — the services that raise it do
    so before any write, or after rolling one back.

    ``provider`` names the boundary, not the vendor, so an error body never discloses
    infrastructure.
    """

    status_code = 503
    code = "PROVIDER_UNAVAILABLE"
    retryable = True

    def __init__(
        self,
        provider: str,
        message: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message
            or (
                "A system this request depends on is temporarily unavailable. "
                "Nothing was changed — please try again shortly."
            ),
            context={"provider": provider},
            log_context={"provider": provider, "cause": str(cause) if cause else None},
        )


class UpstreamTimeoutError(AppError):
    """504 — a dependency was reachable but did not answer in time.

    Deliberately distinct from :class:`ProviderUnavailableError`: a timeout means the request
    may still be running upstream, so the retry has to be idempotent. Every operation that can
    raise this is keyed, which is why it is safe to advertise as retryable.
    """

    status_code = 504
    code = "UPSTREAM_TIMEOUT"
    retryable = True

    def __init__(self, provider: str, message: str | None = None) -> None:
        super().__init__(
            message
            or "A system this request depends on did not respond in time. Please try again.",
            context={"provider": provider},
            log_context={"provider": provider},
        )
