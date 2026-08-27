"""Mock InteractionProvider.

Stands in for whichever component actually delivers coaching responses.  It is READ ONLY
by shape: there is no method here that writes, and the architecture test asserts it.

Scenario coverage is the specification's table: answer; redirect; refusal; clarifying
question; degraded fallback; delivered 23h ago; delivered 25h ago; unavailable -- plus a
timeout, an invalid response, and an unrecognised response category, so that every
documented failure mode of the port has a scenario behind it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

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

log = get_logger("uc10.adapters.mock.interaction")

PORT_NAME = "InteractionProvider"

LEARNER = "user_alice"
OTHER_LEARNER = "user_bob"


@dataclass(frozen=True, slots=True)
class InteractionSpec:
    """One scenario. ``delivered_offset`` is relative to *now*, so a scenario keeps its
    meaning ('delivered 23h ago') however long the test runs."""

    interaction_id: str
    topic_tag: str = "contract_formation"
    user_id: str = LEARNER
    session_id: str = "sess_mock_1"
    session_mode: str = "coaching"
    response_category: str = ResponseCategory.ANSWER.value
    question_text: str = "MOCK_QUESTION_TEXT_DO_NOT_LOG"
    response_text: str = "MOCK_RESPONSE_TEXT_DO_NOT_LOG"
    raw_naric_level: object = "LEVEL_7"
    source_status: SourceStatus = SourceStatus.AVAILABLE
    course_completion_percent: int | None = 40
    delivered_offset: timedelta = field(default_factory=lambda: timedelta(hours=1))
    failure: str | None = None  # 'unavailable' | 'timeout' | 'invalid' | 'not_found'


def _default_specs() -> list[InteractionSpec]:
    return [
        InteractionSpec("int_answer", response_category="answer"),
        InteractionSpec("int_redirect", response_category="redirect"),
        InteractionSpec("int_refusal", response_category="refusal"),
        InteractionSpec("int_clarifying", response_category="clarifying_question"),
        InteractionSpec(
            "int_degraded",
            response_category="degraded_fallback",
            source_status=SourceStatus.PARTIAL,
            raw_naric_level=None,  # upstream answered and had nothing -> empty, not unavailable
        ),
        InteractionSpec(
            "int_unknown_category",
            response_category="some_category_we_have_never_seen",
        ),
        InteractionSpec("int_delivered_23h", delivered_offset=timedelta(hours=23)),
        InteractionSpec("int_delivered_25h", delivered_offset=timedelta(hours=25)),
        InteractionSpec("int_other_learner", user_id=OTHER_LEARNER, session_id="sess_mock_2"),
        InteractionSpec("int_naric_invalid", raw_naric_level=7),  # integer scale -> invalid
        InteractionSpec("int_unavailable", failure="unavailable"),
        InteractionSpec("int_timeout", failure="timeout"),
        InteractionSpec("int_invalid", failure="invalid"),
    ]


class MockInteractionProvider:
    """In-memory InteractionProvider. No write method exists on this class."""

    def __init__(self, clock: Clock, specs: list[InteractionSpec] | None = None) -> None:
        self._clock = clock
        self._specs: dict[str, InteractionSpec] = {
            spec.interaction_id: spec for spec in (specs if specs is not None else _default_specs())
        }

    # Test-support: scenario registration. Not part of the port, and not a write to any
    # upstream system -- it seeds this mock's own fixture table.
    def register(self, spec: InteractionSpec) -> InteractionSpec:
        self._specs[spec.interaction_id] = spec
        return spec

    def get(self, interaction_id: str) -> InteractionRecord:
        spec = self._require(interaction_id)
        self._maybe_fail(spec)
        naric = normalise_naric_level(spec.raw_naric_level)
        if naric.status is not SourceStatus.AVAILABLE:
            # The platform contract requires this to be logged. Shape only, never content.
            log.info(
                "naric_level_defaulted",
                interaction_id=spec.interaction_id,
                naric_level=naric.level.value,
                naric_level_source=naric.source.value,
                naric_source_status=naric.status.value,
                raw_kind=naric.raw_kind,
            )
        return InteractionRecord(
            interaction_id=spec.interaction_id,
            session_id=spec.session_id,
            user_id=spec.user_id,
            question_text=spec.question_text,
            response_text=spec.response_text,
            response_category=self._category(spec),
            topic_tag=spec.topic_tag,
            session_mode=spec.session_mode,
            naric_level=naric.level,
            naric_level_source=naric.source,
            explanation_profile=naric.explanation_profile,
            naric_source_status=naric.status,
            course_completion_percent=spec.course_completion_percent,
            delivered_at=self._delivered_at(spec),
            source_status=spec.source_status,
        )

    def delivered_at(self, interaction_id: str) -> datetime:
        spec = self._require(interaction_id)
        self._maybe_fail(spec)
        return self._delivered_at(spec)

    # ---------------------------------------------------------------- internals

    def _delivered_at(self, spec: InteractionSpec) -> datetime:
        return self._clock.now() - spec.delivered_offset

    def _require(self, interaction_id: str) -> InteractionSpec:
        spec = self._specs.get(interaction_id)
        if spec is None:
            raise RecordNotFound(PORT_NAME, "interaction_not_found")
        return spec

    @staticmethod
    def _category(spec: InteractionSpec) -> ResponseCategory:
        """An unrecognised category becomes ``unknown`` -- never a reason to be unrateable."""
        try:
            return ResponseCategory(spec.response_category)
        except ValueError:
            log.info(
                "response_category_unrecognised",
                interaction_id=spec.interaction_id,
                response_category=ResponseCategory.UNKNOWN.value,
            )
            return ResponseCategory.UNKNOWN

    @staticmethod
    def _maybe_fail(spec: InteractionSpec) -> None:
        if spec.failure is None:
            return
        if spec.failure == "unavailable":
            raise ProviderUnavailable(PORT_NAME, "upstream_unavailable")
        if spec.failure == "timeout":
            raise ProviderTimeout(PORT_NAME, "upstream_timeout")
        if spec.failure == "invalid":
            raise ProviderInvalidResponse(PORT_NAME, "unmappable_response")
        raise RecordNotFound(PORT_NAME, "interaction_not_found")
