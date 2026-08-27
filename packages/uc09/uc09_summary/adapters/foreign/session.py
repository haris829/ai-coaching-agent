"""Foreign ``SessionProvider``. READ ONLY.

Everything upstream-specific stops here: envelope, field names, epoch
milliseconds, ``RQF-7``, ``FINISHED``, the 0..1 progress ratio, and the
upstream error type with its hostnames and status codes. Past this file, the
rest of the component sees only platform types.

Note what this adapter does *not* do with a tier code it cannot map: it does
not guess a nearby level. ``GRADE-XI`` becomes the documented default with the
source marked accordingly, because a plausible-looking guess on a study level
is a wrong answer that looks like a right one.
"""

from __future__ import annotations

from datetime import UTC, datetime

from uc09_summary.adapters.foreign.lexportal_client import (
    LexPortalClient,
    LexPortalError,
    LexPortalTimeout,
)
from uc09_summary.domain.enums import SessionStatus
from uc09_summary.domain.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
    SessionNotFound,
)
from uc09_summary.domain.models import SessionRecord
from uc09_summary.domain.naric import resolve_naric_level

PORT = "session_provider"

#: Upstream tier code -> platform level candidate. Anything absent here falls
#: through to the documented default with status ``invalid``.
_TIER_TO_LEVEL = {
    "RQF-3": "level_3",
    "RQF-4": "level_4",
    "RQF-5": "level_5",
    "RQF-6": "level_6",
    "RQF-7": "level_7",
    "RQF-7+": "level_7_plus",
}

_LIFECYCLE_TO_STATUS = {
    "FINISHED": SessionStatus.COMPLETED,
    "LIVE": SessionStatus.IN_PROGRESS,
    "ABANDONED": SessionStatus.ABANDONED,
    "SUMMARISED": SessionStatus.SUMMARY_GENERATED,
}


class ForeignSessionProvider:
    """Maps a LexPortal session payload onto :class:`SessionRecord`."""

    @classmethod
    def from_settings(cls, settings: object) -> ForeignSessionProvider:
        return cls(LexPortalClient())

    def __init__(self, client: LexPortalClient) -> None:
        self._client = client

    def get_session(self, session_id: str) -> SessionRecord:
        try:
            envelope = self._client.fetch_session(session_id)
        except LexPortalTimeout as exc:
            raise ProviderTimeout(PORT, _safe_detail(exc)) from exc
        except LexPortalError as exc:
            if exc.status_code == 404:
                raise SessionNotFound(session_id) from exc
            raise ProviderUnavailable(PORT, _safe_detail(exc)) from exc

        try:
            payload = envelope["payload"]
            learner = payload["learner"]
            window = payload["window"]
            tier = payload.get("academicTier") or {}
            programme = payload.get("programme") or {}

            level = resolve_naric_level(
                _TIER_TO_LEVEL.get(str(tier.get("code", "")).strip()) or tier.get("code"),
                port=PORT,
            )

            return SessionRecord(
                session_id=str(payload["ref"]),
                user_id=str(learner["ref"]),
                user_display_name=str(learner["fullName"]),
                started_at=_from_epoch_ms(window["openedAtEpochMs"]),
                ended_at=_from_epoch_ms(window.get("closedAtEpochMs")),
                status=_LIFECYCLE_TO_STATUS.get(
                    str(payload.get("lifecycle", "")), SessionStatus.IN_PROGRESS
                ),
                naric_level=level.level,
                naric_level_source=level.source,
                naric_level_status=level.status,
                course_completion_percent=_ratio_to_percent(
                    programme.get("progressRatio")
                ),
                course_title=programme.get("name"),
            )
        except SessionNotFound:
            raise
        except Exception as exc:
            raise ProviderInvalidResponse(PORT, _safe_detail(exc)) from exc

    @classmethod
    def conformance_profile(cls) -> dict[str, object]:
        from uc09_summary.adapters.foreign import lexportal_client as lp

        return {
            "known_id": lp.SESSION_OK,
            "expected_user_id": lp.LEARNER_OK,
            "missing_id": lp.SESSION_ABSENT,
            "unavailable_id": lp.SESSION_DOWN,
            "timeout_id": lp.SESSION_SLOW,
            "invalid_naric_id": lp.SESSION_BAD_TIER,
            "upstream_tokens": (
                "lexportal",
                "LexPortal",
                "eu-west-2",
                "academicTier",
                "RQF-7",
                "FINISHED",
                "progressRatio",
                "openedAtEpochMs",
            ),
        }


def _from_epoch_ms(value: object) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC)


def _ratio_to_percent(value: object) -> int:
    """Convert a 0..1 ratio to the platform integer percentage, clamped 0-100."""
    if value is None:
        return 0
    return max(0, min(100, round(float(value) * 100)))


def _safe_detail(exc: Exception) -> str:
    """A neutral, machine-readable detail.

    Not the upstream message and not the upstream exception class name: both
    are provider identity, and provider identity does not cross this boundary
    even into a log line. The upstream text stays on the ``__cause__`` chain
    for a debugger to inspect locally.
    """
    if isinstance(exc, LexPortalTimeout):
        return "upstream_deadline_exceeded"
    if isinstance(exc, LexPortalError):
        return "upstream_error_response"
    return "payload_mapping_failed"
