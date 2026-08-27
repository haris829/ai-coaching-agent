"""Integration swap proof.

The UNMODIFIED service, API, domain models and persistence run against a
deliberately different upstream shape (the fictional "Nexus LMS"), selected only
through the provider registry and configuration. The resulting report is
identical to the one produced from the mock source, because the difference lives
entirely inside the adapters.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FIXED_NOW, auth, build_harness
from uc07.adapters.clock import FixedClock
from uc07.adapters.foreign import EXTERNAL_LEARNER_ID
from uc07.adapters.persistence import InMemoryGapReportRepository
from uc07.api.app import create_app
from uc07.application.config import Settings
from uc07.composition import Container, build_container

FOREIGN_SETTINGS = dict(
    interaction_log_provider="foreign",
    feedback_provider="foreign",
    profile_provider="foreign",
    courses_provider="foreign",
    current_user_provider="header",
)


def foreign_container(clock: FixedClock | None = None) -> Container:
    return build_container(
        Settings(**FOREIGN_SETTINGS),
        repository=InMemoryGapReportRepository(),
        clock=clock or FixedClock(FIXED_NOW),
    )


def test_report_from_the_foreign_source_is_identical_to_the_mock_report():
    mock_report = build_harness("struggle_mixed").service.current_report(
        EXTERNAL_LEARNER_ID
    ).report
    foreign_report = foreign_container().service.current_report(
        EXTERNAL_LEARNER_ID
    ).report

    assert mock_report is not None and foreign_report is not None
    assert foreign_report.content_fingerprint == mock_report.content_fingerprint
    assert foreign_report.report_id == mock_report.report_id
    assert foreign_report == mock_report


def test_foreign_source_produces_the_same_evidence_identifiers():
    report = foreign_container().service.current_report(EXTERNAL_LEARNER_ID).report
    assert report is not None
    assert [
        (gap.gap_type.value, gap.topic_tag, list(gap.evidence_interaction_ids))
        for gap in report.gaps
    ] == [
        ("struggle", "contract_formation", ["interaction-101", "interaction-103"]),
        ("struggle", "land_registration", ["interaction-301"]),
        ("struggle", "negligence", ["interaction-202", "interaction-203"]),
        ("unexplored", "commercial_drafting", []),
        ("unexplored", "data_protection", []),
    ]


def test_foreign_values_are_normalised_into_platform_types():
    container = foreign_container()
    records = container.service._interactions.for_user(EXTERNAL_LEARNER_ID)
    first = records[0]
    assert first.naric_level.value == "LEVEL_6"  # from "EQF-6"
    assert first.rating_state.value == "rated"  # from "COMPLETE"
    assert first.question_class == "concept"  # from "CONCEPT"
    assert first.asked_at == datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)  # epoch ms

    feedback = container.service._feedback.for_interactions(["interaction-101"])
    assert feedback[0].rating.value == "down"  # from "NEGATIVE"

    profile = container.service._profiles.get_profile(EXTERNAL_LEARNER_ID)
    assert profile.speciality_status.value == "available"  # from "FULL"
    assert profile.naric_level_source.value == "retrieved"  # from "LOOKUP"


def test_the_unmodified_api_serves_the_foreign_source():
    client = TestClient(create_app(foreign_container()), raise_server_exceptions=False)
    response = client.get("/api/v1/gap-report", headers=auth(EXTERNAL_LEARNER_ID))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "available"
    assert len(body["report"]["gaps"]) == 5
    assert body["report"]["source_interaction_count"] == 14


def test_no_nexus_vocabulary_reaches_the_api_response():
    client = TestClient(create_app(foreign_container()), raise_server_exceptions=False)
    text = client.get("/api/v1/gap-report", headers=auth(EXTERNAL_LEARNER_ID)).text
    for token in (
        "entryRef",
        "occurredAtEpochMs",
        "eqfBand",
        "programmeRef",
        "moduleRef",
        "suggestionFeed",
        "Nexus",
        "nexus",
    ):
        assert token not in text


def test_the_swap_touches_only_adapters_registry_and_configuration():
    """These files must stay untouched when a new upstream source is integrated."""
    from pathlib import Path

    untouched = [
        "uc07/domain/models.py",
        "uc07/domain/counting.py",
        "uc07/domain/enums.py",
        "uc07/domain/errors.py",
        "uc07/application/service.py",
        "uc07/application/signals.py",
        "uc07/application/aggregation.py",
        "uc07/application/report_builder.py",
        "uc07/application/recommendations.py",
        "uc07/application/unexplored.py",
        "uc07/api/routes.py",
        "uc07/api/schemas.py",
        "uc07/api/app.py",
        "uc07/adapters/mock/interaction_log.py",
        "uc07/adapters/mock/feedback.py",
        "uc07/adapters/mock/profile.py",
        "uc07/adapters/mock/courses.py",
        "uc07/adapters/persistence/in_memory.py",
    ]
    nexus_vocabulary = (
        "nexus",
        "entryref",
        "occurredatepochms",
        "eqfband",
        "programmeref",
        "moduleref",
        "suggestionfeed",
        "focusareas",
        "verdictlifecycle",
    )
    for relative in untouched:
        text = Path(relative).read_text(encoding="utf-8").lower()
        assert "adapters.foreign" not in text, relative
        assert "adapters import foreign" not in text, relative
        for token in nexus_vocabulary:
            assert token not in text, f"{relative} knows '{token}'"

    # The only files that know the foreign source exists: its own adapter module
    # and the composition root's single registry line per port.
    knowing = {
        path.as_posix()
        for path in Path("uc07").rglob("*.py")
        if "nexus" in path.read_text(encoding="utf-8").lower()
    }
    assert knowing == {
        "uc07/adapters/foreign/adapters.py",
        "uc07/adapters/foreign/payload.py",
        "uc07/adapters/foreign/__init__.py",
        "uc07/composition.py",
    }


def test_foreign_source_is_deterministic_too():
    first = foreign_container().service.current_report(EXTERNAL_LEARNER_ID).report
    second = foreign_container().service.current_report(EXTERNAL_LEARNER_ID).report
    assert first == second


@pytest.mark.parametrize("port", ["interaction_log", "feedback", "profile", "courses"])
def test_each_port_can_be_swapped_independently_without_touching_the_others(port):
    overrides = {f"{port}_provider": "foreign"}
    container = build_container(
        Settings(mock_scenario="struggle_mixed", **overrides),
        repository=InMemoryGapReportRepository(),
        clock=FixedClock(FIXED_NOW),
    )
    report = container.service.current_report(EXTERNAL_LEARNER_ID).report
    assert report is not None
    # Mixed sources describe the same learner, so the report is unchanged.
    expected = build_harness("struggle_mixed").service.current_report(
        EXTERNAL_LEARNER_ID
    ).report
    assert report == expected
