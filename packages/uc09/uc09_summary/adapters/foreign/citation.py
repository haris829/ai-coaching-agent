"""Foreign ``CitationProvider``. READ ONLY.

Maps LexPortal authority records onto :class:`Resource`, translating the
upstream ``STATUTE`` / ``JUDGMENT`` classes onto the platform resource kinds.

An upstream class this adapter does not recognise becomes ``other`` rather than
a guess between legislation and case law. Guessing would put a wrong
characterisation of an authority onto a document of record for no benefit.
"""

from __future__ import annotations

from datetime import UTC, datetime

from uc09_summary.adapters.foreign.lexportal_client import (
    LexPortalClient,
    LexPortalError,
    LexPortalTimeout,
)
from uc09_summary.domain.enums import ResourceKind
from uc09_summary.domain.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from uc09_summary.domain.models import Resource

PORT = "citation_provider"

_CLASS_TO_KIND = {
    "STATUTE": ResourceKind.LEGISLATION,
    "SI": ResourceKind.LEGISLATION,
    "JUDGMENT": ResourceKind.CASE_LAW,
}


class ForeignCitationProvider:
    """Maps LexPortal authorities onto :class:`Resource` values."""

    @classmethod
    def from_settings(cls, settings: object) -> ForeignCitationProvider:
        return cls(LexPortalClient())

    def __init__(self, client: LexPortalClient) -> None:
        self._client = client

    def for_session(self, session_id: str) -> tuple[Resource, ...]:
        try:
            envelope = self._client.fetch_authorities(session_id)
        except LexPortalTimeout as exc:
            raise ProviderTimeout(PORT, "upstream_deadline_exceeded") from exc
        except LexPortalError as exc:
            raise ProviderUnavailable(PORT, "upstream_error_response") from exc

        try:
            return tuple(
                Resource(
                    resource_id=str(item["key"]),
                    kind=_CLASS_TO_KIND.get(
                        str(item.get("class", "")).upper(), ResourceKind.OTHER
                    ),
                    citation=str(item["shortForm"]),
                    title=str(item["longForm"]),
                    cited_in_interaction_ids=tuple(
                        str(i) for i in item.get("seenIn", [])
                    ),
                    first_cited_at=(
                        datetime.fromtimestamp(
                            int(item["firstSeenEpochMs"]) / 1000, tz=UTC
                        )
                        if item.get("firstSeenEpochMs") is not None
                        else None
                    ),
                )
                for item in envelope["payload"]["authorities"]
            )
        except Exception as exc:
            raise ProviderInvalidResponse(PORT, "payload_mapping_failed") from exc

    @classmethod
    def conformance_profile(cls) -> dict[str, object]:
        from uc09_summary.adapters.foreign import lexportal_client as lp

        return {
            "known_id": lp.SESSION_OK,
            "expected_min_records": 2,
            "empty_id": lp.SESSION_NO_AUTHORITIES,
            "unavailable_id": lp.SESSION_DOWN,
            "timeout_id": lp.SESSION_SLOW,
            "upstream_tokens": (
                "lexportal",
                "LexPortal",
                "eu-west-2",
                "shortForm",
                "longForm",
                "STATUTE",
                "JUDGMENT",
                "seenIn",
            ),
        }
