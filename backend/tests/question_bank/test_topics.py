"""Topic tagging (UC-02 §8, §21, §25)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.question_bank.models import QuestionTopic, Topic
from tests import factories
from tests.factories import API


def test_create_and_list_topics(client: TestClient, db: Session) -> None:
    response = client.post(
        f"{API}/topics", json={"name": "OSI Model", "description": "Layered network model"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "OSI Model"
    assert body["slug"] == "osi-model"
    assert body["isActive"] is True

    assert db.execute(select(Topic).where(Topic.slug == "osi-model")).scalar_one()

    listing = client.get(f"{API}/topics").json()
    assert [topic["name"] for topic in listing] == ["OSI Model"]
    assert listing[0]["questionCount"] == 0


def test_duplicate_topic_name_is_rejected_case_insensitively(client: TestClient) -> None:
    client.post(f"{API}/topics", json={"name": "Networking"})
    response = client.post(f"{API}/topics", json={"name": "networking"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TOPIC_ALREADY_EXISTS"


def test_topics_are_stored_relationally_not_as_a_string(
    client: TestClient, db: Session
) -> None:
    created = factories.create(
        client, factories.single_choice(topics=["Networking", "OSI Model", "Routing"])
    )

    links = db.execute(
        select(QuestionTopic).where(QuestionTopic.question_id == created["id"])
    ).scalars().all()
    assert len(links) == 3
    assert {link.topic.name for link in links} == {"Networking", "OSI Model", "Routing"}


def test_topics_referenced_by_a_question_are_created_on_demand(client: TestClient) -> None:
    factories.create(client, factories.single_choice(topics=["Brand New Topic"]))
    topics = client.get(f"{API}/topics").json()
    assert "Brand New Topic" in [topic["name"] for topic in topics]


def test_the_same_topic_name_resolves_to_one_row(client: TestClient, db: Session) -> None:
    factories.create(client, factories.single_choice(topics=["Networking"]))
    factories.create(client, factories.true_false(topics=["networking"]))

    rows = db.execute(select(Topic).where(Topic.slug == "networking")).scalars().all()
    assert len(rows) == 1

    listing = client.get(f"{API}/topics").json()
    entry = next(topic for topic in listing if topic["slug"] == "networking")
    assert entry["questionCount"] == 2


def test_assign_topics_to_an_existing_question(client: TestClient) -> None:
    created = factories.create(client, factories.single_choice())

    response = client.post(
        f"{API}/questions/{created['id']}/topics",
        json={"topicNames": ["Routing", "Layer 3"]},
    )
    assert response.status_code == 200, response.text
    names = {topic["name"] for topic in response.json()["topics"]}
    assert names == {"Networking", "Routing", "Layer 3"}


def test_assign_topics_by_id(client: TestClient) -> None:
    topic = client.post(f"{API}/topics", json={"name": "Security"}).json()
    created = factories.create(client, factories.single_choice())

    response = client.post(
        f"{API}/questions/{created['id']}/topics", json={"topicIds": [topic["id"]]}
    )
    assert response.status_code == 200
    assert "Security" in [t["name"] for t in response.json()["topics"]]


def test_replace_mode_swaps_the_whole_topic_set(client: TestClient) -> None:
    created = factories.create(client, factories.single_choice(topics=["A Topic", "B Topic"]))

    response = client.post(
        f"{API}/questions/{created['id']}/topics",
        json={"topicNames": ["C Topic"], "replace": True},
    )
    assert response.status_code == 200
    assert [topic["name"] for topic in response.json()["topics"]] == ["C Topic"]


def test_remove_a_topic_from_a_question(client: TestClient, db: Session) -> None:
    created = factories.create(client, factories.single_choice(topics=["Networking", "Routing"]))
    topic_id = next(t["id"] for t in created["topics"] if t["name"] == "Routing")

    response = client.delete(f"{API}/questions/{created['id']}/topics/{topic_id}")
    assert response.status_code == 200, response.text
    assert [topic["name"] for topic in response.json()["topics"]] == ["Networking"]

    links = db.execute(
        select(QuestionTopic).where(QuestionTopic.question_id == created["id"])
    ).scalars().all()
    assert len(links) == 1


def test_removing_the_last_topic_is_refused(client: TestClient) -> None:
    created = factories.create(client, factories.single_choice(topics=["Networking"]))
    topic_id = created["topics"][0]["id"]

    response = client.delete(f"{API}/questions/{created['id']}/topics/{topic_id}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LAST_TOPIC_CANNOT_BE_REMOVED"


def test_removing_a_topic_that_is_not_assigned_returns_404(client: TestClient) -> None:
    created = factories.create(client, factories.single_choice())
    other = client.post(f"{API}/topics", json={"name": "Unrelated"}).json()

    response = client.delete(f"{API}/questions/{created['id']}/topics/{other['id']}")
    assert response.status_code == 404


def test_rename_a_topic(client: TestClient) -> None:
    created = factories.create(client, factories.single_choice(topics=["Netwroking"]))
    topic_id = created["topics"][0]["id"]

    response = client.patch(f"{API}/topics/{topic_id}", json={"name": "Networking"})
    assert response.status_code == 200
    assert response.json()["name"] == "Networking"
    assert response.json()["slug"] == "networking"

    # The question follows the rename, because the link is by id.
    question = client.get(f"{API}/questions/{created['id']}").json()
    assert [topic["name"] for topic in question["topics"]] == ["Networking"]


def test_deactivating_a_topic_keeps_it_readable(client: TestClient) -> None:
    topic = client.post(f"{API}/topics", json={"name": "Legacy"}).json()
    client.patch(f"{API}/topics/{topic['id']}", json={"isActive": False})

    active_only = client.get(f"{API}/topics", params={"includeInactive": False}).json()
    assert "Legacy" not in [t["name"] for t in active_only]

    everything = client.get(f"{API}/topics").json()
    assert "Legacy" in [t["name"] for t in everything]


def test_deleting_a_topic_in_use_requires_force(client: TestClient, db: Session) -> None:
    created = factories.create(client, factories.single_choice(topics=["Networking", "Routing"]))
    topic_id = next(t["id"] for t in created["topics"] if t["name"] == "Routing")

    refused = client.delete(f"{API}/topics/{topic_id}")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "TOPIC_IN_USE"

    forced = client.delete(f"{API}/topics/{topic_id}", params={"force": True})
    assert forced.status_code == 200
    assert "1 question" in forced.json()["message"]

    assert db.get(Topic, topic_id) is None
    question = client.get(f"{API}/questions/{created['id']}").json()
    assert [topic["name"] for topic in question["topics"]] == ["Networking"]


def test_unused_topic_deletes_without_force(client: TestClient) -> None:
    topic = client.post(f"{API}/topics", json={"name": "Temporary"}).json()
    response = client.delete(f"{API}/topics/{topic['id']}")
    assert response.status_code == 200


def test_topic_search(client: TestClient) -> None:
    client.post(f"{API}/topics", json={"name": "Networking"})
    client.post(f"{API}/topics", json={"name": "Cryptography"})

    results = client.get(f"{API}/topics", params={"search": "crypt"}).json()
    assert [topic["name"] for topic in results] == ["Cryptography"]


def test_too_many_topics_is_rejected(client: TestClient) -> None:
    response = client.post(
        f"{API}/questions",
        json=factories.single_choice(topics=[f"Topic {n}" for n in range(25)]),
    )
    assert response.status_code == 422
    codes = {issue["code"] for issue in response.json()["error"]["details"]}
    assert "TOO_MANY_TOPICS" in codes


def test_unknown_topic_returns_404(client: TestClient) -> None:
    assert client.get(f"{API}/topics/missing").status_code == 404
