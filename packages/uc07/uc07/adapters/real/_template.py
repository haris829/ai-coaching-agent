"""TEMPLATE for a real (company) read-only adapter. Copy, rename, implement.

How to use this file
====================

1. Copy it to ``uc07/adapters/real/<system>_<port>.py`` (e.g.
   ``acme_interaction_log.py``). Do not edit this template in place.
2. Keep the class read-only. No ``create``/``update``/``delete``/``patch``/
   ``save``/``write``/``post``/``put`` method may exist - the architecture tests
   fail the build if one appears.
3. Everything upstream-specific (URL paths, field names, nesting, enum spellings,
   error strings) stays INSIDE this module. Nothing else in UC-07 may learn it.
4. Never invent missing data. If the payload cannot satisfy the platform
   contract, raise ``ProviderInvalidResponse`` - do not default, guess, or bend
   the domain model.
5. Register the adapter with ONE line in ``uc07/composition.py`` and select it
   with ONE environment variable. Nothing else changes.
6. Run the conformance suite against it (see docs/INTEGRATION.md):
   ``pytest tests/conformance -q``

Checklist of TODO markers below: endpoint, authentication, payload mapping,
error translation, timeout, status mapping.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from uc07.domain.enums import NaricLevel, SourceStatus
from uc07.domain.errors import (
    PortName,
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from uc07.domain.models import InteractionRecord
from uc07.ports.read_only import InteractionLogProvider


class TemplateInteractionLogProvider(InteractionLogProvider):
    """Skeleton adapter for the InteractionLogProvider port.

    The same structure applies to FeedbackProvider, LearnerProfileProvider and
    CoursesProvider: constructor takes configuration, one private ``_get`` does
    transport + error translation, one private ``_map`` does payload mapping, and
    the public methods stay thin.
    """

    _port = PortName.INTERACTION_LOG

    # ---- TODO(endpoint) ---------------------------------------------------
    # Replace with the real path(s). Keep them here, never in the service.
    _HISTORY_PATH = "/TODO/interactions"  # e.g. "/v2/learners/{user_id}/interactions"
    _COUNT_PATH = "/TODO/interactions/count"

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        # ---- TODO(authentication) ----------------------------------------
        # Inject the credential; never read os.environ in here, and never log it.
        # Examples: bearer token, mTLS client, signed service account, gateway
        # header. Configuration comes from Settings via the composition root.
        token: str | None = None,
        http_client: Any | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._token = token
        self._http = http_client  # inject a client so this stays unit-testable

    # -- transport + error translation --------------------------------------

    def _get(self, path: str, **params: Any) -> Any:
        """Perform one read and translate failures into typed provider errors.

        TODO(error translation): map upstream failures onto EXACTLY these types:

        * connection refused / DNS / 5xx / circuit open -> ProviderUnavailable
        * read or connect timeout                       -> ProviderTimeout
        * 2xx body that cannot satisfy the contract,
          4xx contract/validation errors, unparsable JSON -> ProviderInvalidResponse

        Never let an upstream exception, message, status text, URL or provider
        name escape: the typed errors carry only a port label, on purpose.
        Never catch bare ``Exception`` and return an empty list - an unavailable
        source is not an empty source.
        """
        raise NotImplementedError(
            "TODO(endpoint/authentication/error translation): implement the read"
        )

    # -- payload mapping ----------------------------------------------------

    def _map_interaction(self, raw: dict[str, Any], user_id: str) -> InteractionRecord:
        """Map ONE upstream record onto the platform contract.

        TODO(payload mapping): fill in the upstream field names, nesting and value
        vocabularies. Reference points:

        * ``asked_at`` must be timezone-aware. Convert epoch/naive values here.
        * ``naric_level`` must be one of NaricLevel. Map the upstream spelling
          explicitly; an unknown value is a contract error, not a default.
        * ``follow_up_of`` is ``None`` or an interaction id - never "".
        * ``explain_differently_count`` is a non-negative integer.
        * ``rating_state`` is ``pending`` or ``rated``.
        * There is NO question-text field. Do not read, map, store or log one,
          even if the upstream payload contains it.
        """
        try:
            return InteractionRecord(
                interaction_id=raw["TODO_interaction_id"],
                session_id=raw["TODO_session_id"],
                user_id=user_id,  # server-side identity, never echoed from payload
                asked_at=self._map_timestamp(raw["TODO_asked_at"]),
                topic_tag=raw["TODO_topic_tag"],  # consumed exactly as supplied
                question_class=raw["TODO_question_class"],
                naric_level=self._map_naric(raw["TODO_naric_level"]),
                response_id=raw["TODO_response_id"],
                follow_up_of=raw.get("TODO_follow_up_of"),
                explain_differently_count=raw.get("TODO_explain_differently_count", 0),
                rating_state=raw.get("TODO_rating_state", "pending"),
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise ProviderInvalidResponse(self._port) from exc

    @staticmethod
    def _map_timestamp(value: Any) -> datetime:
        # TODO(payload mapping): epoch seconds? epoch millis? ISO with offset?
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        raise ValueError("unmappable timestamp")

    @staticmethod
    def _map_naric(value: Any) -> NaricLevel:
        # TODO(payload mapping): explicit table only. Never invent an integer
        # NARIC scale, and never fall back to a default level.
        table: dict[str, NaricLevel] = {
            # "L3": NaricLevel.LEVEL_3,
        }
        if value not in table:
            raise ValueError("unmappable NARIC level")
        return table[value]

    @staticmethod
    def _map_status(value: Any) -> SourceStatus:
        """TODO(status mapping): preserve all five states.

        ``empty`` (source answered, nothing there) must never be reported as
        ``unavailable`` (source could not answer), and ``partial`` must never be
        reported as ``available``.
        """
        table: dict[str, SourceStatus] = {
            # "COMPLETE": SourceStatus.AVAILABLE,
            # "TRUNCATED": SourceStatus.PARTIAL,
            # "NONE": SourceStatus.EMPTY,
        }
        if value not in table:
            raise ValueError("unmappable source status")
        return table[value]

    # -- port ---------------------------------------------------------------

    def for_user(self, user_id: str) -> Sequence[InteractionRecord]:
        payload = self._get(self._HISTORY_PATH, user_id=user_id)
        records = payload.get("TODO_records", []) if isinstance(payload, dict) else None
        if records is None:
            raise ProviderInvalidResponse(self._port)
        return tuple(self._map_interaction(raw, user_id) for raw in records)

    def count_for_user(self, user_id: str) -> int:
        payload = self._get(self._COUNT_PATH, user_id=user_id)
        count = payload.get("TODO_count") if isinstance(payload, dict) else None
        if not isinstance(count, int) or count < 0:
            raise ProviderInvalidResponse(self._port)
        return count

    def status_for_user(self, user_id: str) -> SourceStatus:
        payload = self._get(self._HISTORY_PATH, user_id=user_id)
        raw = payload.get("TODO_status") if isinstance(payload, dict) else None
        try:
            return self._map_status(raw)
        except ValueError as exc:
            raise ProviderInvalidResponse(self._port) from exc


# ---------------------------------------------------------------------------
# Registration (copy into uc07/composition.py) - ONE line per port:
#
#   INTERACTION_LOG_PROVIDERS["acme"] = lambda settings: AcmeInteractionLogProvider(
#       base_url=settings.company_interaction_log_base_url,
#       timeout_seconds=settings.provider_timeout_seconds,
#       token=settings.company_interaction_log_token,
#   )
#
# Then set INTERACTION_LOG_PROVIDER=acme in the environment. Nothing else -
# no domain model, service, API, persistence, existing mock adapter or existing
# test may be modified.
# ---------------------------------------------------------------------------
