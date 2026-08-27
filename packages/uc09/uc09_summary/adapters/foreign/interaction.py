"""Foreign ``InteractionProvider``. READ ONLY.

Maps ``uid`` / ``atEpochMs`` / ``prompt`` / ``labels.subjects`` / ``labels.ideas``
onto the platform interaction record, and normalises the upstream
``SCREAMING_SNAKE`` tag vocabulary to the platform kebab-case form. The tag
normalisation happens here and only here: the grounding rules compare tags to
tags, and they must be comparing like with like.
"""

from __future__ import annotations

from datetime import UTC, datetime

from uc09_summary.adapters.foreign.lexportal_client import (
    LexPortalClient,
    LexPortalError,
    LexPortalTimeout,
)
from uc09_summary.domain.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from uc09_summary.domain.models import InteractionRecord

PORT = "interaction_provider"


class ForeignInteractionProvider:
    """Maps a LexPortal transcript onto :class:`InteractionRecord` values."""

    @classmethod
    def from_settings(cls, settings: object) -> ForeignInteractionProvider:
        return cls(LexPortalClient())

    def __init__(self, client: LexPortalClient) -> None:
        self._client = client

    def for_session(self, session_id: str) -> tuple[InteractionRecord, ...]:
        try:
            envelope = self._client.fetch_transcript(session_id)
        except LexPortalTimeout as exc:
            raise ProviderTimeout(PORT, "upstream_deadline_exceeded") from exc
        except LexPortalError as exc:
            raise ProviderUnavailable(PORT, "upstream_error_response") from exc

        try:
            items = envelope["payload"]["items"]
            records = [
                InteractionRecord(
                    interaction_id=str(item["uid"]),
                    session_id=session_id,
                    occurred_at=datetime.fromtimestamp(
                        int(item["atEpochMs"]) / 1000, tz=UTC
                    ),
                    question_text=str(item.get("prompt", "")),
                    topic_tags=tuple(
                        _normalise_tag(t)
                        for t in (item.get("labels") or {}).get("subjects", [])
                    ),
                    concept_tags=tuple(
                        _normalise_tag(t)
                        for t in (item.get("labels") or {}).get("ideas", [])
                    ),
                )
                for item in items
            ]
        except Exception as exc:
            raise ProviderInvalidResponse(PORT, "payload_mapping_failed") from exc

        return tuple(sorted(records, key=lambda r: (r.occurred_at, r.interaction_id)))

    @classmethod
    def conformance_profile(cls) -> dict[str, object]:
        from uc09_summary.adapters.foreign import lexportal_client as lp

        return {
            "known_id": lp.SESSION_OK,
            "expected_min_records": 2,
            "empty_id": lp.SESSION_ABSENT,
            "single_record_id": lp.SESSION_BAD_TIER,
            "unavailable_id": lp.SESSION_DOWN,
            "timeout_id": lp.SESSION_SLOW,
            "upstream_tokens": (
                "lexportal",
                "LexPortal",
                "eu-west-2",
                "atEpochMs",
                "UNFAIR_DISMISSAL",
                "prompt",
                "labels",
            ),
        }


def _normalise_tag(tag: object) -> str:
    """``UNFAIR_DISMISSAL`` -> ``unfair-dismissal``."""
    return str(tag).strip().lower().replace("_", "-")
