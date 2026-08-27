"""The suite runs with no network and no API key.

Two levels of proof:

* the component's own code paths run with socket creation disabled entirely;
* the HTTP surface runs with name resolution and outbound connections disabled, which is
  what any adapter reaching a real service would need.

(The in-process test client uses ``socket.socketpair()`` for its event loop on Windows --
a local self-pipe, not a network connection -- so the second test blocks outbound calls
rather than every socket object.)
"""

from __future__ import annotations

import os
import socket

import pytest

from tests.conftest import ADMIN_HEADERS, LEARNER_HEADERS
from tests.helpers import seed_via_api
from uc10.domain.enums import RatingValue

LOOPBACK = {"127.0.0.1", "::1", "localhost", ""}


def _is_loopback(address) -> bool:
    """The test client's own event loop uses a loopback self-pipe; that is not the network."""
    host = address[0] if isinstance(address, (tuple, list)) and address else address
    return str(host) in LOOPBACK


@pytest.fixture
def no_outbound_connections(monkeypatch):
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect

    def guarded_getaddrinfo(host, *args, **kwargs):
        if not _is_loopback(host):
            raise AssertionError(f"this component tried to resolve {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    def guarded_connect(self, address, *args, **kwargs):
        if not _is_loopback(address):
            raise AssertionError(f"this component tried to connect to {address!r}")
        return real_connect(self, address, *args, **kwargs)

    def refuse_create_connection(*args, **kwargs):
        raise AssertionError("this component tried to reach the network")

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", refuse_create_connection)
    return guarded_connect


@pytest.fixture
def no_sockets_at_all(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("this component opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "socketpair", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    return refuse


def test_the_component_opens_no_socket_at_all(no_sockets_at_all, container):
    """Rating capture and flag evaluation, straight through the services."""
    for _ in range(10):
        interaction = container.interactions.get("int_answer")
        assert interaction.interaction_id == "int_answer"
    outcome = container.feedback.capture(
        interaction_id="int_answer", user_id="user_alice", rating=RatingValue.DOWN
    )
    assert outcome.ok
    report = container.flagging.run_cycle()
    assert report.evaluated_topics == ("contract_formation",)


def test_the_http_surface_makes_no_outbound_connection(
    no_outbound_connections, client, interactions, container
):
    seed_via_api(client, interactions, total=10, downs=10, topic_tag="limitation_periods")
    container.flagging.run_cycle()
    flags = client.get("/api/v1/admin/flags", headers=ADMIN_HEADERS).json()
    assert flags["count"] == 1
    assert (
        client.post(
            "/api/v1/interactions/int_answer/rating",
            json={"rating": "up"},
            headers=LEARNER_HEADERS,
        ).status_code
        == 201
    )
    assert client.get("/api/v1/healthz").status_code == 200


def test_no_api_key_or_credential_is_required_to_run_the_component(monkeypatch):
    """Nothing in configuration is a secret this component cannot start without."""
    for key in list(os.environ):
        if key.startswith(("INTERACTION_", "FLAG_", "HISTORICAL_", "DEV_", "ALLOW_")):
            monkeypatch.delenv(key, raising=False)
    from uc10.api.app import create_app
    from uc10.config import Settings, reset_settings_cache

    reset_settings_cache()
    app = create_app(settings=Settings(_env_file=None))
    assert app is not None
