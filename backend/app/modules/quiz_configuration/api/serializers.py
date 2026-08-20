"""ORM / DTO → JSON for UC-01.

Keys are camelCase, matching the question bank's convention and the TypeScript admin UI. Kept in
one place so every endpoint returns the same shape for the same entity.
"""

from __future__ import annotations

from typing import Any

from app.core.time import iso_or_none
from app.modules.quiz_configuration.domain.rules import CapacityReport, capacity_to_json
from app.modules.quiz_configuration.models import ConfigurationVersion, Quiz

#: Publish a stored timestamp as explicit UTC. The shared implementation re-attaches UTC to the
#: naive values SQLite hands back, so a bare local-looking timestamp can never escape — and there
#: is one instant format across the whole API.
iso = iso_or_none


def quiz_summary(quiz: Quiz) -> dict[str, Any]:
    return {
        "id": quiz.id,
        "courseId": quiz.course_id,
        "courseTitle": quiz.course.title,
        "slug": quiz.slug,
        "title": quiz.title,
    }


def question_types(version: ConfigurationVersion) -> list[dict[str, Any]]:
    return [
        {"type": entry.question_type, "quota": entry.question_quota}
        for entry in version.question_types
    ]


def topic_scope(version: ConfigurationVersion) -> list[dict[str, Any]]:
    """The topic scope as it was frozen onto this version."""
    return [
        {"id": entry.topic_id, "slug": entry.topic_slug, "name": entry.topic_name}
        for entry in version.topics
    ]


def configuration_version(
    version: ConfigurationVersion,
    *,
    is_active: bool,
    attempt_count: int,
) -> dict[str, Any]:
    return {
        "id": version.id,
        "quizId": version.quiz_id,
        "versionNumber": version.version_number,
        "questionCount": version.question_count,
        "timeLimitMinutes": version.time_limit_minutes,
        "passMark": version.pass_mark,
        "randomiseQuestions": bool(version.randomise_questions),
        "maxAttempts": version.max_attempts,
        "deliveryMode": version.delivery_mode,
        "questionPresentation": version.question_presentation,
        "randomiseOptionOrder": bool(version.randomise_option_order),
        "allowIncompleteSubmission": bool(version.allow_incomplete_submission),
        "isFormalAssessment": bool(version.is_formal_assessment),
        "requiresHumanReview": bool(version.requires_human_review),
        "requiresAssessorApproval": bool(version.requires_assessor_approval),
        "questionTypes": question_types(version),
        "topics": topic_scope(version),
        "isActive": is_active,
        "settingsFingerprint": version.settings_fingerprint,
        "createdByUserId": version.created_by_user_id,
        "createdBy": version.created_by,
        "createdAt": iso(version.created_at),
        "attemptCount": attempt_count,
    }


def rules_summary(version: ConfigurationVersion) -> dict[str, Any]:
    """The rules a learner is shown, or that an attempt runs under.

    Always built from one configuration version — the active one for a rules screen, the locked
    one for an attempt — so the two can never disagree about what the quiz is.
    """
    return {
        "configurationVersionId": version.id,
        "configurationVersionNumber": version.version_number,
        "questionCount": version.question_count,
        "timeLimitMinutes": version.time_limit_minutes,
        "passMark": version.pass_mark,
        "randomiseQuestions": bool(version.randomise_questions),
        "deliveryMode": version.delivery_mode,
        "questionPresentation": version.question_presentation,
        "randomiseOptionOrder": bool(version.randomise_option_order),
        "allowIncompleteSubmission": bool(version.allow_incomplete_submission),
        # A learner is told a quiz is a formal assessment *before* starting it — that is the whole
        # point of the conditions screen UC-09 gates on. The two flags about what happens after
        # they pass are administrative and stay off this payload.
        "isFormalAssessment": bool(version.is_formal_assessment),
        "maxAttempts": version.max_attempts,
        "questionTypes": question_types(version),
        "topics": topic_scope(version),
    }


def capacity(report: CapacityReport) -> dict[str, Any]:
    return capacity_to_json(report)


def availability(counts: dict[Any, int]) -> dict[str, int]:
    return {question_type.value: total for question_type, total in counts.items()}
