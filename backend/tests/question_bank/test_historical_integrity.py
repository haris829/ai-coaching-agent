"""Historical data protection (UC-02 §16, Rules 2 and 7, §25).

The scenario the requirement spells out:

    Q100 used by completed Attempt A -> Q100 retired
      -> Attempt A must STILL report question text, type, options, correct answer,
         learner response, score and the original question identity.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.question_bank.models import Question, QuestionUsage
from tests import factories
from tests.factories import API

ATTEMPT = "attempt-A"
LEARNER = "learner-42"


def _deliver_and_complete(
    client: TestClient,
    question: dict,
    *,
    attempt_ref: str = ATTEMPT,
    selected: list[str] | None = None,
    ordered: list[str] | None = None,
    presentation_order: list[str] | None = None,
) -> dict:
    usage = client.post(
        f"{API}/delivery/usages",
        json={
            "attemptRef": attempt_ref,
            "questionId": question["id"],
            "learnerRef": LEARNER,
            **({"presentationOrder": presentation_order} if presentation_order else {}),
        },
    )
    assert usage.status_code == 201, usage.text
    usage_id = usage.json()["id"]

    payload: dict = {"attemptStatus": "COMPLETED"}
    if selected is not None:
        payload["selectedLabels"] = selected
    if ordered is not None:
        payload["orderedLabels"] = ordered

    response = client.patch(f"{API}/delivery/usages/{usage_id}", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_completed_attempt_still_reports_a_retired_question(client: TestClient) -> None:
    question = factories.create(client, factories.single_choice())
    _deliver_and_complete(client, question, selected=["B"])

    client.post(
        f"{API}/questions/{question['id']}/retire", json={"reason": "Retired after the attempt"}
    )

    report = client.get(f"{API}/reporting/attempts/{ATTEMPT}")
    assert report.status_code == 200, report.text
    body = report.json()

    assert body["attemptRef"] == ATTEMPT
    assert body["learnerRef"] == LEARNER
    assert body["questionCount"] == 1

    item = body["items"][0]
    # Everything UC-02 §16 requires a completed attempt to display:
    assert item["questionText"] == question["questionText"]          # question text
    assert item["type"] == "SINGLE_CHOICE"                            # question type
    assert len(item["options"]) == 4                                  # options
    assert item["correctLabels"] == ["B"]                             # correct answer
    assert item["learnerResponse"]["selectedLabels"] == ["B"]         # learner response
    assert item["isCorrect"] is True                                  # score information
    assert item["awardedPoints"] == 1.0
    assert item["maxPoints"] == 1.0
    assert item["questionReference"] == question["reference"]          # original identity
    assert item["questionId"] == question["id"]
    # ...and the live status is reported as context, without altering the content.
    assert item["currentQuestionStatus"] == "RETIRED"


def test_report_is_byte_identical_before_and_after_retirement(client: TestClient) -> None:
    question = factories.create(client, factories.multi_select())
    _deliver_and_complete(client, question, selected=["A", "B"])

    before = client.get(f"{API}/reporting/attempts/{ATTEMPT}").json()
    client.post(f"{API}/questions/{question['id']}/retire", json={"reason": "Retired"})
    after = client.get(f"{API}/reporting/attempts/{ATTEMPT}").json()

    def content_only(report: dict) -> list[dict]:
        return [
            {k: v for k, v in item.items() if k != "currentQuestionStatus"}
            for item in report["items"]
        ]

    assert content_only(before) == content_only(after)
    assert before["totalAwardedPoints"] == after["totalAwardedPoints"]


def test_editing_a_question_does_not_alter_an_existing_completed_attempt(
    client: TestClient,
) -> None:
    question = factories.create(client, factories.single_choice())
    _deliver_and_complete(client, question, selected=["B"])

    # Rewrite the question completely: new text and a different correct answer.
    response = client.patch(
        f"{API}/questions/{question['id']}",
        json={
            "questionText": "Completely rewritten question.",
            "options": [
                {"label": "A", "text": "New option A", "isCorrect": True},
                {"label": "B", "text": "New option B", "isCorrect": False},
                {"label": "C", "text": "New option C", "isCorrect": False},
                {"label": "D", "text": "New option D", "isCorrect": False},
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2

    report = client.get(f"{API}/reporting/attempts/{ATTEMPT}").json()
    item = report["items"][0]

    # The attempt is pinned to version 1 and still shows the ORIGINAL question.
    assert item["snapshotVersion"] == 1
    assert item["questionText"] == "Which OSI layer routes packets between networks?"
    assert item["correctLabels"] == ["B"]
    assert item["options"][1]["text"] == "Layer 3 - Network"
    assert item["isCorrect"] is True

    # The live question shows the new content.
    live = client.get(f"{API}/questions/{question['id']}").json()
    assert live["questionText"] == "Completely rewritten question."
    assert live["correctLabels"] == ["A"]


def test_editing_then_retiring_still_leaves_history_intact(client: TestClient) -> None:
    question = factories.create(client, factories.true_false())
    _deliver_and_complete(client, question, selected=["TRUE"])

    client.patch(
        f"{API}/questions/{question['id']}",
        json={
            "options": [
                {"label": "TRUE", "text": "True", "isCorrect": False},
                {"label": "FALSE", "text": "False", "isCorrect": True},
            ]
        },
    )
    client.post(f"{API}/questions/{question['id']}/retire", json={"reason": "Withdrawn"})

    item = client.get(f"{API}/reporting/attempts/{ATTEMPT}").json()["items"][0]
    assert item["snapshotVersion"] == 1
    assert item["correctLabels"] == ["TRUE"]
    assert item["isCorrect"] is True


def test_a_question_with_history_cannot_be_hard_deleted(
    client: TestClient, db: Session
) -> None:
    question = factories.create(client, factories.single_choice())
    _deliver_and_complete(client, question, selected=["B"])

    response = client.delete(f"{API}/questions/{question['id']}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "QUESTION_HAS_HISTORY"
    assert "Retire it instead" in response.json()["error"]["message"]

    # The question — and its history — are still there.
    assert db.get(Question, question["id"]) is not None
    assert (
        db.execute(
            select(QuestionUsage).where(QuestionUsage.question_id == question["id"])
        ).scalars().all()
        != []
    )


def test_usage_summary_drives_the_retire_versus_delete_decision(client: TestClient) -> None:
    fresh = factories.create(client, factories.single_choice())
    used = factories.create(client, factories.true_false())
    _deliver_and_complete(client, used, selected=["TRUE"])

    fresh_body = client.get(f"{API}/questions/{fresh['id']}").json()
    assert fresh_body["usage"] == {
        "total": 0,
        "completed": 0,
        "inProgress": 0,
        "hasHistory": False,
        "canHardDelete": True,
    }

    used_body = client.get(f"{API}/questions/{used['id']}").json()
    assert used_body["usage"]["total"] == 1
    assert used_body["usage"]["completed"] == 1
    assert used_body["usage"]["hasHistory"] is True
    assert used_body["usage"]["canHardDelete"] is False


def test_usage_endpoint_lists_attempts_for_a_retired_question(client: TestClient) -> None:
    question = factories.create(client, factories.single_choice())
    _deliver_and_complete(client, question, selected=["B"])
    client.post(f"{API}/questions/{question['id']}/retire", json={})

    usages = client.get(f"{API}/questions/{question['id']}/usages").json()
    assert len(usages) == 1
    assert usages[0]["attemptRef"] == ATTEMPT
    assert usages[0]["attemptStatus"] == "COMPLETED"
    assert usages[0]["questionReference"] == question["reference"]
    assert usages[0]["isCorrect"] is True


def test_presentation_order_is_recorded_separately_from_the_answer_key(
    client: TestClient,
) -> None:
    """A shuffled delivery must not be mistaken for the correct order."""
    question = factories.create(client, factories.drag_to_order())

    _deliver_and_complete(
        client,
        question,
        presentation_order=["C", "A", "D", "B"],  # shuffled for the learner
        ordered=["A", "B", "C", "D"],  # learner's (correct) answer
    )
    client.post(f"{API}/questions/{question['id']}/retire", json={})

    item = client.get(f"{API}/reporting/attempts/{ATTEMPT}").json()["items"][0]
    assert item["presentationOrder"] == ["C", "A", "D", "B"]
    assert item["correctOrder"] == ["A", "B", "C", "D"]
    assert item["learnerResponse"]["orderedLabels"] == ["A", "B", "C", "D"]
    assert item["isCorrect"] is True
    assert item["awardedPoints"] == 4.0


def test_multi_question_attempt_report_totals(client: TestClient) -> None:
    sc = factories.create(client, factories.single_choice())
    ms = factories.create(client, factories.multi_select())

    _deliver_and_complete(client, sc, selected=["B"])
    _deliver_and_complete(client, ms, selected=["A", "B", "C"])

    for question in (sc, ms):
        client.post(f"{API}/questions/{question['id']}/retire", json={})

    report = client.get(f"{API}/reporting/attempts/{ATTEMPT}").json()
    assert report["questionCount"] == 2
    assert report["attemptStatus"] == "COMPLETED"
    assert report["totalAwardedPoints"] == 4.0  # 1 + 3
    assert report["totalMaxPoints"] == 4.0
    assert all(item["currentQuestionStatus"] == "RETIRED" for item in report["items"])


def test_snapshot_freezes_topic_names_so_topic_deletion_cannot_break_a_report(
    client: TestClient,
) -> None:
    question = factories.create(client, factories.single_choice())
    _deliver_and_complete(client, question, selected=["B"])

    topic_id = question["topics"][0]["id"]
    deleted = client.delete(f"{API}/topics/{topic_id}", params={"force": True})
    assert deleted.status_code == 200, deleted.text

    item = client.get(f"{API}/reporting/attempts/{ATTEMPT}").json()["items"][0]
    assert item["topics"] == ["Networking"]


def test_report_for_an_unknown_attempt_returns_404(client: TestClient) -> None:
    response = client.get(f"{API}/reporting/attempts/never-happened")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
