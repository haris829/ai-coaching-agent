"""DISCLAIMER ENFORCEMENT LAYER 2 - the serialisation boundary check.

There is no frontend in this build, so the outermost boundary at which the
disclaimer can be applied and verified is response serialisation. This module is
that boundary.

It is deliberately independent of the type layer and does not trust it:

* It takes a plain serialised mapping, not a response object, so it cannot be
  satisfied by a type guarantee it never sees.
* It re-checks the exact canonical string byte for byte. No trimming, no case
  folding, no "contains" check.
* It also fails a payload that carries any suppression-shaped key, which is the
  fingerprint of tampering somewhere upstream of it.

It has no configuration. Nothing constructs it with options, and there is no
setting, flag, environment variable or request parameter that changes what it
does or whether it runs.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

from ..domain.disclaimer import (
    CANONICAL_DISCLAIMER,
    DISCLAIMER_FIELD,
    KNOWN_VARIANT_UC06_STEP5,
    is_canonical,
)
from ..domain.errors import DisclaimerBoundaryFailure
from ..domain.guard_vocabulary import DISCLAIMER_SUPPRESSION_KEYS
from ..domain.responses import DisclaimedResponse

#: Reason codes emitted with a boundary failure. Internal-only; never returned
#: to a client.
REASON_ABSENT = "disclaimer_absent"
REASON_EMPTY = "disclaimer_empty"
REASON_WRONG_TYPE = "disclaimer_wrong_type"
REASON_SHORTENED_VARIANT = "disclaimer_shortened_variant"
REASON_ALTERED = "disclaimer_altered"
REASON_SUPPRESSION_KEY = "suppression_key_in_payload"


@runtime_checkable
class PayloadSerializer(Protocol):
    """Turns a response object into the mapping that will be emitted.

    Exists as a seam so that layer 2 can be exercised against a deliberately
    corrupt payload in tests without any production code path - and therefore
    without any configuration key - being able to produce one.
    """

    def serialize(self, response: DisclaimedResponse) -> dict[str, Any]:
        ...


class DefaultPayloadSerializer:
    """The only serializer wired in the composition root."""

    def serialize(self, response: DisclaimedResponse) -> dict[str, Any]:
        return response.to_payload()


def check_payload(payload: Mapping[str, Any]) -> None:
    """Raise DisclaimerBoundaryFailure unless the payload is safe to emit.

    Called on every payload leaving the case-coaching surface, including error
    envelopes and degraded fallbacks.
    """
    for key in payload:
        if key.lower() in DISCLAIMER_SUPPRESSION_KEYS:
            raise DisclaimerBoundaryFailure(REASON_SUPPRESSION_KEY, observed_present=True)

    if DISCLAIMER_FIELD not in payload:
        raise DisclaimerBoundaryFailure(REASON_ABSENT, observed_present=False)

    value = payload[DISCLAIMER_FIELD]
    if not isinstance(value, str):
        raise DisclaimerBoundaryFailure(REASON_WRONG_TYPE, observed_present=False)
    if value == "":
        raise DisclaimerBoundaryFailure(REASON_EMPTY, observed_present=False)
    if is_canonical(value):
        return
    if value.strip() == KNOWN_VARIANT_UC06_STEP5:
        # The shortened UC-06 step 5 wording. Recognised so the incident names
        # the drift precisely - but still refused: only the canonical text ships.
        raise DisclaimerBoundaryFailure(REASON_SHORTENED_VARIANT, observed_present=True)
    raise DisclaimerBoundaryFailure(REASON_ALTERED, observed_present=True)


def scan_text_for_disclaimer(body: str) -> bool:
    """Used by the output-scan tests (layer 3). Exact substring, no normalisation."""
    return CANONICAL_DISCLAIMER in body
