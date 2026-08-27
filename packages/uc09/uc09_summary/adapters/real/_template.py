"""COPY THIS FILE to build a real adapter.

    cp uc09_summary/adapters/real/_template.py \
       uc09_summary/adapters/real/<vendor>_<port>.py

Then fill in every ``TODO``. There are exactly five, and nothing outside this
file needs to change except one registry line and one environment variable.

You do not need to read the rest of the repository. What you need to know:

* Return the platform types imported below. Build them from your payload; do
  not add fields, and do not pass your payload through.
* Translate **every** upstream failure into ``ProviderUnavailable``,
  ``ProviderTimeout`` or ``ProviderInvalidResponse``. Nothing else may escape.
* Nothing upstream-specific may leave this file: not a field name, not a
  nesting shape, not an error string, not a hostname, not your vendor name -
  and that includes the ``detail`` you attach to a raised error, because
  details are written to logs.
* **Never invent data.** A missing value maps to the documented default with
  its source field marked accordingly. It never maps to a plausible guess.
* If your payload genuinely cannot be mapped onto the platform contract, that
  is a contract conversation, not an adapter workaround. Raise it. Do not bend
  the domain model to fit an upstream quirk, and do not smuggle the quirk
  through in a field that was meant for something else.

This template shows a ``SessionProvider``. The other ports differ only in the
method name and return type; the five TODOs are the same in each. Port
signatures are in ``uc09_summary/ports/``.

Run the conformance suite when you are done - see docs/INTEGRATION.md. It is
adapter-agnostic and already covers your adapter once the registry line is in.
"""

from __future__ import annotations

from datetime import UTC, datetime
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

#: Logical port name. Used in error records only.
PORT = "session_provider"

# TODO(1) ENDPOINT ----------------------------------------------------------
# The path or operation this adapter calls on your system, relative to
# ``settings.upstream_base_url``. Keep it here; no other module may know it.
SESSION_PATH = "/TODO/sessions/{session_id}"

# TODO(2) VALUE MAPPINGS ----------------------------------------------------
# Map your vocabularies onto the platform ones. Anything absent from these
# tables must fall through to the documented default - never to a near miss.
UPSTREAM_LEVEL_TO_PLATFORM: dict[str, str] = {
    # "YOUR_TIER_CODE": "level_7",
}
UPSTREAM_STATUS_TO_PLATFORM: dict[str, SessionStatus] = {
    # "YOUR_STATE": SessionStatus.COMPLETED,
}


