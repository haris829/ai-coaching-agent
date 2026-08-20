"""API tests (spec sections 19, 23, 24, 25).

Exercises the HTTP surface: status codes, envelopes, headers, authentication and
the error contract. Business behaviour is covered by the service-level tests;
what matters here is that the routes stay thin and the contract holds.
"""

from __future__ import annotations

import csv
import io

import pytest

from app.modules.analytics.api.deps import build_container
from app.modules.analytics.repositories.in_memory import InMemoryReviewRepository
from tests.analytics.world import admin_auth_headers, build_analytics_app

from .conftest import ADMIN_ID, AUTH_HEADERS
from .doubles import FailingAnalyticsRepository

#: UC-10's services are asynchronous, and this repository drives async tests with anyio
#: — the plugin that arrives with starlette — exactly as UC-07, UC-08 and UC-09 do.
pytestmark = pytest.mark.anyio

H = AUTH_HEADERS


class TestAuthentication:
    def test_missing_key_is_rejected(self, client, api):
        response = client.get(f"{api}/analytics/overall")

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"

    def test_unknown_key_is_rejected(self, client, api):
        response = client.get(f"{api}/analytics/overall", headers={"Authorization": "Bearer wrong"})

        assert response.status_code == 401

    def test_bearer_token_is_accepted(self, client, api):
        response = client.get(
            f"{api}/analytics/overall", headers=admin_auth_headers()
        )

        assert response.status_code == 200

    def test_every_data_endpoint_requires_authentication(self, client, api):
        for path in (
            f"{api}/analytics/overall",
            f"{api}/analytics/questions",
            f"{api}/analytics/questions/question-1",
            f"{api}/analytics/questions/flagged",
            f"{api}/analytics/exports/questions.csv",
            f"{api}/analytics/review/actions",
            f"{api}/analytics/config",
        ):
            assert client.get(path).status_code == 401, path

        assert client.post(f"{api}/analytics/questions/flags/evaluate").status_code == 401
        assert (
            client.post(
                f"{api}/analytics/review/actions", json={"question_id": "q", "action": "NO_CHANGE"}
            ).status_code
            == 401
        )

    def test_health_does_not_require_authentication(self, client):
        """One readiness endpoint for the whole application, and analytics is listed on it.

        UC-10 served its own ``/health``. The merged application has ``/api/health``, which reports
        every capability and the database they share — so the assertion moves rather than
        disappearing.
        """
        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert "UC-10 Analytics & Reporting" in response.json()["modules"]

    def test_error_body_does_not_reveal_whether_the_key_exists(self, client, api):
        missing = client.get(f"{api}/analytics/overall").json()
        wrong = client.get(f"{api}/analytics/overall", headers={"Authorization": "Bearer nope"}).json()

        assert missing["error"]["code"] == wrong["error"]["code"]

    def test_api_key_is_never_echoed(self, client, api):
        response = client.get(f"{api}/analytics/overall", headers=H)

        assert "test-api-key" not in response.text


class TestOverallEndpoint:
    def test_returns_the_dashboard_metrics(self, client, api):
        body = client.get(f"{api}/analytics/overall", headers=H).json()

        assert body["scope"] == "PLATFORM"
        assert body["attempt_volume"] == 5
        assert body["average_score"] == 63.33
        assert body["pass_rate"] == 66.67
        assert body["completion_rate"] == 60.0
        assert body["data_state"] == "OK"
        assert body["calculated_at"] == "2026-03-01T12:00:00Z"

    def test_course_scoped_path(self, client, api):
        body = client.get(f"{api}/analytics/courses/course-1/overall", headers=H).json()

        assert body["scope"] == "COURSE"
        assert body["course_id"] == "course-1"
        assert body["attempt_volume"] == 4

    def test_query_filters_are_applied(self, client, api):
        body = client.get(
            f"{api}/analytics/overall",
            params={"cohort_id": "cohort-b", "assessment_type": "FORMAL_ASSESSMENT"},
            headers=H,
        ).json()

        assert body["attempt_volume"] == 2
        assert body["filters"]["cohort_id"] == "cohort-b"

    def test_empty_state_returns_200_with_nulls(self, client, api):
        body = client.get(
            f"{api}/analytics/overall", params={"course_id": "missing"}, headers=H
        ).json()

        assert body["data_state"] == "NO_ATTEMPTS"
        assert body["average_score"] is None
        assert body["attempt_volume"] == 0

    def test_learner_identifiers_are_never_returned(self, client, api):
        text = client.get(f"{api}/analytics/overall", headers=H).text

        assert "learner_id" not in text
        assert "l1" not in text.replace("filters", "")
        assert "unique_learners" in text  # aggregate count only


