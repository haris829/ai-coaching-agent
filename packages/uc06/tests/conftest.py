"""Fixtures for the UC-06 suite.

Everything runs against the fake generator: no network, no API key, no cost.

Log output from the whole session is captured into one buffer so that the
privacy requirement - no case fact text and no question text in any log output -
can be asserted across the entire suite, not just within one test.
"""

from __future__ import annotations

import io
import logging
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from uc06.adapters.identity.header_user import USER_HEADER
from uc06.api.app import create_app
from uc06.composition import Container, build_container
from uc06.config import Settings
from uc06.logging_setup import LOGGER_NAME, JsonFormatter

from . import support

DEFAULT_USER = "user-alice"
OTHER_USER = "user-bob"

#: One buffer for the whole session.
_LOG_BUFFER = io.StringIO()


@pytest.fixture(scope="session", autouse=True)
def capture_all_logs() -> Iterator[io.StringIO]:
    logger = logging.getLogger(LOGGER_NAME)
    handler = logging.StreamHandler(_LOG_BUFFER)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield _LOG_BUFFER
    logger.removeHandler(handler)


@pytest.fixture
def log_buffer() -> io.StringIO:
    return _LOG_BUFFER


def make_settings(**overrides: Any) -> Settings:
    base = {
        "answer_generator": "fake",
        "case_file_provider": "mock",
        "learner_context_provider": "mock",
        "guard_classifier": "mock",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def container(settings: Settings) -> Container:
    return build_container(settings)


@pytest.fixture
def client(container: Container) -> TestClient:
    app = create_app(container)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def ask(client: TestClient):
    """Post a case-linked question. Records the question for the privacy scan."""

    def _ask(
        question: str,
        case_file_id: str = "CASE-FULL-001",
        session_id: str | None = "sess-level-5",
        user: str = DEFAULT_USER,
        **extra: Any,
    ):
        support.record_question(question)
        body: dict[str, Any] = {"question": question, "case_file_id": case_file_id}
        if session_id is not None:
            body["session_id"] = session_id
        body.update(extra)
        return client.post(
            "/api/v1/case-coaching/questions",
            headers={USER_HEADER: user},
            json=body,
        )

    return _ask


@pytest.fixture
def service_ask(container: Container):
    """Call the service directly. Records the question for the privacy scan."""

    def _ask(
        question: str,
        case_file_id: str = "CASE-FULL-001",
        session_id: str = "sess-level-5",
        user_id: str = DEFAULT_USER,
        request_id: str = "req-test",
    ):
        support.record_question(question)
        return container.service.ask(
            session_id=session_id,
            user_id=user_id,
            question=question,
            case_file_id=case_file_id,
            request_id=request_id,
        )

    return _ask


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Session-wide privacy assertion.

    A test can only scan the output produced before it runs. This hook scans the
    complete session output, so the guarantee holds for every test regardless of
    ordering.
    """
    leaks = support.scan_for_leaks(_LOG_BUFFER.getvalue())
    if leaks:
        print("\nPRIVACY FAILURE - sensitive text found in captured log output:")
        for leak in leaks:
            print("  " + leak)
        session.exitstatus = 1
    else:
        questions = len(support.asked_questions())
        facts = len(support.sensitive_texts())
        print(
            f"\nprivacy scan: {len(_LOG_BUFFER.getvalue().splitlines())} log lines checked against "
            f"{facts} case-fact strings and {questions} question strings - no leaks"
        )