class TemplateSessionProvider:
    """TODO: rename to ``<Vendor>SessionProvider`` and describe the upstream."""

    # -- construction: required by the registry, do not rename --------------

    @classmethod
    def from_settings(cls, settings: Settings) -> TemplateSessionProvider:
        """Build from configuration. The registry calls exactly this."""
        # TODO(3) AUTH ------------------------------------------------------
        # Build your client here: base URL, credential, timeout. Read them from
        # ``settings`` - never from ``os.environ`` directly, and never from a
        # literal in this file.
        #
        #     client = httpx.Client(
        #         base_url=settings.upstream_base_url,
        #         headers={"Authorization": f"Bearer {settings.upstream_api_key}"},
        #         timeout=settings.provider_timeout_seconds,
        #     )
        #
        # Authorisation stays server-side, inside this adapter. The credential
        # must not reach a request, a response, a log line or a domain model.
        client: Any = None
        return cls(client=client, timeout=settings.provider_timeout_seconds)

    def __init__(self, client: Any, timeout: float) -> None:
        self._client = client
        self._timeout = timeout

    # -- the port method ----------------------------------------------------

    def get_session(self, session_id: str) -> SessionRecord:
        """Return the session. Signature fixed by the port; do not change it."""
        try:
            payload = self._fetch(session_id)
        except TimeoutError as exc:
            # TODO(4) ERROR TRANSLATION -------------------------------------
            # Catch YOUR client exception types here, one branch each:
            #   - deadline exceeded          -> ProviderTimeout
            #   - unreachable / 5xx / refused -> ProviderUnavailable
            #   - 404 / no such session       -> SessionNotFound
            #   - anything unmappable         -> ProviderInvalidResponse
            # The ``detail`` argument is operator-facing and reaches logs, so
            # it must be a neutral machine code - never the upstream message,
            # never the upstream exception class name.
            raise ProviderTimeout(PORT, "upstream_deadline_exceeded") from exc
        except ConnectionError as exc:
            raise ProviderUnavailable(PORT, "upstream_error_response") from exc

        if payload is None:
            raise SessionNotFound(session_id)

        return self._to_record(payload)

    # -- internals ----------------------------------------------------------

    def _fetch(self, session_id: str) -> dict[str, Any] | None:
        """Call the upstream. The only method in the codebase that may do so."""
        raise NotImplementedError("TODO(1): call SESSION_PATH via self._client")

    def _to_record(self, payload: dict[str, Any]) -> SessionRecord:
        """Map the upstream payload onto the platform record."""
        try:
            # TODO(5) PAYLOAD MAPPING ---------------------------------------
            # Replace every right-hand side with your field. Only the company
            # knows this part; it is the reason this file exists at all.
            #
            # Rules that are not negotiable:
            #  * ``course_completion_percent`` is an integer 0-100. If your
            #    system sends a 0..1 ratio, convert it here.
            #  * The NARIC level must come back from ``resolve_naric_level``.
            #    Translate your code first; pass the raw value through if you
            #    cannot, and the platform default with status ``invalid`` will
            #    be applied for you. Do not pick a level yourself.
            #  * Timestamps must be timezone-aware UTC.
            level = resolve_naric_level(
                UPSTREAM_LEVEL_TO_PLATFORM.get(str(payload.get("TODO_level", "")))
                or payload.get("TODO_level"),
                port=PORT,
            )
            return SessionRecord(
                session_id=str(payload["TODO_session_id"]),
                user_id=str(payload["TODO_user_id"]),
                user_display_name=str(payload["TODO_display_name"]),
                started_at=_utc(payload["TODO_started_at"]),
                ended_at=_utc(payload.get("TODO_ended_at")),
                status=UPSTREAM_STATUS_TO_PLATFORM.get(
                    str(payload.get("TODO_status", "")), SessionStatus.IN_PROGRESS
                ),
                naric_level=level.level,
                naric_level_source=level.source,
                naric_level_status=level.status,
                course_completion_percent=int(payload.get("TODO_percent", 0)),
                course_title=payload.get("TODO_course_title"),
            )
        except Exception as exc:
            raise ProviderInvalidResponse(PORT, "payload_mapping_failed") from exc

    # -- conformance --------------------------------------------------------

    @classmethod
    def conformance_profile(cls) -> dict[str, object]:
        """Identifiers the shared conformance suite drives your adapter with.

        Point these at records in your integration environment. The suite is
        already written; this is the only thing it needs from you, and once it
        is filled in you do not write a test to validate this adapter.

        ``upstream_tokens`` are strings that must never escape this file -
        your vendor name, your hostnames, your field names, your status codes.
        The suite asserts none of them appear in a returned record or a raised
        error. Be generous with this list; it is what proves the boundary holds.
        """
        return {
            "known_id": "TODO: an id that resolves in your integration environment",
            "expected_user_id": "TODO: the owner of that session",
            "missing_id": "TODO: an id that returns not-found",
            "unavailable_id": "TODO: an id that forces a 5xx or a refusal",
            "timeout_id": "TODO: an id that forces a deadline breach",
            "invalid_naric_id": "TODO: a session whose level code you cannot map",
            "upstream_tokens": ("TODO_vendor_name", "TODO_field_name"),
        }


def _utc(value: Any) -> datetime | None:
    """TODO: convert your timestamp representation to timezone-aware UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    # e.g. epoch milliseconds: datetime.fromtimestamp(int(value)/1000, tz=timezone.utc)
    return datetime.fromisoformat(str(value)).astimezone(UTC)
