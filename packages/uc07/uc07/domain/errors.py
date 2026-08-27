"""Typed error taxonomy.

Design rules enforced here (and by tests):

* Provider failures are typed. Business logic branches on the *type*, never on a
  message string, and never by catching bare ``Exception``.
* Errors carry a *port* label (``interaction_log``, ``feedback``, ...), never a
  provider/adapter name, never upstream error text, never a URL, never payload
  content. This is why the constructors accept no free-text detail: leakage is
  impossible by construction.
"""

from __future__ import annotations

from enum import Enum


class PortName(str, Enum):
    """Stable, non-identifying labels for the read-only ports."""

    INTERACTION_LOG = "interaction_log"
    FEEDBACK = "feedback"
    LEARNER_PROFILE = "learner_profile"
    COURSES = "courses"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


class Uc07Error(Exception):
    """Base class for every error raised by UC-07."""

    code: str = "uc07_error"


class ProviderError(Uc07Error):
    """Base class for read-only provider failures.

    Deliberately has no ``detail``/``cause_text`` parameter so that no upstream
    string can travel with the exception.
    """

    code = "provider_error"
    _summary = "could not be read"

    def __init__(self, port: PortName | str) -> None:
        self.port = PortName(port)
        super().__init__(f"Source for port '{self.port.value}' {self._summary}.")


class ProviderUnavailable(ProviderError):
    """The source could not answer at all (transport down, refused, 5xx...)."""

    code = "provider_unavailable"
    _summary = "is unavailable"


class ProviderTimeout(ProviderError):
    """The source did not answer within the configured budget."""

    code = "provider_timeout"
    _summary = "timed out"


class ProviderInvalidResponse(ProviderError):
    """The source answered with something that cannot satisfy the platform contract.

    Adapters raise this instead of bending the domain model or inventing values.
    """

    code = "provider_invalid_response"
    _summary = "returned a response that does not satisfy the platform contract"


class ConfigurationError(Uc07Error):
    """Raised at startup for an unknown/unusable configuration (fails loudly)."""

    code = "configuration_error"


class EvidenceIntegrityError(Uc07Error):
    """A generated gap carried unresolvable or malformed evidence.

    This is a defect guard: the offending gap is rejected rather than emitted.
    """

    code = "evidence_integrity_error"


class ReportOwnershipError(Uc07Error):
    """A stored report did not belong to the resolved server-side user."""

    code = "report_ownership_error"


class InteractionSourceUnusable(Uc07Error):
    """Interaction history could not be loaded, so no report can be derived.

    UC-07 never returns an empty report to paper over this.
    """

    code = "interaction_source_unusable"

    def __init__(self, status_code: str) -> None:
        # ``status_code`` is one of SourceStatus.UNAVAILABLE/INVALID/... values.
        self.source_status = status_code
        super().__init__(
            "Interaction history could not be loaded "
            f"(source status: {status_code}); no gap report can be derived."
        )