class TestQuestionEndpoints:
    def test_list_returns_paged_questions(self, client, api):
        body = client.get(f"{api}/analytics/questions", headers=H).json()

        assert [q["question_id"] for q in body["items"]] == ["question-1", "question-2"]
        assert body["page"]["total"] == 2
        assert body["calculated_at"].startswith("2026-03-01")

    def test_pagination_and_sorting_parameters(self, client, api):
        body = client.get(
            f"{api}/analytics/questions",
            params={"limit": 1, "offset": 0, "sort_by": "accuracy_percentage", "direction": "desc"},
            headers=H,
        ).json()

        assert body["items"][0]["question_id"] == "question-2"
        assert body["page"]["limit"] == 1

    def test_single_question(self, client, api):
        body = client.get(f"{api}/analytics/questions/question-1", headers=H).json()

        assert body["question"]["question_id"] == "question-1"
        assert body["question"]["accuracy_percentage"] == 25.0
        assert body["question"]["most_frequent_wrong_answer"]["answer"] == "B"

    def test_unknown_question_returns_404_with_the_standard_envelope(self, client, api):
        response = client.get(f"{api}/analytics/questions/nope", headers=H)

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
        assert response.json()["error"]["requestId"]

    def test_flagged_path_is_not_read_as_a_question_id(self, client, api):
        response = client.get(f"{api}/analytics/questions/flagged", headers=H)

        assert response.status_code == 200
        assert "items" in response.json()
        assert "threshold_used" in response.json()

    def test_flag_evaluation_persists_and_reports(self, client, api, review_store):
        body = client.post(f"{api}/analytics/questions/flags/evaluate", headers=H).json()

        assert body["newly_flagged"] == ["question-1"]
        assert body["threshold_used"] == 40.0
        assert review_store.flags_snapshot()["question-1"].status.value == "FLAGGED"

    def test_candidates_are_visible_without_persisting(self, client, api, review_store):
        body = client.get(
            f"{api}/analytics/questions/flagged",
            params={"include_candidates": "true"},
            headers=H,
        ).json()

        assert [q["question_id"] for q in body["items"]] == ["question-1"]
        assert body["includes_unpersisted_candidates"] is True
        assert review_store.flags_snapshot() == {}


