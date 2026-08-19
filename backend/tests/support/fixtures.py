"""Test fixtures for UC-03.

These build the *inputs* UC-03 consumes across its boundaries — a quiz, a configuration version, an
enrolment, a question pool — through the port **fakes** in ``tests.support.fakes``, so a test states
the world in UC-03's own vocabulary and nothing else has to be true for it to run.

The real UC-01 and UC-02 adapters are exercised by ``tests/integration/`` instead. Keeping the two
apart is what lets this suite arrange situations UC-01 rightly forbids — an incoherent
configuration,
a withdrawn version — which are exactly the cases UC-03's boundary guard exists for.
"""

from __future__ import annotations

from typing import Any

from app.modules.attempt_delivery.container import RequestContext
from app.modules.attempt_delivery.domain.enums import (
    EnrolmentStatus,
    QuestionPresentation,
    QuestionType,
)
from app.modules.attempt_delivery.integration.uc01.types import QuizConfigurationVersion
from app.modules.attempt_delivery.integration.uc02.types import (
    BankQuestion,
    QuestionOption,
    QuestionOrderItem,
    ScenarioSubQuestion,
)

COURSE_ID = "course-fire-safety"
QUIZ_ID = "quiz-fire-safety-final"

#: Learner ids are numeric strings because UC-03 now resolves the learner through the shared
#: identity seam, and ``qa_users`` keys on an integer. The quiz and course stay opaque strings —
#: UC-03 treats every cross-boundary reference as opaque, and the fakes never parse them.
LEARNER_ID = "9001"
OTHER_LEARNER_ID = "9002"

DEFAULT_COUNTS: dict[QuestionType, int] = {
    QuestionType.SINGLE_CHOICE: 15,
    QuestionType.TRUE_FALSE: 10,
    QuestionType.MULTI_SELECT: 6,
    QuestionType.SCENARIO: 4,
    QuestionType.DRAG_TO_ORDER: 5,
}

#: The rules used unless a test overrides them.
DEFAULT_RULES: dict[str, Any] = {
    "questionCount": 5,
    "timeLimitSeconds": 1800,
    "passMarkPercentage": 70,
    "maxAttempts": 3,
    "randomiseQuestionOrder": False,
    "randomiseOptionOrder": False,
    "questionPresentation": str(QuestionPresentation.ALL_AT_ONCE),
    "allowIncompleteSubmission": True,
}


# ---------------------------------------------------------------------------
# Question builders - one per supported UC-02 structure
# ---------------------------------------------------------------------------


def _build(defaults: dict[str, Any], overrides: dict[str, Any]) -> BankQuestion:
    """Merge overrides over defaults so any field can be replaced by a test."""
    return BankQuestion(**{**defaults, **overrides})


def single_choice_question(index: int, **overrides: Any) -> BankQuestion:
    qid = overrides.get("question_id", f"q-sc-{index:02d}")
    defaults: dict[str, Any] = {
        "question_id": qid,
        "version": 1,
        "type": QuestionType.SINGLE_CHOICE,
        "prompt": f"Single choice {index}: what is the first action on hearing the alarm?",
        "quiz_id": QUIZ_ID,
        "course_id": COURSE_ID,
        "topic_id": "topic-evacuation",
        "options": (
            QuestionOption(f"{qid}-o1", "Leave by the nearest fire exit", is_correct=True),
            QuestionOption(f"{qid}-o2", "Collect personal belongings", is_correct=False),
            QuestionOption(f"{qid}-o3", "Use the lift", is_correct=False),
            QuestionOption(f"{qid}-o4", "Wait for a second alarm", is_correct=False),
        ),
        "points": 1.0,
    }
    return _build(defaults, overrides)


def true_false_question(index: int, **overrides: Any) -> BankQuestion:
    qid = overrides.get("question_id", f"q-tf-{index:02d}")
    defaults: dict[str, Any] = {
        "question_id": qid,
        "version": 1,
        "type": QuestionType.TRUE_FALSE,
        "prompt": f"True/false {index}: a CO2 extinguisher suits electrical fires.",
        "quiz_id": QUIZ_ID,
        "course_id": COURSE_ID,
        "topic_id": "topic-extinguishers",
        "points": 1.0,
    }
    return _build(defaults, overrides)


