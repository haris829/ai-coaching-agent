"""Typed errors. Failure is handled by category, never by catching bare Exception.

Two families:

* Contract exceptions (ProviderUnavailable / ProviderTimeout /
  ProviderInvalidResponse) - what every port is allowed to raise. Adapters
  translate upstream failures into these. No upstream error text, payload shape
  or provider name may travel inside them past the adapter boundary.

* Domain refusals - deliberate, safe refusals decided by UC-06 itself.
"""

from __future__ import annotations


class Uc06Error(Exception):
    """Root of every error UC-06 raises. Message text is internal-only."""

    code = "internal_error"


# --------------------------------------------------------------------------
# Port contract errors
# --------------------------------------------------------------------------
class ProviderError(Uc06Error):
    """Base for every failure an adapter is permitted to surface.

    `port` names the port (not the provider implementation) so that logs can say
    what failed without naming a third party, and `detail` is internal-only.
    """

    code = "provider_error"

    def __init__(self, port: str, detail: str = "") -> None:
        super().__init__(f"{port}: {detail}" if detail else port)
        self.port = port
        self.detail = detail


class ProviderUnavailable(ProviderError):
    code = "provider_unavailable"


class ProviderTimeout(ProviderError):
    code = "provider_timeout"


class ProviderInvalidResponse(ProviderError):
    """The upstream answered, but with something we refuse to map or trust.

    Includes: a NARIC value matching no enum member; a case file of the wrong
    shape; a generation referencing a fact identifier that is not in the case
    file (fabricated evidence about a live matter).
    """

    code = "provider_invalid_response"


# --------------------------------------------------------------------------
# Domain refusals
# --------------------------------------------------------------------------
class CaseAccessDenied(Uc06Error):
    """The user does not hold read access to the requested case file."""

    code = "case_access_denied"


class CaseOriginRejected(Uc06Error):
    """The case file did not originate from the Case Prep Agent."""

    code = "case_origin_rejected"


class SessionNotCaseLinked(Uc06Error):
    code = "session_not_case_linked"


class SessionHalted(Uc06Error):
    """Case-linked coaching is halted for this session pending admin clearance."""

    code = "session_halted"


class DisclaimerBoundaryFailure(Uc06Error):
    """CRITICAL. A case-linked payload reached the boundary without the exact
    canonical disclaimer. Fails closed: nothing is emitted, the session halts,
    the admin is alerted and a security incident is recorded."""

    code = "disclaimer_boundary_failure"

    def __init__(self, reason: str, observed_present: bool) -> None:
        super().__init__(reason)
        self.reason = reason
        self.observed_present = observed_present


class DevSessionMintingDisabled(Uc06Error):
    code = "dev_session_minting_disabled"


class ConfigurationError(Uc06Error):
    """Raised at startup. Never falls back to a mock."""

    code = "configuration_error"
