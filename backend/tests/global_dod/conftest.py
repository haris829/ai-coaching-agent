"""Fixtures for UC-11.

Deliberately thin. The Global DoD's job is to exercise the system as it ships, so it boots the same
``create_app`` the production entry point does, against the same models and the same migrations,
through the same ``make_ctx`` harness every other integration suite uses. A bespoke system builder
here would be a second way to construct the application, and the second one is always the one that
drifts.

What this file adds is the *scenario* vocabulary the Global DoD's flows are written in — sit a
quiz, sit it wrongly, sit it as a formal assessment — so an assertion about an invariant reads as
that assertion and not as twenty lines of HTTP.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from app.core.question_types import QuestionType
from app.modules.question_bank.models import Question
from tests import bank
from tests.harness import ADMIN_TOKEN, LEARNER_TOKEN, Ctx, auth

V1 = "/api/v1"

#: One question of every type the system supports, which is what §11's regression needs. The counts
#: are generous enough that a retake can draw a completely fresh paper (§17).
ALL_TYPES_BANK: dict[QuestionType, int] = {
    QuestionType.SINGLE_CHOICE: 6,
    QuestionType.TRUE_FALSE: 6,
    QuestionType.MULTI_SELECT: 6,
    QuestionType.SCENARIO: 6,
    QuestionType.DRAG_TO_ORDER: 6,
}

#: A paper containing one of each type, so a single sitting exercises all five.
ALL_TYPES_CONFIGURATION: dict[str, Any] = {
    "questionCount": 5,
    "timeLimitMinutes": 30,
    "passMark": 60,
    "questionTypes": [
        {"type": "SINGLE_CHOICE", "quota": 1},
        {"type": "TRUE_FALSE", "quota": 1},
        {"type": "MULTI_SELECT", "quota": 1},
        {"type": "SCENARIO", "quota": 1},
        {"type": "DRAG_TO_ORDER", "quota": 1},
    ],
    "randomiseQuestions": False,
    "maxAttempts": 3,
    "deliveryMode": "assessment",
}

#: A two-question single-choice paper, for the flows where the question shape is beside the point.
SIMPLE_CONFIGURATION: dict[str, Any] = {
    "questionCount": 2,
    "timeLimitMinutes": 30,
    "passMark": 50,
    "questionTypes": [{"type": "SINGLE_CHOICE"}],
    "randomiseQuestions": False,
    "maxAttempts": 3,
    "deliveryMode": "assessment",
}


# ---------------------------------------------------------------------------
# The system, as it ships
# ---------------------------------------------------------------------------


@pytest.fixture
def system(make_ctx: Any):
    """The integrated system with every question type stocked and a paper of one of each.

    A factory rather than a value, so a test that needs a different configuration says so instead
    of working around this one.
    """

    def build(configuration: dict[str, Any] | None = None, plan: dict | None = None) -> Ctx:
        ctx = make_ctx(plan or ALL_TYPES_BANK)
        saved = ctx.save_configuration(configuration or ALL_TYPES_CONFIGURATION)
        assert saved.status_code == 201, saved.text
        return ctx

    return build


@pytest.fixture
def simple_system(system: Any):
    """A two-question single-choice quiz, for flows where the question shape does not matter."""
    return system(SIMPLE_CONFIGURATION, bank.DEFAULT_BANK)


# ---------------------------------------------------------------------------
# Answering, in the terms the requirements are written in
# ---------------------------------------------------------------------------


def answer_payload(ctx: Ctx, question: dict[str, Any], *, correctly: bool) -> dict[str, Any]:
    """A response for one delivered question, built from UC-02's own answer key.

    Reading the key from the bank is what makes "answered correctly" mean correctly rather than
    "matched the fixture". It is also why the answer-key assertions elsewhere in this package can
    be about real key values rather than invented strings.

    The delivered option ids are UC-02's option *labels* — that is the mapping
    ``Uc02QuestionBankAdapter`` makes — so the key read here can be used directly as the answer.
    """
    question_type = question["questionType"]
    with ctx.session() as session:
        row = session.get(Question, question["questionId"])
        assert row is not None, question["questionId"]
        options = sorted(row.options, key=lambda option: option.position)
        correct = [option.label for option in options if option.is_correct]
        wrong = [option.label for option in options if not option.is_correct]
        ordered = [
            option.label
            for option in sorted(
                (item for item in options if item.correct_position is not None),
                key=lambda item: item.correct_position or 0,
            )
        ]

    if question_type == "SINGLE_CHOICE":
        return {"selectedOptionId": correct[0] if correctly else wrong[0]}

    if question_type == "TRUE_FALSE":
        truth = correct[0].upper() == "TRUE"
        return {"value": truth if correctly else not truth}

    if question_type == "MULTI_SELECT":
        # Every correct option when correct; only a wrong one when not, which is the case §13's
        # negative-protection rule is about.
        return {"selectedOptionIds": sorted(correct if correctly else wrong[:1])}

    if question_type == "DRAG_TO_ORDER":
        return {"orderedItemIds": ordered if correctly else list(reversed(ordered))}

    if question_type == "SCENARIO":
        # UC-02 models a scenario as a vignette plus one question with a primary answer, and the
        # delivery adapter maps that onto exactly one single-choice sub-question. Read from the
        # delivered payload rather than reconstructed, so the id is whatever UC-03 actually sent.
        sub = question["subQuestions"][0]
        return {
            "responses": [
                {
                    "subQuestionId": sub["subQuestionId"],
                    "answer": {
                        "selectedOptionId": correct[0] if correctly else wrong[0]
                    },
                }
            ]
        }

    raise AssertionError(f"unsupported question type {question_type}")


def sit(
    ctx: Ctx,
    *,
    token: str = LEARNER_TOKEN,
    correctly: bool = True,
    submit: bool = True,
    only_types: set[str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Start an attempt, answer it, and by default submit it.

    ``only_types`` names the types to answer *correctly*; everything else is answered wrongly,
    which is how a flow arranges a partial score without arithmetic of its own.
    """
    attempt_id, questions = ctx.start_and_read_questions(token)
    for question in questions:
        right = question["questionType"] in only_types if only_types is not None else correctly
        saved = ctx.client.put(
            f"{V1}/attempts/{attempt_id}/questions/{question['questionId']}/answer",
            json={"response": answer_payload(ctx, question, correctly=right), "source": "MANUAL"},
            headers=auth(token),
        )
        assert saved.status_code == 200, saved.text

    if submit:
        submitted = ctx.client.post(
            f"{V1}/attempts/{attempt_id}/submission",
            json={"confirmed": True},
            headers=auth(token),
        )
        assert submitted.status_code == 200, submitted.text
    return attempt_id, questions


