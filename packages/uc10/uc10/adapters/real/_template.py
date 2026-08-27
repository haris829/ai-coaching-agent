"""COPY-PASTE TEMPLATE for a real InteractionProvider adapter.

You do not need to read the rest of this repository to use this file.

    1. Copy this file to  uc10/adapters/real/<yourname>_interaction_provider.py
    2. Fill in every TODO below.
    3. Add ONE line to INTERACTION_PROVIDERS in uc10/adapters/registry.py:
           "<yourname>": lambda ctx: YourInteractionProvider(clock=ctx.clock),
    4. Set INTERACTION_PROVIDER=<yourname> in the environment.
    5. Run the conformance suite:
           pytest tests/conformance -q --adapter=<yourname>

Nothing else in the repository changes. If you find yourself editing a domain model, an
application service, the API layer or an existing test, stop: that is a contract
conversation, not an adapter workaround.

NON-NEGOTIABLES
    * This adapter is the ONLY place your upstream's payload shape is known. No upstream
      field name, nesting or error string may escape it.
    * This adapter NEVER invents data. A missing value maps to the documented default
      with its source field marked accordingly -- never to a plausible-looking guess.
    * Authorisation stays server-side, inside this adapter.
    * This port is READ ONLY. Do not add a method that writes, corrects or annotates an
      interaction; an architecture test fails the build if you do.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from uc10.domain.enums import ResponseCategory, SourceStatus
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

log = get_logger("uc10.adapters.real.template")

#: The port name carried on every contract error raised here. Do not put a vendor or
#: product name in an error: it would leak a provider name past the boundary.
PORT_NAME = "InteractionProvider"

# TODO(endpoint): the upstream base URL, read from configuration -- never a literal here,
# and never a URL that has not been given to you by the system's owner.
BASE_URL_SETTING = "TODO_SET_ME"

# TODO(timeout): the deadline this adapter enforces on the upstream, in seconds. The
# conformance suite asserts that a slow upstream becomes ProviderTimeout rather than a
# hung request.
REQUEST_TIMEOUT_SECONDS = 5.0

# TODO(mapping): your upstream's response-category vocabulary -> the platform's.
# Anything not listed becomes ResponseCategory.UNKNOWN, which is still rateable: no
# response category is ever excluded from feedback.
CATEGORY_BY_UPSTREAM_VALUE: dict[str, ResponseCategory] = {
    # "ANSWER": ResponseCategory.ANSWER,
    # "HANDOFF": ResponseCategory.REDIRECT,
    # "REFUSED": ResponseCategory.REFUSAL,
    # "FOLLOWUP": ResponseCategory.CLARIFYING_QUESTION,
    # "FALLBACK": ResponseCategory.DEGRADED_FALLBACK,
}

# TODO(mapping): your upstream's attainment vocabulary -> platform NARIC tokens.
# Valid tokens are exactly: level_3 level_4 level_5 level_6 level_7 level_7_plus
# Anything else is an INVALID RESPONSE, not a level: normalise_naric_level applies the
# LEVEL_5 default, marks the source `default` and the status `invalid`, and logs it.
NARIC_TOKEN_BY_UPSTREAM_VALUE: dict[str, str] = {
    # "L3": "level_3",
}

# TODO(mapping): your upstream's health/completeness vocabulary -> SourceStatus.
# `empty` (the upstream answered and had nothing) and `unavailable` (the upstream could
# not be reached) are DIFFERENT states and must never be conflated.
STATUS_BY_UPSTREAM_VALUE: dict[str, SourceStatus] = {
    # "OK": SourceStatus.AVAILABLE,
    # "NO_DATA": SourceStatus.EMPTY,
    # "PARTIAL": SourceStatus.PARTIAL,
}


class TemplateInteractionProvider:
    """Implements uc10.ports.interaction_provider.InteractionProvider.

    Exactly two public methods. Add no others.
    """

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        # TODO(auth): build your authenticated client here -- a service credential read
        # from configuration or an ambient identity. Authorisation stays server-side; a
        # caller-supplied token must never be forwarded from the request into upstream.
        self._client: Any = None

    # ---------------------------------------------------------------- port API

    def get(self, interaction_id: str) -> InteractionRecord:
        raw = self._fetch(interaction_id)
        return self._to_platform(raw)

    def delivered_at(self, interaction_id: str) -> datetime:
        """Server-side delivery time, UTC.

        The 24-hour historical rating window is measured against THIS value. Return the
        upstream's authoritative delivery timestamp, never a client-supplied one.
        """
        raw = self._fetch(interaction_id)
        return self._delivered_at(raw)

    # ------------------------------------------------------- upstream boundary

    def _fetch(self, interaction_id: str) -> dict[str, Any]:
        """TODO(transport): call the upstream and return its raw payload.

        Translate every failure into a typed contract error with a lowercase snake_case
        reason code. Never let an upstream exception, status line or error body escape:

            timeout / deadline exceeded -> ProviderTimeout(PORT_NAME, "upstream_timeout")
            refused / 5xx / 401         -> ProviderUnavailable(PORT_NAME, "upstream_unavailable")
            404 / unknown identifier    -> RecordNotFound(PORT_NAME, "interaction_not_found")
            unparseable / unmappable    -> ProviderInvalidResponse(PORT_NAME, "unmappable_response")
        """
        raise NotImplementedError(
            "TODO(transport): fetch the interaction and map failures to contract errors"
        )
        # Example skeleton:
        # try:
        #     response = self._client.get(f"{base_url}/interactions/{interaction_id}",
        #                                 timeout=REQUEST_TIMEOUT_SECONDS)
        # except TimeoutError as exc:
        #     raise ProviderTimeout(PORT_NAME, "upstream_timeout") from exc
        # except Exception as exc:
        #     raise ProviderUnavailable(PORT_NAME, "upstream_unavailable") from exc
        # if response.status_code == 404:
        #     raise RecordNotFound(PORT_NAME, "interaction_not_found")
        # if response.status_code >= 400:
        #     raise ProviderUnavailable(PORT_NAME, "upstream_unavailable")
        # try:
        #     return response.json()
        # except ValueError as exc:
        #     raise ProviderInvalidResponse(PORT_NAME, "unmappable_response") from exc

    def _delivered_at(self, raw: dict[str, Any]) -> datetime:
        """TODO(mapping): return the delivery time as a timezone-aware UTC datetime.

        Epoch seconds:  datetime.fromtimestamp(raw["ts"], tz=UTC)
        Epoch millis:   datetime.fromtimestamp(raw["ts"] / 1000, tz=UTC)
        ISO string:     datetime.fromisoformat(raw["ts"]).astimezone(UTC)

        A naive datetime is a mapping bug: raise ProviderInvalidResponse rather than
        assuming a timezone.
        """
        raise NotImplementedError("TODO(mapping): map the upstream delivery timestamp")

    def _to_platform(self, raw: dict[str, Any]) -> InteractionRecord:
        """TODO(mapping): map the upstream payload onto the platform contract.

        Every field below is required by the platform contract. Where the upstream has no
        value, use the documented default and mark its source -- do not guess.
        """
        try:
            naric = normalise_naric_level(
                NARIC_TOKEN_BY_UPSTREAM_VALUE.get(str(raw.get("TODO_level_field")))
            )
            return InteractionRecord(
                # TODO(mapping): the upstream's identifier for this response.
                interaction_id=str(raw["TODO_id_field"]),
                # TODO(mapping): the opaque platform session id. This component receives
                # one and never creates one.
                session_id=str(raw["TODO_session_field"]),
                # TODO(mapping): the learner who received this response. Used to refuse
                # cross-user rating, so it must be the upstream's authoritative value.
                user_id=str(raw["TODO_user_field"]),
                question_text=str(raw["TODO_question_field"]),
                response_text=str(raw["TODO_response_field"]),
                response_category=CATEGORY_BY_UPSTREAM_VALUE.get(
                    str(raw.get("TODO_category_field")), ResponseCategory.UNKNOWN
                ),
                # TODO(mapping): lowercase slug; the flag groups ratings by this value.
                topic_tag=str(raw["TODO_topic_field"]).strip().lower().replace(" ", "_"),
                session_mode=str(raw["TODO_mode_field"]).strip().lower().replace(" ", "_"),
                naric_level=naric.level,
                naric_level_source=naric.source,
                explanation_profile=naric.explanation_profile,
                naric_source_status=naric.status,
                # TODO(mapping): integer 0-100, or None. Never a float, never a string.
                course_completion_percent=None,
                delivered_at=self._delivered_at(raw),
                source_status=STATUS_BY_UPSTREAM_VALUE.get(
                    str(raw.get("TODO_status_field")), SourceStatus.INVALID
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            # Never re-raise the original: its text would carry upstream field names.
            raise ProviderInvalidResponse(PORT_NAME, "unmappable_response") from exc


# TODO(errors): delete this line once you have used every import above; it exists so the
# template's imports document the full error vocabulary you are expected to raise.
_CONTRACT_ERRORS = (ProviderUnavailable, ProviderTimeout, ProviderInvalidResponse, RecordNotFound)
_UTC = UTC
