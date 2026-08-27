"""Typed contract errors.

Adapters translate every upstream failure into one of these. Nothing upstream --
no payload shape, no provider name, no vendor error text -- crosses the port
boundary. See ``docs/SHARED_CONTRACT.md``.
"""

from __future__ import annotations


class Uc08Error(Exception):
    """Base for every error this component raises."""


# --------------------------------------------------------------------------
# Upstream (read-only provider) contract errors
# --------------------------------------------------------------------------
class ProviderError(Uc08Error):
    """Base for upstream provider failures.

    ``port`` is the abstract port name (e.g. ``activity``), never a vendor name.
    """

    def __init__(self, port: str, detail: str = "") -> None:
        self.port = port
        self.detail = detail
        super().__init__(f"{self.__class__.__name__}(port={port!r})" + (f": {detail}" if detail else ""))


class ProviderUnavailable(ProviderError):
    """The upstream did not answer. Distinct from an empty answer."""


class ProviderTimeout(ProviderError):
    """The upstream exceeded the configured deadline."""


class ProviderInvalidResponse(ProviderError):
    """The upstream answered with something that cannot be mapped to the
    platform contract."""


# --------------------------------------------------------------------------
# Persistence errors (records this component owns)
# --------------------------------------------------------------------------
class RepositoryError(Uc08Error):
    """Base for persistence failures."""


class RepositoryWriteFailed(RepositoryError):
    """A write did not commit. Handled by retry-once-then-preserve.

    Catching this must never lead to a streak reset.
    """


class RepositoryReadFailed(RepositoryError):
    """A read did not complete."""


# --------------------------------------------------------------------------
# Sink errors
# --------------------------------------------------------------------------
class NotificationSendFailed(Uc08Error):
    """A notification could not be delivered. Never blocks coaching."""


# --------------------------------------------------------------------------
# Composition-root errors
# --------------------------------------------------------------------------
class ProviderNotRegistered(Uc08Error):
    """A configured provider name has no registered implementation.

    Raised at startup. There is no silent fallback to a mock.
    """


class ProviderRegistrationBroken(Uc08Error):
    """A registered provider entry could not be loaded (bad module, missing
    class, or the class does not implement the port)."""


# --------------------------------------------------------------------------
# Domain rule violations
# --------------------------------------------------------------------------
class FreezeNotAvailable(Uc08Error):
    """No acceptable freeze offer exists for this user."""


class SessionIdRequired(Uc08Error):
    """A production path was reached without an inbound session id and dev
    minting is disabled."""
