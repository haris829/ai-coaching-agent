"""NARIC: valid, incomplete, unavailable, invalid, Level 5 default, continue anyway."""

from __future__ import annotations

import pytest

from uc01.application.dto import OpenSessionCommand
from uc01.domain import messages
from uc01.domain.enums import (
    DependencyName,
    DependencyState,
    NaricAssessmentState,
    NaricLevelSource,
    SessionMode,
    SessionStatus,
)
from uc01.domain.models import DependencyStatus, NaricAssessment, UserContext
from uc01.domain.policy import resolve_naric_level

from .conftest import ALICE, BOB, CAROL, auth, scenarios
from .stubs import StubNaricService

AVAILABLE = DependencyStatus(
    dependency=DependencyName.NARIC, state=DependencyState.AVAILABLE
)
UNAVAILABLE = DependencyStatus(
    dependency=DependencyName.NARIC, state=DependencyState.UNAVAILABLE
)
INCOMPLETE = DependencyStatus(
    dependency=DependencyName.NARIC, state=DependencyState.INCOMPLETE
)


# --------------------------------------------------------------------------- #
# Domain policy
# --------------------------------------------------------------------------- #


def test_valid_assessment_uses_the_naric_level_and_naric_source():
    resolution = resolve_naric_level(
        NaricAssessment(state=NaricAssessmentState.COMPLETE, level=8), AVAILABLE
    )
    assert resolution.level == 8
    assert resolution.source is NaricLevelSource.NARIC
    assert resolution.is_fallback is False
    assert resolution.calibration_offer is False
    assert resolution.notice is None


@pytest.mark.parametrize(
    "state",
    [NaricAssessmentState.INCOMPLETE, NaricAssessmentState.CALIBRATING],
)
def test_incomplete_or_calibrating_falls_back_to_level_five(state):
    resolution = resolve_naric_level(
        NaricAssessment(state=state, level=None), INCOMPLETE
    )
    assert resolution.level == 5
    assert resolution.source is NaricLevelSource.DEFAULT
    assert resolution.is_fallback is True
    assert resolution.calibration_offer is True
    assert resolution.notice


def test_unavailable_naric_falls_back_to_level_five():
    resolution = resolve_naric_level(None, UNAVAILABLE)
    assert resolution.level == 5
    assert resolution.source is NaricLevelSource.DEFAULT
    assert resolution.notice == messages.NARIC_UNAVAILABLE_NOTICE


@pytest.mark.parametrize("bad_level", [0, -1, 99, None, True, "8"])
def test_complete_assessment_with_an_unusable_level_is_not_trusted(bad_level):
    resolution = resolve_naric_level(
        NaricAssessment(state=NaricAssessmentState.COMPLETE, level=bad_level), AVAILABLE
    )
    assert resolution.level == 5
    assert resolution.source is NaricLevelSource.DEFAULT


def test_continue_without_calibration_marks_the_source_as_acknowledged():
    resolution = resolve_naric_level(
        None, UNAVAILABLE, continue_without_calibration=True
    )
    assert resolution.level == 5
    assert resolution.source is NaricLevelSource.DEFAULT_USER_ACKNOWLEDGED
    assert resolution.is_fallback is True
    assert resolution.calibration_offer is False
    assert resolution.notice is None


def test_continue_without_calibration_cannot_fake_a_naric_source():
    """The flag never turns a defaulted level into a NARIC-sourced one."""
    resolution = resolve_naric_level(
        NaricAssessment(state=NaricAssessmentState.INCOMPLETE, level=None),
        INCOMPLETE,
        continue_without_calibration=True,
    )
    assert resolution.source is not NaricLevelSource.NARIC


# --------------------------------------------------------------------------- #
# Service / API level
# --------------------------------------------------------------------------- #


def test_session_opens_with_a_real_naric_level(client):
    body = client.post("/api/v1/sessions", headers=auth(ALICE), json={"mode": "free-form"}).json()
    assert body["session"]["naric_level"] == 8
    assert body["session"]["naric_level_source"] == "naric"
    assert body["session"]["explanation_level"] == 8
    assert "NARIC Level 8" in body["greeting"]["text"]


