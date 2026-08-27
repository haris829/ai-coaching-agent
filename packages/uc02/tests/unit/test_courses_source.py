"""Courses Agent behaviour end to end through the assembly service."""

from __future__ import annotations

from uc02.domain.models.enums import SourceName, SourceStatus
from uc02.infrastructure.providers.mocks import CoursesScenario
from tests.fixtures.factories import make_harness, make_identity


async def _context(scenario: CoursesScenario):
    harness = make_harness(courses=scenario)
    outcome = await harness.service.initialize(make_identity())
    return outcome.context


async def test_single_enrolment_is_normalised_with_completion_and_lesson():
    context = await _context(CoursesScenario.SINGLE_ENROLMENT)
    assert len(context.courses.enrolments) == 1
    enrolment = context.courses.enrolments[0]
    assert enrolment.course_id == "course-contract-law-101"
    assert enrolment.course_name == "Contract Law Foundations"
    assert enrolment.completion_percentage == 42.5
    assert enrolment.last_accessed_lesson_id == "lesson-004"
    assert enrolment.last_accessed_lesson_name == "Offer and Acceptance"
    assert context.source_status[SourceName.COURSES].status is SourceStatus.AVAILABLE


async def test_multiple_enrolments_preserve_each_completion_percentage():
    context = await _context(CoursesScenario.MULTIPLE_ENROLMENTS)
    assert len(context.courses.enrolments) == 3
    percentages = [e.completion_percentage for e in context.courses.enrolments]
    assert percentages == [42.5, 100.0, 0.0]
    assert all(0.0 <= p <= 100.0 for p in percentages)


async def test_empty_enrolments_are_empty_not_unavailable():
    context = await _context(CoursesScenario.EMPTY)
    assert context.courses.enrolments == ()
    outcome = context.source_status[SourceName.COURSES]
    assert outcome.status is SourceStatus.EMPTY
    assert outcome.status is not SourceStatus.UNAVAILABLE
    # A learner with no enrolments did not trigger a fallback.
    assert outcome.fallback_applied is False


async def test_missing_last_accessed_lesson_is_partial_not_a_failure():
    context = await _context(CoursesScenario.PARTIAL_MISSING_LESSON)
    assert len(context.courses.enrolments) == 2
    without_lesson = [e for e in context.courses.enrolments if e.last_accessed_lesson_id is None]
    assert len(without_lesson) == 1
    outcome = context.source_status[SourceName.COURSES]
    assert outcome.status is SourceStatus.PARTIAL
    assert outcome.fallback_applied is False


async def test_unavailable_courses_defaults_to_empty_enrolments():
    context = await _context(CoursesScenario.UNAVAILABLE)
    assert context.courses.enrolments == ()
    outcome = context.source_status[SourceName.COURSES]
    assert outcome.status is SourceStatus.UNAVAILABLE
    assert outcome.fallback_applied is True
    # Nothing was invented to fill the gap.
    assert context.courses.enrolments == ()


async def test_invalid_courses_response_is_recorded_as_invalid():
    context = await _context(CoursesScenario.INVALID_RESPONSE)
    assert context.source_status[SourceName.COURSES].status is SourceStatus.INVALID


async def test_courses_failure_does_not_affect_other_sources():
    context = await _context(CoursesScenario.UNAVAILABLE)
    assert context.naric.level == 5
    assert context.naric.level_source.value == "retrieved"
    assert context.question_history.count == 20
    assert context.legal_profile.explanation_domain.value == "speciality"
