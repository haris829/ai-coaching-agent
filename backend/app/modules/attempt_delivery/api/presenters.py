"""Learner-facing serialisation.

The delivered snapshot deliberately retains grading data (``isCorrect``,
``correctPosition``) so a future grading capability can score the attempt against
exactly what the learner saw. That data must never leave the service on a learner
request, so every field is copied explicitly here rather than spread — an allow-list,
so adding a field to the bank model cannot silently leak it.
"""

from __future__ import annotations

from typing import Any

from app.core.time import iso_or_none, to_iso
from app.modules.attempt_delivery.integration.uc02.types import BankQuestion, ScenarioSubQuestion
from app.modules.attempt_delivery.models import AttemptQuestion, QuizAttempt


def present_option(option: Any) -> dict[str, Any]:
    return {"optionId": option.option_id, "text": option.text}


def present_order_item(item: Any) -> dict[str, Any]:
    # Presented in delivered (possibly shuffled) order; correct_position is dropped.
    return {"itemId": item.item_id, "text": item.text}


def present_sub_question(sub: ScenarioSubQuestion) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "subQuestionId": sub.sub_question_id,
        "type": str(sub.type),
        "prompt": sub.prompt,
    }
    if sub.options:
        payload["options"] = [present_option(option) for option in sub.options]
    if sub.order_items:
        payload["orderItems"] = [present_order_item(item) for item in sub.order_items]
    if sub.min_selections is not None:
        payload["minSelections"] = sub.min_selections
    if sub.max_selections is not None:
        payload["maxSelections"] = sub.max_selections
    return payload


def present_question(question: AttemptQuestion) -> dict[str, Any]:
    """Serialise a delivered question for a learner, stripping all grading data."""
    snapshot: BankQuestion = BankQuestion.from_dict(question.question_snapshot)

    payload: dict[str, Any] = {
        "questionId": question.question_id,
        "position": question.position,
        "questionType": question.question_type,
        "questionVersion": question.question_version,
        "points": question.points,
        "prompt": snapshot.prompt,
    }
    if snapshot.scenario_text is not None:
        payload["scenarioText"] = snapshot.scenario_text
    if snapshot.topic_id is not None:
        payload["topicId"] = snapshot.topic_id
    if snapshot.options:
        payload["options"] = [present_option(option) for option in snapshot.options]
    if snapshot.order_items:
        payload["orderItems"] = [present_order_item(item) for item in snapshot.order_items]
    if snapshot.sub_questions:
        payload["subQuestions"] = [present_sub_question(sub) for sub in snapshot.sub_questions]
    if snapshot.min_selections is not None:
        payload["minSelections"] = snapshot.min_selections
    if snapshot.max_selections is not None:
        payload["maxSelections"] = snapshot.max_selections
    return payload


def present_configuration(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The *effective rules* of the attempt, from its locked configuration snapshot.

    An allow-list rather than an echo: future UC-01 fields (authoring metadata,
    internal notes) cannot leak, and it stays explicit which rules UC-03 honours.
    """
    return {
        "configurationVersionId": snapshot.get("configurationVersionId"),
        "version": snapshot.get("version"),
        "questionCount": snapshot.get("questionCount"),
        "timeLimitSeconds": snapshot.get("timeLimitSeconds"),
        "passMarkPercentage": snapshot.get("passMarkPercentage"),
        "maxAttempts": snapshot.get("maxAttempts"),
        "questionPresentation": snapshot.get("questionPresentation"),
        "randomiseQuestionOrder": snapshot.get("randomiseQuestionOrder"),
        "randomiseOptionOrder": snapshot.get("randomiseOptionOrder"),
        "allowIncompleteSubmission": snapshot.get("allowIncompleteSubmission"),
        "questionTypeQuotas": snapshot.get("questionTypeQuotas") or [],
        "activatedAt": snapshot.get("activatedAt"),
    }


def present_attempt(
    attempt: QuizAttempt, timing: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "attemptId": attempt.id,
        "learnerId": attempt.learner_id,
        "courseId": attempt.course_id,
        "quizId": attempt.quiz_id,
        "attemptNumber": attempt.attempt_number,
        "status": attempt.status,
        "questionPresentation": attempt.question_presentation,
        "totalQuestions": attempt.total_questions,
        "currentPosition": attempt.current_position,
        "startedAt": to_iso(attempt.started_at),
        "expiresAt": iso_or_none(attempt.expires_at),
        "submittedAt": iso_or_none(attempt.submitted_at),
        "finalisedAt": iso_or_none(attempt.finalised_at),
        "submissionReason": attempt.submission_reason,
        "lastActivityAt": to_iso(attempt.last_activity_at),
        "configurationVersionId": attempt.configuration_version_id,
        "configuration": present_configuration(attempt.configuration_snapshot),
    }
    if timing is not None:
        payload["timing"] = timing
    return payload


def navigation_links(attempt: QuizAttempt, question: AttemptQuestion) -> dict[str, Any]:
    """Sibling links so a client can page through a one-at-a-time paper."""
    base = f"/api/v1/attempts/{attempt.id}/questions"
    return {
        "position": question.position,
        "totalQuestions": attempt.total_questions,
        "isFirst": question.position == 1,
        "isLast": question.position == attempt.total_questions,
        "previousUrl": f"{base}/at/{question.position - 1}" if question.position > 1 else None,
        "nextUrl": (
            f"{base}/at/{question.position + 1}"
            if question.position < attempt.total_questions
            else None
        ),
        "selfUrl": f"{base}/at/{question.position}",
    }
