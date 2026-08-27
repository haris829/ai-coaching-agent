"""Contract-level exceptions.

These are the **only** failure signals an adapter may raise. An adapter must never let
an upstream library exception (``httpx.HTTPError``, ``sqlite3.Error``, a vendor SDK
error, ...) escape: it catches it, logs the technical detail, and re-raises one of these
with a safe summary.

That is the boundary that keeps UC-01 business logic free of external API specifics.
"""

from __future__ import annotations


class ContractError(Exception):
    """Base class for adapter-boundary failures."""

    def __init__(
        self,
        dependency: str,
        *,
        technical_detail: str | None = None,
        retryable: bool = True,
    ) -> None:
        self.dependency = dependency
        self.technical_detail = technical_detail
        self.retryable = retryable
        super().__init__(f"{type(self).__name__}({dependency})")


class DependencyUnavailableError(ContractError):
    """The dependency could not be reached, timed out, or returned a server error."""


class InvalidUpstreamResponseError(ContractError):
    """The dependency answered, but the payload could not be normalised.

    Treated exactly like unavailable by UC-01 business logic — never partially trusted.
    """

    def __init__(self, dependency: str, *, technical_detail: str | None = None) -> None:
        super().__init__(dependency, technical_detail=technical_detail, retryable=False)


class ResourceNotAccessibleError(ContractError):
    """The requested resource does not exist for this user, or the user may not use it.

    Adapters must not distinguish 'missing' from 'forbidden' to the caller: UC-01 maps
    both to one non-enumerable user-facing message.
    """

    def __init__(
        self,
        dependency: str,
        *,
        resource_id: str | None = None,
        technical_detail: str | None = None,
    ) -> None:
        self.resource_id = resource_id
        super().__init__(dependency, technical_detail=technical_detail, retryable=False)


__all__ = [
    "ContractError",
    "DependencyUnavailableError",
    "InvalidUpstreamResponseError",
    "ResourceNotAccessibleError",
]
