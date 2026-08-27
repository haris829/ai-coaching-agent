"""Access control: no cross-user access, and no learner path to the admin endpoints."""

from __future__ import annotations

import pytest

from tests.conftest import ADMIN_HEADERS, ADMIN_TOKEN, LEARNER_HEADERS, OTHER_LEARNER_HEADERS
from tests.helpers import seed_via_api

ADMIN_ROUTES = [
    ("get", "/api/v1/admin/flags", None),
    ("patch", "/api/v1/admin/flags/flg_any", {"status": "reviewed"}),
]


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ROUTES)
def test_a_learner_credential_cannot_reach_an_admin_endpoint(client, method, path, body):
    response = client.request(method.upper(), path, json=body, headers=LEARNER_HEADERS)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "admin_required"


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ROUTES)
def test_an_anonymous_caller_cannot_reach_an_admin_endpoint(client, method, path, body):
    assert client.request(method.upper(), path, json=body).status_code == 403


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Admin-Token": "guessed-token"},
        {"X-Admin-Id": "admin_test"},  # claiming an admin identity without the credential
        {"X-User-Id": "admin_test"},
        {"X-Admin-Token": ""},
    ],
)
def test_admin_authority_requires_the_configured_credential(client, headers):
    assert client.get("/api/v1/admin/flags", headers=headers).status_code == 403


def test_admin_authority_is_refused_entirely_when_no_credential_is_configured(
    make_client, settings
):
    """No configured admin credential means the endpoints deny everyone, rather than
    trusting a self-asserted header."""
    from uc10.adapters.mock.identity import ConfiguredAdminIdentityProvider

    unconfigured = settings.model_copy(update={"dev_admin_token": None})
    client = make_client(admin_identity=ConfiguredAdminIdentityProvider(lambda: unconfigured))
    assert client.get("/api/v1/admin/flags", headers=ADMIN_HEADERS).status_code == 403


def test_the_admin_endpoints_are_reachable_with_the_configured_credential(client):
    response = client.get("/api/v1/admin/flags", headers={"X-Admin-Token": ADMIN_TOKEN})
    assert response.status_code == 200


def test_the_learner_and_admin_identities_come_from_separate_ports(container):
    """Structural, not conventional: there is no role flag to escalate."""
    learner_port = type(container.current_user)
    admin_port = type(container.admin_identity)
    assert learner_port is not admin_port
    assert not hasattr(container.current_user, "resolve_admin")
    assert not hasattr(container.admin_identity, "resolve")


def test_a_learner_cannot_see_another_learners_ratings_through_any_endpoint(
    client, interactions, container
):
    seed_via_api(client, interactions, total=10, downs=10, topic_tag="conflicts_of_interest")

    # The flag names topics and interactions; a learner cannot read it at all.
    assert client.get("/api/v1/admin/flags", headers=OTHER_LEARNER_HEADERS).status_code == 403
    # And another learner's own-rating read returns nothing rather than their record.
    for interaction_id in ("int_conflicts_of_interest_0", "int_conflicts_of_interest_1"):
        body = client.get(
            f"/api/v1/interactions/{interaction_id}/rating", headers=OTHER_LEARNER_HEADERS
        ).json()
        assert body["rating"] is None
    assert len(container.flag_repository.list_open()) == 1


def test_healthz_needs_no_credential_and_leaks_no_secret(client, settings):
    response = client.get("/api/v1/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["wiring"]["interaction_provider"] == "mock"
    assert ADMIN_TOKEN not in response.text
