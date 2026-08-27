"""Typed error taxonomy.

Failure is handled by category. UC-04 never catches bare ``Exception`` around a port call, and
no adapter-internal exception type, upstream error string or provider name reaches the client.
"""

from __future__ import annotations


class UC04Error(Exception):
    """Base for everything raised inside UC-04."""


class ProviderError(UC04Error):
    """Base for a failure attributable to an upstream dependency."""

    def __init__(self, port: str, detail: str = "") -> None:
        super().__init__(detail or port)
        self.port = port
        #: Kept for server-side logging only. Never serialised into a response.
        self.detail = detail


class ProviderUnavailable(ProviderError):
    """The dependency could not be reached, or reported itself down."""


class ProviderTimeout(ProviderError):
    """The dependency did not answer inside its budget."""


class ProviderInvalidResponse(ProviderError):
    """The dependency answered with something that cannot be mapped to the domain model."""


class NotFound(ProviderError):
    """The dependency answered correctly: the thing does not exist."""


class NotEnrolled(UC04Error):
    """The learner is not enrolled on the course the session is linked to."""

    def __init__(self, user_id: str, course_id: str, reason: str | None = None) -> None:
        super().__init__("not enrolled")
        self.user_id = user_id
        self.course_id = course_id
        self.reason = reason


class AccessDenied(UC04Error):
    """The caller does not own the resource they asked for."""


class SessionIdentityError(UC04Error):
    """A session identifier was required and none was supplied."""
