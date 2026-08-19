"""Backend validation is authoritative (UC-02 §14, Rule 4, Rule 5, §25).

Every test here asserts BOTH that the request was refused with 422 AND that nothing was
written — validation must happen before persistence.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.question_bank.models import Question
from tests import factories
from tests.factories import API


def _reject(client: TestClient, payload: dict, db: Session) -> set[str]:
    """POST an invalid question; assert 422 and that nothing persisted. Return error codes."""
    response = client.post(f"{API}/questions", json=payload)
    assert response.status_code == 422, response.text

    body = response.json()
    assert body["error"]["code"] == "VALIDATION_FAILED"
    assert body["error"]["details"], "a validation failure must carry field-level detail"

    stored = int(db.execute(select(func.count(Question.id))).scalar_one())
    assert stored == 0, "an invalid question must never reach the database"

    return {issue["code"] for issue in body["error"]["details"]}


# ---------------------------------------------------------------------------
# General rules
# ---------------------------------------------------------------------------


def test_missing_question_text_is_rejected(client: TestClient, db: Session) -> None:
    codes = _reject(client, factories.single_choice(questionText="   "), db)
    assert "QUESTION_TEXT_REQUIRED" in codes


def test_missing_question_type_is_rejected(client: TestClient, db: Session) -> None:
    codes = _reject(client, factories.single_choice(type=""), db)
    assert "QUESTION_TYPE_REQUIRED" in codes


def test_invalid_question_type_is_rejected_with_a_helpful_message(
    client: TestClient, db: Session
) -> None:
    response = client.post(f"{API}/questions", json=factories.single_choice(type="multiplechoicee"))
    assert response.status_code == 422

    issue = next(
        i for i in response.json()["error"]["details"] if i["code"] == "INVALID_QUESTION_TYPE"
    )
    assert 'multiplechoicee' in issue["message"]
    assert "SINGLE_CHOICE" in issue["message"]
    assert int(db.execute(select(func.count(Question.id))).scalar_one()) == 0


def test_missing_explanation_is_rejected(client: TestClient, db: Session) -> None:
    codes = _reject(client, factories.single_choice(explanation=None), db)
    assert "EXPLANATION_REQUIRED" in codes


def test_missing_topics_is_rejected(client: TestClient, db: Session) -> None:
    codes = _reject(client, factories.single_choice(topics=[]), db)
    assert "TOPICS_REQUIRED" in codes


def test_unknown_topic_id_is_rejected(client: TestClient, db: Session) -> None:
    response = client.post(
        f"{API}/questions",
        json=factories.single_choice(topics=[], topicIds=["no-such-topic"]),
    )
    assert response.status_code == 422
    codes = {i["code"] for i in response.json()["error"]["details"]}
    assert "TOPIC_NOT_FOUND" in codes
    assert int(db.execute(select(func.count(Question.id))).scalar_one()) == 0


def test_invalid_difficulty_is_rejected(client: TestClient, db: Session) -> None:
    codes = _reject(client, factories.single_choice(difficulty="TRIVIAL"), db)
    assert "INVALID_DIFFICULTY" in codes


def test_every_problem_is_reported_at_once(client: TestClient, db: Session) -> None:
    """The admin should see the full list, not just the first failure."""
    codes = _reject(
        client,
        factories.single_choice(questionText="", explanation="", topics=[], difficulty="X"),
        db,
    )
    assert {
        "QUESTION_TEXT_REQUIRED",
        "EXPLANATION_REQUIRED",
        "TOPICS_REQUIRED",
        "INVALID_DIFFICULTY",
    } <= codes


# ---------------------------------------------------------------------------
# Scoring metadata
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("points", "expected"),
    [(0, "POINTS_MUST_BE_POSITIVE"), (-3, "POINTS_MUST_BE_POSITIVE"), (10_000, "POINTS_TOO_LARGE")],
)
def test_invalid_points_are_rejected(
    client: TestClient, db: Session, points: float, expected: str
) -> None:
    codes = _reject(
        client,
        factories.single_choice(scoring={"points": points, "scoringStrategy": "ALL_OR_NOTHING"}),
        db,
    )
    assert expected in codes


def test_invalid_scoring_strategy_is_rejected(client: TestClient, db: Session) -> None:
    codes = _reject(
        client,
        factories.single_choice(scoring={"points": 1, "scoringStrategy": "GENEROUS"}),
        db,
    )
    # Rejected by the request contract or the domain layer — either way, nothing persisted.
    assert codes


def test_partial_credit_is_not_allowed_for_single_choice(
    client: TestClient, db: Session
) -> None:
    codes = _reject(
        client,
        factories.single_choice(scoring={"points": 2, "scoringStrategy": "PARTIAL_CREDIT"}),
        db,
    )
    assert "SCORING_STRATEGY_NOT_ALLOWED_FOR_TYPE" in codes


def test_penalty_requires_the_matching_strategy(client: TestClient, db: Session) -> None:
    codes = _reject(
        client,
        factories.multi_select(
            scoring={"points": 3, "scoringStrategy": "PARTIAL_CREDIT", "penaltyPerIncorrect": 1}
        ),
        db,
    )
    assert "PENALTY_NOT_ALLOWED_FOR_STRATEGY" in codes


def test_penalty_strategy_requires_a_penalty_value(client: TestClient, db: Session) -> None:
    codes = _reject(
        client,
        factories.multi_select(
            scoring={
                "points": 3,
                "scoringStrategy": "PARTIAL_CREDIT_WITH_PENALTY",
                "penaltyPerIncorrect": 0,
            }
        ),
        db,
    )
    assert "PENALTY_REQUIRED_FOR_STRATEGY" in codes


def test_penalty_may_not_exceed_the_question_points(client: TestClient, db: Session) -> None:
    codes = _reject(
        client,
        factories.multi_select(
            scoring={
                "points": 1,
                "scoringStrategy": "PARTIAL_CREDIT_WITH_PENALTY",
                "penaltyPerIncorrect": 5,
            }
        ),
        db,
    )
    assert "PENALTY_EXCEEDS_POINTS" in codes


# ---------------------------------------------------------------------------
# Single choice
# ---------------------------------------------------------------------------


def test_single_choice_requires_exactly_four_options(client: TestClient, db: Session) -> None:
    codes = _reject(
        client,
        factories.single_choice(
            options=[
                {"label": "A", "text": "Layer 2", "isCorrect": False},
                {"label": "B", "text": "Layer 3", "isCorrect": True},
                {"label": "C", "text": "Layer 4", "isCorrect": False},
            ]
        ),
        db,
    )
    assert "SINGLE_CHOICE_REQUIRES_FOUR_OPTIONS" in codes


def test_single_choice_rejects_two_correct_answers(client: TestClient, db: Session) -> None:
    payload = factories.single_choice()
    payload["options"][0]["isCorrect"] = True  # now A and B are both correct
    codes = _reject(client, payload, db)
    assert "SINGLE_CHOICE_REQUIRES_ONE_CORRECT" in codes


def test_single_choice_rejects_no_correct_answer(client: TestClient, db: Session) -> None:
    payload = factories.single_choice()
    for option in payload["options"]:
        option["isCorrect"] = False
    codes = _reject(client, payload, db)
    assert "SINGLE_CHOICE_REQUIRES_ONE_CORRECT" in codes


def test_duplicate_option_labels_are_rejected(client: TestClient, db: Session) -> None:
    payload = factories.single_choice()
    payload["options"][1]["label"] = "A"
    codes = _reject(client, payload, db)
    assert "DUPLICATE_OPTION_LABEL" in codes


def test_option_without_text_is_rejected(client: TestClient, db: Session) -> None:
    payload = factories.single_choice()
    payload["options"][2]["text"] = "   "
    codes = _reject(client, payload, db)
    assert "OPTION_TEXT_REQUIRED" in codes


def test_option_label_with_reserved_characters_is_rejected(
    client: TestClient, db: Session
) -> None:
    payload = factories.single_choice()
    payload["options"][0]["label"] = "A|B"
    codes = _reject(client, payload, db)
    assert "OPTION_LABEL_INVALID" in codes


# ---------------------------------------------------------------------------
# True / False
# ---------------------------------------------------------------------------


def test_true_false_rejects_non_boolean_option_labels(client: TestClient, db: Session) -> None:
    codes = _reject(
        client,
        factories.true_false(
            options=[
                {"label": "YES", "text": "Yes", "isCorrect": True},
                {"label": "NO", "text": "No", "isCorrect": False},
            ]
        ),
        db,
    )
    assert "TRUE_FALSE_REQUIRES_TRUE_FALSE_OPTIONS" in codes


def test_true_false_rejects_both_answers_correct(client: TestClient, db: Session) -> None:
    codes = _reject(
        client,
        factories.true_false(
            options=[
                {"label": "TRUE", "text": "True", "isCorrect": True},
                {"label": "FALSE", "text": "False", "isCorrect": True},
            ]
        ),
        db,
    )
    assert "TRUE_FALSE_REQUIRES_ONE_CORRECT" in codes


def test_true_false_without_an_answer_is_rejected(client: TestClient, db: Session) -> None:
    codes = _reject(client, factories.true_false(options=[]), db)
    assert "TRUE_FALSE_ANSWER_REQUIRED" in codes


# ---------------------------------------------------------------------------
# Multi-select
# ---------------------------------------------------------------------------


def test_multi_select_requires_a_correct_answer(client: TestClient, db: Session) -> None:
    payload = factories.multi_select(scoring={"points": 1, "scoringStrategy": "ALL_OR_NOTHING"})
    for option in payload["options"]:
        option["isCorrect"] = False
    codes = _reject(client, payload, db)
    assert "MULTI_SELECT_REQUIRES_CORRECT_ANSWER" in codes


def test_multi_select_requires_enough_options(client: TestClient, db: Session) -> None:
    codes = _reject(
        client,
        factories.multi_select(
            options=[
                {"label": "A", "text": "10.0.0.0/8", "isCorrect": True},
                {"label": "B", "text": "8.8.8.0/24", "isCorrect": False},
            ],
            scoring={"points": 1, "scoringStrategy": "ALL_OR_NOTHING"},
        ),
        db,
    )
    assert "MULTI_SELECT_REQUIRES_MIN_OPTIONS" in codes


def test_multi_select_rejects_every_option_being_correct(
    client: TestClient, db: Session
) -> None:
    payload = factories.multi_select(scoring={"points": 3, "scoringStrategy": "PARTIAL_CREDIT"})
    for option in payload["options"]:
        option["isCorrect"] = True
    codes = _reject(client, payload, db)
    assert "MULTI_SELECT_ALL_OPTIONS_CORRECT" in codes


def test_multi_select_partial_credit_requires_multiple_correct_answers(
    client: TestClient, db: Session
) -> None:
    """Invalid multi-select scoring: marks cannot be divided across a single correct answer."""
    payload = factories.multi_select(
        options=[
            {"label": "A", "text": "10.0.0.0/8", "isCorrect": True},
            {"label": "B", "text": "8.8.8.0/24", "isCorrect": False},
            {"label": "C", "text": "203.0.113.0/24", "isCorrect": False},
        ],
        scoring={"points": 3, "scoringStrategy": "PARTIAL_CREDIT"},
    )
    codes = _reject(client, payload, db)
    assert "PARTIAL_CREDIT_REQUIRES_MULTIPLE_CORRECT" in codes


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------


def test_scenario_requires_scenario_text(client: TestClient, db: Session) -> None:
    codes = _reject(client, factories.scenario(scenarioText=None), db)
    assert "SCENARIO_TEXT_REQUIRED" in codes


def test_scenario_rejects_a_too_short_vignette(client: TestClient, db: Session) -> None:
    codes = _reject(client, factories.scenario(scenarioText="Too short."), db)
    assert "SCENARIO_TEXT_TOO_SHORT" in codes


def test_scenario_requires_question_text_as_well_as_the_vignette(
    client: TestClient, db: Session
) -> None:
    codes = _reject(client, factories.scenario(questionText=""), db)
    assert "QUESTION_TEXT_REQUIRED" in codes


def test_scenario_rejects_two_primary_answers(client: TestClient, db: Session) -> None:
    payload = factories.scenario()
    payload["options"][0]["isPrimary"] = True
    payload["options"][0]["isCorrect"] = True
    codes = _reject(client, payload, db)
    assert "SCENARIO_MULTIPLE_PRIMARY_ANSWERS" in codes


def test_scenario_primary_answer_must_also_be_correct(client: TestClient, db: Session) -> None:
    payload = factories.scenario()
    payload["options"][1]["isPrimary"] = False
    payload["options"][0]["isPrimary"] = True  # A is primary but not correct
    codes = _reject(client, payload, db)
    assert "SCENARIO_PRIMARY_ANSWER_NOT_CORRECT" in codes


def test_scenario_text_is_not_allowed_on_other_types(client: TestClient, db: Session) -> None:
    codes = _reject(
        client, factories.single_choice(scenarioText="A" * 60), db
    )
    assert "SCENARIO_TEXT_NOT_ALLOWED" in codes


# ---------------------------------------------------------------------------
# Drag-to-order
# ---------------------------------------------------------------------------


def test_drag_to_order_requires_correct_positions(client: TestClient, db: Session) -> None:
    payload = factories.drag_to_order()
    payload["options"][2]["correctPosition"] = None
    codes = _reject(client, payload, db)
    assert "DRAG_TO_ORDER_MISSING_POSITIONS" in codes


def test_drag_to_order_rejects_duplicate_positions(client: TestClient, db: Session) -> None:
    payload = factories.drag_to_order()
    payload["options"][1]["correctPosition"] = 1
    codes = _reject(client, payload, db)
    assert "DRAG_TO_ORDER_DUPLICATE_POSITION" in codes


def test_drag_to_order_rejects_a_gap_in_the_sequence(client: TestClient, db: Session) -> None:
    payload = factories.drag_to_order()
    payload["options"][3]["correctPosition"] = 9
    codes = _reject(client, payload, db)
    assert "DRAG_TO_ORDER_INVALID_SEQUENCE" in codes


def test_drag_to_order_rejects_duplicate_items(client: TestClient, db: Session) -> None:
    payload = factories.drag_to_order()
    payload["options"][1]["text"] = payload["options"][0]["text"]
    codes = _reject(client, payload, db)
    assert "DRAG_TO_ORDER_DUPLICATE_ITEM" in codes


def test_drag_to_order_requires_at_least_two_items(client: TestClient, db: Session) -> None:
    codes = _reject(
        client,
        factories.drag_to_order(
            options=[{"label": "A", "text": "Only step", "correctPosition": 1}],
            scoring={"points": 1, "scoringStrategy": "ALL_OR_NOTHING"},
        ),
        db,
    )
    assert "DRAG_TO_ORDER_REQUIRES_MIN_ITEMS" in codes


def test_correct_position_is_rejected_on_choice_types(client: TestClient, db: Session) -> None:
    payload = factories.single_choice()
    payload["options"][0]["correctPosition"] = 1
    codes = _reject(client, payload, db)
    assert "CORRECT_POSITION_NOT_ALLOWED" in codes


# ---------------------------------------------------------------------------
# Validation on update
# ---------------------------------------------------------------------------


def test_an_edit_cannot_make_a_question_invalid(client: TestClient, db: Session) -> None:
    created = factories.create(client, factories.single_choice())

    response = client.patch(f"{API}/questions/{created['id']}", json={"questionText": ""})
    assert response.status_code == 422
    codes = {i["code"] for i in response.json()["error"]["details"]}
    assert "QUESTION_TEXT_REQUIRED" in codes

    # The stored question is untouched.
    fetched = client.get(f"{API}/questions/{created['id']}").json()
    assert fetched["questionText"] == created["questionText"]
    assert fetched["version"] == 1


def test_an_edit_removing_the_correct_answer_is_rejected(client: TestClient) -> None:
    created = factories.create(client, factories.single_choice())
    response = client.patch(
        f"{API}/questions/{created['id']}",
        json={
            "options": [
                {"label": "A", "text": "Layer 2", "isCorrect": False},
                {"label": "B", "text": "Layer 3", "isCorrect": False},
                {"label": "C", "text": "Layer 4", "isCorrect": False},
                {"label": "D", "text": "Layer 7", "isCorrect": False},
            ]
        },
    )
    assert response.status_code == 422
    codes = {i["code"] for i in response.json()["error"]["details"]}
    assert "SINGLE_CHOICE_REQUIRES_ONE_CORRECT" in codes
