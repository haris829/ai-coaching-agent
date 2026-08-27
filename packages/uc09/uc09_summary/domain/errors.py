"""Typed errors. Port errors are the only failure vocabulary that crosses an adapter boundary.

An adapter must translate every upstream failure into one of the three
``ProviderError`` subclasses. Upstream error text, payload shapes and provider
names must not travel with them: ``ProviderError.detail`` is for logs only and
is never rendered into an API response.
"""

from __future__ import annotations


class Uc09Error(Exception):
    """Base class for everything this component raises deliberately."""


class ProviderError(Uc09Error):
    """Base class for a failure at a port boundary.

    Args:
        port: logical port name, e.g. ``"session_provider"``.
        detail: operator-facing text. Never returned to a client.
    """

    code = "provider_error"

    def __init__(self, port: str, detail: str = "") -> None:
        self.port = port
        self.detail = detail
        super().__init__(f"{self.code}: {port}")


class ProviderUnavailable(ProviderError):
    """Upstream could not be reached, refused, or returned a transport error."""

    code = "provider_unavailable"


class ProviderTimeout(ProviderError):
    """Upstream did not answer inside the configured deadline."""

    code = "provider_timeout"


class ProviderInvalidResponse(ProviderError):
    """Upstream answered with something that violates the contract.

    Also raised when a generator returns content it cannot ground in session
    data. On a document of record an ungrounded response is rejected whole; it
    is never silently stripped and continued.
    """

    code = "provider_invalid_response"


class GroundingViolation(ProviderInvalidResponse):
    """A generated section carried a claim with no source in the session record.

    Args:
        violations: one message per ungrounded claim, carrying the kind, the
            identifier and a machine reason. Operator-facing detail; never
            rendered into an API response.
        reasons: the same violations with identifiers dropped. This is the form
            that is safe to write to application logs, because a topic or
            concept identifier would disclose what a named learner studied.
    """

    code = "grounding_violation"

    def __init__(
        self,
        violations: list[str],
        port: str = "summary_generator",
        *,
        reasons: list[str] | None = None,
    ) -> None:
        self.violations = list(violations)
        self.reasons = list(reasons) if reasons is not None else []
        super().__init__(port, detail="; ".join(violations))


class SummaryNotFound(Uc09Error):
    """No summary with that identifier is visible to the caller.

    Deliberately also raised when a summary exists but belongs to somebody
    else, so that a probe cannot distinguish the two.
    """

    code = "summary_not_found"


class SessionNotFound(Uc09Error):
    """No session with that identifier is visible to the caller."""

    code = "session_not_found"


class AccessDenied(Uc09Error):
    """Caller is not the owner of the record. Mapped to a not-found response."""

    code = "access_denied"


class IdentityUnresolved(Uc09Error):
    """No user identity could be resolved for the request."""

    code = "identity_unresolved"


class ProviderNotRegistered(Uc09Error):
    """Configuration names a provider that has no registered implementation.

    Raised at startup. Never followed by a silent fallback to a mock.
    """

    code = "provider_not_registered"


class RenderingUnavailable(Uc09Error):
    """PDF rendering failed. The caller is served the canonical HTML instead."""

    code = "rendering_unavailable"
