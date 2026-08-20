"""The attempt allowance (§1).

::

    configured maximum attempts
            −  attempts already used
            +  learner-specific granted attempts
            =  available attempts

UC-05 already owns the first two terms and states the rule they follow: the maximum comes from
the configuration version **locked to the attempt**, so an administrator who lowers the limit
afterwards cannot retroactively strip an attempt the learner already held. UC-08 keeps that rule
exactly and adds the third term, which is the only part of the calculation this module owns.

The third term is what makes a grant a *learner-specific* entitlement rather than a configuration
change: ``maximum_attempts`` is carried through untouched and reported separately from
``granted_attempts``, so a caller can always see that the course-wide maximum is still 2 while
this learner's entitlement is 3 (§11).

Pure functions only. The caller supplies the numbers; this module decides what they mean. Every
input is treated as untrusted because two of the three come from other use cases and the third
comes from an administrator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AttemptAllowance:
    """What the learner may do next, and the arithmetic that produced it.

    ``available_attempts`` is ``None`` only when attempts are unlimited, which is a genuinely
    different statement from ``0``. ``has_available_attempts`` is the flag a caller branches on,
    so an unlimited quiz never has to be special-cased at the call site.
    """

    #: The course-wide configured maximum. Never modified by a grant.
    maximum_attempts: int | None
    #: Attempts consumed: UC-03's count plus any UC-08 reservation UC-03 has not seen yet.
    attempts_used: int
    #: Additional attempts granted to *this* learner for *this* quiz by an administrator.
    granted_attempts: int
    #: maximum + granted, i.e. what this learner is entitled to in total.
    total_entitlement: int | None
    available_attempts: int | None
    has_available_attempts: bool
    unlimited: bool
    #: True when the learner would have nothing left without the grant. Lets the caller report
    #: ADDITIONAL_ATTEMPT_AVAILABLE rather than a plain ELIGIBLE.
    relies_on_grant: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "maximum_attempts": self.maximum_attempts,
            "attempts_used": self.attempts_used,
            "granted_attempts": self.granted_attempts,
            "total_entitlement": self.total_entitlement,
            "available_attempts": self.available_attempts,
            "has_available_attempts": self.has_available_attempts,
            "unlimited": self.unlimited,
            "relies_on_grant": self.relies_on_grant,
        }


def compute_allowance(
    *,
    maximum_attempts: int | None,
    attempts_used: int,
    granted_attempts: int = 0,
) -> AttemptAllowance:
    """Calculate the allowance from a configured maximum, a used count and granted attempts.

    Defensive on every input:

    * a negative or non-integer maximum is treated as *unconfigured* (unlimited) rather than as
      zero, so a configuration defect can never silently tell a learner they are out of attempts
      — the same choice UC-05 makes, reported as an anomaly by the caller;
    * a used count is clamped at zero, and a count above the entitlement yields ``0`` available
      rather than a negative number;
    * granted attempts are clamped at zero, so a corrupt grant total can only ever fail to help
      a learner, never take an attempt away from them.
    """
    used = max(0, int(attempts_used)) if _is_int(attempts_used) else 0
    granted = max(0, int(granted_attempts)) if _is_int(granted_attempts) else 0

    if not _is_positive_int(maximum_attempts):
        return AttemptAllowance(
            maximum_attempts=None,
            attempts_used=used,
            granted_attempts=granted,
            total_entitlement=None,
            available_attempts=None,
            has_available_attempts=True,
            unlimited=True,
            relies_on_grant=False,
        )

    maximum = int(maximum_attempts)  # type: ignore[arg-type]  - guarded above
    entitlement = maximum + granted
    available = max(0, entitlement - used)
    return AttemptAllowance(
        maximum_attempts=maximum,
        attempts_used=used,
        granted_attempts=granted,
        total_entitlement=entitlement,
        available_attempts=available,
        has_available_attempts=available > 0,
        unlimited=False,
        # The learner has something left, but would not have without the grant.
        relies_on_grant=available > 0 and granted > 0 and used >= maximum,
    )


def is_valid_maximum(maximum_attempts: object) -> bool:
    """True when a configured maximum is usable.

    ``None`` (unlimited) is valid configuration; ``0``, a negative number or a non-integer is
    not, and the caller records an ``INVALID_ATTEMPT_ALLOWANCE`` anomaly for it.
    """
    return maximum_attempts is None or _is_positive_int(maximum_attempts)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_positive_int(value: object) -> bool:
    return _is_int(value) and int(value) > 0  # type: ignore[arg-type]
