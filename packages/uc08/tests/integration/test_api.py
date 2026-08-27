"""The HTTP surface, end to end, with no network and no API key.

Security assertions live here too: no endpoint accepts a user identifier, no
learner can reach another learner data, and unknown request fields are rejected
outright rather than silently ignored.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from uc08.adapters.clock.clocks import FixedClock
from uc08.adapters.mock.gap_report import GapReportPlan
from uc08.adapters.mock.ledger import ActivityLedger
from uc08.api.app import create_app
from uc08.composition import build_container
from uc08.config import load_settings
from uc08.domain.models import StreakRecord

ANCHOR = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
MONDAY = datetime(2026, 3, 16, 9, 0, tzinfo=timezone.utc)
SUBJECT = "learner-7781"
OTHER = "learner-9902"
HEADERS = {"X-UC08-Subject": SUBJECT}
OTHER_HEADERS = {"X-UC08-Subject": OTHER}


class Rig:
    def __init__(self, *, now: datetime = ANCHOR, **overrides):
        self.clock = FixedClock(now)
        self.ledger = ActivityLedger()
        self.gap_plan = GapReportPlan()
        settings = load_settings(**overrides)
        from uc08.adapters.mock.activity import MockActivityProvider
        from uc08.adapters.mock.gap_report import MockGapReportProvider

        self.container = build_container(
            settings,
            clock=self.clock,
            activity=MockActivityProvider(self.clock, self.ledger),
            gap_report=MockGapReportProvider(self.clock, self.gap_plan),
        )
        self.client = TestClient(create_app(self.container))

    def interact(self, interaction_id: str, *, headers=HEADERS, user: str = SUBJECT, **body):
        self.ledger.add_interaction(user, self.clock.now(), interaction_id, topic="conduct")
        payload = {"interaction_id": interaction_id, "session_id": "sess-abc", **body}
        return self.client.post("/api/v1/streaks/record-activity", json=payload, headers=headers)


@pytest.fixture
def rig() -> Rig:
    return Rig()


# --------------------------------------------------------------------------
# Happy paths
# --------------------------------------------------------------------------
def test_healthz_reports_the_wiring(rig):
    response = rig.client.get("/api/v1/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["component"] == "uc08-learning-streaks"
    assert body["activity_provider"] == "mock"
    assert body["persistence"] == "memory"


def test_record_activity_then_read_the_streak(rig):
    rig.ledger.set_question_count(SUBJECT, 10)
    first = rig.interact("i-1")
    assert first.status_code == 200
    body = first.json()
    assert body["outcome"] == "started"
    assert body["streak"]["current_streak_days"] == 1
    assert body["session_id"] == "sess-abc"
    assert body["session_id_source"] == "received"
    assert [badge["milestone"] for badge in body["awarded_badges"]] == [10]
    assert [event["event_type"] for event in body["badge_events"]] == ["badge_awarded"]

    rig.clock.advance(hours=23)
    second = rig.interact("i-2")
    assert second.json()["outcome"] == "incremented"
    assert second.json()["streak"]["current_streak_days"] == 2

    state = rig.client.get("/api/v1/streaks", headers=HEADERS)
    assert state.status_code == 200
    assert state.json()["streak"]["current_streak_days"] == 2
    assert state.json()["streak"]["longest_streak_days"] == 2
    assert state.json()["window_hours"] == 24
    assert state.json()["freeze_offer"] is None


def test_every_emitted_enum_value_is_lowercase(rig):
    rig.ledger.set_question_count(SUBJECT, 100)
    body = rig.interact("i-1").json()

    def check(value):
        if isinstance(value, str):
            # Enum-shaped values only: single tokens of letters and underscores.
            if value and value.replace("_", "").isalpha():
                assert value == value.lower(), value
        elif isinstance(value, dict):
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)

    check(body)
    assert body["activity_status"] == "available"
    assert body["persistence_outcome"] == "saved"


def test_badges_endpoint_lists_the_collection(rig):
    rig.ledger.set_question_count(SUBJECT, 60)
    rig.interact("i-1")

    response = rig.client.get("/api/v1/badges", headers=HEADERS)

    assert response.status_code == 200
    assert [badge["milestone"] for badge in response.json()["badges"]] == [10, 50]
    assert response.json()["milestones"] == [10, 50, 100]


def test_replaying_an_interaction_over_http_is_idempotent(rig):
    rig.interact("i-1")
    rig.clock.advance(hours=23)
    rig.interact("i-2")

    replay = rig.client.post(
        "/api/v1/streaks/record-activity",
        json={"interaction_id": "i-2", "session_id": "sess-abc"},
        headers=HEADERS,
    )

    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["outcome"] == "idempotent_replay"
    assert replay.json()["streak"]["current_streak_days"] == 2


def test_freeze_endpoint_accepts_an_offered_freeze(rig):
    rig.container.streaks.save(
        StreakRecord(
            user_id=SUBJECT,
            current_streak_days=7,
            longest_streak_days=7,
            last_activity_at=ANCHOR - timedelta(hours=48),
            streak_started_at=ANCHOR - timedelta(days=7),
            freeze_available=True,
            freeze_used_at=None,
            updated_at=ANCHOR - timedelta(hours=48),
        )
    )
    missed = rig.interact("back-after-a-miss")
    assert missed.json()["outcome"] == "reset"
    assert missed.json()["freeze_offer"]["status"] == "offered"

    accepted = rig.client.post("/api/v1/streaks/freeze", headers=HEADERS)

    assert accepted.status_code == 200
    assert accepted.json()["streak"]["current_streak_days"] == 8
    assert accepted.json()["freeze_offer"] is None


def test_freeze_endpoint_refuses_when_no_offer_is_open(rig):
    rig.interact("i-1")
    response = rig.client.post("/api/v1/streaks/freeze", headers=HEADERS)
    assert response.status_code == 409
    assert response.json()["error"] == "freeze_not_available"


def test_weekly_summary_generation_and_listing():
    rig = Rig(now=MONDAY)
    rig.gap_plan.set_suggestion(
        SUBJECT,
        {
            "topic_id": "topic-1",
            "name": "Solicitors Accounts Rules",
            "naric_level": "level_7_plus",
            "course_progress_percent": 30,
        },
    )
    rig.ledger.add_interaction(SUBJECT, MONDAY - timedelta(days=3), "last-week-1", topic="conduct")

    generated = rig.client.post("/api/v1/weekly-summaries/generate", headers=HEADERS)

    assert generated.status_code == 200
    summary = generated.json()["generated"]
    assert summary["week"] == "2026-W11"
    assert summary["topics_covered"] == ["conduct"]
    assert summary["questions_asked"] == 1
    assert summary["suggested_topic"]["naric_level"] == "level_7_plus"
    assert summary["suggested_topic"]["explanation_profile"] == "advanced"
    assert summary["delivery_status"] == "sent"

    listed = rig.client.get("/api/v1/weekly-summaries", headers=HEADERS)
    assert [item["week"] for item in listed.json()["summaries"]] == ["2026-W11"]


# --------------------------------------------------------------------------
# Security
# --------------------------------------------------------------------------
def test_an_unauthenticated_request_is_refused(rig):
    for method, path in [
        ("post", "/api/v1/streaks/record-activity"),
        ("get", "/api/v1/streaks"),
        ("get", "/api/v1/badges"),
        ("post", "/api/v1/streaks/freeze"),
        ("post", "/api/v1/weekly-summaries/generate"),
        ("get", "/api/v1/weekly-summaries"),
    ]:
        call = getattr(rig.client, method)
        response = call(path, json={"interaction_id": "x", "session_id": "s"}) if method == "post" else call(path)
        assert response.status_code == 401, path
        assert response.json()["error"] == "identity_not_resolved"


def test_no_endpoint_accepts_a_user_identifier(rig):
    """Sending one is a visible validation error, not a silent ignore."""
    for field in ("user_id", "learner_id", "account_id", "subject"):
        response = rig.client.post(
            "/api/v1/streaks/record-activity",
            json={"interaction_id": "i-1", "session_id": "s", field: OTHER},
            headers=HEADERS,
        )
        assert response.status_code == 422, field
        assert any(detail["type"] == "extra_forbidden" for detail in response.json()["detail"])


def test_a_learner_cannot_read_or_write_another_learner_state(rig):
    rig.ledger.set_question_count(SUBJECT, 50)
    rig.interact("i-1")
    rig.clock.advance(hours=23)
    rig.interact("i-2")
    assert rig.client.get("/api/v1/streaks", headers=HEADERS).json()["streak"]["current_streak_days"] == 2

    # A different subject sees nothing of the first.
    other = rig.client.get("/api/v1/streaks", headers=OTHER_HEADERS)
    assert other.json()["streak"]["current_streak_days"] == 0
    assert other.json()["badges"] == []
    assert rig.client.get("/api/v1/badges", headers=OTHER_HEADERS).json()["badges"] == []
    assert rig.client.get("/api/v1/weekly-summaries", headers=OTHER_HEADERS).json()["summaries"] == []

    # And cannot act on the first, whatever it puts in the body.
    attempt = rig.client.post(
        "/api/v1/streaks/record-activity",
        json={"interaction_id": "i-3", "session_id": "s", "user_id": SUBJECT},
        headers=OTHER_HEADERS,
    )
    assert attempt.status_code == 422
    assert rig.container.streaks.get(SUBJECT).current_streak_days == 2


def test_there_is_no_path_segment_to_change(rig):
    for path in (
        f"/api/v1/streaks/{SUBJECT}",
        f"/api/v1/badges/{SUBJECT}",
        f"/api/v1/weekly-summaries/{SUBJECT}",
    ):
        assert rig.client.get(path, headers=HEADERS).status_code == 404


@pytest.mark.parametrize(
    "field",
    ["current_streak_days", "longest_streak_days", "milestone", "freeze_available", "freeze_used_at", "badge_id"],
)
def test_owned_fields_cannot_be_supplied_by_a_client(rig, field):
    response = rig.client.post(
        "/api/v1/streaks/record-activity",
        json={"interaction_id": "i-1", "session_id": "s", field: 999},
        headers=HEADERS,
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any(item["type"] == "extra_forbidden" and item["loc"][-1] == field for item in detail)


def test_unknown_fields_are_rejected_on_every_request_body(rig):
    for path in ("/api/v1/streaks/freeze", "/api/v1/weekly-summaries/generate"):
        response = rig.client.post(path, json={"user_id": OTHER}, headers=HEADERS)
        assert response.status_code == 422, path


def test_a_missing_interaction_id_is_a_validation_error(rig):
    response = rig.client.post(
        "/api/v1/streaks/record-activity", json={"session_id": "s"}, headers=HEADERS
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------
# Session identity
# --------------------------------------------------------------------------
def test_a_missing_session_id_is_refused_by_default(rig):
    response = rig.client.post(
        "/api/v1/streaks/record-activity", json={"interaction_id": "i-1"}, headers=HEADERS
    )
    assert response.status_code == 400
    assert response.json()["error"] == "session_id_required"
    assert "does not create one" in response.json()["detail"]


def test_dev_minting_is_available_only_when_explicitly_enabled():
    rig = Rig(ALLOW_DEV_SESSION_MINTING=True)
    response = rig.client.post(
        "/api/v1/streaks/record-activity", json={"interaction_id": "i-1"}, headers=HEADERS
    )
    assert response.status_code == 200
    assert response.json()["session_id_source"] == "dev_minted"
    assert response.json()["session_id"].startswith("dev-minted-session-")


def test_an_opaque_session_id_is_passed_through_untouched(rig):
    response = rig.client.post(
        "/api/v1/streaks/record-activity",
        json={"interaction_id": "i-1", "session_id": "opaque::A7f-9920::whatever"},
        headers=HEADERS,
    )
    assert response.json()["session_id"] == "opaque::A7f-9920::whatever"
    assert response.json()["session_id_source"] == "received"


# --------------------------------------------------------------------------
# Degradation
# --------------------------------------------------------------------------
def test_an_upstream_outage_does_not_fail_the_request_or_the_streak(rig):
    from uc08.adapters.mock.ledger import Fault

    rig.container.streaks.save(
        StreakRecord(
            user_id=SUBJECT,
            current_streak_days=12,
            longest_streak_days=12,
            last_activity_at=ANCHOR - timedelta(days=6),
            streak_started_at=ANCHOR - timedelta(days=18),
            freeze_available=True,
            freeze_used_at=None,
            updated_at=ANCHOR - timedelta(days=6),
        )
    )
    rig.ledger.with_fault(Fault.UNAVAILABLE)

    response = rig.client.post(
        "/api/v1/streaks/record-activity",
        json={"interaction_id": "i-1", "session_id": "s"},
        headers=HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "unchanged_source_degraded"
    assert body["activity_status"] == "unavailable"
    assert body["streak"]["current_streak_days"] == 12


def test_no_response_leaks_an_upstream_detail(rig):
    from uc08.adapters.mock.ledger import Fault

    rig.ledger.with_fault(Fault.UNAVAILABLE)
    rig.gap_plan.with_fault(Fault.UNAVAILABLE)
    body = rig.client.post(
        "/api/v1/streaks/record-activity",
        json={"interaction_id": "i-1", "session_id": "s"},
        headers=HEADERS,
    ).text.lower()

    for token in ("mock", "ledger", "traceback", "http://", "bearer"):
        assert token not in body
