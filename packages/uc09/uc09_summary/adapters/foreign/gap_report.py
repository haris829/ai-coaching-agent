"""Foreign ``GapReportProvider``. READ ONLY.

The upstream distinguishes "no recommendation engine result for this learner"
(no document at all) from "the engine ran and recommended nothing" (an empty
list). The adapter preserves that distinction as ``None`` versus ``()``,
because the platform vocabulary distinguishes ``unavailable`` from ``empty``
and collapsing the two here would make the summary state a falsehood about why
Next Steps is short.
"""

from __future__ import annotations

from uc09_summary.adapters.foreign.lexportal_client import (
    LexPortalClient,
    LexPortalError,
    LexPortalTimeout,
)
from uc09_summary.domain.enums import SuggestionSource
from uc09_summary.domain.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from uc09_summary.domain.models import Suggestion

PORT = "gap_report_provider"


class ForeignGapReportProvider:
    """Maps LexPortal recommendations onto :class:`Suggestion` values."""

    @classmethod
    def from_settings(cls, settings: object) -> ForeignGapReportProvider:
        return cls(LexPortalClient())

    def __init__(self, client: LexPortalClient) -> None:
        self._client = client

    def suggestions(self, user_id: str) -> tuple[Suggestion, ...] | None:
        try:
            envelope = self._client.fetch_recommendations(user_id)
        except LexPortalTimeout as exc:
            raise ProviderTimeout(PORT, "upstream_deadline_exceeded") from exc
        except LexPortalError as exc:
            raise ProviderUnavailable(PORT, "upstream_error_response") from exc

        if envelope is None:
            return None

        try:
            return tuple(
                Suggestion(
                    suggestion_id=str(item["code"]),
                    label=str(item["headline"]),
                    rationale=str(item.get("because", "")),
                    source=SuggestionSource.GAP_REPORT,
                    related_topic_id=None,
                )
                for item in envelope["result"]["recommendations"]
            )
        except Exception as exc:
            raise ProviderInvalidResponse(PORT, "payload_mapping_failed") from exc

    @classmethod
    def conformance_profile(cls) -> dict[str, object]:
        from uc09_summary.adapters.foreign import lexportal_client as lp

        return {
            "known_id": lp.LEARNER_OK,
            "expected_min_records": 1,
            "empty_id": lp.LEARNER_NO_RECOMMENDATIONS,
            "none_id": "LP-USER-does-not-exist",
            "unavailable_id": lp.LEARNER_DOWN,
            "timeout_id": lp.LEARNER_SLOW,
            "upstream_tokens": (
                "lexportal",
                "LexPortal",
                "eu-west-2",
                "recommendations",
                "headline",
                "because",
            ),
        }
