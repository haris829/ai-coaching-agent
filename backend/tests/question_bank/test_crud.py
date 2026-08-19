"""Question Bank CRUD (UC-02 §6, §7, §25)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.question_bank.models import Question, QuestionOption, QuestionSnapshot
from tests import factories
from tests.factories import API


def test_create_question_persists_to_the_database(client: TestClient, db: Session) -> None:
    body = factories.create(client, factories.single_choice())

    # Assert against the DATABASE, not just the response, so this proves real persistence.
    stored = db.execute(select(Question).where(Question.id == body["id"])).scalar_one()
    assert stored.question_text == "Which OSI layer routes packets between networks?"
    assert stored.type == "SINGLE_CHOICE"
    assert stored.status == "ACTIVE"
    assert stored.version == 1
    assert stored.content_hash

    options = db.execute(
        select(QuestionOption).where(QuestionOption.question_id == stored.id)
    ).scalars().all()
    assert len(options) == 4
    assert [o.label for o in sorted(options, key=lambda o: o.position)] == ["A", "B", "C", "D"]
    assert [o.label for o in options if o.is_correct] == ["B"]


def test_create_records_all_required_metadata(client: TestClient) -> None:
    body = factories.create(client, factories.multi_select())

    # UC-02 §7 metadata checklist.
    assert body["id"]
    assert body["reference"].startswith("Q-")
    assert body["type"] == "MULTI_SELECT"
    assert body["questionText"]
    assert len(body["options"]) == 5
    assert body["correctLabels"] == ["A", "B", "C"]
    assert body["explanation"]
    assert [topic["name"] for topic in body["topics"]] == ["IP Addressing"]
    assert body["scoring"] == {
        "points": 3.0,
        "scoringStrategy": "PARTIAL_CREDIT_WITH_PENALTY",
        "penaltyPerIncorrect": 0.5,
    }
    assert body["status"] == "ACTIVE"
    assert body["createdAt"] and body["updatedAt"]


def test_reference_is_human_readable_and_sequential(client: TestClient) -> None:
    first = factories.create(client, factories.single_choice())
    second = factories.create(client, factories.true_false())
    assert first["reference"] == "Q-000001"
    assert second["reference"] == "Q-000002"


def test_get_question_by_id_and_by_reference(client: TestClient) -> None:
    created = factories.create(client, factories.single_choice())

    by_id = client.get(f"{API}/questions/{created['id']}")
    by_reference = client.get(f"{API}/questions/{created['reference']}")

    assert by_id.status_code == 200
    assert by_reference.status_code == 200
    assert by_id.json()["id"] == by_reference.json()["id"] == created["id"]


def test_get_unknown_question_returns_404(client: TestClient) -> None:
    response = client.get(f"{API}/questions/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_update_question_text_persists_and_bumps_version(client: TestClient, db: Session) -> None:
    created = factories.create(client, factories.single_choice())

    response = client.patch(
        f"{API}/questions/{created['id']}",
        json={"questionText": "Which OSI layer is responsible for inter-network routing?"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["questionText"] == "Which OSI layer is responsible for inter-network routing?"
    # A content edit creates a new immutable version.
    assert body["version"] == 2

    stored = db.execute(select(Question).where(Question.id == created["id"])).scalar_one()
    assert stored.question_text == body["questionText"]

    snapshots = db.execute(
        select(QuestionSnapshot).where(QuestionSnapshot.question_id == created["id"])
    ).scalars().all()
    assert sorted(s.version for s in snapshots) == [1, 2]
    # Version 1 still holds the ORIGINAL text — history is append-only.
    original = next(s for s in snapshots if s.version == 1)
    assert original.question_text == "Which OSI layer routes packets between networks?"


def test_update_replaces_options(client: TestClient, db: Session) -> None:
    created = factories.create(client, factories.single_choice())

    response = client.patch(
        f"{API}/questions/{created['id']}",
        json={
            "options": [
                {"label": "A", "text": "Physical", "isCorrect": False},
                {"label": "B", "text": "Data Link", "isCorrect": False},
                {"label": "C", "text": "Network", "isCorrect": True},
                {"label": "D", "text": "Session", "isCorrect": False},
            ]
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["correctLabels"] == ["C"]

    options = db.execute(
        select(QuestionOption).where(QuestionOption.question_id == created["id"])
    ).scalars().all()
    assert {o.text for o in options} == {"Physical", "Data Link", "Network", "Session"}


def test_partial_update_leaves_other_fields_untouched(client: TestClient) -> None:
    created = factories.create(client, factories.multi_select())

    response = client.patch(
        f"{API}/questions/{created['id']}", json={"difficulty": "HARD"}
    )
    assert response.status_code == 200
    body = response.json()

    assert body["difficulty"] == "HARD"
    assert body["questionText"] == created["questionText"]
    assert body["correctLabels"] == created["correctLabels"]
    assert len(body["options"]) == len(created["options"])
    # Difficulty is not semantic content, so no new version is cut.
    assert body["version"] == 1


def test_metadata_only_edit_does_not_create_a_new_version(client: TestClient) -> None:
    created = factories.create(client, factories.single_choice())
    response = client.patch(
        f"{API}/questions/{created['id']}", json={"explanation": "Rewritten explanation."}
    )
    assert response.status_code == 200
    assert response.json()["version"] == 1


def test_scoring_change_creates_a_new_version(client: TestClient) -> None:
    created = factories.create(client, factories.multi_select())
    response = client.patch(
        f"{API}/questions/{created['id']}",
        json={"scoring": {"points": 5, "scoringStrategy": "PARTIAL_CREDIT"}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scoring"]["points"] == 5.0
    assert body["scoring"]["scoringStrategy"] == "PARTIAL_CREDIT"
    assert body["scoring"]["penaltyPerIncorrect"] == 0.0
    assert body["version"] == 2


def test_list_questions_returns_metadata_and_pagination(client: TestClient) -> None:
    for builder in factories.ALL_BUILDERS.values():
        factories.create(client, builder())

    response = client.get(f"{API}/questions", params={"pageSize": 3})
    assert response.status_code == 200
    body = response.json()

    assert len(body["items"]) == 3
    assert body["meta"] == {
        "page": 1,
        "pageSize": 3,
        "total": 5,
        "totalPages": 2,
        "hasNext": True,
        "hasPrevious": False,
    }
    row = body["items"][0]
    for field in (
        "id",
        "reference",
        "type",
        "questionText",
        "topics",
        "status",
        "createdAt",
        "updatedAt",
    ):
        assert field in row


def test_list_filters_by_type_status_and_search(client: TestClient) -> None:
    sc = factories.create(client, factories.single_choice())
    factories.create(client, factories.true_false())
    factories.create(client, factories.drag_to_order())

    by_type = client.get(f"{API}/questions", params={"type": "TRUE_FALSE"})
    assert [item["type"] for item in by_type.json()["items"]] == ["TRUE_FALSE"]

    client.post(f"{API}/questions/{sc['id']}/retire", json={"reason": "Superseded"})

    by_status = client.get(f"{API}/questions", params={"status": "RETIRED"})
    assert [item["id"] for item in by_status.json()["items"]] == [sc["id"]]

    by_search = client.get(f"{API}/questions", params={"search": "handshake"})
    assert len(by_search.json()["items"]) == 1
    assert by_search.json()["items"][0]["type"] == "DRAG_TO_ORDER"

    by_reference = client.get(f"{API}/questions", params={"search": sc["reference"]})
    assert [item["id"] for item in by_reference.json()["items"]] == [sc["id"]]


def test_list_filters_by_topic(client: TestClient) -> None:
    factories.create(client, factories.single_choice())  # Networking
    tf = factories.create(client, factories.true_false())  # Transport Protocols

    topic_id = tf["topics"][0]["id"]
    response = client.get(f"{API}/questions", params={"topicId": topic_id})
    assert [item["id"] for item in response.json()["items"]] == [tf["id"]]

    by_slug = client.get(f"{API}/questions", params={"topicSlug": "transport-protocols"})
    assert [item["id"] for item in by_slug.json()["items"]] == [tf["id"]]


def test_delete_question_without_history_is_permitted(client: TestClient, db: Session) -> None:
    created = factories.create(client, factories.single_choice())

    response = client.delete(f"{API}/questions/{created['id']}")
    assert response.status_code == 200, response.text
    assert response.json()["deleted"] is True

    assert db.get(Question, created["id"]) is None
    # Cascades cleared the owned rows.
    assert (
        db.execute(
            select(QuestionOption).where(QuestionOption.question_id == created["id"])
        ).scalars().all()
        == []
    )
    assert (
        db.execute(
            select(QuestionSnapshot).where(QuestionSnapshot.question_id == created["id"])
        ).scalars().all()
        == []
    )


def test_duplicate_question_is_rejected(client: TestClient) -> None:
    factories.create(client, factories.single_choice())

    response = client.post(f"{API}/questions", json=factories.single_choice())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_QUESTION"


def test_reordering_options_is_recognised_as_the_same_question(client: TestClient) -> None:
    """The content hash ignores presentation order, so a shuffled duplicate is still caught."""
    factories.create(client, factories.single_choice())

    shuffled = factories.single_choice()
    shuffled["options"] = list(reversed(shuffled["options"]))
    response = client.post(f"{API}/questions", json=shuffled)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_QUESTION"


def test_external_ref_must_be_unique(client: TestClient) -> None:
    factories.create(client, factories.single_choice(externalRef="SRC-1"))

    response = client.post(f"{API}/questions", json=factories.true_false(externalRef="SRC-1"))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EXTERNAL_REF_ALREADY_USED"


def test_draft_questions_are_created_but_not_deliverable(client: TestClient) -> None:
    body = factories.create(client, factories.single_choice(status="DRAFT"))
    assert body["status"] == "DRAFT"
    assert body["isDeliverable"] is False

    pool = client.get(f"{API}/delivery/pool").json()
    assert pool["items"] == []


def test_cannot_create_a_question_directly_as_retired(client: TestClient) -> None:
    response = client.post(f"{API}/questions", json=factories.single_choice(status="RETIRED"))
    assert response.status_code == 422
    codes = {issue["code"] for issue in response.json()["error"]["details"]}
    assert "CANNOT_CREATE_RETIRED" in codes


def test_version_history_endpoint_lists_every_snapshot(client: TestClient) -> None:
    created = factories.create(client, factories.single_choice())
    client.patch(f"{API}/questions/{created['id']}", json={"questionText": "Edited once."})
    client.patch(f"{API}/questions/{created['id']}", json={"questionText": "Edited twice."})

    versions = client.get(f"{API}/questions/{created['id']}/versions").json()
    assert [v["version"] for v in versions] == [1, 2, 3]
    assert versions[0]["questionText"] == "Which OSI layer routes packets between networks?"
    assert versions[2]["questionText"] == "Edited twice."

    one = client.get(f"{API}/questions/{created['id']}/versions/1").json()
    assert one["questionText"] == "Which OSI layer routes packets between networks?"
    assert one["payload"]["correctLabels"] == ["B"]


def test_unknown_version_returns_404(client: TestClient) -> None:
    created = factories.create(client, factories.single_choice())
    response = client.get(f"{API}/questions/{created['id']}/versions/99")
    assert response.status_code == 404
