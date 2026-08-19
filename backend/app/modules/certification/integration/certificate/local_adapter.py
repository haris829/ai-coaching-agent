"""The certificate service, as implemented locally until the company's exists.

It issues a real, deterministic certificate number and nothing else: no PDF, no email, no external
call. That is a deliberate choice over simulating an integration -- a fake that pretended to render
a document would have to be unpicked, whereas this is the honest local implementation of the port
and is replaced by pointing the composition root at the company's adapter.

The number is derived from the attempt id, so it is **stable across retries**: driving a pending
certificate again produces the same number rather than a second document. Duplicate prevention does
not rest on that alone -- the partial unique index on ``qg_certificates`` is the guarantee -- but a
deterministic number means a retry cannot even look like a new certificate.
"""

from __future__ import annotations

import hashlib

from app.modules.certification.integration.certificate.port import (
    CertificateIssued,
    CertificateRequest,
)

#: Prefix for locally issued certificate numbers, so they are recognisable in support tickets.
LOCAL_PREFIX = "CERT"


def certificate_number_for(attempt_id: str) -> str:
    """A stable, human-quotable certificate number for one attempt."""
    digest = hashlib.sha256(attempt_id.encode("utf-8")).hexdigest()[:10].upper()
    return f"{LOCAL_PREFIX}-{digest}"


class LocalCertificateService:
    """:class:`~...certificate.port.CertificateServicePort`, implemented in-process."""

    __slots__ = ()

    def issue(self, request: CertificateRequest) -> CertificateIssued:
        number = certificate_number_for(request.attempt_id)
        return CertificateIssued(
            certificate_number=number,
            document_reference=f"local://certificates/{number}",
            metadata={
                "courseName": request.course_name,
                "quizTitle": request.quiz_title,
                "percentage": request.percentage,
                "attemptDate": request.attempt_date,
                "issuedBy": "local-certificate-service",
                "idempotencyKey": request.idempotency_key,
            },
        )
