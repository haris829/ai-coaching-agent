"""The certificate-service boundary. Certificate generation belongs to a system UC-05 does not own
-- today a local adapter, tomorrow the company's certificate service. Modelling it as a port with
an explicit transient/permanent distinction is what makes the ``PENDING`` certificate state
meaningful and testable, exactly as UC-03's submission-dispatch port does for its own pending
state. The contract is deliberately narrow: * ``issue`` is given everything the document needs
(learner, course name, score, date) and returns the identifier of the issued certificate; *
raising :class:`TransientCertificateError` means "try again later" -- the certificate stays
``PENDING`` and the retry endpoint can drive it; * any other exception is permanent: the
certificate is marked ``FAILED`` with the reason, and a human has to look at it. In neither case
does the quiz result change. UC-05 has already determined and persisted the outcome before this
port is called, which is the whole point of the ordering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

__all__ = [
    "CertificateRequest",
    "CertificateIssued",
    "CertificateServicePort",
    "TransientCertificateError",
]


@dataclass(frozen=True, slots=True)
class CertificateRequest:
    """What the certificate service is asked to produce."""

    attempt_id: str
    learner_id: str
    course_id: str
    quiz_id: str
    course_name: str
    quiz_title: str | None
    percentage: float
    total_marks: float
    maximum_marks: float
    #: ISO-8601 UTC instant of the attempt's submission -- the date that goes on the certificate.
    attempt_date: str
    #: Stable key for this certificate. The same key must never mint two documents.
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class CertificateIssued:
    """Acknowledgement from the certificate service."""

    certificate_number: str
    document_reference: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class TransientCertificateError(Exception):
    """Raised when generation failed for a reason that may succeed on a retry. Leaves the
    certificate ``PENDING``. Any other exception is treated as permanent."""


class CertificateServicePort(Protocol):
    """Generation of a certificate for a passing attempt."""

    def issue(self, request: CertificateRequest) -> CertificateIssued: ...
