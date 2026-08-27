"""Recommendation resolution: real identifiers only, no duplicate enrolment."""

from __future__ import annotations

from tests.conftest import build_harness
from uc07.application.recommendations import CoursesLoad, validate_recommendations
from uc07.domain.enums import RecommendationStatus, RecommendationType, SourceStatus
from uc07.domain.models import CourseSummary, Enrolment, LessonSummary, Recommendation

USER = "learner-rec"

COURSE = CourseSummary(
    course_id="course-1",
    title="Course One",
    topic_tags=("alpha",),
    lessons=(
        LessonSummary(lesson_id="lesson-1", title="L1", topic_tags=("alpha",)),
        LessonSummary(lesson_id="lesson-2", title="L2", topic_tags=("alpha", "beta")),
        LessonSummary(lesson_id="lesson-3", title="L3", topic_tags=("beta",)),
    ),
)


def course_candidate(topic="alpha", course_id="course-1") -> Recommendation:
    return Recommendation(
        topic_tag=topic,
        recommendation_type=RecommendationType.COURSE,
        course_id=course_id,
        title="Course One",
    )


def lesson_candidate(lesson_id="lesson-1", topic="alpha") -> Recommendation:
    return Recommendation(
        topic_tag=topic,
        recommendation_type=RecommendationType.LESSON,
        course_id="course-1",
        lesson_id=lesson_id,
    )


def load(candidates, *, enrolments=(), status=SourceStatus.AVAILABLE) -> CoursesLoad:
    return CoursesLoad(
        status=status,
        candidates=tuple(candidates),
        enrolments=tuple(enrolments),
        catalogue=(COURSE,),
    )


def test_valid_lesson_recommendation_is_kept():
    plan = validate_recommendations(
        load([lesson_candidate()]), ("alpha",), user_id=USER
    )
    assert plan.for_topic("alpha") == (
        Recommendation(
            topic_tag="alpha",
            recommendation_type=RecommendationType.LESSON,
            course_id="course-1",
            lesson_id="lesson-1",
            title="L1",
        ),
    )
    assert plan.summary.status is RecommendationStatus.AVAILABLE
    assert plan.summary.resolved_count == 1


def test_unknown_lesson_id_is_removed_and_not_replaced():
    plan = validate_recommendations(
        load([lesson_candidate("lesson-999")]), ("alpha",), user_id=USER
    )
    assert plan.for_topic("alpha") == ()
    assert plan.summary.rejected_unresolvable_count == 1
    assert plan.summary.resolved_count == 0
    assert plan.summary.status is RecommendationStatus.EMPTY


def test_unknown_course_id_is_removed_and_not_replaced():
    plan = validate_recommendations(
        load([course_candidate(course_id="course-ghost")]), ("alpha",), user_id=USER
    )
    assert plan.for_topic("alpha") == ()
    assert plan.summary.rejected_unresolvable_count == 1


def test_existing_enrolment_becomes_lesson_recommendations():
    plan = validate_recommendations(
        load(
            [course_candidate()],
            enrolments=[Enrolment(user_id=USER, course_id="course-1")],
        ),
        ("alpha",),
        user_id=USER,
    )
    recommendations = plan.for_topic("alpha")
    assert [rec.lesson_id for rec in recommendations] == ["lesson-1", "lesson-2"]
    assert all(
        rec.recommendation_type is RecommendationType.LESSON for rec in recommendations
    )
    assert plan.summary.converted_to_lesson_count == 1
    # No duplicate enrolment is ever recommended.
    assert not any(
        rec.recommendation_type is RecommendationType.COURSE for rec in recommendations
    )


def test_enrolment_without_a_matching_lesson_drops_the_candidate_rather_than_guessing():
    plan = validate_recommendations(
        load(
            [course_candidate(topic="gamma")],
            enrolments=[Enrolment(user_id=USER, course_id="course-1")],
        ),
        ("gamma",),
        user_id=USER,
    )
    assert plan.for_topic("gamma") == ()
    assert plan.summary.dropped_already_enrolled_count == 1
    assert plan.summary.converted_to_lesson_count == 0


def test_another_learners_enrolment_does_not_affect_this_learner():
    plan = validate_recommendations(
        load(
            [course_candidate()],
            enrolments=[Enrolment(user_id="someone-else", course_id="course-1")],
        ),
        ("alpha",),
        user_id=USER,
    )
    assert [rec.recommendation_type for rec in plan.for_topic("alpha")] == [
        RecommendationType.COURSE
    ]


