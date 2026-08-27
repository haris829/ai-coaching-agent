"""Fail-closed evidence.

A boundary failure must produce, in order: no emitted response, a halted session,
an admin alert, a logged critical defect, and a security incident. Each of the
three corruption modes is exercised end to end through the real API.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from uc06.adapters.identity.header_user import USER_HEADER
from uc06.adapters.mock import case_file as cf
from uc06.api.app import create_app
from uc06.application import boundary
from uc06.application.emitter import HALT_REASON_BOUNDARY_FAILURE, WITHHELD_CODE, ResponseEmitter
from uc06.composition import build_container
from uc06.domain.disclaimer import CANONICAL_DISCLAIMER
from uc06.domain.enums import SecurityIncidentKind

from . import support
from .conftest import DEFAULT_USER, make_settings
from .tamper import AlteringSerializer, DroppingSerializer, SuppressionKeySerializer, with_serializer

QUESTION = "How does the defence of duress apply to the account in this file?"
SESSION = "sess-failclosed"

SERIALIZERS = [
    ("dropped", DroppingSerializer, boundary.REASON_ABSENT, SecurityIncidentKind.INTERNAL_DISCLAIMER_ABSENT),
    (
        "altered",
        AlteringSerializer,
        boundary.REASON_SHORTENED_VARIANT,
        SecurityIncidentKind.INTERNAL_DISCLAIMER_ALTERED,
    ),
    (
        "suppression-key",
        SuppressionKeySerializer,
        boundary.REASON_SUPPRESSION_KEY,
        SecurityIncidentKind.INTERNAL_DISCLAIMER_ALTERED,
    ),
]


def _tampered_client(serializer_class, once: bool = True):
    """A fresh container per test, with a fresh tamperer over its own ports."""
    container = with_serializer(build_container(make_settings()), serializer_class(once=once))
    return TestClient(create_app(container), raise_server_exceptions=False), container


def _ask(client, session_id=SESSION, question=QUESTION):
    support.record_question(question)
    return client.post(
        "/api/v1/case-coaching/questions",
        headers={USER_HEADER: DEFAULT_USER},
        json={"question": question, "case_file_id": cf.CASE_FULL, "session_id": session_id},
    )


@pytest.mark.parametrize("name,serializer_class,reason,kind", SERIALIZERS, ids=[s[0] for s in SERIALIZERS])
def test_no_unlabelled_response_is_emitted(name, serializer_class, reason, kind):
    client, _ = _tampered_client(serializer_class)
    response = _ask(client)

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == WITHHELD_CODE
    # The safe error still carries the disclaimer: no response from this surface
    # is ever unlabelled, including the one saying a response was withheld.
    assert body["disclaimer"] == CANONICAL_DISCLAIMER
    # And none of the case-linked content escaped.
    assert "content" not in body
    assert "case_facts_referenced" not in body


@pytest.mark.parametrize("name,serializer_class,reason,kind", SERIALIZERS, ids=[s[0] for s in SERIALIZERS])
def test_the_session_is_halted(name, serializer_class, reason, kind):
    client, container = _tampered_client(serializer_class)
    _ask(client)

    assert container.halts.is_halted(SESSION) is True
    assert container.halts.get(SESSION).reason_code == HALT_REASON_BOUNDARY_FAILURE


@pytest.mark.parametrize("name,serializer_class,reason,kind", SERIALIZERS, ids=[s[0] for s in SERIALIZERS])
def test_the_admin_is_alerted_with_technical_detail_and_no_case_content(name, serializer_class, reason, kind):
    client, container = _tampered_client(serializer_class)
    _ask(client)

    alerts = container.admin_alerts.incidents()
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.severity == "critical"
    assert alert.code == reason
    assert alert.session_id == SESSION
    assert alert.technical_detail, "the responder needs technical detail"
    assert alert.remediation
    # Full technical detail, and still no case content in it.
    for fact in cf._full_case().facts:
        assert fact.text not in alert.technical_detail


@pytest.mark.parametrize("name,serializer_class,reason,kind", SERIALIZERS, ids=[s[0] for s in SERIALIZERS])
def test_a_security_incident_is_recorded(name, serializer_class, reason, kind):
    client, container = _tampered_client(serializer_class)
    _ask(client)

    incidents = [i for i in container.security_incidents.incidents() if i.detail_code == reason]
    assert incidents, f"{name}: no internal-state security incident recorded"
    assert incidents[0].kind is kind


@pytest.mark.parametrize("name,serializer_class,reason,kind", SERIALIZERS, ids=[s[0] for s in SERIALIZERS])
def test_the_critical_defect_is_logged(name, serializer_class, reason, kind, log_buffer):
    mark = log_buffer.tell()
    client, _ = _tampered_client(serializer_class)
    _ask(client)
    log_buffer.seek(mark)
    written = log_buffer.read()
    log_buffer.seek(0, 2)

    assert "disclaimer.boundary_failure" in written
    assert '"level": "CRITICAL"' in written
    assert reason in written


@pytest.mark.parametrize("name,serializer_class,reason,kind", SERIALIZERS, ids=[s[0] for s in SERIALIZERS])
def test_the_halt_blocks_every_further_case_linked_response(name, serializer_class, reason, kind):
    client, container = _tampered_client(serializer_class)
    _ask(client)

    second = _ask(client)
    assert second.status_code == 409
    body = second.json()
    assert body["error"]["code"] == "session_halted"
    assert body["error"]["session_halted"] is True
    assert body["disclaimer"] == CANONICAL_DISCLAIMER
    assert "content" not in body


@pytest.mark.parametrize("name,serializer_class,reason,kind", SERIALIZERS, ids=[s[0] for s in SERIALIZERS])
def test_the_halt_is_scoped_to_the_session_that_failed(name, serializer_class, reason, kind):
    client, container = _tampered_client(serializer_class)
    _ask(client, session_id="sess-failed-one")

    assert container.halts.is_halted("sess-failed-one") is True
    assert container.halts.is_halted("sess-untouched") is False


@pytest.mark.parametrize("name,serializer_class,reason,kind", SERIALIZERS, ids=[s[0] for s in SERIALIZERS])
def test_a_cleared_halt_allows_case_linked_coaching_again(name, serializer_class, reason, kind):
    client, container = _tampered_client(serializer_class)
    _ask(client)
    assert _ask(client).status_code == 409

    # Clearing is an administrative act, performed through the port. There is no
    # endpoint and no configuration key for it - see assumptions row A-06.
    container.halts.clear(SESSION)

    resumed = _ask(client)
    assert resumed.status_code == 200
    assert resumed.json()["disclaimer"] == CANONICAL_DISCLAIMER
    assert resumed.json()["mode"] == "case_linked"


def test_the_status_endpoint_reports_the_halt_for_a_caller_to_render():
    client, container = _tampered_client(DroppingSerializer)
    _ask(client)

    status = client.get(
        f"/api/v1/case-coaching/sessions/{SESSION}/status", headers={USER_HEADER: DEFAULT_USER}
    )
    assert status.status_code == 200
    body = status.json()
    assert body["case_linked_coaching_halted"] is True
    assert body["halt_reason_code"] == HALT_REASON_BOUNDARY_FAILURE
    assert body["disclaimer"] == CANONICAL_DISCLAIMER


def test_the_emitter_never_trusts_the_serializer_for_the_error_it_returns():
    """The serializer just proved untrustworthy, so the safe error is built
    without it. A serializer that corrupts everything still cannot produce an
    unlabelled response."""
    client, _ = _tampered_client(DroppingSerializer)
    response = _ask(client)
    assert response.json()["disclaimer"] == CANONICAL_DISCLAIMER


def test_a_permanently_broken_serialiser_never_emits_an_unlabelled_response():
    """The one-shot tamperers model a transient defect. This one never recovers:
    every request fails closed, and not one of them is unlabelled."""
    client, container = _tampered_client(DroppingSerializer, once=False)

    for _ in range(3):
        response = _ask(client, session_id="sess-permanently-broken")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == WITHHELD_CODE
        assert response.json()["disclaimer"] == CANONICAL_DISCLAIMER
        assert "content" not in response.json()

    assert container.halts.is_halted("sess-permanently-broken") is True
