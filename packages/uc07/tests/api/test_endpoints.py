"""API behaviour: envelopes, privacy, strict input, degraded sources."""

from __future__ import annotations

import pytest

from tests.conftest import auth, build_client
from uc07.adapters.mock.scenarios import LEARNER, OTHER_LEARNER


def test_healthz_reports_versions_and_threshold():
    response = build_client().get("/api/v1/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["threshold"] == 10
    assert body["report_version"] and body["analysis_version"]


@pytest.mark.parametrize(
    ("scenario", "completed", "remaining"),
    [("count_0", 0, 10), ("count_5", 5, 5), ("count_9", 9, 1)],
)
def test_below_threshold_returns_progress_not_an_error(scenario, completed, remaining):
    client = build_client(scenario)
    for path in ("/api/v1/gap-report", "/api/v1/gap-report/progress"):
        response = client.get(path, headers=auth())
        assert response.status_code == 200, path
        body = response.json()
        assert body["status"] == "below_threshold"
        assert body["interactions_completed"] == completed
        assert body["threshold"] == 10
        assert body["interactions_remaining"] == remaining
    assert client.get("/api/v1/gap-report", headers=auth()).json()["report"] is None


@pytest.mark.parametrize("scenario", ["count_10", "count_11", "count_50"])
def test_available_report_envelope(scenario):
    response = build_client(scenario).get("/api/v1/gap-report", headers=auth())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "available"
    assert body["interactions_remaining"] == 0
    report = body["report"]
    assert report["threshold"] == 10
    assert report["gaps"]
    for gap in report["gaps"]:
        assert gap["evidence_interaction_ids"] == gap["evidence"]["interaction_ids"]
        assert gap["signals"]
        assert gap["description"]


def test_report_response_never_contains_the_user_id():
    response = build_client("struggle_mixed").get("/api/v1/gap-report", headers=auth())
    assert response.status_code == 200
    assert "user_id" not in response.json()["report"]
    assert LEARNER not in response.text


def test_no_endpoint_accepts_a_user_id_parameter():
    client = build_client("struggle_mixed")
    spec = client.get("/openapi.json").json()
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            for parameter in operation.get("parameters", []):
                assert "user" not in parameter["name"].lower(), (path, method)
            assert "requestBody" not in operation, (path, method)


def test_query_parameters_are_rejected():
    client = build_client("struggle_mixed")
    response = client.get("/api/v1/gap-report?user_id=someone-else", headers=auth())
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "invalid_request"
    assert body["error"]["details"]["rejected_fields"] == ["user_id"]
    assert "report" not in body


def test_unknown_query_parameters_are_rejected_on_progress_too():
    response = build_client("struggle_mixed").get(
        "/api/v1/gap-report/progress?include=everything", headers=auth()
    )
    assert response.status_code == 400
    assert response.json()["error"]["details"]["rejected_fields"] == ["include"]


def test_request_body_is_rejected():
    response = build_client("struggle_mixed").request(
        "GET",
        "/api/v1/gap-report",
        headers=auth(),
        json={"user_id": OTHER_LEARNER},
    )
    assert response.status_code == 400
    assert "body" in response.json()["error"]["details"]["rejected_fields"]


def test_missing_identity_is_rejected():
    response = build_client("struggle_mixed").get("/api/v1/gap-report")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "identity_unresolved"


def test_each_caller_only_ever_sees_their_own_data():
    client = build_client("struggle_mixed")
    mine = client.get("/api/v1/gap-report", headers=auth(LEARNER))
    theirs = client.get("/api/v1/gap-report", headers=auth(OTHER_LEARNER))

    assert mine.status_code == 200
    assert mine.json()["status"] == "available"
    # The other learner has no history in this scenario: they get their own
    # progress state, never the first learner's report.
    assert theirs.status_code == 200
    assert theirs.json()["status"] == "below_threshold"
    assert theirs.json()["report"] is None
    assert mine.json()["report"]["report_id"] not in theirs.text


def test_cross_user_report_access_is_refused_at_the_service_boundary():
    """A repository handing back a foreign report must never be served."""
    from uc07.adapters.persistence import InMemoryGapReportRepository
    from uc07.domain.models import GapReport

    class LeakyRepository(InMemoryGapReportRepository):
        def get_current(self, user_id: str) -> GapReport | None:
            # Deliberately ignores the ownership scope.
            for report in self._current.values():
                return report
            return None

    repository = LeakyRepository()
    client = build_client("struggle_mixed", repository=repository)
    assert client.get("/api/v1/gap-report", headers=auth(LEARNER)).status_code == 200

    response = client.get("/api/v1/gap-report", headers=auth("learner-003"))
    # learner-003 has no history, so they stop at the threshold check.
    assert response.json()["status"] == "below_threshold"

    from uc07.adapters.mock.scenarios import sequence_records
    from uc07.adapters.mock.interaction_log import MockInteractionPayload
    from uc07.adapters.mock import get_scenario
    from dataclasses import replace

    scenario = get_scenario("struggle_mixed")
    foreign = replace(
        scenario,
        user_id="learner-003",
        interactions={
            "learner-003": MockInteractionPayload(records=sequence_records(10, user_id="learner-003"))
        },
    )
    leaky = LeakyRepository()
    other_client = build_client(foreign, repository=leaky)
    assert other_client.get("/api/v1/gap-report", headers=auth("learner-003")).status_code == 200

    # Now the repository holds learner-003's report; a different caller must be
    # refused rather than shown it.
    scenario_two = replace(
        scenario,
        user_id="learner-004",
        interactions={
            "learner-004": MockInteractionPayload(records=sequence_records(10, user_id="learner-004"))
        },
    )
    hostile = build_client(scenario_two, repository=leaky)
    refused = hostile.get("/api/v1/gap-report", headers=auth("learner-004"))
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "forbidden"
    assert "gaps" not in refused.text


def test_interaction_source_failure_returns_a_clear_error_not_an_empty_report():
    for scenario in ("interactions_unavailable", "interactions_timeout", "interactions_invalid"):
        response = build_client(scenario).get("/api/v1/gap-report", headers=auth())
        assert response.status_code == 503, scenario
        body = response.json()
        assert body["error"]["code"] == "interaction_source_unusable"
        assert "gaps" not in response.text
        assert body["error"]["details"]["interaction_source_status"] in {
            "unavailable",
            "invalid",
        }


def test_error_responses_never_leak_provider_names_or_internals():
    response = build_client("interactions_unavailable").get(
        "/api/v1/gap-report", headers=auth()
    )
    text = response.text.lower()
    for forbidden in ("mock", "nexus", "traceback", "exception", "adapter", "provider"):
        assert forbidden not in text


def test_degraded_report_exposes_source_information_explicitly():
    response = build_client("courses_unavailable").get(
        "/api/v1/gap-report", headers=auth()
    )
    report = response.json()["report"]
    assert report["source_statuses"] == {
        "interactions": "available",
        "feedback": "available",
        "profile": "available",
        "courses": "unavailable",
    }
    assert report["recommendations"]["status"] == "unavailable"
    codes = {notice["code"] for notice in report["notices"]}
    assert "recommendations_temporarily_unavailable" in codes
    assert report["gaps"]


def test_response_contains_no_question_text_fields():
    response = build_client("struggle_mixed").get("/api/v1/gap-report", headers=auth())
    text = response.text
    for forbidden in ("question_text", "question", "prompt_text", "comment"):
        assert forbidden not in text


def test_report_is_stable_across_repeated_requests():
    client = build_client("struggle_mixed")
    first = client.get("/api/v1/gap-report", headers=auth()).json()
    second = client.get("/api/v1/gap-report", headers=auth()).json()
    assert first == second
