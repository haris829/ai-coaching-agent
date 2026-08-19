"""Immutable configuration versioning."""

from __future__ import annotations

import pytest

from tests.harness import Ctx, valid_configuration


def test_first_save_creates_version_1(ctx: Ctx) -> None:
    response = ctx.save_configuration(
        valid_configuration(questionCount=10, passMark=60, timeLimitMinutes=20)
    )

    assert response.status_code == 201
    body = response.json()
    assert body["created"] is True
    configuration = body["configuration"]
    assert configuration["versionNumber"] == 1
    assert configuration["questionCount"] == 10
    assert configuration["passMark"] == 60
    assert configuration["timeLimitMinutes"] == 20
    assert configuration["isActive"] is True
    assert configuration["attemptCount"] == 0


def test_a_change_creates_version_2_and_leaves_version_1_untouched(ctx: Ctx) -> None:
    v1 = ctx.save_configuration(
        valid_configuration(questionCount=10, passMark=60, timeLimitMinutes=20)
    ).json()["configuration"]

    v2 = ctx.save_configuration(
        valid_configuration(questionCount=10, passMark=70, timeLimitMinutes=20)
    )

    assert v2.status_code == 201
    new_version = v2.json()["configuration"]
    assert new_version["versionNumber"] == 2
    assert new_version["passMark"] == 70
    assert new_version["id"] != v1["id"]

    # Version 1 is field-for-field what it was, except that it is no longer active.
    versions = ctx.get_versions().json()["versions"]
    stored_v1 = next(item for item in versions if item["versionNumber"] == 1)
    assert stored_v1 == {**v1, "isActive": False}
    assert stored_v1["passMark"] == 60


def test_the_newest_version_becomes_active(ctx: Ctx) -> None:
    for pass_mark in (60, 70, 80):
        assert ctx.save_configuration(valid_configuration(passMark=pass_mark)).status_code == 201

    current = ctx.get_configuration().json()["configuration"]
    assert current["versionNumber"] == 3
    assert current["passMark"] == 80
    assert current["isActive"] is True

    versions = ctx.get_versions().json()["versions"]
    assert [item["versionNumber"] for item in versions] == [3, 2, 1]
    assert len([item for item in versions if item["isActive"]]) == 1


def test_every_version_retains_its_full_historical_settings(ctx: Ctx) -> None:
    ctx.save_configuration(
        valid_configuration(
            questionCount=10,
            passMark=50,
            maxAttempts=1,
            randomiseQuestions=False,
            deliveryMode="practice",
            timeLimitMinutes=None,
            questionTypes=[{"type": "SINGLE_CHOICE", "quota": None}],
        )
    )
    ctx.save_configuration(
        valid_configuration(
            questionCount=6,
            passMark=90,
            maxAttempts=5,
            randomiseQuestions=True,
            deliveryMode="exam",
            timeLimitMinutes=15,
            questionTypes=[
                {"type": "SINGLE_CHOICE", "quota": 3},
                {"type": "TRUE_FALSE", "quota": 3},
            ],
        )
    )

    v2, v1 = ctx.get_versions().json()["versions"]

    assert (v1["versionNumber"], v1["questionCount"], v1["passMark"]) == (1, 10, 50)
    assert (v1["maxAttempts"], v1["deliveryMode"], v1["timeLimitMinutes"]) == (1, "practice", None)
    assert v1["randomiseQuestions"] is False
    assert v1["questionTypes"] == [{"type": "SINGLE_CHOICE", "quota": None}]

    assert (v2["versionNumber"], v2["questionCount"], v2["passMark"]) == (2, 6, 90)
    assert (v2["maxAttempts"], v2["deliveryMode"], v2["timeLimitMinutes"]) == (5, "exam", 15)
    assert v2["randomiseQuestions"] is True
    assert v2["questionTypes"] == [
        {"type": "SINGLE_CHOICE", "quota": 3},
        {"type": "TRUE_FALSE", "quota": 3},
    ]


