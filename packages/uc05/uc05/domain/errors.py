"""Typed errors.

Two families:

*   ``ProviderError`` and its three subclasses form the **port contract**.
    Every adapter, mock or real, translates whatever its upstream does into
    exactly one of these.  Application code handles them by category and never
    catches bare ``Exception``.
*   ``DomainError`` and its subclasses are UC-05's own refusals.

No error message produced here is ever returned to a client verbatim; the API
layer maps error *classes* onto a fixed, safe envelope (see
``uc05/api/errors.py``).
"""

from __future__ import annotations


class UC05Error(Exception):
    """Root of every error UC-05 raises deliberately."""


# --------------------------------------------------------------------------
# Port contract errors.  An adapter may raise ONLY these past its boundary.
# --------------------------------------------------------------------------


class ProviderError(UC05Error):
    """Base class for every failure of an external dependency.

    ``port`` names the port (not the vendor, not the module) so that operators
    can attribute a failure without the provider name reaching a client.
    """

    retryable: bool = False

    def __init__(self, port: str, detail: str = "") -> None:
        self.port = port
        self.detail = detail
        super().__init__(f"{type(self).__name__}[{port}]")


class ProviderUnavailable(ProviderError):
    """The dependency could not be reached, or refused the request."""

    retryable = True


class ProviderTimeout(ProviderError):
    """The dependency did not answer inside the configured budget."""

    retryable = True


class ProviderInvalidResponse(ProviderError):
    """The dependency answered, but the answer does not satisfy the contract.

    This is the error raised when a guiding-question generator returns a direct
    answer, when a four-part answer is missing a part, when a NARIC value maps
    to no enum member, and when output is structurally malformed.  It is never
    retried automatically and never passed through to the learner.
    """

    retryable = False


# --------------------------------------------------------------------------
# Domain errors.
# --------------------------------------------------------------------------


class DomainError(UC05Error):
    """Base class for UC-05's own refusals."""


class InvalidTransition(DomainError):
    """The event is not legal from the dialogue's current state."""

    def __init__(self, state: str, event: str) -> None:
        self.state = state
        self.event = event
        super().__init__(f"event {event!r} is not legal from state {state!r}")


class DialogueNotFound(DomainError):
    pass


class AccessDenied(DomainError):
    """The caller is not the owner of the dialogue or session addressed."""


class UnknownProvider(DomainError):
    """A configured provider key has no registered implementation.

    Raised at composition time, never at request time: a service that quietly
    runs on fake data in production is worse than one that refuses to start.
    """


class DevEndpointDisabled(DomainError):
    """A development-only helper was called while gated off by config."""
