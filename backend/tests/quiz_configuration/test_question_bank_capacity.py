"""Question-bank capacity validation — the UC-01 ↔ UC-02 boundary.

Every count here comes from the real question bank through
:class:`app.modules.quiz_configuration.ports.QuestionBankPort`, and questions are retired through
the real Question Bank API. So these tests exercise the actual integration, not a stand-in.
"""

from __future__ import annotations

from app.core.question_types import QuestionType
from tests.harness import Ctx, valid_configuration


def test_accepts_a_configuration_the_bank_can_satisfy(make_ctx) -> None:
    ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 10, QuestionType.TRUE_FALSE: 10})

    response = ctx.save_configuration(
        valid_configuration(
            questionCount=20,
            questionTypes=[
                {"type": "SINGLE_CHOICE", "quota": 10},
                {"type": "TRUE_FALSE", "quota": 10},
            ],
        )
    )

    assert response.status_code == 201, response.text
    capacity = response.json()["capacity"]
    assert capacity["satisfiable"] is True
    assert capacity["availableTotal"] == 20


def test_rejects_the_documented_example(make_ctx) -> None:
    """20 questions (10 single-choice + 10 true/false) against a bank holding 5 + 10."""
    ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 5, QuestionType.TRUE_FALSE: 10})

    response = ctx.save_configuration(
        valid_configuration(
            questionCount=20,
            questionTypes=[
                {"type": "SINGLE_CHOICE", "quota": 10},
                {"type": "TRUE_FALSE", "quota": 10},
            ],
        )
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "QUESTION_BANK_INSUFFICIENT"

    capacity = body["error"]["capacity"]
    assert capacity["satisfiable"] is False
    # The administrator is told exactly which type falls short, and by how much.
    assert capacity["breakdown"] == [
        {"type": "SINGLE_CHOICE", "requested": 10, "available": 5, "shortfall": 5},
        {"type": "TRUE_FALSE", "requested": 10, "available": 10, "shortfall": 0},
    ]
    assert "Single choice" in capacity["messages"][0]
    assert "5 short" in capacity["messages"][0]

    # An impossible configuration is never persisted.
    assert ctx.version_count() == 0


def test_rejects_insufficient_total_questions_without_quotas(make_ctx) -> None:
    ctx: Ctx = make_ctx(
        {
            QuestionType.SINGLE_CHOICE: 4,
            QuestionType.TRUE_FALSE: 4,
            QuestionType.MULTI_SELECT: 20,
        }
    )

    response = ctx.save_configuration(
        valid_configuration(
            questionCount=15,
            questionTypes=[
                {"type": "SINGLE_CHOICE", "quota": None},
                {"type": "TRUE_FALSE", "quota": None},
            ],
        )
    )

    assert response.status_code == 422
    capacity = response.json()["error"]["capacity"]
    # The 20 multi-select questions are irrelevant: that type is not selected.
    assert capacity["availableTotal"] == 8
    assert capacity["totalShortfall"] == 7
    assert ctx.version_count() == 0


def test_retired_questions_do_not_count_towards_capacity(make_ctx) -> None:
    ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 10, QuestionType.TRUE_FALSE: 10})
    ctx.retire(QuestionType.SINGLE_CHOICE)

    response = ctx.save_configuration(
        valid_configuration(
            questionCount=10, questionTypes=[{"type": "SINGLE_CHOICE", "quota": 10}]
        )
    )

    assert response.status_code == 422
    assert response.json()["error"]["capacity"]["breakdown"][0] == {
        "type": "SINGLE_CHOICE",
        "requested": 10,
        "available": 0,
        "shortfall": 10,
    }


def test_draft_questions_do_not_count_towards_capacity(make_ctx) -> None:
    """Only ACTIVE questions are deliverable, so a draft bank cannot satisfy a quiz."""
    from tests import bank

    ctx: Ctx = make_ctx({QuestionType.TRUE_FALSE: 5})
    with ctx.session() as session:
        bank.seed_questions(session, QuestionType.SINGLE_CHOICE, 10, status="DRAFT")

    response = ctx.save_configuration(
        valid_configuration(
            questionCount=10, questionTypes=[{"type": "SINGLE_CHOICE", "quota": 10}]
        )
    )
    assert response.status_code == 422
    assert response.json()["error"]["capacity"]["breakdown"][0]["available"] == 0


