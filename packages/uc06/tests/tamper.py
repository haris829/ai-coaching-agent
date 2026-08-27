"""Test-only serializers that corrupt a payload at the boundary.

These exist so layer 2 can be proven to catch a failure that layer 1 cannot
produce. They are deliberately NOT reachable from configuration: there is no
registry entry, no environment variable and no request field that selects them.
The only way to install one is to construct a ResponseEmitter with it in a test,
which is why the "no configuration key can disable the disclaimer" guarantee
survives their existence - and tests/test_config_surface.py asserts that.

They corrupt the FIRST payload only by default, which models a transient
serialisation defect and lets the halt behaviour be observed on the next request
through the same client. `once=False` models a permanently broken serialiser.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from uc06.domain.disclaimer import KNOWN_VARIANT_UC06_STEP5
from uc06.domain.responses import DisclaimedResponse


class _Tamperer:
    def __init__(self, once: bool = True) -> None:
        self.once = once
        self.calls = 0

    def serialize(self, response: DisclaimedResponse) -> dict[str, Any]:
        payload = response.to_payload()
        self.calls += 1
        if self.once and self.calls > 1:
            return payload
        return self._corrupt(payload)

    def _corrupt(self, payload: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError


class DroppingSerializer(_Tamperer):
    """Simulates a serialisation defect that loses the field entirely."""

    def _corrupt(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload.pop("disclaimer", None)
        return payload


class AlteringSerializer(_Tamperer):
    """Simulates drift to the shortened UC-06 step 5 wording."""

    def _corrupt(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload["disclaimer"] = KNOWN_VARIANT_UC06_STEP5
        return payload


class SuppressionKeySerializer(_Tamperer):
    """Simulates an upstream component adding a suppression flag."""

    def _corrupt(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload["suppress_disclaimer"] = True
        return payload


def with_serializer(container, serializer):
    """Return the SAME container with a tampering emitter over the SAME ports.

    Rebuilding the container would give the emitter a fresh set of halt and sink
    instances, and the halt raised by a boundary failure would land somewhere the
    test never looks.
    """
    from uc06.application.emitter import ResponseEmitter

    return dataclasses.replace(
        container,
        emitter=ResponseEmitter(
            halts=container.halts,
            admin_alerts=container.admin_alerts,
            security_incidents=container.security_incidents,
            serializer=serializer,
        ),
    )
