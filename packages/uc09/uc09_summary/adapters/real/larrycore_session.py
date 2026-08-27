"""Larry Core SessionProvider. READ ONLY.

Demonstration adapter for the integration swap proof, written by copying
``_template.py`` and filling its five TODOs. It maps a fictional "Larry Core"
session payload onto the platform record.

Every Larry Core detail stops in this file: the ``data`` envelope,
``sessionRef``, ``qualificationLevel``, ``NQF7``, ``CLOSED``,
``courseProgressPct``, and the transport error types. Nothing past this
boundary knows any of it.

The upstream is supplied as a callable so the adapter can be exercised without
a live service; in a real deployment ``from_settings`` builds an HTTP client
from configuration instead. See docs/INTEGRATION.md for that version.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from uc09_summary.config import Settings
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

# TODO(1) ENDPOINT -> filled in
SESSION_PATH = "/v2/sessions/{session_id}"

# TODO(2) VALUE MAPPINGS -> filled in. Anything absent falls through to the
# documented default with status `invalid`, never to a near miss.
LEVEL_MAP = {
    "NQF3": "level_3",
    "NQF4": "level_4",
    "NQF5": "level_5",
    "NQF6": "level_6",
    "NQF7": "level_7",
    "NQF7D": "level_7_plus",
}
STATE_MAP = {
    "OPEN": SessionStatus.IN_PROGRESS,
    "CLOSED": SessionStatus.COMPLETED,
    "DROPPED": SessionStatus.ABANDONED,
}


class LarryCoreTimeout(Exception):
    """Upstream deadline exceeded. Never escapes this module."""


class LarryCoreHttpError(Exception):
    """Upstream returned an error status. Never escapes this module."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"larrycore responded {status_code}")


class LarryCoreSessionProvider:
    """Maps a Larry Core session payload onto the platform SessionRecord."""

    @classmethod
    def from_settings(cls, settings: Settings) -> LarryCoreSessionProvider:
        # TODO(3) AUTH -> filled in. Authorisation stays server-side, in here.
        # A real deployment builds an HTTP client from settings; the stub below
        # keeps the demonstration hermetic.
        return cls(_stub_upstream(settings))

    def __init__(self, fetch: Callable[[str], dict[str, Any]]) -> None:
        self._fetch = fetch

    def get_session(self, session_id: str) -> SessionRecord:
        # TODO(4) ERROR TRANSLATION -> filled in. Details are neutral machine
        # codes: they reach logs, and provider identity must not.
        try:
            envelope = self._fetch(SESSION_PATH.format(session_id=session_id))
        except LarryCoreTimeout as exc:
            raise ProviderTimeout(PORT, "upstream_deadline_exceeded") from exc
        except LarryCoreHttpError as exc:
            if exc.status_code == 404:
                raise SessionNotFound(session_id) from exc
            raise ProviderUnavailable(PORT, "upstream_error_response") from exc

        return self._to_record(envelope)

    def _to_record(self, envelope: dict[str, Any]) -> SessionRecord:
        # TODO(5) PAYLOAD MAPPING -> filled in.
        try:
            data = envelope["data"]
            student = data["student"]
            level = resolve_naric_level(
                LEVEL_MAP.get(str(data.get("qualificationLevel", "")))
                or data.get("qualificationLevel"),
                port=PORT,
            )
            return SessionRecord(
                session_id=str(data["sessionRef"]),
                user_id=str(student["id"]),
                user_display_name=str(student["displayName"]),
                started_at=_iso(data["openedAt"]),
                ended_at=_iso(data.get("closedAt")),
                status=STATE_MAP.get(
                    str(data.get("state", "")), SessionStatus.IN_PROGRESS
                ),
                naric_level=level.level,
                naric_level_source=level.source,
                naric_level_status=level.status,
                course_completion_percent=int(data.get("courseProgressPct", 0)),
                course_title=data.get("courseName"),
            )
        except Exception as exc:
            raise ProviderInvalidResponse(PORT, "payload_mapping_failed") from exc

    @classmethod
    def conformance_profile(cls) -> dict[str, object]:
        return {
            "known_id": "SESS-99201",
            "expected_user_id": "USR-4471",
            "missing_id": "SESS-00000",
            "unavailable_id": "SESS-FAULT-503",
            "timeout_id": "SESS-FAULT-SLOW",
            "invalid_naric_id": "SESS-99999",
            "upstream_tokens": (
                "larrycore",
                "LarryCore",
                "sessionRef",
                "qualificationLevel",
                "NQF7",
                "CLOSED",
                "courseProgressPct",
                "displayName",
            ),
        }


def _iso(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _stub_upstream(settings: Settings) -> Callable[[str], dict[str, Any]]:
    """Stand-in for the Larry Core client, so the swap can be run hermetically."""
    payloads = {
        "SESS-99201": {
            "data": {
                "sessionRef": "SESS-99201",
                "student": {"id": "USR-4471", "displayName": "Amara Osei"},
                "openedAt": "2026-03-04T09:00:00Z",
                "closedAt": "2026-03-04T09:47:00Z",
                "state": "CLOSED",
                "qualificationLevel": "NQF7",
                "courseProgressPct": 62,
                "courseName": "Employment Law Practice",
            }
        },
        "SESS-99999": {
            "data": {
                "sessionRef": "SESS-99999",
                "student": {"id": "USR-4471", "displayName": "Amara Osei"},
                "openedAt": "2026-03-04T09:00:00Z",
                "closedAt": "2026-03-04T09:15:00Z",
                "state": "CLOSED",
                "qualificationLevel": "NQF-UNKNOWN",
                "courseProgressPct": 50,
                "courseName": "Employment Law Practice",
            }
        },
    }

    def fetch(path: str) -> dict[str, Any]:
        session_id = path.rsplit("/", 1)[-1]
        if session_id == "SESS-FAULT-SLOW":
            raise LarryCoreTimeout
        if session_id == "SESS-FAULT-503":
            raise LarryCoreHttpError(503)
        if session_id not in payloads:
            raise LarryCoreHttpError(404)
        return payloads[session_id]

    return fetch