def test_reactivating_a_question_restores_capacity(make_ctx) -> None:
    ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 10})
    retired = ctx.retire(QuestionType.SINGLE_CHOICE, count=3)

    assert ctx.get_question_bank().json()["availableByType"]["SINGLE_CHOICE"] == 7

    for question_id in retired:
        response = ctx.client.post(
            f"/api/question-bank/questions/{question_id}/reactivate",
            headers={"Authorization": "Bearer test-admin-token"},
        )
        assert response.status_code == 200, response.text

    assert ctx.get_question_bank().json()["availableByType"]["SINGLE_CHOICE"] == 10


def test_exposes_live_availability_for_every_type(make_ctx) -> None:
    ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 3, QuestionType.TRUE_FALSE: 7})

    response = ctx.get_question_bank()

    assert response.status_code == 200
    assert response.json()["availableByType"] == {
        "SINGLE_CHOICE": 3,
        "TRUE_FALSE": 7,
        "MULTI_SELECT": 0,
        "SCENARIO": 0,
        "DRAG_TO_ORDER": 0,
    }


def test_availability_can_be_scoped_to_a_topic(make_ctx) -> None:
    ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 4}, topics=["Scoped Topic"])
    with ctx.session() as session:
        from tests import bank

        bank.seed_questions(session, QuestionType.TRUE_FALSE, 6, topics=["Other Topic"])
        scoped_id = bank.topic_named(session, "Scoped Topic").id

    unscoped = ctx.get_question_bank().json()["availableByType"]
    assert unscoped["SINGLE_CHOICE"] == 4
    assert unscoped["TRUE_FALSE"] == 6

    scoped = ctx.get_question_bank(topicId=scoped_id).json()["availableByType"]
    assert scoped["SINGLE_CHOICE"] == 4
    assert scoped["TRUE_FALSE"] == 0


def test_a_topic_scoped_configuration_only_counts_that_topic(make_ctx) -> None:
    ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 4}, topics=["Scoped Topic"])
    with ctx.session() as session:
        from tests import bank

        bank.seed_questions(session, QuestionType.SINGLE_CHOICE, 10, topics=["Other Topic"])
        scoped_id = bank.topic_named(session, "Scoped Topic").id

    # 14 single-choice questions exist, but only 4 are in scope.
    response = ctx.save_configuration(
        valid_configuration(
            questionCount=10,
            questionTypes=[{"type": "SINGLE_CHOICE", "quota": 10}],
            topicIds=[scoped_id],
        )
    )
    assert response.status_code == 422
    assert response.json()["error"]["capacity"]["breakdown"][0]["available"] == 4

    # Unscoped, the same configuration is satisfiable.
    assert (
        ctx.save_configuration(
            valid_configuration(
                questionCount=10, questionTypes=[{"type": "SINGLE_CHOICE", "quota": 10}]
            )
        ).status_code
        == 201
    )


def test_reports_capacity_on_read_for_a_configured_quiz(make_ctx) -> None:
    ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 10, QuestionType.TRUE_FALSE: 10})
    assert ctx.save_configuration(valid_configuration(questionCount=10)).status_code == 201

    response = ctx.get_configuration()
    assert response.status_code == 200
    assert response.json()["capacity"]["satisfiable"] is True


def test_flags_an_existing_configuration_the_bank_can_no_longer_satisfy(make_ctx) -> None:
    ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 10, QuestionType.TRUE_FALSE: 10})
    ctx.save_configuration(
        valid_configuration(
            questionCount=20,
            questionTypes=[
                {"type": "SINGLE_CHOICE", "quota": 10},
                {"type": "TRUE_FALSE", "quota": 10},
            ],
        )
    )

    # Questions retired after the configuration was saved.
    ctx.retire(QuestionType.SINGLE_CHOICE)

    body = ctx.get_configuration().json()
    assert body["capacity"]["satisfiable"] is False
    # The stored version is untouched by the bank change — retirement does not edit history.
    assert body["configuration"]["versionNumber"] == 1
    assert body["configuration"]["questionCount"] == 20
