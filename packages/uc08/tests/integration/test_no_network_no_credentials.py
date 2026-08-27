"""The suite runs with no network and no API key -- proved, not assumed.

Sockets are disabled and the environment is stripped of anything credential
shaped, then a full end-to-end flow is exercised.
"""

from __future__ import annotations

import socket
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from uc08.adapters.clock.clocks import FixedClock
from uc08.adapters.mock.activity import MockActivityProvider
from uc08.adapters.mock.gap_report import GapReportPlan, MockGapReportProvider
from uc08.adapters.mock.ledger import ActivityLedger
from uc08.api.app import create_app
from uc08.composition import build_container
from uc08.config import load_settings

MONDAY = datetime(2026, 3, 16, 9, 0, tzinfo=timezone.utc)
SUBJECT = "learner-7781"
HEADERS = {"X-UC08-Subject": SUBJECT}

CREDENTIAL_SHAPED = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "UPSTREAM_ACTIVITY_TOKEN",
    "UPSTREAM_ACTIVITY_BASE_URL",
    "UC08_TOKEN",
)


LOOPBACK = {"localhost", "127.0.0.1", "::1", "", None}


@pytest.fixture
def no_network(monkeypatch):
    """Egress is blocked: no name resolution, no non-loopback connection.

    Loopback is left alone because the asyncio event loop this test runs on
    builds its self-pipe from a loopback socket pair on Windows. That is
    in-process plumbing, not a network call, and blocking it would test the
    event loop rather than this component.
    """
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host not in LOOPBACK:
            raise AssertionError(f"name resolution attempted for {host!r}; UC-08 must not reach the network")
        return real_getaddrinfo(host, *args, **kwargs)

    def guarded_connect(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if host not in LOOPBACK:
            raise AssertionError(f"connection attempted to {host!r}; UC-08 must not reach the network")
        return real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    for name in CREDENTIAL_SHAPED:
        monkeypatch.delenv(name, raising=False)
    return None


def test_a_full_flow_runs_with_sockets_disabled_and_no_credentials(no_network):
    clock = FixedClock(MONDAY)
    ledger = ActivityLedger()
    plan = GapReportPlan()
    plan.set_suggestion(
        SUBJECT,
        {
            "topic_id": "topic-1",
            "name": "Solicitors Accounts Rules",
            "naric_level": "level_6",
            "course_progress_percent": 40,
        },
    )
    ledger.add_interaction(SUBJECT, MONDAY - timedelta(days=4), "last-week", topic="conduct")
    ledger.set_question_count(SUBJECT, 50)

    container = build_container(
        load_settings(),
        clock=clock,
        activity=MockActivityProvider(clock, ledger),
        gap_report=MockGapReportProvider(clock, plan),
    )
    app = create_app(container)

    # TestClient drives the ASGI app in-process; no listening socket is created.
    with TestClient(app) as client:
        assert client.get("/api/v1/healthz").status_code == 200

        ledger.add_interaction(SUBJECT, clock.now(), "i-1", topic="conduct")
        recorded = client.post(
            "/api/v1/streaks/record-activity",
            json={"interaction_id": "i-1", "session_id": "sess-1"},
            headers=HEADERS,
        )
        assert recorded.status_code == 200
        assert [badge["milestone"] for badge in recorded.json()["awarded_badges"]] == [10, 50]

        generated = client.post("/api/v1/weekly-summaries/generate", headers=HEADERS)
        assert generated.status_code == 200
        assert generated.json()["generated"]["suggested_topic"]["naric_level"] == "level_6"

        assert client.get("/api/v1/streaks", headers=HEADERS).status_code == 200
        assert client.get("/api/v1/badges", headers=HEADERS).status_code == 200
        assert client.get("/api/v1/weekly-summaries", headers=HEADERS).status_code == 200


def test_the_network_guard_actually_bites(no_network):
    """Guard on the guard: the fixture would catch a real network call."""
    with pytest.raises(AssertionError):
        socket.getaddrinfo("activity.example.invalid", 443)
    with socket.socket() as probe, pytest.raises(AssertionError):
        probe.connect(("activity.example.invalid", 443))


def test_no_module_imports_an_http_client():
    """Nothing in the component opens a connection today.

    A real adapter will import an HTTP client -- inside
    ``uc08/adapters/real/``, which is where that knowledge belongs. The
    template documents the call rather than making one, so the shipped
    component has no transport at all.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "uc08"
    offenders = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            for module in modules:
                if module.split(".")[0] in {"httpx", "requests", "urllib", "urllib3", "aiohttp", "socket"}:
                    offenders.append(f"{path.relative_to(root.parent).as_posix()}:{node.lineno} {module}")
    assert not offenders, offenders
