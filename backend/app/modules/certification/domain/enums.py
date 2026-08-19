"""UC-05's vocabulary."""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "CERTIFICATE_TERMINAL_STATUSES",
    "CertificateStatus",
    "CpdSyncStatus",
    "Outcome",
    "OUTCOME_LABELS",
]


class Outcome(StrEnum):
    """The pass/fail determination. Derived from a confirmed score, and never revised."""

    PASS = "PASS"
    FAIL = "FAIL"


OUTCOME_LABELS: dict[Outcome, str] = {Outcome.PASS: "Pass", Outcome.FAIL: "Fail"}


class CertificateStatus(StrEnum):
    """Lifecycle of one certificate request.

    ``PENDING`` is the honest state for "the learner passed, and the certificate service has not
    confirmed issue yet". It is retryable and carries the failure reason, which is what makes an
    unavailable certificate service a delay rather than a lost pass.
    """

    #: Requested, not yet issued. Retryable.
    PENDING = "PENDING"
    #: Issued. Terminal, and immutable.
    ISSUED = "ISSUED"
    #: Permanently rejected by the certificate service. Retryable only after the cause is fixed.
    FAILED = "FAILED"


#: Statuses that must never be turned back into PENDING by a retry.
CERTIFICATE_TERMINAL_STATUSES: frozenset[CertificateStatus] = frozenset({CertificateStatus.ISSUED})


class CpdSyncStatus(StrEnum):
    """Lifecycle of one CPD synchronisation."""

    PENDING = "PENDING"
    SYNCHRONISED = "SYNCHRONISED"
    FAILED = "FAILED"
