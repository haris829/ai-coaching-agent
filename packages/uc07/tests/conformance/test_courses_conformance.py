"""CoursesProvider conformance. Adapter-agnostic."""

from __future__ import annotations

import pytest

from tests.conformance.adapters import COURSES_CASES, AdapterCase
from tests.conformance.shared import (
    assert_error_is_opaque,
    assert_no_upstream_leakage,
    assert_read_only,
)
from uc07.domain.enums import RecommendationType, SourceStatus
from uc07.domain.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from uc07.domain.models import CourseSummary, Enrolment, Recommendation
from uc07.ports.read_only import CoursesProvider

pytestmark = pytest.mark.parametrize("case", COURSES_CASES, ids=lambda case: case.id)


def _topics(case: AdapterCase) -> tuple[str, ...]:
    return tuple(case.extras["topic_tags"])


def test_adapter_implements_the_port(case: AdapterCase):
    assert isinstance(case.build(), CoursesProvider)


def test_adapter_is_read_only(case: AdapterCase):
    assert_read_only(case.build())


def test_recommendations_are_domain_objects_for_requested_topics(case: AdapterCase):
    recommendations = case.build().resolve_recommendations(_topics(case))
    assert recommendations
    for recommendation in recommendations:
        assert isinstance(recommendation, Recommendation)
        assert isinstance(recommendation.recommendation_type, RecommendationType)
        assert recommendation.topic_tag in _topics(case)


def test_catalogue_returns_course_summaries_with_lessons(case: AdapterCase):
    catalogue = case.build().catalogue()
    assert catalogue
    for course in catalogue:
        assert isinstance(course, CourseSummary)
        assert course.course_id
        for lesson in course.lessons:
            assert lesson.lesson_id


def test_enrolments_are_domain_objects_scoped_to_the_learner(case: AdapterCase):
    enrolments = case.build().enrolments_for(case.user_id)
    for enrolment in enrolments:
        assert isinstance(enrolment, Enrolment)
        assert enrolment.user_id == case.user_id
        if enrolment.completion_percentage is not None:
            assert 0 <= enrolment.completion_percentage <= 100


def test_no_upstream_payload_leaks_into_domain_objects(case: AdapterCase):
    adapter = case.build()
    for recommendation in adapter.resolve_recommendations(_topics(case)):
        assert_no_upstream_leakage(recommendation, case.upstream_tokens)
    for course in adapter.catalogue():
        assert_no_upstream_leakage(course, case.upstream_tokens)
    for enrolment in adapter.enrolments_for(case.user_id):
        assert_no_upstream_leakage(enrolment, case.upstream_tokens)


def test_status_is_a_source_status(case: AdapterCase):
    assert isinstance(case.build().status(), SourceStatus)


def test_empty_catalogue_is_reported_as_empty_not_unavailable(case: AdapterCase):
    adapter = case.build_empty()
    assert adapter.status() is SourceStatus.EMPTY
    assert adapter.catalogue() == ()


def test_unavailable_source_raises_provider_unavailable(case: AdapterCase):
    adapter = case.build_unavailable()
    with pytest.raises(ProviderUnavailable) as excinfo:
        adapter.resolve_recommendations(_topics(case))
    assert excinfo.value.port.value == "courses"
    assert_error_is_opaque(excinfo.value, case.upstream_tokens)
    with pytest.raises(ProviderUnavailable):
        adapter.catalogue()
    with pytest.raises(ProviderUnavailable):
        adapter.enrolments_for(case.user_id)
    with pytest.raises(ProviderUnavailable):
        adapter.status()


def test_timeout_raises_provider_timeout(case: AdapterCase):
    with pytest.raises(ProviderTimeout) as excinfo:
        case.build_timeout().catalogue()
    assert_error_is_opaque(excinfo.value, case.upstream_tokens)


def test_contract_breach_raises_provider_invalid_response(case: AdapterCase):
    with pytest.raises(ProviderInvalidResponse) as excinfo:
        case.build_invalid().catalogue()
    assert_error_is_opaque(excinfo.value, case.upstream_tokens)


def test_reads_are_repeatable_and_side_effect_free(case: AdapterCase):
    adapter = case.build()
    assert adapter.catalogue() == adapter.catalogue()
    assert adapter.resolve_recommendations(_topics(case)) == (
        adapter.resolve_recommendations(_topics(case))
    )


def test_unrequested_topics_are_not_recommended(case: AdapterCase):
    assert case.build().resolve_recommendations(["topic-that-does-not-exist"]) == ()
