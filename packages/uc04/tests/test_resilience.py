"""Resilience: a dependency failing degrades the answer, never removes it."""

from __future__ import annotations

import pytest

from conftest import IN_LESSON_QUESTION, build_harness
from uc04.adapters.mock import fixtures as fx
from uc04.domain.enums import Grounding, SourceStatus
from uc04.domain.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
    SessionIdentityError,
)
from uc04.domain.models import GenerationResult


# ------------------------------------------------------------------- lesson unavailable

UNAVAILABLE_LESSONS = [
    ("provider down", fx.LESSON_UNAVAILABLE, SourceStatus.UNAVAILABLE),
    ("provider timeout", fx.LESSON_TIMEOUT, SourceStatus.UNAVAILABLE),
    ("unmappable payload", fx.LESSON_INVALID, SourceStatus.INVALID),
    ("lesson missing", fx.LESSON_UNKNOWN, SourceStatus.UNAVAILABLE),
]


@pytest.mark.parametrize(("label", "lesson_id", "expected"), UNAVAILABLE_LESSONS)
def test_lesson_unavailable_degrades_to_general_topic_coaching(
    harness, label: str, lesson_id: str, expected: SourceStatus
) -> None:
    response = harness.ask(IN_LESSON_QUESTION, lesson_id=lesson_id)

    assert response.explanation.strip(), f"{label}: a content outage must never mean silence"
    assert response.grounding is Grounding.GENERAL_KNOWLEDGE
    assert response.source_status["lesson"] is expected
    assert response.notice and "could not be accessed" in response.notice


def test_lesson_unavailable_carries_an_explicit_notice(harness) -> None:
    response = harness.ask(IN_LESSON_QUESTION, lesson_id=fx.LESSON_UNAVAILABLE)
    assert "linked lesson could not be accessed" in (response.notice or "")
    assert response.section_reference.lesson_section_id is None


def test_empty_and_unavailable_are_never_conflated(harness) -> None:
    """Two different states with two different meanings."""
    unavailable = harness.ask(IN_LESSON_QUESTION, lesson_id=fx.LESSON_UNAVAILABLE)
    assert unavailable.source_status["lesson"] is SourceStatus.UNAVAILABLE

    empty_context = harness.ask(IN_LESSON_QUESTION, user_id=fx.USER_NO_CONTEXT)
    assert empty_context.source_status["learner_context"] is SourceStatus.EMPTY

    down_context = harness.ask(IN_LESSON_QUESTION, user_id=fx.USER_CONTEXT_DOWN)
    assert down_context.source_status["learner_context"] is SourceStatus.UNAVAILABLE


def test_course_structure_unavailable_still_answers_without_references() -> None:
    """Nothing can be verified, so nothing is referenced - and the answer still lands."""
    from uc04.adapters.mock.courses import MockCoursesProvider

    class StructureDownCourses(MockCoursesProvider):
        def get_course_structure(self, course_id: str):  # noqa: ANN201
            raise ProviderUnavailable("courses", "catalogue unavailable")

    harness = build_harness(courses=StructureDownCourses())
    response = harness.ask(IN_LESSON_QUESTION)

    assert response.explanation.strip()
    assert response.cross_lesson_references == ()
    assert response.source_status["course_structure"] is SourceStatus.UNAVAILABLE
    # The lesson itself still loaded, so the answer is still lesson-grounded.
    assert response.grounding is Grounding.LESSON


# ---------------------------------------------------------------------- generator failure


def test_generator_timeout_surfaces_as_a_retryable_typed_error() -> None:
    class TimingOutGenerator:
        def generate(self, request):  # noqa: ANN001, ANN201
            raise ProviderTimeout("answer_generator", "generation exceeded its budget")

    harness = build_harness(generator=TimingOutGenerator())
    with pytest.raises(ProviderTimeout):
        harness.ask(IN_LESSON_QUESTION)


