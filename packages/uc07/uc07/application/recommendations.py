"""Recommendation resolution and validation.

Rules:

* Every recommendation must resolve to a real course id, and a lesson-level
  recommendation must resolve to a real lesson id inside that course. Anything
  unresolvable is removed and never replaced with a guessed identifier.
* If the learner is already enrolled in a recommended course, the course-level
  recommendation is replaced by specific lessons inside that course (no duplicate
  enrolment). If no lesson in that course carries the gap topic, the candidate is
  dropped rather than guessed.
* Gap analysis never depends on course availability: when the courses source
  cannot be read, gaps survive and the recommendation status becomes
  ``unavailable``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from uc07.domain.enums import RecommendationStatus, RecommendationType, SourceStatus
from uc07.domain.models import (
    CourseSummary,
    Enrolment,
    Recommendation,
    RecommendationSummary,
)


@dataclass(frozen=True, slots=True)
class CoursesLoad:
    """Outcome of reading the courses source.

    ``candidates is None`` means the source could not be read at all; an empty
    tuple means it answered and offered nothing.
    """

    status: SourceStatus
    candidates: tuple[Recommendation, ...] | None
    enrolments: tuple[Enrolment, ...] = ()
    catalogue: tuple[CourseSummary, ...] = ()

    @classmethod
    def failed(cls, status: SourceStatus) -> "CoursesLoad":
        return cls(status=status, candidates=None)


@dataclass(frozen=True, slots=True)
class RecommendationPlan:
    """Validated recommendations per gap topic, plus what validation discarded."""

    by_topic: dict[str, tuple[Recommendation, ...]]
    summary: RecommendationSummary

    def for_topic(self, topic_tag: str) -> tuple[Recommendation, ...]:
        return self.by_topic.get(topic_tag, ())


def _lessons_for_topic(course: CourseSummary, topic_tag: str) -> tuple[Recommendation, ...]:
    return tuple(
        Recommendation(
            topic_tag=topic_tag,
            recommendation_type=RecommendationType.LESSON,
            course_id=course.course_id,
            lesson_id=lesson.lesson_id,
            title=lesson.title,
        )
        for lesson in course.lessons
        if topic_tag in lesson.topic_tags
    )


def validate_recommendations(
    load: CoursesLoad, gap_topic_tags: tuple[str, ...], *, user_id: str
) -> RecommendationPlan:
    """Validate candidates against the catalogue and the learner's enrolments."""
    if load.candidates is None:
        return RecommendationPlan(
            by_topic={},
            summary=RecommendationSummary(
                status=RecommendationStatus.UNAVAILABLE,
                resolved_count=0,
                rejected_unresolvable_count=0,
                converted_to_lesson_count=0,
                dropped_already_enrolled_count=0,
            ),
        )

    courses = {course.course_id: course for course in load.catalogue}
    enrolled = {
        enrolment.course_id
        for enrolment in load.enrolments
        if enrolment.user_id == user_id
    }
    wanted = set(gap_topic_tags)

    accepted: dict[str, set[Recommendation]] = defaultdict(set)
    rejected = 0
    converted = 0
    dropped_enrolled = 0

    for candidate in load.candidates:
        if candidate.topic_tag not in wanted:
            # Not a topic we asked about: silently irrelevant, not a defect.
            continue

        course = courses.get(candidate.course_id)
        if course is None:
            rejected += 1
            continue

        if candidate.recommendation_type is RecommendationType.LESSON:
            lesson = course.lesson(candidate.lesson_id or "")
            if lesson is None:
                rejected += 1
                continue
            accepted[candidate.topic_tag].add(
                Recommendation(
                    topic_tag=candidate.topic_tag,
                    recommendation_type=RecommendationType.LESSON,
                    course_id=course.course_id,
                    lesson_id=lesson.lesson_id,
                    title=candidate.title or lesson.title,
                )
            )
            continue

        # Course-level candidate.
        if candidate.course_id in enrolled:
            lessons = _lessons_for_topic(course, candidate.topic_tag)
            if not lessons:
                dropped_enrolled += 1
                continue
            converted += 1
            accepted[candidate.topic_tag].update(lessons)
            continue

        accepted[candidate.topic_tag].add(
            Recommendation(
                topic_tag=candidate.topic_tag,
                recommendation_type=RecommendationType.COURSE,
                course_id=course.course_id,
                lesson_id=None,
                title=candidate.title or course.title,
            )
        )

    by_topic = {
        topic_tag: tuple(sorted(items, key=lambda rec: rec.sort_key))
        for topic_tag, items in sorted(accepted.items())
        if items
    }
    resolved_count = sum(len(items) for items in by_topic.values())

    if load.status is SourceStatus.PARTIAL:
        status = RecommendationStatus.PARTIAL
    elif resolved_count == 0:
        status = RecommendationStatus.EMPTY
    else:
        status = RecommendationStatus.AVAILABLE

    return RecommendationPlan(
        by_topic=by_topic,
        summary=RecommendationSummary(
            status=status,
            resolved_count=resolved_count,
            rejected_unresolvable_count=rejected,
            converted_to_lesson_count=converted,
            dropped_already_enrolled_count=dropped_enrolled,
        ),
    )
