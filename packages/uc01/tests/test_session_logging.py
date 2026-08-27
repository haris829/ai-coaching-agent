"""Session record creation, including partial / failed initialisation.

The rule under test: **a record exists for every open attempt**, with enough information
to diagnose a partial initialisation, and with the correct status.
"""

from __future__ import annotations

import pytest

from uc01.application.dto import OpenSessionCommand
from uc01.domain.enums import (
    DependencyName,
    NaricLevelSource,
    SessionEventType,
    SessionMode,
    SessionStatus,
)
from uc01.domain.errors import SessionInitializationError
from uc01.domain.models import UserContext

from .conftest import BOB, auth, scenarios
from .stubs import (
    ExplodingGreetingGenerator,
    StubCoursesService,
    StubNaricService,
    StubProfileService,
)

USER = UserContext(user_id="u_test")


# --------------------------------------------------------------------------- #
# Required record fields
# --------------------------------------------------------------------------- #


def test_normal_session_is_logged_with_every_required_field(client, repository):
    body = client.post(
        "/api/v1/sessions",
        headers=auth(),
        json={"mode": "course-linked", "course_id": "crs_contract_law", "lesson_id": "lsn_terms"},
    ).json()

    record = repository.get(body["session"]["session_id"])
    assert record is not None
    # session_id / user_id / session_type / linked_resource / timestamp / naric_level
    assert record.session_id == body["session"]["session_id"]
    assert record.user_id == "u_alice"
    assert record.session_type is SessionMode.COURSE_LINKED
    assert record.linked_resource is not None
    assert record.linked_resource.resource_id == "crs_contract_law"
    assert record.linked_resource.secondary_id == "lsn_terms"
    assert record.timestamp is not None
    assert record.naric_level == 8
    assert record.naric_level_source is NaricLevelSource.NARIC
    assert record.status is SessionStatus.ACTIVE


def test_free_form_session_records_no_linked_resource(client, repository):
    body = client.post("/api/v1/sessions", headers=auth(), json={"mode": "free-form"}).json()
    record = repository.get(body["session"]["session_id"])
    assert record.linked_resource is None
    assert record.session_type is SessionMode.FREE_FORM


# --------------------------------------------------------------------------- #
# Partial / degraded / failed
# --------------------------------------------------------------------------- #


def test_partial_session_is_logged_as_degraded_with_the_failing_dependency(client, repository):
    body = client.post(
        "/api/v1/sessions",
        headers={**auth(), **scenarios(profile="unavailable", naric="unavailable")},
        json={"mode": "free-form"},
    ).json()
    record = repository.get(body["session"]["session_id"])
    assert record.status is SessionStatus.DEGRADED
    assert set(record.degraded_dependencies) == {DependencyName.PROFILE, DependencyName.NARIC}
    # Enough information to diagnose it.
    detail = record.diagnostics["dependencies"]["profile"]
    assert detail["state"] == "unavailable"
    assert detail["technical_detail"]


def test_dependency_failure_still_creates_a_session_record(client, repository):
    """Courses Agent down + course-linked requested: the attempt is rejected but kept."""
    response = client.post(
        "/api/v1/sessions",
        headers={**auth(), **scenarios(courses="unavailable")},
        json={"mode": "course-linked", "course_id": "crs_contract_law", "lesson_id": "lsn_offer"},
    )
    assert response.status_code == 409
    session_id = response.json()["recovery"]["session_id"]

    record = repository.get(session_id)
    assert record is not None
    assert record.status is SessionStatus.FAILED
    assert record.failure_code == "session_mode_unavailable"
    assert record.requested_mode is SessionMode.COURSE_LINKED
    assert DependencyName.COURSES in record.degraded_dependencies
    assert record.diagnostics["failure"]["technical_detail"]
    # The level that would have applied is still recorded.
    assert record.naric_level == 8


def test_rejected_selection_is_also_recorded(client, repository):
    response = client.post(
        "/api/v1/sessions",
        headers=auth(),
        json={"mode": "course-linked", "course_id": "crs_tort", "lesson_id": "lsn_duty"},
    )
    assert response.status_code == 403
    records = repository.list_for_user("u_alice")
    assert len(records) == 1
    assert records[0].status is SessionStatus.FAILED
    assert records[0].failure_code == "selection_not_accessible"
    assert records[0].diagnostics["requested"]["course_id"] == "crs_tort"


def test_downgraded_session_records_the_downgrade(client, repository):
    body = client.post(
        "/api/v1/sessions",
        headers={**auth(), **scenarios(courses="unavailable")},
        json={
            "mode": "course-linked",
            "course_id": "crs_contract_law",
            "lesson_id": "lsn_offer",
            "on_dependency_failure": "fallback_free_form",
        },
    ).json()
    record = repository.get(body["session"]["session_id"])
    assert record.session_type is SessionMode.FREE_FORM
    assert record.requested_mode is SessionMode.COURSE_LINKED
    assert record.downgraded_from is SessionMode.COURSE_LINKED
    assert record.status is SessionStatus.DEGRADED