def test_no_new_version_when_nothing_meaningful_changed(ctx: Ctx) -> None:
    config = valid_configuration()
    assert ctx.save_configuration(config).status_code == 201

    # Same settings, question types submitted in a different order.
    resave = ctx.save_configuration(
        {**config, "questionTypes": list(reversed(config["questionTypes"]))}
    )

    assert resave.status_code == 200
    body = resave.json()
    assert body["created"] is False
    assert body["unchanged"] is True
    assert body["configuration"]["versionNumber"] == 1
    assert len(ctx.get_versions().json()["versions"]) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"questionCount": 11},
        {"timeLimitMinutes": 31},
        {"passMark": 61},
        {"maxAttempts": 4},
        {"deliveryMode": "practice"},
        {"randomiseQuestions": True},
        {"questionTypes": [{"type": "SINGLE_CHOICE", "quota": None}]},
    ],
)
def test_any_individual_setting_change_creates_a_new_version(ctx: Ctx, change: dict) -> None:
    assert ctx.save_configuration(valid_configuration()).status_code == 201

    response = ctx.save_configuration(valid_configuration(**change))
    assert response.status_code == 201
    assert response.json()["configuration"]["versionNumber"] == 2


def test_changing_the_topic_scope_creates_a_new_version(ctx: Ctx) -> None:
    """The eligible pool is part of what a version means, so scoping it is a real change."""
    from tests import bank

    with ctx.session() as session:
        topic = bank.topic_named(session, "Networking")
        topic_id = topic.id

    assert ctx.save_configuration(valid_configuration()).status_code == 201
    response = ctx.save_configuration(valid_configuration(topicIds=[topic_id]))

    assert response.status_code == 201
    body = response.json()["configuration"]
    assert body["versionNumber"] == 2
    assert body["topics"] == [{"id": topic_id, "slug": "networking", "name": "Networking"}]


def test_unconfigured_quiz_reports_no_configuration(ctx: Ctx) -> None:
    response = ctx.get_configuration()
    assert response.status_code == 200
    body = response.json()
    assert body["configuration"] is None
    assert body["capacity"] is None
    assert body["quiz"]["title"] == "Test Quiz"


def test_a_stored_version_row_cannot_be_updated(ctx: Ctx) -> None:
    """Immutability is enforced by the database, not only by the service layer."""
    version_id = ctx.save_configuration(valid_configuration()).json()["configuration"]["id"]

    with pytest.raises(Exception, match="IMMUTABLE_CONFIGURATION_VERSION"):
        ctx.execute(
            "UPDATE qc_configuration_versions SET pass_mark = 99 WHERE id = :id", id=version_id
        )

    with pytest.raises(Exception, match="IMMUTABLE_CONFIGURATION_VERSION"):
        ctx.execute(
            "UPDATE qc_configuration_version_question_types SET question_quota = 1 "
            "WHERE configuration_version_id = :id",
            id=version_id,
        )

    assert ctx.get_configuration().json()["configuration"]["passMark"] == 60


def test_a_frozen_topic_scope_cannot_be_updated(ctx: Ctx) -> None:
    from tests import bank

    with ctx.session() as session:
        topic_id = bank.topic_named(session, "Networking").id

    version_id = ctx.save_configuration(valid_configuration(topicIds=[topic_id])).json()[
        "configuration"
    ]["id"]

    with pytest.raises(Exception, match="IMMUTABLE_CONFIGURATION_VERSION"):
        ctx.execute(
            "UPDATE qc_configuration_version_topics SET topic_name = 'Rewritten' "
            "WHERE configuration_version_id = :id",
            id=version_id,
        )


def test_version_numbers_are_unique_per_quiz(ctx: Ctx) -> None:
    ctx.save_configuration(valid_configuration())

    with pytest.raises(Exception, match="UNIQUE constraint"):
        ctx.execute(
            "INSERT INTO qc_configuration_versions "
            "(quiz_id, version_number, question_count, pass_mark, randomise_questions, "
            " max_attempts, delivery_mode, settings_fingerprint, created_by_user_id, created_at) "
            "VALUES (:quiz, 1, 5, 50, 0, 1, 'practice', 'fp', :user, '2026-01-01 00:00:00')",
            quiz=ctx.quiz_id,
            user=ctx.admin_id,
        )
