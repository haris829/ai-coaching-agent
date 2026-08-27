"""API surface: the five endpoints, their shapes and their status codes."""

from __future__ import annotations

import pytest

from tests.support.harness import build_harness
from uc09_summary.adapters.mock import scenarios as S
from uc09_summary.rendering.html_document import CPD_LABEL, PRODUCT_NAME


class TestGenerateEndpoint:
    def test_it_creates_a_summary(self) -> None:
        harness = build_harness()

        response = harness.client.post(
            f"/api/v1/sessions/{S.SESSION_COMPLETE}/summary",
            headers=harness.as_user(S.OWNER_USER_ID),
            json={},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["session_id"] == S.SESSION_COMPLETE
        assert body["generation_mode"] == "generated"
        assert body["session_status"] == "summary_generated"

    def test_it_works_without_a_body(self) -> None:
        harness = build_harness()
        response = harness.client.post(
            f"/api/v1/sessions/{S.SESSION_COMPLETE}/summary",
            headers=harness.as_user(S.OWNER_USER_ID),
        )
        assert response.status_code == 201

    def test_regeneration_returns_a_new_identifier(self) -> None:
        harness = build_harness()
        first = harness.client.post(
            f"/api/v1/sessions/{S.SESSION_COMPLETE}/summary",
            headers=harness.as_user(S.OWNER_USER_ID),
            json={},
        ).json()
        second = harness.client.post(
            f"/api/v1/sessions/{S.SESSION_COMPLETE}/summary",
            headers=harness.as_user(S.OWNER_USER_ID),
            json={},
        ).json()

        assert first["summary_id"] != second["summary_id"]

    def test_a_missing_session_is_a_404(self) -> None:
        harness = build_harness()
        response = harness.client.post(
            f"/api/v1/sessions/{S.SESSION_MISSING}/summary",
            headers=harness.as_user(S.OWNER_USER_ID),
            json={},
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "session_not_found"


class TestSummaryResponseShape:
    @pytest.fixture
    def body(self):
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        return harness.client.get(
            f"/api/v1/summaries/{record.summary_id}",
            headers=harness.as_user(S.OWNER_USER_ID),
        ).json()

    def test_it_carries_the_specified_summary_record_fields(self, body) -> None:
        for field in (
            "summary_id",
            "session_id",
            "user_id",
            "generated_at",
            "is_partial",
            "covers_interactions_through",
            "topics_covered",
            "key_concepts",
            "resources_referenced",
            "next_steps",
            "source_status",
            "generation_mode",
            "session_status",
        ):
            assert field in body

    def test_it_carries_what_a_frontend_needs_to_render(self, body) -> None:
        assert body["cpd_label"] == CPD_LABEL
        assert body["product_name"] == PRODUCT_NAME
        assert body["partial_marker"] is None
        assert len(body["sections"]) == 4

    def test_enum_values_are_lowercase_on_the_wire(self, body) -> None:
        assert body["naric_level"] == "level_7"
        assert body["naric_level_source"] == "retrieved"
        assert body["explanation_profile"] == "advanced"
        for status in body["source_status"].values():
            assert status == status.lower()

    def test_every_resource_names_the_interactions_it_was_cited_in(self, body) -> None:
        for resource in body["resources_referenced"]:
            assert resource["cited_in_interaction_ids"]

    def test_the_question_log_is_empty_on_a_generated_summary(self, body) -> None:
        assert body["question_log"] == []

    def test_a_missing_summary_is_a_404(self) -> None:
        harness = build_harness()
        response = harness.client.get(
            "/api/v1/summaries/sum_nope", headers=harness.as_user(S.OWNER_USER_ID)
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "summary_not_found"


class TestPreviewEndpoint:
    def test_it_returns_html(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        response = harness.client.get(
            f"/api/v1/summaries/{record.summary_id}/preview",
            headers=harness.as_user(S.OWNER_USER_ID),
        )

        assert response.status_code == 200
        assert response.text.startswith("<!DOCTYPE html>")
        assert response.headers["x-summary-id"] == record.summary_id
        assert response.headers["x-summary-is-partial"] == "false"

    def test_the_partial_header_reflects_the_record(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_IN_PROGRESS, S.OWNER_USER_ID)

        response = harness.client.get(
            f"/api/v1/summaries/{record.summary_id}/preview",
            headers=harness.as_user(S.OWNER_USER_ID),
        )

        assert response.headers["x-summary-is-partial"] == "true"

    def test_it_contains_no_script_or_external_reference(self) -> None:
        """No frontend. A printable document, not an application."""
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        html = harness.client.get(
            f"/api/v1/summaries/{record.summary_id}/preview",
            headers=harness.as_user(S.OWNER_USER_ID),
        ).text

        for forbidden in ("<script", "src=", "href=", "http://", "https://"):
            assert forbidden not in html


class TestPdfEndpoint:
    def test_it_reports_the_session_id_in_a_header_for_verification(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        response = harness.client.get(
            f"/api/v1/summaries/{record.summary_id}/pdf",
            headers=harness.as_user(S.OWNER_USER_ID),
        )

        assert response.headers["x-session-id"] == record.session_id
        assert response.headers["x-pdf-available"] == "true"


class TestHealth:
    def test_it_needs_no_identity(self) -> None:
        harness = build_harness()
        response = harness.client.get("/api/v1/healthz")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_it_discloses_no_credential(self) -> None:
        harness = build_harness()
        text = harness.client.get("/api/v1/healthz").text

        assert "api_key" not in text
        assert "upstream_base_url" not in text


class TestOpenApi:
    def test_the_schema_is_generated(self) -> None:
        harness = build_harness()
        schema = harness.client.get("/openapi.json").json()

        for path in (
            "/api/v1/sessions/{session_id}/summary",
            "/api/v1/summaries/{summary_id}",
            "/api/v1/summaries/{summary_id}/preview",
            "/api/v1/summaries/{summary_id}/pdf",
            "/api/v1/healthz",
        ):
            assert path in schema["paths"]

    def test_the_request_schema_forbids_unknown_fields(self) -> None:
        harness = build_harness()
        schema = harness.client.get("/openapi.json").json()
        request = schema["components"]["schemas"]["GenerateSummaryRequest"]

        assert request.get("additionalProperties") is False