def test_candidates_for_topics_that_are_not_gaps_are_ignored():
    plan = validate_recommendations(
        load([course_candidate(topic="not-a-gap")]), ("alpha",), user_id=USER
    )
    assert plan.by_topic == {}
    assert plan.summary.rejected_unresolvable_count == 0


def test_courses_unavailable_marks_recommendations_unavailable():
    plan = validate_recommendations(
        CoursesLoad.failed(SourceStatus.UNAVAILABLE), ("alpha",), user_id=USER
    )
    assert plan.by_topic == {}
    assert plan.summary.status is RecommendationStatus.UNAVAILABLE


def test_partial_course_data_marks_recommendations_partial():
    plan = validate_recommendations(
        load([lesson_candidate()], status=SourceStatus.PARTIAL), ("alpha",), user_id=USER
    )
    assert plan.summary.status is RecommendationStatus.PARTIAL
    assert plan.summary.resolved_count == 1


def test_recommendations_are_deduplicated_and_sorted():
    plan = validate_recommendations(
        load([lesson_candidate("lesson-2"), lesson_candidate("lesson-1"), lesson_candidate("lesson-1")]),
        ("alpha",),
        user_id=USER,
    )
    assert [rec.lesson_id for rec in plan.for_topic("alpha")] == ["lesson-1", "lesson-2"]


# ---------------------------------------------------------------------------
# End to end through the service
# ---------------------------------------------------------------------------


def test_report_recommendations_resolve_to_real_catalogue_identifiers():
    harness = build_harness("struggle_mixed")
    report = harness.service.current_report(harness.user_id).report
    assert report is not None

    catalogue = {
        course["course_id"]: {
            lesson["lesson_id"] for lesson in course.get("lessons", ())
        }
        for course in harness.scenario.courses.catalogue
    }
    assert report.recommendations.status is RecommendationStatus.AVAILABLE
    assert report.recommendations.rejected_unresolvable_count == 2

    for gap in report.gaps:
        for rec in gap.recommendations:
            assert rec.course_id in catalogue
            if rec.lesson_id is not None:
                assert rec.lesson_id in catalogue[rec.course_id]


def test_enrolled_course_yields_lesson_recommendations_in_the_report():
    report = build_harness("struggle_mixed").service.current_report("learner-001").report
    assert report is not None
    contract = next(gap for gap in report.gaps if gap.topic_tag == "contract_formation")
    assert [
        (rec.recommendation_type.value, rec.course_id, rec.lesson_id)
        for rec in contract.recommendations
    ] == [
        ("lesson", "course-contract-essentials", "lesson-cf-01"),
        ("lesson", "course-contract-essentials", "lesson-cf-02"),
    ]


def test_not_enrolled_learner_gets_the_course_level_recommendation():
    report = (
        build_harness("courses_not_enrolled").service.current_report("learner-001").report
    )
    assert report is not None
    contract = next(gap for gap in report.gaps if gap.topic_tag == "contract_formation")
    assert [
        (rec.recommendation_type.value, rec.course_id) for rec in contract.recommendations
    ] == [("course", "course-contract-essentials")]


def test_gaps_survive_when_the_course_source_is_unavailable():
    report = (
        build_harness("courses_unavailable").service.current_report("learner-001").report
    )
    assert report is not None
    assert report.recommendations.status is RecommendationStatus.UNAVAILABLE
    assert report.source_statuses.courses is SourceStatus.UNAVAILABLE
    assert all(gap.recommendations == () for gap in report.gaps)
    assert len(report.gaps) == 5
    assert "recommendations_temporarily_unavailable" in {
        notice.code.value for notice in report.notices
    }


def test_partial_course_source_is_reported_as_partial_in_the_report():
    report = build_harness("courses_partial").service.current_report("learner-001").report
    assert report is not None
    assert report.recommendations.status is RecommendationStatus.PARTIAL
    assert report.source_statuses.courses is SourceStatus.PARTIAL
    assert "recommendations_partial" in {
        notice.code.value for notice in report.notices
    }


def test_invalid_course_source_keeps_gaps_and_marks_recommendations_unavailable():
    report = build_harness("courses_invalid").service.current_report("learner-001").report
    assert report is not None
    assert report.source_statuses.courses is SourceStatus.INVALID
    assert report.recommendations.status is RecommendationStatus.UNAVAILABLE
    assert len(report.gaps) == 5


def test_all_invalid_candidates_leave_gaps_without_recommendations():
    report = (
        build_harness("courses_only_invalid_candidates")
        .service.current_report("learner-001")
        .report
    )
    assert report is not None
    assert report.recommendations.resolved_count == 0
    assert report.recommendations.rejected_unresolvable_count == 2
    assert all(gap.recommendations == () for gap in report.gaps)