class TestExportEndpoints:
    @pytest.mark.parametrize(
        "path", ["overall.csv", "questions.csv", "flagged-questions.csv"]
    )
    def test_exports_are_served_as_csv_attachments(self, client, api, path):
        response = client.get(f"{api}/analytics/exports/{path}", headers=H)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert path.replace(".csv", "") in response.headers["content-disposition"] or "uc10-" in response.headers["content-disposition"]

    def test_data_state_travels_in_headers(self, client, api):
        populated = client.get(f"{api}/analytics/exports/questions.csv", headers=H)
        empty = client.get(
            f"{api}/analytics/exports/questions.csv",
            params={"course_id": "missing"},
            headers=H,
        )

        assert populated.headers["X-Analytics-Data-State"] == "OK"
        assert populated.headers["X-Analytics-Row-Count"] == "2"
        assert empty.headers["X-Analytics-Data-State"] == "NO_ATTEMPTS"
        assert empty.headers["X-Analytics-Row-Count"] == "0"
        assert populated.headers["X-Analytics-Calculated-At"].startswith("2026-03-01")

    def test_exported_body_parses_and_matches_the_json_api(self, client, api):
        api_body = client.get(f"{api}/analytics/questions", headers=H).json()
        csv_text = client.get(f"{api}/analytics/exports/questions.csv", headers=H).text

        rows = list(csv.DictReader(io.StringIO(csv_text)))

        assert len(rows) == len(api_body["items"])
        # strict=True: the lengths are asserted equal on the line above, and this is the test that
        # a CSV export matches the dashboard row for row — a silent truncation would defeat it.
        for row, item in zip(rows, api_body["items"], strict=True):
            assert row["question_id"] == item["question_id"]
            assert row["attempt_count"] == str(item["attempt_count"])

    def test_export_honours_filters(self, client, api):
        text = client.get(
            f"{api}/analytics/exports/questions.csv",
            params={"course_id": "course-2"},
            headers=H,
        ).text

        rows = list(csv.DictReader(io.StringIO(text)))
        assert [row["question_id"] for row in rows] == ["question-1"]