def test_generator_unavailable_surfaces_as_a_typed_error() -> None:
    class DownGenerator:
        def generate(self, request):  # noqa: ANN001, ANN201
            raise ProviderUnavailable("answer_generator", "generation service unavailable")

    harness = build_harness(generator=DownGenerator())
    with pytest.raises(ProviderUnavailable):
        harness.ask(IN_LESSON_QUESTION)


def test_a_malformed_generator_result_is_an_invalid_response_not_a_regex_job() -> None:
    class MalformedGenerator:
        def generate(self, request):  # noqa: ANN001, ANN201
            return "just a string, not the contract shape"

    harness = build_harness(generator=MalformedGenerator())
    with pytest.raises(ProviderInvalidResponse):
        harness.ask(IN_LESSON_QUESTION)


def test_an_empty_explanation_is_an_invalid_response() -> None:
    class EmptyGenerator:
        def generate(self, request):  # noqa: ANN001, ANN201
            return GenerationResult(explanation="   ", framing_used=request.framing)

    harness = build_harness(generator=EmptyGenerator())
    with pytest.raises(ProviderInvalidResponse):
        harness.ask(IN_LESSON_QUESTION)


def test_a_generator_that_ignores_the_requested_framing_is_rejected() -> None:
    """Non-repetition depends on the framing being honoured, so this must not pass silently."""
    from uc04.domain.enums import FramingStrategy

    class StubbornGenerator:
        def generate(self, request):  # noqa: ANN001, ANN201
            return GenerationResult(
                explanation="Always the same approach.",
                framing_used=FramingStrategy.ANALOGY,
            )

    harness = build_harness(generator=StubbornGenerator())
    with pytest.raises(ProviderInvalidResponse):
        harness.ask(IN_LESSON_QUESTION)


# ------------------------------------------------------------------- registry failures


def test_a_framing_registry_outage_still_answers() -> None:
    harness = build_harness()
    harness.framings.always_fail = True
    response = harness.ask(IN_LESSON_QUESTION)
    assert response.explanation.strip()


def test_a_tagger_outage_falls_back_to_unclassified_and_still_answers(harness) -> None:
    from uc04.adapters.mock.concept_tagger import UNAVAILABLE_MARKER
    from uc04.domain.enums import UNCLASSIFIED

    response = harness.ask(f"What does hearsay mean {UNAVAILABLE_MARKER}?")
    assert response.explanation.strip()
    assert response.concept_tag in (UNCLASSIFIED, "hearsay")
    assert response.source_status.get("concept_tagger") is SourceStatus.UNAVAILABLE


# ------------------------------------------------------------------------ session identity


def test_a_missing_session_id_is_refused_not_invented(harness) -> None:
    """UC-04 receives a session identifier. It never creates one on a production path."""
    with pytest.raises(SessionIdentityError):
        harness.ask(IN_LESSON_QUESTION, session_id="")
    with pytest.raises(SessionIdentityError):
        harness.ask(IN_LESSON_QUESTION, session_id="   ")


def test_no_session_is_ever_minted(harness) -> None:
    response = harness.ask(IN_LESSON_QUESTION, session_id="sess_supplied_by_caller")
    assert response.session_id == "sess_supplied_by_caller"
    record = harness.interactions.get(response.interaction_id)
    assert record.session_id == "sess_supplied_by_caller"


# --------------------------------------------------------------- combined degradation


def test_several_dependencies_failing_at_once_still_answers() -> None:
    harness = build_harness()
    harness.interactions.always_fail = True
    harness.framings.always_fail = True

    response = harness.ask(
        IN_LESSON_QUESTION, lesson_id=fx.LESSON_UNAVAILABLE, user_id=fx.USER_CONTEXT_DOWN
    )
    assert response.explanation.strip()
    assert response.grounding is Grounding.GENERAL_KNOWLEDGE
    assert response.naric_level.value == "LEVEL_5"
    assert response.naric_level_source.value == "default"