def delivered_question_ids(ctx: Ctx, attempt_id: str) -> list[str]:
    """The frozen paper, from UC-03's own table, in delivery order."""
    with ctx.session() as session:
        return list(
            session.execute(
                text(
                    "SELECT question_id FROM qd_attempt_questions "
                    "WHERE attempt_id = :id ORDER BY position"
                ),
                {"id": attempt_id},
            ).scalars()
        )


def fingerprint(ctx: Ctx, tables: tuple[str, ...]) -> dict[str, list[dict[str, Any]]]:
    """Every row of the named tables, for a before/after comparison.

    Used wherever a requirement says something must not change: the assertion compares the whole
    table rather than the one column a test author thought to check, which is the difference
    between proving immutability and proving one field of it.
    """
    with ctx.session() as session:
        return {
            table: [
                dict(row)
                for row in session.execute(
                    text(f"SELECT * FROM {table} ORDER BY 1")  # noqa: S608 - fixed table names
                ).mappings()
            ]
            for table in tables
        }


#: The tables that hold a learner's submitted work. Nothing in the application may change these
#: once an attempt is submitted — §10.
ASSESSMENT_TABLES: tuple[str, ...] = (
    "qd_attempts",
    "qd_attempt_answers",
    "qd_attempt_answer_revisions",
    "qd_attempt_questions",
    "qd_attempt_submissions",
    "qr_attempt_results",
    "qr_question_scores",
    "qg_attempt_outcomes",
)

__all__ = [
    "ALL_TYPES_BANK",
    "ALL_TYPES_CONFIGURATION",
    "ASSESSMENT_TABLES",
    "SIMPLE_CONFIGURATION",
    "V1",
    "ADMIN_TOKEN",
    "LEARNER_TOKEN",
    "answer_payload",
    "auth",
    "delivered_question_ids",
    "fingerprint",
    "sit",
]