class TestReviewEndpoints:
    def test_recording_an_action_returns_201(self, client, api, review_store):
        response = client.post(
            f"{api}/analytics/review/actions",
            headers=H,
            json={"question_id": "question-1", "action": "QUESTION_UPDATED", "note": "reworded"},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["action"]["admin_id"] == ADMIN_ID
        assert body["action"]["action"] == "QUESTION_UPDATED"
        assert body["recorded_at"].startswith("2026-03-01")

    def test_action_resolves_an_existing_flag(self, client, api, review_store):
        client.post(f"{api}/analytics/questions/flags/evaluate", headers=H)

        body = client.post(
            f"{api}/analytics/review/actions",
            headers=H,
            json={"question_id": "question-1", "action": "NO_CHANGE"},
        ).json()

        assert body["flag"]["status"] == "RESOLVED"
        assert body["flag"]["resolved_by"] == ADMIN_ID

    def test_identity_mismatch_is_forbidden(self, client, api):
        response = client.post(
            f"{api}/analytics/review/actions",
            headers=H,
            json={"question_id": "q1", "action": "NO_CHANGE", "admin_id": "someone-else"},
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    def test_invalid_action_type_is_a_validation_error(self, client, api):
        response = client.post(
            f"{api}/analytics/review/actions",
            headers=H,
            json={"question_id": "q1", "action": "DELETE_EVERYTHING"},
        )

        # 400 and BAD_REQUEST in the merged envelope: an unrecognised enum value in a body is a
        # malformed request, and the application distinguishes that from "understood but invalid".
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "BAD_REQUEST"

    def test_unknown_body_field_is_rejected(self, client, api):
        response = client.post(
            f"{api}/analytics/review/actions",
            headers=H,
            json={"question_id": "q1", "action": "NO_CHANGE", "score_override": 100},
        )

        # 400, not 422: the merged application distinguishes "this request is malformed" from
        # "what it describes is not valid", and an unknown body field is the former. UC-10 used one
        # code for both; the envelope is now shared, so the distinction is too.
        assert response.status_code == 400

    def test_retired_question_conflicts(self, client, api, review_store):
        client.post(
            f"{api}/analytics/review/actions",
            headers=H,
            json={"question_id": "question-1", "action": "QUESTION_RETIRED"},
        )

        response = client.post(
            f"{api}/analytics/review/actions",
            headers=H,
            json={"question_id": "question-1", "action": "NO_CHANGE"},
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "REVIEW_CONFLICT"

    def test_history_and_audit_log(self, client, api):
        client.post(
            f"{api}/analytics/review/actions",
            headers=H,
            json={"question_id": "question-1", "action": "NO_CHANGE"},
        )

        history = client.get(f"{api}/analytics/review/questions/question-1/history", headers=H).json()
        audit = client.get(f"{api}/analytics/review/actions", headers=H).json()

        assert history["total"] == 1
        assert history["actions"][0]["action"] == "NO_CHANGE"
        assert audit["total"] == 1
        assert audit["items"][0]["question_id"] == "question-1"


class TestConfigEndpoints:
    def test_effective_configuration_hides_secrets(self, client, api):
        body = client.get(f"{api}/analytics/config", headers=H).json()

        assert "admin_api_keys" not in body["effective"]
        # ``admin_api_keys_configured`` is gone with the key map itself. What matters is that the
        # payload still carries the tunables an administrator is about to change, and no credential.
        assert body["effective"]["flag_wrong_answer_rate_threshold"] is not None
        assert not any("key" in name.lower() for name in body["effective"])
        assert "test-api-key" not in str(body)

    def test_validation_reports_errors(self, client, api):
        body = client.post(
            f"{api}/analytics/config/validate", headers=H, json={"flag_wrong_answer_rate_threshold": 150}
        ).json()

        assert body["valid"] is False
        assert body["issues"][0]["severity"] == "ERROR"

    def test_validation_requires_confirmation_for_dangerous_values(self, client, api):
        body = client.post(
            f"{api}/analytics/config/validate", headers=H, json={"flag_wrong_answer_rate_threshold": 1}
        ).json()

        assert body["valid"] is False
        assert body["requires_confirmation"] is True

    def test_confirmation_accepts_dangerous_values(self, client, api):
        body = client.post(
            f"{api}/analytics/config/validate",
            params={"confirm_dangerous": "true"},
            headers=H,
            json={"flag_wrong_answer_rate_threshold": 1},
        ).json()

        assert body["valid"] is True

    def test_validation_does_not_change_the_running_configuration(self, client, api):
        client.post(
            f"{api}/analytics/config/validate", headers=H, json={"flag_wrong_answer_rate_threshold": 90}
        )

        assert client.get(f"{api}/analytics/config", headers=H).json()["effective"][
            "flag_wrong_answer_rate_threshold"
        ] == 40.0


class TestErrorContract:
    def test_invalid_date_range_is_a_structured_422(self, client, api):
        response = client.get(
            f"{api}/analytics/overall",
            params={"start_date": "2026-02-01T00:00:00Z", "end_date": "2026-01-01T00:00:00Z"},
            headers=H,
        )

        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "INVALID_FILTER"
        assert "start_date" in body["error"]["details"]

    def test_malformed_query_parameter_is_a_structured_error(self, client, api):
        response = client.get(
            f"{api}/analytics/overall", params={"assessment_type": "NOT_A_TYPE"}, headers=H
        )

        # 400 in the merged envelope, and still structured: the point of the test is that a bad
        # parameter names the field it objected to rather than returning a bare failure.
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "BAD_REQUEST"
        assert response.json()["error"]["details"]

    def test_provider_outage_is_a_503_without_internals(self, settings, clock, review_store):
        app = build_analytics_app(
            build_container(
                analytics_repository=FailingAnalyticsRepository(),
                review_repository=InMemoryReviewRepository(review_store),
                settings=settings,
                clock=clock,
            )
        )
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as failing_client:
            response = failing_client.get("/api/admin/analytics/overall", headers=H)

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DATA_PROVIDER_UNAVAILABLE"
        for leak in ("psycopg2", "db-prod-01", "Traceback"):
            assert leak not in response.text

    def test_every_error_carries_a_request_id(self, client, api):
        response = client.get(f"{api}/analytics/questions/unknown", headers=H)

        assert response.json()["error"]["requestId"]
        assert response.headers["X-Request-ID"]

    def test_supplied_request_id_is_echoed(self, client, api):
        response = client.get(
            f"{api}/analytics/overall", headers={**H, "X-Request-ID": "trace-123"}
        )

        assert response.headers["X-Request-ID"] == "trace-123"


class TestOpenApiContract:
    def test_all_expected_endpoints_are_published(self, app, api):
        """Every UC-10 endpoint, and nothing UC-10 does not own.

        Scoped to the analytics prefix: standalone this could compare against the whole document,
        because the document held nothing else. The merged one also holds UC-01's through UC-09's
        routes, and asserting their absence here would make this test fail every time another
        capability gained an endpoint.
        """
        paths = {
            path
            for path in app.openapi()["paths"]
            if path.startswith(f"{api}/analytics")
        }

        assert paths == {
            f"{api}/analytics/overall",
            f"{api}/analytics/courses/{{course_id}}/overall",
            f"{api}/analytics/questions",
            f"{api}/analytics/questions/{{question_id}}",
            f"{api}/analytics/questions/flagged",
            f"{api}/analytics/questions/flags/evaluate",
            f"{api}/analytics/exports/overall.csv",
            f"{api}/analytics/exports/questions.csv",
            f"{api}/analytics/exports/flagged-questions.csv",
            f"{api}/analytics/review/actions",
            f"{api}/analytics/review/questions/{{question_id}}/history",
            f"{api}/analytics/config",
            f"{api}/analytics/config/validate",
        }

    def test_schema_documents_the_empty_state(self, app):
        schema = app.openapi()["components"]["schemas"]["OverallAnalytics"]

        assert "NO_ATTEMPTS" in str(schema)

    def test_no_answer_key_field_is_exposed_anywhere_in_uc10s_contract(self, app, api):
        """UC-10's own schemas expose no answer key and no learner identity.

        Scoped to the schemas UC-10's endpoints actually reference. Standalone this could scan the
        whole component set; the merged document also contains UC-09's ``FormalAttemptModel``,
        which legitimately carries a ``learner_id`` because a supervised sitting belongs to a named
        learner. Analytics is aggregate — it must never expose one — and that is what is asserted.
        """
        document = app.openapi()
        analytics_paths = {
            path: spec
            for path, spec in document["paths"].items()
            if path.startswith(f"{api}/analytics")
        }
        assert analytics_paths, "the scan must actually find UC-10's endpoints"

        # Collect the schema names UC-10's own responses reference, then read those schemas.
        referenced: set[str] = set()

        def collect(node) -> None:
            if isinstance(node, dict):
                ref = node.get("$ref")
                if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                    referenced.add(ref.rsplit("/", 1)[-1])
                for value in node.values():
                    collect(value)
            elif isinstance(node, list):
                for item in node:
                    collect(item)

        collect(analytics_paths)
        schemas = document["components"]["schemas"]
        # Follow one level of nesting, which is where the record models sit.
        for name in list(referenced):
            collect(schemas.get(name, {}))

        rendered = str({name: schemas[name] for name in referenced if name in schemas})
        for forbidden in ("correct_answer", "answer_key", "learner_id", "learner_email"):
            assert forbidden not in rendered, forbidden


class TestRouteThinness:
    def test_routes_contain_no_business_logic(self):
        """Routes must delegate: no arithmetic, no filtering, no branching on data."""
        import pathlib
        import re

        route_dir = pathlib.Path(__file__).resolve().parents[1] / "uc10_analytics" / "api"
        banned = re.compile(r"\b(sum|len)\(|/ 100|\* 100|sorted\(|\.append\(")
        offenders = []
        for path in route_dir.glob("routes_*.py"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("#")[0]
                if banned.search(code):
                    offenders.append(f"{path.name}:{number}: {line.strip()}")

        assert offenders == []

    def test_each_route_delegates_to_a_single_service_call(self):
        import pathlib
        import re

        route_dir = pathlib.Path(__file__).resolve().parents[1] / "uc10_analytics" / "api"
        for path in route_dir.glob("routes_*.py"):
            source = path.read_text(encoding="utf-8")
            for body in re.findall(r"\nasync def [^\n]+\n(?:.*?)(?=\n@|\Z)", source, re.S):
                awaits = len(re.findall(r"await ", body))
                assert awaits <= 1, f"{path.name}: route performs {awaits} awaited calls"