def test_every_status_in_the_model_is_reachable(client, repository):
    """initializing -> active / degraded / failed are all produced by real flows."""
    client.post("/api/v1/sessions", headers=auth(), json={"mode": "free-form"})
    client.post(
        "/api/v1/sessions",
        headers={**auth(), **scenarios(profile="unavailable")},
        json={"mode": "free-form"},
    )
    client.post("/api/v1/sessions", headers=auth(), json={"mode": "case-linked", "case_id": "nope"})
    statuses = {record.status for record in repository.list_for_user("u_alice")}
    assert statuses == {SessionStatus.ACTIVE, SessionStatus.DEGRADED, SessionStatus.FAILED}


# --------------------------------------------------------------------------- #
# Events (the forward-compatibility seam)
# --------------------------------------------------------------------------- #


def test_events_record_the_initialisation_lifecycle(client, repository):
    body = client.post(
        "/api/v1/sessions",
        headers={**auth(), **scenarios(naric="unavailable")},
        json={"mode": "free-form"},
    ).json()
    events = repository.list_events(body["session"]["session_id"])
    types = [event.event_type for event in events]
    assert types[0] == SessionEventType.SESSION_INITIALIZING.value
    assert SessionEventType.DEPENDENCY_DEGRADED.value in types
    assert types[-1] == SessionEventType.SESSION_OPENED.value

    opened = events[-1]
    # The fields UC-07 / UC-10 will eventually need are already emitted.
    assert opened.payload["naric_level"] == 5
    assert opened.payload["naric_level_source"] == "default"
    assert opened.payload["session_type"] == "free-form"


def test_failed_attempt_emits_a_failure_event(client, repository):
    response = client.post(
        "/api/v1/sessions", headers=auth(BOB), json={"mode": "case-linked", "case_id": "case_alpha"}
    )
    session_id = response.json()["recovery"]["session_id"]
    types = [event.event_type for event in repository.list_events(session_id)]
    assert SessionEventType.SESSION_FAILED.value in types


# --------------------------------------------------------------------------- #
# Robustness of the record-first guarantee
# --------------------------------------------------------------------------- #


def test_record_is_created_before_any_dependency_is_contacted(build_service, memory_repository):
    """Every dependency is down: the record still exists and the session still opens."""
    service = build_service(
        naric=StubNaricService(unavailable=True),
        profile=StubProfileService(unavailable=True),
        courses=StubCoursesService(unavailable=True),
    )
    result = service.open_session(USER, OpenSessionCommand(mode=SessionMode.FREE_FORM))
    stored = memory_repository.get(result.record.session_id)
    assert stored is not None
    assert stored.status is SessionStatus.DEGRADED


def test_unexpected_error_still_leaves_a_failed_record(build_service, memory_repository):
    """An exploding greeting layer must not lose the session."""
    service = build_service(greeting=ExplodingGreetingGenerator())
    result = service.open_session(USER, OpenSessionCommand(mode=SessionMode.FREE_FORM))
    # The greeting failure is contained: the session still opens with a safe greeting.
    assert result.greeting.variant == "generic.fallback"
    assert memory_repository.get(result.record.session_id) is not None


def test_persistence_failure_is_reported_safely(build_service):
    class BrokenRepository:
        def create(self, record):
            raise RuntimeError("disk on fire")

        def update(self, record):
            raise RuntimeError("disk on fire")

        def get(self, session_id):
            return None

        def list_for_user(self, user_id, limit=50):
            return ()

        def append_event(self, event):
            raise RuntimeError("disk on fire")

        def list_events(self, session_id):
            return ()

    service = build_service(repository=BrokenRepository())
    with pytest.raises(SessionInitializationError) as excinfo:
        service.open_session(USER, OpenSessionCommand(mode=SessionMode.FREE_FORM))
    # The user-facing message carries no technical detail.
    assert "disk on fire" not in excinfo.value.user_message
    assert "disk on fire" in (excinfo.value.technical_detail or "")


def test_sqlite_records_round_trip(sqlite_repository, build_service):
    service = build_service(repository=sqlite_repository)
    result = service.open_session(
        USER,
        OpenSessionCommand(
            mode=SessionMode.COURSE_LINKED,
            course_id="stub_course_1",
            lesson_id="stub_lesson_2",
        ),
    )
    reloaded = sqlite_repository.get(result.record.session_id)
    assert reloaded.session_type is SessionMode.COURSE_LINKED
    assert reloaded.linked_resource.secondary_label == "Stub Lesson Two"
    assert reloaded.naric_level == 7
    assert reloaded.naric_level_source is NaricLevelSource.NARIC
    assert reloaded.status is SessionStatus.ACTIVE
    assert [event.event_type for event in sqlite_repository.list_events(result.record.session_id)]
