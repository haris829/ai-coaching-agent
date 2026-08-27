"""Ownership, identity and request hygiene.

A summary is a personal record. A session identifier alone must never be
sufficient to fetch one, and every read path - structured, preview and
download - is checked independently, because it only takes one unchecked route
to make the other two decorative.
"""

from __future__ import annotations

import pytest

from tests.support.harness import build_harness
from uc09_summary.adapters.mock import scenarios as S
from uc09_summary.domain.errors import AccessDenied, SummaryNotFound

READ_PATHS = ("", "/preview", "/pdf", "/downloads")


class TestOwnershipOnEveryReadPath:
    @pytest.mark.parametrize("suffix", READ_PATHS)
    def test_another_learner_cannot_read_the_summary(self, suffix: str) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        response = harness.client.get(
            f"/api/v1/summaries/{record.summary_id}{suffix}",
            headers=harness.as_user(S.OTHER_USER_ID),
        )

        assert response.status_code == 404

    @pytest.mark.parametrize("suffix", READ_PATHS)
    def test_the_owner_can(self, suffix: str) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        response = harness.client.get(
            f"/api/v1/summaries/{record.summary_id}{suffix}",
            headers=harness.as_user(S.OWNER_USER_ID),
        )

        assert response.status_code == 200

    @pytest.mark.parametrize("suffix", READ_PATHS)
    def test_an_unidentified_caller_cannot(self, suffix: str) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        response = harness.client.get(f"/api/v1/summaries/{record.summary_id}{suffix}")

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "identity_unresolved"

    def test_the_service_layer_refuses_too_not_just_the_route(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        with pytest.raises(SummaryNotFound):
            harness.service.get(record.summary_id, S.OTHER_USER_ID)
        with pytest.raises(SummaryNotFound):
            harness.service.preview_html(record.summary_id, S.OTHER_USER_ID)
        with pytest.raises(SummaryNotFound):
            harness.service.export(record.summary_id, S.OTHER_USER_ID)


class TestASessionIdentifierIsNotEnough:
    def test_knowing_the_session_id_does_not_grant_generation(self) -> None:
        harness = build_harness()

        response = harness.client.post(
            f"/api/v1/sessions/{S.SESSION_NOT_OWNED}/summary",
            headers=harness.as_user(S.OWNER_USER_ID),
            json={},
        )

        assert response.status_code == 404

    def test_generation_for_another_learner_session_is_refused_in_the_service(
        self,
    ) -> None:
        harness = build_harness()
        with pytest.raises(AccessDenied):
            harness.service.generate(S.SESSION_NOT_OWNED, S.OWNER_USER_ID)

    def test_no_summary_is_written_for_a_refused_generation(self) -> None:
        harness = build_harness()
        harness.client.post(
            f"/api/v1/sessions/{S.SESSION_NOT_OWNED}/summary",
            headers=harness.as_user(S.OWNER_USER_ID),
            json={},
        )

        assert harness.summaries.for_session(S.SESSION_NOT_OWNED) == ()

    def test_the_summary_id_is_not_derivable_from_the_session_id(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        assert S.SESSION_COMPLETE not in record.summary_id

    def test_a_stranger_cannot_confirm_that_a_summary_exists(self) -> None:
        """Denied and absent must be indistinguishable to a probe."""
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        denied = harness.client.get(
            f"/api/v1/summaries/{record.summary_id}",
            headers=harness.as_user(S.OTHER_USER_ID),
        )
        absent = harness.client.get(
            "/api/v1/summaries/sum_definitely_not_a_real_identifier",
            headers=harness.as_user(S.OTHER_USER_ID),
        )

        assert denied.status_code == absent.status_code == 404
        assert denied.json() == absent.json()


class TestRequestHygiene:
    def test_unknown_body_fields_are_rejected_outright(self) -> None:
        harness = build_harness()

        response = harness.client.post(
            f"/api/v1/sessions/{S.SESSION_COMPLETE}/summary",
            headers=harness.as_user(S.OWNER_USER_ID),
            json={"unexpected_field": "value"},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"

    def test_a_client_cannot_assert_its_own_identity_in_the_body(self) -> None:
        harness = build_harness()

        response = harness.client.post(
            f"/api/v1/sessions/{S.SESSION_NOT_OWNED}/summary",
            headers=harness.as_user(S.OWNER_USER_ID),
            json={"user_id": S.OTHER_USER_ID},
        )

        assert response.status_code == 422, (
            "user_id is resolved server-side. A body field claiming an "
            "identity is rejected rather than ignored, so that the attempt is "
            "never mistaken for an accepted request."
        )

    def test_a_client_cannot_declare_a_summary_complete(self) -> None:
        harness = build_harness()

        response = harness.client.post(
            f"/api/v1/sessions/{S.SESSION_IN_PROGRESS}/summary",
            headers=harness.as_user(S.OWNER_USER_ID),
            json={"is_partial": False},
        )

        assert response.status_code == 422

    def test_an_empty_body_is_accepted(self) -> None:
        harness = build_harness()

        response = harness.client.post(
            f"/api/v1/sessions/{S.SESSION_COMPLETE}/summary",
            headers=harness.as_user(S.OWNER_USER_ID),
            json={},
        )

        assert response.status_code == 201

    def test_a_blank_identity_header_is_refused(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        response = harness.client.get(
            f"/api/v1/summaries/{record.summary_id}",
            headers={"X-User-Id": "   "},
        )

        assert response.status_code == 401


class TestErrorResponsesLeakNothing:
    def test_a_not_found_response_carries_no_content(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        response = harness.client.get(
            f"/api/v1/summaries/{record.summary_id}",
            headers=harness.as_user(S.OTHER_USER_ID),
        )

        text = response.text
        assert record.session_id not in text
        assert record.user_display_name not in text
        for topic in record.topics_covered:
            assert topic.label not in text
        for resource in record.resources_referenced:
            assert resource.title not in text

    def test_a_validation_failure_does_not_echo_the_body(self) -> None:
        harness = build_harness()

        response = harness.client.post(
            f"/api/v1/sessions/{S.SESSION_COMPLETE}/summary",
            headers=harness.as_user(S.OWNER_USER_ID),
            json={"secret_note": "confidential-string-abc123"},
        )

        assert "confidential-string-abc123" not in response.text
        assert "secret_note" not in response.text

    def test_an_upstream_failure_does_not_disclose_the_provider(self) -> None:
        harness = build_harness()

        response = harness.client.post(
            f"/api/v1/sessions/{S.SESSION_UNAVAILABLE}/summary",
            headers=harness.as_user(S.OWNER_USER_ID),
            json={},
        )

        assert response.status_code == 503
        for token in ("Mock", "mock", "scenario", "adapter", "Traceback"):
            assert token not in response.text

    def test_error_bodies_have_a_stable_shape(self) -> None:
        harness = build_harness()
        response = harness.client.get(
            "/api/v1/summaries/nope", headers=harness.as_user(S.OWNER_USER_ID)
        )

        body = response.json()
        assert set(body) == {"error"}
        assert set(body["error"]) == {"code", "message"}


class TestDevSessionMinting:
    def test_minting_is_absent_by_default(self) -> None:
        harness = build_harness()

        response = harness.client.post(
            "/api/v1/dev/sessions", headers=harness.as_user(S.OWNER_USER_ID)
        )

        assert response.status_code == 404, (
            "This component receives a session id and never creates one on a "
            "production path. The route must not exist unless enabled."
        )

    def test_the_default_is_off(self) -> None:
        from uc09_summary.config import Settings

        assert Settings().allow_dev_session_minting is False

    def test_minting_exists_only_when_explicitly_enabled(self) -> None:
        harness = build_harness(allow_dev_session_minting=True)

        response = harness.client.post(
            "/api/v1/dev/sessions", headers=harness.as_user(S.OWNER_USER_ID)
        )

        assert response.status_code == 201
        assert response.json()["session_id"].startswith("dev-sess-")