def multi_select_question(index: int, **overrides: Any) -> BankQuestion:
    qid = overrides.get("question_id", f"q-ms-{index:02d}")
    defaults: dict[str, Any] = {
        "question_id": qid,
        "version": 1,
        "type": QuestionType.MULTI_SELECT,
        "prompt": f"Multi-select {index}: which of these are fire hazards?",
        "quiz_id": QUIZ_ID,
        "course_id": COURSE_ID,
        "topic_id": "topic-hazards",
        "options": (
            QuestionOption(f"{qid}-o1", "Overloaded socket", is_correct=True),
            QuestionOption(f"{qid}-o2", "Blocked fire door", is_correct=True),
            QuestionOption(f"{qid}-o3", "Closed window", is_correct=False),
            QuestionOption(f"{qid}-o4", "Frayed cable", is_correct=True),
            QuestionOption(f"{qid}-o5", "Labelled extinguisher", is_correct=False),
        ),
        "min_selections": 1,
        "max_selections": 4,
        "points": 2.0,
    }
    return _build(defaults, overrides)


def drag_to_order_question(index: int, **overrides: Any) -> BankQuestion:
    qid = overrides.get("question_id", f"q-do-{index:02d}")
    defaults: dict[str, Any] = {
        "question_id": qid,
        "version": 1,
        "type": QuestionType.DRAG_TO_ORDER,
        "prompt": f"Drag-to-order {index}: order the evacuation steps.",
        "quiz_id": QUIZ_ID,
        "course_id": COURSE_ID,
        "topic_id": "topic-procedure",
        "order_items": (
            QuestionOrderItem(f"{qid}-i1", "Raise the alarm", correct_position=1),
            QuestionOrderItem(f"{qid}-i2", "Evacuate the building", correct_position=2),
            QuestionOrderItem(f"{qid}-i3", "Report to the assembly point", correct_position=3),
            QuestionOrderItem(f"{qid}-i4", "Await the roll call", correct_position=4),
        ),
        "points": 3.0,
    }
    return _build(defaults, overrides)


def scenario_question(index: int, **overrides: Any) -> BankQuestion:
    qid = overrides.get("question_id", f"q-sn-{index:02d}")
    defaults: dict[str, Any] = {
        "question_id": qid,
        "version": 1,
        "type": QuestionType.SCENARIO,
        "prompt": f"Scenario {index}: respond to the situation below.",
        "quiz_id": QUIZ_ID,
        "course_id": COURSE_ID,
        "topic_id": "topic-scenarios",
        "scenario_text": (
            "Smoke is coming from a server cabinet on the second floor. "
            "The corridor is clear and the alarm has not sounded."
        ),
        "sub_questions": (
            ScenarioSubQuestion(
                sub_question_id=f"{qid}-s1",
                type=QuestionType.SINGLE_CHOICE,
                prompt="What do you do first?",
                options=(
                    QuestionOption(f"{qid}-s1-o1", "Activate the nearest call point", is_correct=True),
                    QuestionOption(f"{qid}-s1-o2", "Open the cabinet to investigate", is_correct=False),
                    QuestionOption(f"{qid}-s1-o3", "Finish the current task", is_correct=False),
                ),
            ),
            ScenarioSubQuestion(
                sub_question_id=f"{qid}-s2",
                type=QuestionType.TRUE_FALSE,
                prompt="A water extinguisher is appropriate here.",
            ),
            ScenarioSubQuestion(
                sub_question_id=f"{qid}-s3",
                type=QuestionType.MULTI_SELECT,
                prompt="Who must be informed?",
                options=(
                    QuestionOption(f"{qid}-s3-o1", "The fire warden", is_correct=True),
                    QuestionOption(f"{qid}-s3-o2", "The IT on-call engineer", is_correct=True),
                    QuestionOption(f"{qid}-s3-o3", "The catering team", is_correct=False),
                ),
                min_selections=1,
            ),
        ),
        "points": 4.0,
    }
    return _build(defaults, overrides)


BUILDERS = {
    QuestionType.SINGLE_CHOICE: single_choice_question,
    QuestionType.TRUE_FALSE: true_false_question,
    QuestionType.MULTI_SELECT: multi_select_question,
    QuestionType.SCENARIO: scenario_question,
    QuestionType.DRAG_TO_ORDER: drag_to_order_question,
}


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def seed_quiz(
    ctx: RequestContext,
    *,
    quiz_id: str = QUIZ_ID,
    course_id: str = COURSE_ID,
    available: bool = True,
    reason: str | None = None,
) -> None:
    ctx.seedable_configurations.upsert_quiz(
        quiz_id=quiz_id,
        course_id=course_id,
        title="Fire Safety - Final Assessment",
        available=available,
        reason=reason,
    )


