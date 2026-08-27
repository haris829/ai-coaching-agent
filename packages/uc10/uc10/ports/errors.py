"""Typed contract errors raised by every port.

An adapter translates *whatever* its upstream did into exactly one of these.  The error
carries a port name and a ``reason_code`` drawn from a closed, machine-readable
vocabulary -- never upstream error text, never an upstream field name, never a provider
or vendor name.  The conformance suite asserts this at the boundary.
"""

from __future__ import annotations

import re

_REASON_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class PortError(Exception):
    """Base class for every error crossing a port boundary."""

    default_retryable: bool = False

    def __init__(self, port: str, reason_code: str, *, retryable: bool | None = None) -> None:
        if not _REASON_CODE.match(reason_code):
            raise ValueError(
                "reason_code must be a lowercase snake_case token; upstream error text "
                "must never be forwarded across a port boundary"
            )
        self.port = port
        self.reason_code = reason_code
        self.retryable = self.default_retryable if retryable is None else retryable
        super().__init__(f"{port}: {reason_code}")

    def __str__(self) -> str:
        return f"{self.port}: {self.reason_code}"


class ProviderUnavailable(PortError):
    """The upstream could not be reached, or refused to serve. Distinct from 'empty'."""

    default_retryable = True


class ProviderTimeout(PortError):
    """The upstream did not answer inside the adapter's own deadline."""

    default_retryable = True


class ProviderInvalidResponse(PortError):
    """The upstream answered with something that cannot be mapped to the platform contract."""

    default_retryable = False


class RecordNotFound(PortError):
    """ASSUMED BY US (A-10): a well-formed request for a record that does not exist."""

    default_retryable = False
