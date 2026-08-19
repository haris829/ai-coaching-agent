"""Retirement (UC-02 §15, Rules 2/3, §25).

    Active question -> Retire -> not available for future delivery
                             -> still available for historical reporting
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.question_bank.models import Question, QuestionOption, QuestionSnapshot
from tests import factories
from tests.factories import API


def test_retire_sets_status_and_records_who_and_why(client: TestClient, db: Session) -> None:
    created = factories.create(client, factories.single_choice())

    response = client.post(
        f"{API}/questions/{created['id']}/retire",
        json={"reason": "Superseded by the 2026 syllabus"},
        headers={"X-Admin-User": "hkhan"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["status"] == "RETIRED"
    assert body["retiredAt"] is not None
    assert body["retiredReason"] == "Superseded by the 2026 syllabus"
    assert body["retiredBy"] == "hkhan"
    assert body["isDeliverable"] is False

    stored = db.execute(select(Question).where(Question.id == created["id"])).scalar_one()
    assert stored.status == "RETIRED"


def test_retired_question_remains_fully_in_the_database(client: TestClient, db: Session) -> None:
    created = factories.create(client, factories.multi_select())
    client.post(f"{API}/questions/{created['id']}/retire", json={"reason": "Retired"})

    stored = db.execute(select(Question).where(Question.id == created["id"])).scalar_one()
    # Identity is preserved.
    assert stored.id == created["id"]
    assert stored.reference == created["reference"]
    assert stored.seq == created["seq"]
    # Content is preserved.
    assert stored.question_text == created["questionText"]

    options = db.execute(
        select(QuestionOption).where(QuestionOption.question_id == created["id"])
    ).scalars().all()
    assert len(options) == 5
    assert sorted(o.label for o in options if o.is_correct) == ["A", "B", "C"]

    snapshots = db.execute(
        select(QuestionSnapshot).where(QuestionSnapshot.question_id == created["id"])
    ).scalars().all()
    assert len(snapshots) == 1


def test_retired_question_is_still_readable_and_reportable(client: TestClient) -> None:
    created = factories.create(client, factories.single_choice())
    client.post(f"{API}/questions/{created['id']}/retire", json={"reason": "Retired"})

    fetched = client.get(f"{API}/questions/{created['id']}")
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["status"] == "RETIRED"
    assert body["questionText"] == created["questionText"]
    assert body["correctLabels"] == ["B"]
    assert body["topics"] == created["topics"]

    # Still reachable by its human-readable reference, which reporting relies on.
    by_reference = client.get(f"{API}/questions/{created['reference']}")
    assert by_reference.status_code == 200


def test_retired_question_is_excluded_from_future_delivery(client: TestClient) -> None:
    keep = factories.create(client, factories.single_choice())
    retire = factories.create(client, factories.true_false())

    before = client.get(f"{API}/delivery/pool", params={"limit": 50}).json()
    assert {item["id"] for item in before["items"]} == {keep["id"], retire["id"]}

    client.post(f"{API}/questions/{retire['id']}/retire", json={"reason": "Retired"})

    after = client.get(f"{API}/delivery/pool", params={"limit": 50}).json()
    assert {item["id"] for item in after["items"]} == {keep["id"]}
    assert after["totalAvailable"] == 1


def test_retired_question_cannot_be_delivered_to_a_new_attempt(client: TestClient) -> None:
    created = factories.create(client, factories.single_choice())
    client.post(f"{API}/questions/{created['id']}/retire", json={"reason": "Retired"})

    response = client.post(
        f"{API}/delivery/usages",
        json={"attemptRef": "attempt-new", "questionId": created["id"]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "QUESTION_NOT_DELIVERABLE"


def test_deliverable_only_filter_excludes_retired_questions(client: TestClient) -> None:
    keep = factories.create(client, factories.single_choice())
    retired = factories.create(client, factories.true_false())
    client.post(f"{API}/questions/{retired['id']}/retire", json={})

    response = client.get(f"{API}/questions", params={"deliverableOnly": True})
    assert [item["id"] for item in response.json()["items"]] == [keep["id"]]

    # ...while the unfiltered list still shows both, so an admin can audit the bank.
    everything = client.get(f"{API}/questions").json()
    assert len(everything["items"]) == 2


def test_retiring_twice_is_rejected(client: TestClient) -> None:
    created = factories.create(client, factories.single_choice())
    client.post(f"{API}/questions/{created['id']}/retire", json={})

    response = client.post(f"{API}/questions/{created['id']}/retire", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "QUESTION_ALREADY_RETIRED"


def test_retired_question_content_is_read_only(client: TestClient) -> None:
    created = factories.create(client, factories.single_choice())
    client.post(f"{API}/questions/{created['id']}/retire", json={})

    response = client.patch(
        f"{API}/questions/{created['id']}", json={"questionText": "Trying to edit"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "QUESTION_RETIRED"


def test_topics_can_still_be_managed_on_a_retired_question(client: TestClient) -> None:
    """Tagging is metadata; snapshots freeze topic names, so history is unaffected."""
    created = factories.create(client, factories.single_choice())
    client.post(f"{API}/questions/{created['id']}/retire", json={})

    response = client.post(
        f"{API}/questions/{created['id']}/topics", json={"topicNames": ["Deprecated"]}
    )
    assert response.status_code == 200, response.text
    assert "Deprecated" in [topic["name"] for topic in response.json()["topics"]]
    assert response.json()["status"] == "RETIRED"


def test_status_cannot_be_set_to_retired_through_a_plain_update(client: TestClient) -> None:
    created = factories.create(client, factories.single_choice())
    response = client.patch(f"{API}/questions/{created['id']}", json={"status": "RETIRED"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "USE_RETIRE_ENDPOINT"


def test_reactivate_returns_a_retired_question_to_delivery(client: TestClient) -> None:
    created = factories.create(client, factories.single_choice())
    client.post(f"{API}/questions/{created['id']}/retire", json={"reason": "Temporary"})

    response = client.post(f"{API}/questions/{created['id']}/reactivate")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ACTIVE"
    assert body["retiredAt"] is None
    assert body["retiredReason"] is None
    assert body["isDeliverable"] is True

    pool = client.get(f"{API}/delivery/pool").json()
    assert [item["id"] for item in pool["items"]] == [created["id"]]


def test_reactivating_a_live_question_is_rejected(client: TestClient) -> None:
    created = factories.create(client, factories.single_choice())
    response = client.post(f"{API}/questions/{created['id']}/reactivate")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "QUESTION_NOT_RETIRED"


def test_a_retired_duplicate_may_exist_alongside_a_live_question(client: TestClient) -> None:
    """Duplicate detection ignores retired questions, so a replacement can be authored."""
    first = factories.create(client, factories.single_choice())
    client.post(f"{API}/questions/{first['id']}/retire", json={"reason": "Replacing"})

    replacement = client.post(f"{API}/questions", json=factories.single_choice())
    assert replacement.status_code == 201

    # ...but the retired original may not be reactivated back into a clash.
    response = client.post(f"{API}/questions/{first['id']}/reactivate")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_QUESTION"