@pytest.mark.parametrize(
    "scenario", ["unavailable", "invalid", "incomplete", "calibrating"]
)
def test_naric_failure_never_blocks_session_creation(client, scenario):
    response = client.post(
        "/api/v1/sessions",
        headers={**auth(), **scenarios(naric=scenario)},
        json={"mode": "free-form"},
    )
    assert response.status_code == 201
    session = response.json()["session"]
    assert session["naric_level"] == 5
    assert session["naric_level_source"] == "default"
    assert session["status"] == SessionStatus.DEGRADED.value


def test_naric_fallback_is_labelled_as_a_default_not_as_naric(client):
    body = client.post(
        "/api/v1/sessions",
        headers={**auth(), **scenarios(naric="unavailable")},
        json={"mode": "free-form"},
    ).json()
    assert body["context"]["naric"]["is_fallback"] is True
    assert body["context"]["naric"]["source"] == "default"
    # The greeting must not claim the level came from NARIC.
    text = body["greeting"]["text"]
    assert "Level 5 by default" in text
    assert "calibrated to your NARIC" not in text


def test_bootstrap_offers_continue_without_calibration(client):
    body = client.get(
        "/api/v1/session-bootstrap", headers={**auth(), **scenarios(naric="unavailable")}
    ).json()
    assert body["naric"]["offer_continue_without_calibration"] is True
    assert body["naric"]["notice"] == messages.NARIC_UNAVAILABLE_NOTICE
    notice = next(
        item for item in body["notices"] if item["code"] == "naric_calibration_unavailable"
    )
    assert notice["action"] == "continue_without_calibration"
    # NARIC problems never disable a mode.
    assert all(
        mode["available"] for mode in body["modes"]
    ), "NARIC must not disable any session mode"


def test_continue_without_calibration_opens_the_session(client):
    body = client.post(
        "/api/v1/sessions",
        headers={**auth(), **scenarios(naric="unavailable")},
        json={"mode": "free-form", "continue_without_calibration": True},
    ).json()
    assert body["session"]["naric_level"] == 5
    assert body["session"]["naric_level_source"] == "default_user_acknowledged"
    assert body["context"]["naric"]["offer_continue_without_calibration"] is False
    assert not any(
        notice["code"] == "naric_calibration_unavailable" for notice in body["notices"]
    )
    assert any(
        notice["code"] == "naric_default_level_applied" for notice in body["notices"]
    )


def test_calibration_is_never_required_before_coaching(client):
    """Carol's assessment is incomplete; every mode she has access to still opens."""
    free_form = client.post("/api/v1/sessions", headers=auth(CAROL), json={"mode": "free-form"})
    assert free_form.status_code == 201
    case_linked = client.post(
        "/api/v1/sessions", headers=auth(CAROL), json={"mode": "case-linked", "case_id": "case_beta"}
    )
    assert case_linked.status_code == 201
    assert case_linked.json()["session"]["naric_level"] == 5


def test_bob_calibrating_state_is_reported_as_a_fallback(client):
    body = client.get("/api/v1/session-bootstrap", headers=auth(BOB)).json()
    assert body["naric"]["source"] == "default"
    assert body["naric"]["notice"] == messages.NARIC_CALIBRATING_NOTICE


def test_naric_level_is_recorded_even_when_the_open_attempt_is_rejected(
    build_service, memory_repository
):
    """A rejected attempt still records the level that would have applied."""
    service = build_service(naric=StubNaricService(unavailable=True))
    user = UserContext(user_id="u_test")
    with pytest.raises(Exception):
        service.open_session(
            user,
            OpenSessionCommand(mode=SessionMode.COURSE_LINKED, course_id="nope", lesson_id="nope"),
        )
    records = memory_repository.list_for_user("u_test")
    assert len(records) == 1
    assert records[0].naric_level == 5
    assert records[0].naric_level_source is NaricLevelSource.DEFAULT
    assert records[0].status is SessionStatus.FAILED
