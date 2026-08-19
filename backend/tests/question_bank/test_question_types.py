"""All five question types round-trip correctly (UC-02 §9–§13, §25)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.question_bank.models import QuestionOption
from tests import factories
from tests.factories import API


@pytest.mark.parametrize("question_type", sorted(factories.ALL_BUILDERS))
def test_every_type_can_be_created_and_read_back(
    client: TestClient, question_type: str
) -> None:
    created = factories.create(client, factories.ALL_BUILDERS[question_type]())
    assert created["type"] == question_type

    fetched = client.get(f"{API}/questions/{created['id']}").json()
    assert fetched["type"] == question_type
    assert fetched["questionText"]
    assert fetched["explanation"]
    assert fetched["topics"]
    assert fetched["scoring"]["points"] > 0
    assert len(fetched["options"]) >= 2


# ---------------------------------------------------------------------------
# Type 1 — Single choice
# ---------------------------------------------------------------------------


def test_single_choice_stores_four_options_and_one_correct_answer(client: TestClient) -> None:
    body = factories.create(client, factories.single_choice())
    assert len(body["options"]) == 4
    assert body["correctLabels"] == ["B"]
    assert body["correctOrder"] == []
    # The single correct option is implicitly the primary answer.
    assert body["primaryLabel"] == "B"


# ---------------------------------------------------------------------------
# Type 2 — True / False
# ---------------------------------------------------------------------------


def test_true_false_stores_the_fixed_option_pair(client: TestClient) -> None:
    body = factories.create(client, factories.true_false())
    assert [o["label"] for o in body["options"]] == ["TRUE", "FALSE"]
    assert body["correctLabels"] == ["TRUE"]


def test_true_false_accepts_false_as_the_answer(client: TestClient) -> None:
    body = factories.create(
        client,
        factories.true_false(
            questionText="UDP guarantees delivery of every datagram.",
            explanation="UDP is connectionless and offers no delivery guarantee.",
            options=[
                {"label": "TRUE", "text": "True", "isCorrect": False},
                {"label": "FALSE", "text": "False", "isCorrect": True},
            ],
        ),
    )
    assert body["correctLabels"] == ["FALSE"]


# ---------------------------------------------------------------------------
# Type 3 — Multi-select
# ---------------------------------------------------------------------------


def test_multi_select_stores_multiple_correct_selections(client: TestClient) -> None:
    body = factories.create(client, factories.multi_select())
    assert len(body["correctLabels"]) == 3
    assert body["correctLabels"] == ["A", "B", "C"]
    assert body["scoring"]["scoringStrategy"] == "PARTIAL_CREDIT_WITH_PENALTY"
    assert body["scoring"]["penaltyPerIncorrect"] == 0.5


def test_multi_select_accepts_a_single_correct_answer_with_all_or_nothing(
    client: TestClient,
) -> None:
    """"At least one correct" is the rule — a single correct selection is legitimate."""
    body = factories.create(
        client,
        factories.multi_select(
            options=[
                {"label": "A", "text": "10.0.0.0/8", "isCorrect": True},
                {"label": "B", "text": "8.8.8.0/24", "isCorrect": False},
                {"label": "C", "text": "203.0.113.0/24", "isCorrect": False},
            ],
            scoring={"points": 1, "scoringStrategy": "ALL_OR_NOTHING"},
        ),
    )
    assert body["correctLabels"] == ["A"]


# ---------------------------------------------------------------------------
# Type 4 — Scenario
# ---------------------------------------------------------------------------


def test_scenario_stores_the_vignette_and_primary_answer(client: TestClient) -> None:
    body = factories.create(client, factories.scenario())
    assert body["scenarioText"] == factories.SCENARIO_VIGNETTE
    assert len(body["scenarioText"]) > 100  # a genuine long-form vignette
    assert body["primaryLabel"] == "B"
    assert body["correctLabels"] == ["B"]


def test_scenario_primary_answer_is_inferred_from_a_single_correct_option(
    client: TestClient,
) -> None:
    payload = factories.scenario()
    for option in payload["options"]:
        option.pop("isPrimary", None)

    body = factories.create(client, payload)
    assert body["primaryLabel"] == "B"


# ---------------------------------------------------------------------------
# Type 5 — Drag-to-order
# ---------------------------------------------------------------------------


def test_drag_to_order_preserves_items_and_correct_order(
    client: TestClient, db: Session
) -> None:
    body = factories.create(client, factories.drag_to_order())

    assert body["correctOrder"] == ["A", "B", "C", "D"]
    # Ordering questions carry no isCorrect flags — correctness lives in correctPosition.
    assert body["correctLabels"] == []
    assert all(option["isCorrect"] is False for option in body["options"])
    assert [option["correctPosition"] for option in body["options"]] == [1, 2, 3, 4]

    stored = db.execute(
        select(QuestionOption).where(QuestionOption.question_id == body["id"])
    ).scalars().all()
    assert sorted(o.correct_position or 0 for o in stored) == [1, 2, 3, 4]


def test_drag_to_order_keeps_presentation_order_separate_from_correct_order(
    client: TestClient,
) -> None:
    """The admin may enter items in one order while the answer key is a different order."""
    body = factories.create(
        client,
        factories.drag_to_order(
            options=[
                # Entered (presented) D, C, B, A — but the correct sequence is A, B, C, D.
                {"label": "D", "text": "Data transfer begins", "position": 1, "correctPosition": 4},
                {"label": "C", "text": "Client sends ACK", "position": 2, "correctPosition": 3},
                {"label": "B", "text": "Server replies SYN-ACK", "position": 3, "correctPosition": 2},
                {"label": "A", "text": "Client sends SYN", "position": 4, "correctPosition": 1},
            ]
        ),
    )

    presentation = [option["label"] for option in body["options"]]
    assert presentation == ["D", "C", "B", "A"]
    assert body["correctOrder"] == ["A", "B", "C", "D"]
    # The two concepts must not be conflated.
    assert presentation != body["correctOrder"]


def test_drag_to_order_correct_order_survives_a_presentation_reshuffle(
    client: TestClient,
) -> None:
    created = factories.create(client, factories.drag_to_order())

    reshuffled = [
        {"label": "C", "text": "Client sends ACK", "position": 1, "correctPosition": 3},
        {"label": "A", "text": "Client sends SYN", "position": 2, "correctPosition": 1},
        {"label": "D", "text": "Data transfer begins", "position": 3, "correctPosition": 4},
        {"label": "B", "text": "Server replies SYN-ACK", "position": 4, "correctPosition": 2},
    ]
    response = client.patch(f"{API}/questions/{created['id']}", json={"options": reshuffled})
    assert response.status_code == 200, response.text
    body = response.json()

    assert [o["label"] for o in body["options"]] == ["C", "A", "D", "B"]
    assert body["correctOrder"] == ["A", "B", "C", "D"]
    # Only presentation changed, so the semantic content — and the version — is unchanged.
    assert body["version"] == 1
