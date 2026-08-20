"""Test harness for the merged backend.

Every test runs against a REAL SQLite database created from the SQLAlchemy models — no mocks and
no in-memory fakes standing in for persistence — so a passing test proves the data actually
round-trips, the foreign keys hold, and the immutability triggers fire.

Environment variables are set before any ``app`` import because ``app.core.config`` builds its
settings object at import time.

Two ways in, deliberately
-------------------------
``client``   shares the test's session with the app (``get_db`` overridden). Convenient when a test
             sets up data through ``db`` and reads it back over HTTP.
``ctx``      the quiz-configuration harness (``tests/harness.py``), whose client uses the app's own
             request-scoped sessions exactly as production does. Required by the tests that assert
             on committed state through a separate connection and simulate mid-transaction failures.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Environment — MUST precede every `app.*` import
# ---------------------------------------------------------------------------

_TEST_DIR = Path(tempfile.mkdtemp(prefix="quiz-agent-tests-"))
_TEST_DB = _TEST_DIR / "quiz_agent_test.db"

os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["DATABASE_ECHO"] = "false"
os.environ["LOG_LEVEL"] = "CRITICAL"
# Guard disabled by default so the question-bank tests exercise the endpoints directly; the auth
# tests enable it explicitly.
os.environ["ADMIN_API_TOKEN"] = ""
os.environ["CSV_MAX_ROWS"] = "5000"
os.environ["AUTO_SEED"] = "0"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.metadata import target_metadata  # noqa: E402  (registers every module's tables)
from app.db.session import SessionLocal, engine, get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from tests import harness  # noqa: E402  (imports app modules, so it must come after the env)

#: Child-before-parent order, so truncation never trips a foreign key.
_TRUNCATION_ORDER = [
    # UC-08 — retakes. Cleared first, for the reason the list is ordered newest-first: its rows
    # softly reference UC-03's attempts and UC-01's configuration versions. A capability whose
    # tables are missing from this list leaks state between tests, and the symptom is a *later*
    # test failing on a uniqueness constraint it never triggered itself.
    "qt_retake_requests",
    "qt_additional_attempt_grants",
    # UC-07 — coaching. Its sessions reference UC-03's attempts and UC-02's questions (softly).
    "qk_coaching_messages",
    "qk_coaching_activity",
    "qk_knowledge_gaps",
    "qk_coaching_sessions",
    # UC-06 — feedback. References UC-04's result and UC-05's outcome, softly.
    "qf_feedback_items",
    "qf_feedback_reports",
    # UC-05 — pass/fail, certificates, CPD
    "qg_cpd_records",
    "qg_certificates",
    "qg_attempt_outcomes",
    # UC-04 — scoring
    "qr_question_scores",
    "qr_attempt_results",
    # UC-03 — attempt delivery
    "qd_attempt_answer_revisions",
    "qd_attempt_answers",
    "qd_attempt_question_flags",
    "qd_attempt_submissions",
    "qd_attempt_questions",
    "qd_attempts",
    # UC-02 — question bank
    "qb_question_import_errors",
    "qb_question_usages",
    "qb_question_snapshots",
    "qb_question_topics",
    "qb_question_options",
    "qb_questions",
    "qb_question_imports",
    "qb_topics",
    "qb_sequences",
    # UC-01 — quiz configuration. UC-03 owns attempts, so nothing here references a version with
    # a foreign key; the quiz's forward pointer to its active version is cleared below.
    "qc_configuration_version_topics",
    "qc_configuration_version_question_types",
    "qc_configuration_versions",
    "qc_quizzes",
    "qc_courses",
    # Platform placeholders
    "qa_enrolments",
    "qa_users",
]


@pytest.fixture(scope="session", autouse=True)
def _database() -> Generator[None, None, None]:
    target_metadata.create_all(bind=engine)
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_tables() -> Generator[None, None, None]:
    """Give every test an empty database.

    Deliberately does NOT toggle ``PRAGMA foreign_keys``: SQLite silently ignores that pragma
    inside a transaction, which would leave the pooled connection with foreign keys disabled and
    quietly neuter the ON DELETE RESTRICT guarantees these tests rely on. Deleting in
    child-before-parent order needs no such escape hatch.
    """
    with engine.begin() as connection:
        # A quiz points forward at its newest configuration version, so that pointer has to be
        # released before the versions can be deleted.
        connection.execute(text("UPDATE qc_quizzes SET active_configuration_version_id = NULL"))
        for table in _TRUNCATION_ORDER:
            connection.execute(text(f"DELETE FROM {table}"))
    yield


@pytest.fixture
def db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db: Session) -> Generator[TestClient, None, None]:
    """HTTP client sharing the test's session, so assertions see what the request wrote."""
    app = create_app()

    def _override() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def test_db_path() -> Path:
    """Directory holding the test database, for the migration test."""
    return _TEST_DIR


# ---------------------------------------------------------------------------
# Quiz-configuration harness
#
# Registered here rather than in a sub-package conftest so the integration tests — which drive
# both capabilities — get the same context the UC-01 tests use.
# ---------------------------------------------------------------------------


@pytest.fixture
def ctx() -> Generator[harness.Ctx, None, None]:
    """A configured course/quiz plus a stocked question bank (see ``bank.DEFAULT_BANK``)."""
    context = harness.build_ctx()
    try:
        yield context
    finally:
        context.client.close()


@pytest.fixture
def make_ctx():
    """Build a context with a specific question-bank shape, and optionally an AI coach."""
    created: list[harness.Ctx] = []

    def factory(
        plan: dict,
        *,
        topics: list[str] | None = None,
        coaching_llm: object | None = None,
    ) -> harness.Ctx:
        context = harness.build_ctx(plan, topics=topics, coaching_llm=coaching_llm)
        created.append(context)
        return context

    try:
        yield factory
    finally:
        for context in created:
            context.client.close()
