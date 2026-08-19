"""Delivery seam, scoring metadata and response grading.

These cover the parts of UC-02 §16 that require a completed attempt to carry a learner response
and score information, and they prove that grading a drag-to-order question uses the stored
correct order rather than the order the options were presented in.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests import factories
from tests.factories import API


def _deliver(client: TestClient, question: dict, attempt: str = "att-1", **extra: object) -> dict:
    response = client.post(
        f"{API}/delivery/usages",
        json={"attemptRef": attempt, "questionId": question["id"], **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _answer(client: TestClient, usage_id: str, **payload: object) -> dict:
    response = client.patch(f"{API}/delivery/usages/{usage_id}", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Delivery pool
# ---------------------------------------------------------------------------


def test_pool_withholds_the_answer_key(client: TestClient) -> None:
    factories.create(client, factories.single_choice())
    item = client.get(f"{API}/delivery/pool").json()["items"][0]

    assert item["questionText"]
    assert len(item["options"]) == 4
    # No correctness information is exposed to the delivery layer.
    for option in item["options"]:
        assert set(option) == {"label", "text", "position"}
    assert "correctLabels" not in item
    assert "correctOrder" not in item


def test_pool_filters_by_topic_and_type(client: TestClient) -> None:
    sc = factories.create(client, factories.single_choice())  # Networking
    tf = factories.create(client, factories.true_false())  # Transport Protocols

    by_type = client.get(f"{API}/delivery/pool", params={"type": "TRUE_FALSE"}).json()
    assert [item["id"] for item in by_type["items"]] == [tf["id"]]

    by_topic = client.get(f"{API}/delivery/pool", params={"topicSlug": "networking"}).json()
    assert [item["id"] for item in by_topic["items"]] == [sc["id"]]


def test_pool_respects_the_limit(client: TestClient) -> None:
    for builder in factories.ALL_BUILDERS.values():
        factories.create(client, builder())

    body = client.get(f"{API}/delivery/pool", params={"limit": 2}).json()
    assert len(body["items"]) == 2
    assert body["totalAvailable"] == 5
    assert body["requested"] == 2


# ---------------------------------------------------------------------------
# Usage recording
# ---------------------------------------------------------------------------


def test_recording_a_delivery_pins_the_current_snapshot(client: TestClient) -> None:
    question = factories.create(client, factories.single_choice())
    usage = _deliver(client, question)

    assert usage["snapshotVersion"] == 1
    assert usage["attemptStatus"] == "IN_PROGRESS"
    assert usage["maxPoints"] == 1.0
    assert usage["questionReference"] == question["reference"]


def test_the_same_question_cannot_be_delivered_twice_to_one_attempt(
    client: TestClient,
) -> None:
    question = factories.create(client, factories.single_choice())
    _deliver(client, question)

    response = client.post(
        f"{API}/delivery/usages",
        json={"attemptRef": "att-1", "questionId": question["id"]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "USAGE_ALREADY_RECORDED"


def test_delivering_a_draft_question_is_refused(client: TestClient) -> None:
    question = factories.create(client, factories.single_choice(status="DRAFT"))
    response = client.post(
        f"{API}/delivery/usages", json={"attemptRef": "att-1", "questionId": question["id"]}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "QUESTION_NOT_DELIVERABLE"


def test_a_completed_response_is_immutable(client: TestClient) -> None:
    question = factories.create(client, factories.single_choice())
    usage = _deliver(client, question)
    _answer(client, usage["id"], selectedLabels=["B"], attemptStatus="COMPLETED")

    response = client.patch(
        f"{API}/delivery/usages/{usage['id']}",
        json={"selectedLabels": ["A"], "attemptStatus": "COMPLETED"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "USAGE_ALREADY_COMPLETED"


def test_response_referencing_an_unknown_option_is_rejected(client: TestClient) -> None:
    question = factories.create(client, factories.single_choice())
    usage = _deliver(client, question)

    response = client.patch(
        f"{API}/delivery/usages/{usage['id']}", json={"selectedLabels": ["Z"]}
    )
    assert response.status_code == 422
    codes = {issue["code"] for issue in response.json()["error"]["details"]}
    assert "UNKNOWN_OPTION_LABEL" in codes


def test_wrong_response_shape_is_rejected(client: TestClient) -> None:
    question = factories.create(client, factories.drag_to_order())
    usage = _deliver(client, question)

    response = client.patch(
        f"{API}/delivery/usages/{usage['id']}", json={"selectedLabels": ["A"]}
    )
    assert response.status_code == 422
    codes = {issue["code"] for issue in response.json()["error"]["details"]}
    assert "RESPONSE_SHAPE_MISMATCH" in codes


def test_single_choice_rejects_multiple_selections(client: TestClient) -> None:
    question = factories.create(client, factories.single_choice())
    usage = _deliver(client, question)

    response = client.patch(
        f"{API}/delivery/usages/{usage['id']}", json={"selectedLabels": ["A", "B"]}
    )
    assert response.status_code == 422
    codes = {issue["code"] for issue in response.json()["error"]["details"]}
    assert "TOO_MANY_SELECTIONS" in codes


def test_presentation_order_must_reference_real_options(client: TestClient) -> None:
    question = factories.create(client, factories.drag_to_order())
    response = client.post(
        f"{API}/delivery/usages",
        json={
            "attemptRef": "att-1",
            "questionId": question["id"],
            "presentationOrder": ["A", "B", "Q"],
        },
    )
    assert response.status_code == 422
    codes = {issue["code"] for issue in response.json()["error"]["details"]}
    assert "UNKNOWN_OPTION_LABEL" in codes


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("selected", "expected_correct", "expected_points"),
    [(["B"], True, 1.0), (["A"], False, 0.0)],
)
def test_single_choice_all_or_nothing_scoring(
    client: TestClient, selected: list[str], expected_correct: bool, expected_points: float
) -> None:
    question = factories.create(client, factories.single_choice())
    usage = _deliver(client, question)
    body = _answer(client, usage["id"], selectedLabels=selected)

    assert body["isCorrect"] is expected_correct
    assert body["awardedPoints"] == expected_points
    assert body["maxPoints"] == 1.0


@pytest.mark.parametrize(
    ("selected", "expected_points", "expected_correct"),
    [
        (["A", "B", "C"], 3.0, True),   # all three correct
        (["A", "B"], 2.0, False),        # 2/3 of the marks
        (["A", "B", "C", "D"], 2.5, False),  # full credit minus one 0.5 penalty
        (["D", "E"], 0.0, False),        # two penalties, floored at zero
    ],
)
def test_multi_select_partial_credit_with_penalty(
    client: TestClient, selected: list[str], expected_points: float, expected_correct: bool
) -> None:
    question = factories.create(client, factories.multi_select())
    usage = _deliver(client, question)
    body = _answer(client, usage["id"], selectedLabels=selected)

    assert body["awardedPoints"] == expected_points
    assert body["isCorrect"] is expected_correct


@pytest.mark.parametrize(
    ("ordered", "expected_points", "expected_correct"),
    [
        (["A", "B", "C", "D"], 4.0, True),   # perfect
        (["A", "B", "D", "C"], 2.0, False),   # first two positions right
        (["D", "C", "B", "A"], 0.0, False),   # nothing in the right place
    ],
)
def test_drag_to_order_partial_credit_scoring(
    client: TestClient, ordered: list[str], expected_points: float, expected_correct: bool
) -> None:
    question = factories.create(client, factories.drag_to_order())
    usage = _deliver(client, question)
    body = _answer(client, usage["id"], orderedLabels=ordered)

    assert body["awardedPoints"] == expected_points
    assert body["isCorrect"] is expected_correct


def test_drag_to_order_grading_ignores_the_presentation_order(client: TestClient) -> None:
    """The learner saw D, C, B, A but the answer key is still A, B, C, D."""
    question = factories.create(client, factories.drag_to_order())
    usage = _deliver(client, question, presentationOrder=["D", "C", "B", "A"])

    body = _answer(client, usage["id"], orderedLabels=["A", "B", "C", "D"])
    assert body["isCorrect"] is True
    assert body["awardedPoints"] == 4.0
    assert body["presentationOrder"] == ["D", "C", "B", "A"]


def test_scoring_uses_the_snapshot_not_the_edited_question(client: TestClient) -> None:
    """An in-progress attempt is graded against the version it was delivered."""
    question = factories.create(client, factories.single_choice())
    usage = _deliver(client, question)

    # Change the correct answer AFTER delivery.
    client.patch(
        f"{API}/questions/{question['id']}",
        json={
            "options": [
                {"label": "A", "text": "Layer 2 - Data Link", "isCorrect": True},
                {"label": "B", "text": "Layer 3 - Network", "isCorrect": False},
                {"label": "C", "text": "Layer 4 - Transport", "isCorrect": False},
                {"label": "D", "text": "Layer 7 - Application", "isCorrect": False},
            ]
        },
    )

    body = _answer(client, usage["id"], selectedLabels=["B"])
    # B was correct in version 1, which is what this learner was shown.
    assert body["isCorrect"] is True
    assert body["snapshotVersion"] == 1


def test_true_false_and_scenario_scoring(client: TestClient) -> None:
    tf = factories.create(client, factories.true_false())
    sn = factories.create(client, factories.scenario())

    tf_usage = _deliver(client, tf, attempt="att-tf")
    tf_body = _answer(client, tf_usage["id"], selectedLabels=["TRUE"])
    assert tf_body["isCorrect"] is True
    assert tf_body["awardedPoints"] == 1.0

    sn_usage = _deliver(client, sn, attempt="att-sn")
    sn_body = _answer(client, sn_usage["id"], selectedLabels=["B"])
    assert sn_body["isCorrect"] is True
    assert sn_body["awardedPoints"] == 2.0


def test_abandoned_attempt_is_recorded_without_completing(client: TestClient) -> None:
    question = factories.create(client, factories.single_choice())
    usage = _deliver(client, question)
    body = _answer(client, usage["id"], selectedLabels=["A"], attemptStatus="ABANDONED")

    assert body["attemptStatus"] == "ABANDONED"
    assert body["completedAt"] is None
    assert body["respondedAt"] is not None

    # An abandoned attempt still counts as history, so hard delete stays blocked.
    detail = client.get(f"{API}/questions/{question['id']}").json()
    assert detail["usage"]["total"] == 1
    assert detail["usage"]["completed"] == 0
    assert detail["usage"]["canHardDelete"] is False


def test_unknown_usage_returns_404(client: TestClient) -> None:
    response = client.patch(f"{API}/delivery/usages/nope", json={"selectedLabels": ["A"]})
    assert response.status_code == 404
