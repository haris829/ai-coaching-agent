"""A deliberately foreign InteractionProvider.

Its fictional upstream shares nothing with the mock: different field names, deeper
nesting, epoch-millisecond timestamps, an attainment scheme of its own ("EQF band 7+"),
percentages as strings, uppercase category codes and a title-cased subject line.

The service runs against this adapter unmodified.  That is the proof that the platform
contract lives in the domain and not in the mock: every upstream quirk is absorbed here,
and nothing past this boundary knows the words ``txnRef``, ``servedAtEpochMillis`` or
``EQF``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from uc10.domain.enums import NaricLevelSource, ResponseCategory, SourceStatus
from uc10.domain.models import InteractionRecord
from uc10.domain.naric import normalise_naric_level
from uc10.logging_setup import get_logger
from uc10.ports.clock import Clock
from uc10.ports.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
    RecordNotFound,
)

log = get_logger("uc10.adapters.foreign.interaction")

PORT_NAME = "InteractionProvider"

# --- upstream vocabulary. Known ONLY inside this file. -----------------------

_CATEGORY_BY_UPSTREAM_KIND = {
    "RESPONSE": ResponseCategory.ANSWER,
    "SIGNPOST": ResponseCategory.REDIRECT,
    "DECLINED": ResponseCategory.REFUSAL,
    "QUERY_BACK": ResponseCategory.CLARIFYING_QUESTION,
    "FALLBACK_DEGRADED": ResponseCategory.DEGRADED_FALLBACK,
}

_STATUS_BY_UPSTREAM_INTEGRITY = {
    "OK": SourceStatus.AVAILABLE,
    "NO_CONTENT": SourceStatus.EMPTY,
    "PART": SourceStatus.PARTIAL,
}

# The upstream's attainment scheme expressed in platform tokens. The mapping lives here;
# the domain only ever sees a platform token.
_LEVEL_TOKEN_BY_UPSTREAM_BAND = {
    "3": "level_3",
    "4": "level_4",
    "5": "level_5",
    "6": "level_6",
    "7": "level_7",
    "7+": "level_7_plus",
}

_PROVENANCE_RETRIEVED = "LOOKUP"


def _fixtures() -> dict[str, dict[str, Any]]:
    """Raw upstream payloads, in the upstream's own shape.

    ``_offset_seconds`` stands in for network reality: the fixture is materialised
    relative to the clock at read time so 'delivered 23 hours ago' stays true.
    """
    def payload(
        ref: str,
        *,
        kind: str = "RESPONSE",
        band: str = "7+",
        offset_seconds: int = 3600,
        learner: str = "LRN-ALICE",
        subject: str = "Contract Formation",
        integrity: str = "OK",
        provenance: str = _PROVENANCE_RETRIEVED,
        completion: str = "40%",
    ) -> dict[str, Any]:
        return {
            "envelope": {"txnRef": ref, "learnerRef": learner, "threadRef": "THR-1001"},
            "content": {
                "prompt": {"body": f"FOREIGN_QUESTION_TEXT_DO_NOT_LOG::{ref}"},
                "reply": {"body": f"FOREIGN_RESPONSE_TEXT_DO_NOT_LOG::{ref}", "kind": kind},
            },
            "classification": {"subject": subject, "mode": "Coaching Mode"},
            "attainment": {"scheme": "EQF", "band": band, "provenance": provenance},
            "progress": {"completionPct": completion},
            "integrity": integrity,
            "_offset_seconds": offset_seconds,
        }

    return {
        "TXN-ANSWER": payload("TXN-ANSWER"),
        "TXN-SIGNPOST": payload("TXN-SIGNPOST", kind="SIGNPOST"),
        "TXN-DECLINED": payload("TXN-DECLINED", kind="DECLINED"),
        "TXN-QUERYBACK": payload("TXN-QUERYBACK", kind="QUERY_BACK"),
        "TXN-FALLBACK": payload(
            "TXN-FALLBACK", kind="FALLBACK_DEGRADED", integrity="PART", band="?"
        ),
        "TXN-NEWKIND": payload("TXN-NEWKIND", kind="KIND_WE_HAVE_NEVER_SEEN"),
        "TXN-23H": payload("TXN-23H", offset_seconds=23 * 3600),
        "TXN-25H": payload("TXN-25H", offset_seconds=25 * 3600),
        "TXN-OTHER": payload("TXN-OTHER", learner="LRN-BOB"),
        "TXN-BADLEVEL": payload("TXN-BADLEVEL", band="Level Seven-ish"),
        "TXN-DOWN": {"_failure": "unavailable"},
        "TXN-SLOW": {"_failure": "timeout"},
        "TXN-GARBLED": {"_failure": "invalid"},
    }


class ForeignInteractionProvider:
    """InteractionProvider over a fictional upstream with an unrelated payload shape."""

    def __init__(self, clock: Clock, payloads: dict[str, dict[str, Any]] | None = None) -> None:
        self._clock = clock
        self._payloads = payloads if payloads is not None else _fixtures()

    def get(self, interaction_id: str) -> InteractionRecord:
        raw = self._fetch(interaction_id)
        return self._to_platform(raw)

    def delivered_at(self, interaction_id: str) -> datetime:
        raw = self._fetch(interaction_id)
        return self._delivered_at(raw)

    # ------------------------------------------------------- upstream boundary

    def _fetch(self, interaction_id: str) -> dict[str, Any]:
        """Stands in for the upstream call. Upstream failures become typed contract errors
        here, carrying a reason code and never upstream error text."""
        raw = self._payloads.get(interaction_id)
        if raw is None:
            raise RecordNotFound(PORT_NAME, "interaction_not_found")
        failure = raw.get("_failure")
        if failure == "unavailable":
            raise ProviderUnavailable(PORT_NAME, "upstream_unavailable")
        if failure == "timeout":
            raise ProviderTimeout(PORT_NAME, "upstream_timeout")
        if failure == "invalid":
            raise ProviderInvalidResponse(PORT_NAME, "unmappable_response")
        return raw

    def _delivered_at(self, raw: dict[str, Any]) -> datetime:
        served_at_epoch_millis = int(
            (self._clock.now() - timedelta(seconds=int(raw["_offset_seconds"]))).timestamp() * 1000
        )
        return datetime.fromtimestamp(served_at_epoch_millis / 1000, tz=UTC)

    def _to_platform(self, raw: dict[str, Any]) -> InteractionRecord:
        try:
            envelope = raw["envelope"]
            content = raw["content"]
            classification = raw["classification"]
            attainment = raw.get("attainment", {})
        except (KeyError, TypeError) as exc:
            raise ProviderInvalidResponse(PORT_NAME, "unmappable_response") from exc

        naric = normalise_naric_level(
            _LEVEL_TOKEN_BY_UPSTREAM_BAND.get(str(attainment.get("band", "")).strip())
            or attainment.get("band")
        )
        if naric.status is not SourceStatus.AVAILABLE:
            log.info(
                "naric_level_defaulted",
                interaction_id=envelope.get("txnRef"),
                naric_level=naric.level.value,
                naric_level_source=naric.source.value,
                naric_source_status=naric.status.value,
                raw_kind=naric.raw_kind,
            )
        # The upstream tells us whether it looked the level up or fell back to its own
        # default. A level the upstream defaulted is never reported as retrieved.
        source = (
            naric.source
            if str(attainment.get("provenance")) == _PROVENANCE_RETRIEVED
            else NaricLevelSource.DEFAULT
        )

        return InteractionRecord(
            interaction_id=str(envelope["txnRef"]),
            session_id=str(envelope["threadRef"]),
            user_id=str(envelope["learnerRef"]),
            question_text=str(content["prompt"]["body"]),
            response_text=str(content["reply"]["body"]),
            response_category=_CATEGORY_BY_UPSTREAM_KIND.get(
                str(content["reply"].get("kind")), ResponseCategory.UNKNOWN
            ),
            topic_tag=self._slug(classification["subject"]),
            session_mode=self._slug(classification.get("mode", "unknown")),
            naric_level=naric.level,
            naric_level_source=source,
            explanation_profile=naric.explanation_profile,
            naric_source_status=naric.status,
            course_completion_percent=self._percent(raw.get("progress", {}).get("completionPct")),
            delivered_at=self._delivered_at(raw),
            source_status=_STATUS_BY_UPSTREAM_INTEGRITY.get(
                str(raw.get("integrity")), SourceStatus.INVALID
            ),
        )

    @staticmethod
    def _slug(value: str) -> str:
        return str(value).strip().lower().replace(" ", "_")

    @staticmethod
    def _percent(value: Any) -> int | None:
        """The upstream sends '40%'. The platform contract is an integer 0-100.

        An unmappable value becomes None -- the adapter never invents a plausible number.
        """
        if value is None:
            return None
        try:
            parsed = int(str(value).strip().rstrip("%"))
        except ValueError:
            return None
        return parsed if 0 <= parsed <= 100 else None