def seed_enrolment(
    ctx: RequestContext,
    *,
    learner_id: str = LEARNER_ID,
    course_id: str = COURSE_ID,
    status: EnrolmentStatus = EnrolmentStatus.ACTIVE,
) -> None:
    ctx.seedable_enrolments.upsert_enrolment(
        learner_id=learner_id, course_id=course_id, status=status
    )


def seed_question_bank(
    ctx: RequestContext, *, counts: dict[QuestionType, int] | None = None
) -> list[BankQuestion]:
    resolved = {**DEFAULT_COUNTS, **(counts or {})}
    created: list[BankQuestion] = []
    for question_type, count in resolved.items():
        for index in range(1, count + 1):
            question = BUILDERS[question_type](index)
            ctx.seedable_question_bank.upsert_question(question)
            created.append(question)
    return created


def publish_configuration(
    ctx: RequestContext,
    *,
    version: int = 1,
    configuration_version_id: str | None = None,
    quiz_id: str = QUIZ_ID,
    course_id: str = COURSE_ID,
    activated_at: str = "2026-01-01T00:00:00Z",
    rules: dict[str, Any] | None = None,
    activate: bool = True,
) -> QuizConfigurationVersion:
    return ctx.seedable_configurations.publish_version(
        configuration_version_id=configuration_version_id or f"cfg-v{version}",
        quiz_id=quiz_id,
        course_id=course_id,
        version=version,
        activated_at=activated_at,
        rules={**DEFAULT_RULES, **(rules or {})},
        activate=activate,
    )


def seed_world(
    ctx: RequestContext,
    *,
    rules: dict[str, Any] | None = None,
    counts: dict[QuestionType, int] | None = None,
    enrolment_status: EnrolmentStatus = EnrolmentStatus.ACTIVE,
    quiz_available: bool = True,
) -> QuizConfigurationVersion:
    """Seed the full default world.

    An available quiz, an active enrolment, a generous question bank and configuration
    version 1.
    """
    seed_quiz(ctx, available=quiz_available)
    seed_enrolment(ctx, status=enrolment_status)
    seed_question_bank(ctx, counts=counts)
    configuration = publish_configuration(ctx, rules=rules)
    return configuration


# ---------------------------------------------------------------------------
# Answer payload builders - keeps tests readable
# ---------------------------------------------------------------------------


def answer_for(question: dict[str, Any], *, variant: int = 0) -> Any:
    """Build a valid answer payload for a presented question.

    ``variant`` picks a different-but-valid answer, which is how the tests distinguish
    "saved again unchanged" from "genuinely updated".
    """
    question_type = question["questionType"]

    if question_type == str(QuestionType.SINGLE_CHOICE):
        options = question["options"]
        return {"selectedOptionId": options[variant % len(options)]["optionId"]}

    if question_type == str(QuestionType.TRUE_FALSE):
        return {"value": variant % 2 == 0}

    if question_type == str(QuestionType.MULTI_SELECT):
        options = question["options"]
        take = 1 + (variant % 2)
        return {"selectedOptionIds": [option["optionId"] for option in options[:take]]}

    if question_type == str(QuestionType.DRAG_TO_ORDER):
        items = [item["itemId"] for item in question["orderItems"]]
        if variant % 2 == 1:
            items = list(reversed(items))
        return {"orderedItemIds": items}

    if question_type == str(QuestionType.SCENARIO):
        responses = []
        for sub in question["subQuestions"]:
            responses.append({"subQuestionId": sub["subQuestionId"], "answer": _sub_answer(sub, variant)})
        return {"responses": responses}

    raise AssertionError(f"Unsupported question type in fixture: {question_type}")


def _sub_answer(sub: dict[str, Any], variant: int) -> Any:
    sub_type = sub["type"]
    if sub_type == str(QuestionType.SINGLE_CHOICE):
        options = sub["options"]
        return {"selectedOptionId": options[variant % len(options)]["optionId"]}
    if sub_type == str(QuestionType.TRUE_FALSE):
        return {"value": variant % 2 == 0}
    if sub_type == str(QuestionType.MULTI_SELECT):
        options = sub["options"]
        return {"selectedOptionIds": [options[0]["optionId"]]}
    if sub_type == str(QuestionType.DRAG_TO_ORDER):
        return {"orderedItemIds": [item["itemId"] for item in sub["orderItems"]]}
    raise AssertionError(f"Unsupported scenario sub-question type: {sub_type}")


def partial_scenario_answer(question: dict[str, Any]) -> Any:
    """A scenario answer covering only the first sub-question.

    Used to assert the answered-but-not-complete distinction.
    """
    sub = question["subQuestions"][0]
    return {"responses": [{"subQuestionId": sub["subQuestionId"], "answer": _sub_answer(sub, 0)}]}
