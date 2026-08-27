"""Typed errors for the UC-02 domain.

The assembly service handles upstream failure *by category*, never by catching
bare ``Exception``. Adapters (mock or real) are expected to translate whatever
their transport raises into one of the ``ProviderError`` subclasses below.
"""

from __future__ import annotations

from uc02.domain.models.enums import ErrorCategory, SourceName


class Uc02Error(Exception):
    """Base class for every error UC-02 raises deliberately."""


# --------------------------------------------------------------------------
# Provider (upstream integration) errors
# --------------------------------------------------------------------------
class ProviderError(Uc02Error):
    """Base class for an upstream source failing to deliver usable data."""

    category: ErrorCategory = ErrorCategory.UNAVAILABLE

    def __init__(self, source: SourceName | str, message: str = "") -> None:
        self.source = SourceName(source) if not isinstance(source, SourceName) else source
        self.message = message or self.__class__.__name__
        super().__init__(f"{self.source.value}: {self.message}")


class ProviderUnavailable(ProviderError):
    """The upstream system could not be reached or refused to serve the request."""

    category = ErrorCategory.UNAVAILABLE


class ProviderTimeout(ProviderError):
    """The upstream system did not respond inside the per-provider timeout."""

    category = ErrorCategory.TIMEOUT


class ProviderInvalidResponse(ProviderError):
    """The upstream system responded, but not in a shape we can normalise."""

    category = ErrorCategory.INVALID_RESPONSE


class ProviderBudgetExceeded(ProviderError):
    """The overall assembly budget elapsed before this source resolved."""

    category = ErrorCategory.BUDGET_EXCEEDED


class ProviderUnexpectedError(ProviderError):
    """An adapter raised something outside its declared contract.

    Treated as an invalid response: the adapter, not the network, is at fault.
    """

    category = ErrorCategory.UNEXPECTED


# --------------------------------------------------------------------------
# Application / API errors
# --------------------------------------------------------------------------
class ContextNotFound(Uc02Error):
    """No stored context for the given session id (or it expired)."""


class ContextAccessDenied(Uc02Error):
    """A stored context exists but is bound to a different user.

    The API deliberately answers this with 404 so a session id cannot be used
    to probe for the existence of another learner's context.
    """


class SessionIdRequired(Uc02Error):
    """No session id supplied and dev-minted session ids are disabled."""


class ForceRefreshNotPermitted(Uc02Error):
    """``force_refresh`` was requested on a path that does not allow it."""


class IdentityResolutionFailed(Uc02Error):
    """The caller's identity could not be resolved; the request is unauthenticated."""


class ProviderNotImplemented(Uc02Error):
    """A configured provider implementation does not exist yet (see docs/integration.md)."""
