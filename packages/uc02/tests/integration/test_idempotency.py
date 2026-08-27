"""Refresh policy over HTTP: build once, reuse thereafter, and force_refresh gating."""

from __future__ import annotations

from tests.fixtures.factories import auth, make_client, make_harness, make_settings


def test_second_initialize_returns_the_stored_context_with_200_and_no_provider_calls():
    harness = make_harness()
    client = make_client(harness)

    first = client.post(
        "/api/v1/context/initialize", json={"session_id": "sess-1"}, headers=auth("learner-1")
    )
    assert first.status_code == 201
    assert first.json()["context_status"] == "created"
    assert harness.provider_call_count == 4

    second = client.post(
        "/api/v1/context/initialize", json={"session_id": "sess-1"}, headers=auth("learner-1")
    )
    assert second.status_code == 200
    assert second.json()["context_status"] == "existing"
    assert harness.provider_call_count == 4
    assert second.json()["built_at"] == first.json()["built_at"]


def test_repeated_initialisation_never_re_queries_providers():
    harness = make_harness()
    client = make_client(harness)
    for _ in range(5):
        client.post(
            "/api/v1/context/initialize",
            json={"session_id": "sess-1"},
            headers=auth("learner-1"),
        )
    assert harness.provider_call_count == 4


def test_force_refresh_is_rejected_on_the_public_path():
    harness = make_harness()
    client = make_client(harness)
    client.post(
        "/api/v1/context/initialize", json={"session_id": "sess-1"}, headers=auth("learner-1")
    )
    harness.reset_call_counts()

    response = client.post(
        "/api/v1/context/initialize",
        json={"session_id": "sess-1", "force_refresh": True},
        headers=auth("learner-1"),
    )
    assert response.status_code == 403
    assert response.json()["error"] == "force_refresh_not_permitted"
    assert harness.provider_call_count == 0


def test_force_refresh_is_rejected_on_the_public_path_even_when_config_enables_it():
    """The config flag gates the internal path only; the public path never honours it."""
    settings = make_settings(allow_force_refresh=True)
    harness = make_harness(settings=settings)
    client = make_client(harness)

    response = client.post(
        "/api/v1/context/initialize",
        json={"session_id": "sess-1", "force_refresh": True},
        headers=auth("learner-1"),
    )
    assert response.status_code == 403


def test_the_internal_refresh_endpoint_is_absent_by_default():
    harness = make_harness()
    client = make_client(harness)
    client.post(
        "/api/v1/context/initialize", json={"session_id": "sess-1"}, headers=auth("learner-1")
    )
    harness.reset_call_counts()

    response = client.post(
        "/api/v1/internal/context/sess-1/refresh",
        headers={**auth("learner-1"), "X-Internal-Admin": "yes"},
    )
    assert response.status_code == 404
    assert harness.provider_call_count == 0


def test_the_internal_refresh_endpoint_requires_the_admin_header_when_enabled():
    settings = make_settings(allow_force_refresh=True)
    harness = make_harness(settings=settings)
    client = make_client(harness)
    client.post(
        "/api/v1/context/initialize", json={"session_id": "sess-1"}, headers=auth("learner-1")
    )
    harness.reset_call_counts()

    response = client.post(
        "/api/v1/internal/context/sess-1/refresh", headers=auth("learner-1")
    )
    assert response.status_code == 404
    assert harness.provider_call_count == 0


def test_the_internal_refresh_endpoint_rebuilds_when_fully_enabled():
    settings = make_settings(allow_force_refresh=True)
    harness = make_harness(settings=settings)
    client = make_client(harness)
    client.post(
        "/api/v1/context/initialize", json={"session_id": "sess-1"}, headers=auth("learner-1")
    )
    harness.reset_call_counts()

    response = client.post(
        "/api/v1/internal/context/sess-1/refresh",
        headers={**auth("learner-1"), "X-Internal-Admin": "yes"},
    )
    assert response.status_code == 200
    assert response.json()["context_status"] == "refreshed"
    assert harness.provider_call_count == 4


def test_the_internal_refresh_endpoint_still_enforces_ownership():
    settings = make_settings(allow_force_refresh=True)
    harness = make_harness(settings=settings)
    client = make_client(harness)
    client.post(
        "/api/v1/context/initialize", json={"session_id": "sess-1"}, headers=auth("learner-1")
    )
    harness.reset_call_counts()

    response = client.post(
        "/api/v1/internal/context/sess-1/refresh",
        headers={**auth("attacker"), "X-Internal-Admin": "yes"},
    )
    assert response.status_code == 404
    assert harness.provider_call_count == 0
