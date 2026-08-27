"""Copy-paste skeleton for a real integration adapter.

This module is a template, not a live adapter: ``_transport`` raises
``NotImplementedError`` so it can never be mistaken for a working integration, and it is
not registered in the container. Copy it to e.g. ``naric.py`` and fill in the transport
plus the mapping.

The important properties to preserve:

* the class satisfies one Protocol from ``uc01/contracts/services.py``;
* every upstream failure becomes a contract exception, with technical detail attached
  for server-side logging only;
* the returned objects are UC-01 domain types, never upstream payloads.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from ...domain.enums import NaricAssessmentState
from ...domain.models import NaricAssessment, UserContext

logger = logging.getLogger(__name__)

DEPENDENCY = "naric"


class RealNaricAdapterTemplate:
    """Example shape of a real ``NaricService`` implementation."""

    def __init__(self, base_url: str, api_key: str, timeout_seconds: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key  # never logged, never returned
        self._timeout = timeout_seconds

    # -- contract ----------------------------------------------------------- #

    def get_assessment(self, user: UserContext) -> NaricAssessment:
        payload = self._transport(f"/assessments/{user.user_id}")
        return self._map(payload)

    # -- transport ---------------------------------------------------------- #

    def _transport(self, path: str) -> Mapping[str, Any]:
        """Perform the HTTP call.

        Reference implementation to fill in::

            try:
                response = httpx.get(
                    f"{self._base_url}{path}",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=self._timeout,
                )
                response.raise_for_status()
                return response.json()
            except httpx.TimeoutException as exc:
                logger.warning("naric.timeout", extra={"uc01": {"path": path}})
                raise DependencyUnavailableError(
                    DEPENDENCY, technical_detail=f"timeout after {self._timeout}s"
                ) from exc
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "naric.http_error",
                    extra={"uc01": {"status": exc.response.status_code}},
                )
                raise DependencyUnavailableError(
                    DEPENDENCY,
                    technical_detail=f"HTTP {exc.response.status_code}",
                ) from exc
            except ValueError as exc:  # JSON decode
                raise InvalidUpstreamResponseError(
                    DEPENDENCY, technical_detail="response was not valid JSON"
                ) from exc

        Note what is *absent*: no ``except Exception: pass``, no user-facing text, no
        re-raising of the library exception.
        """
        raise NotImplementedError(
            "template adapter: copy this module and implement the transport"
        )

    # -- mapping ------------------------------------------------------------ #

    @staticmethod
    def _map(payload: Mapping[str, Any]) -> NaricAssessment:
        """Map the real payload onto the internal contract.

        The only rule that matters: if the level cannot be trusted, return
        ``level=None`` with a non-COMPLETE state (or raise
        ``InvalidUpstreamResponseError``). UC-01 will then apply the documented Level 5
        fallback and record ``naric_level_source="default"`` — the adapter must never
        invent a level itself, because that would make a defaulted level look calibrated.
        """
        raise NotImplementedError(
            "template adapter: map the real payload to NaricAssessment "
            f"({NaricAssessmentState.COMPLETE.value}/incomplete/calibrating)"
        )


__all__ = ["RealNaricAdapterTemplate"]
