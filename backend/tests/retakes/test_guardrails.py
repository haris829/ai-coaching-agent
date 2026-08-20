"""Scope boundaries, asserted rather than promised (§18, §19, §23, §25).

Every claim this module makes about what it does *not* do is checked here, because a scope boundary
that exists only in a README is a boundary that erodes.

**The merge moved three of these boundaries, and the tests say so rather than being deleted.**
Standalone, UC-08 owned no schema, no SQL, and shipped a single-file test console. In the merged
application it owns two ``qt_`` tables and a SQLAlchemy binding for them, and there is one React
test UI for the whole system. Those were never quite the point; the point was that *the module's
rules* do not depend on a database, and that UC-08 does not build a second frontend. So the tests
below assert the narrower, still-true form: persistence is confined to two files, the domain and
the services never import an ORM, and UC-08 contributes no frontend of its own.

The boundaries that did **not** move — no write onto anything upstream, no answer or mark, no
scoring arithmetic, no delete, ownership-scoped reads — are asserted exactly as before.
"""

from __future__ import annotations

import inspect
import pathlib
import re
from pathlib import Path

import pytest

from app.core.config import Settings
from app.modules.retakes.integration import downstream, uc01, uc02, uc03
from app.modules.retakes.repositories import protocols

pytestmark = pytest.mark.anyio

APP_ROOT = Path(__file__).resolve().parents[2] / "app"
MODULE_ROOT = APP_ROOT / "modules" / "retakes"
SOURCE_FILES = sorted(
    path for path in MODULE_ROOT.rglob("*.py") if "__pycache__" not in path.parts
)

#: The layers that must stay free of any database: the rules, the orchestration, the wire format
#: and the HTTP surface. Everything UC-08 actually *decides* lives here, and all of it stays
#: runnable and testable without a database — which is what the in-memory binding still being this
#: suite's default demonstrates.
#:
#: ``models.py``, ``repositories/sqlalchemy.py``, the ``integration/*_adapter.py`` files and the
#: composition root are deliberately outside it: an adapter's whole job is to hold a session, and
#: a composition root's is to bind one.
DATABASE_FREE_LAYERS = ("domain", "services", "schemas", "api")


def _database_free_sources() -> dict[pathlib.Path, str]:
    return {
        path: text
        for path, text in _sources().items()
        if path.relative_to(MODULE_ROOT).parts[0] in DATABASE_FREE_LAYERS
    }


def _sources() -> dict[Path, str]:
    return {path: path.read_text(encoding="utf-8") for path in SOURCE_FILES}


# ---------------------------------------------------------------------------
# §19 — no database, no schema, no credential
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden",
    ["import sqlalchemy", "from sqlalchemy", "import alembic", "import sqlite3", "import psycopg"],
)
def test_the_orm_never_reaches_the_rules(forbidden):
    """Persistence is still a protocol; only its binding and its adapters know SQLAlchemy.

    Standalone this asserted zero ORM imports, because UC-08 owned no schema at all. The merged
    module owns ``qt_retake_requests`` and ``qt_additional_attempt_grants``, so the claim narrows
    to where it always mattered: a domain rule or a service that imported an ORM could no longer
    be reasoned about, or tested, without a database.
    """
    offenders = [
        str(path.relative_to(MODULE_ROOT))
        for path, text in _database_free_sources().items()
        if forbidden in text
    ]
    assert offenders == []


def test_uc08_contributes_no_database_setting():
    """UC-08 does not decide where the system's data lives.

    The application has one ``DATABASE_URL``, set once for every capability. What is checked here
    is that none of UC-08's *own* settings names a database, a connection or a credential — a
    second one would mean a second store.
    """
    uc08_settings = {
        "retake_configuration_policy",
        "max_grant_additional_attempts",
        "exhausted_contact_guidance",
    }
    assert uc08_settings <= set(Settings.model_fields)
    assert not {
        name
        for name in uc08_settings
        if any(word in name for word in ("database", "dsn", "url", "password", "token"))
    }


def test_no_credential_is_hard_coded():
    """The one secret-shaped setting is a token read from the environment, unset by default."""
    assert Settings().admin_api_token is None
    suspicious = [
        path.name
        for path, text in _sources().items()
        if "password=" in text.lower() or "secret_key" in text.lower()
    ]
    assert suspicious == []


def test_no_raw_sql_is_written_anywhere():
    """Queries are built through the ORM, so no string in this module can become a query.

    The persistence binding is exempt from the *import* rule above but not from this one: it
    composes ``select()`` expressions, and there is no hand-written SQL anywhere in the module.
    """
    # Matched as statements rather than as keywords, so the ``MULTI_SELECT`` question type is not
    # mistaken for a query.
    statements = re.compile(
        r"\bINSERT\s+INTO\b|\bDELETE\s+FROM\b|\b(CREATE|ALTER|DROP)\s+(TABLE|INDEX)\b"
        r"|\bSELECT\b[\s\S]{0,120}?\bFROM\b",
        re.IGNORECASE,
    )
    offenders = [
        str(path.relative_to(MODULE_ROOT))
        for path, text in _database_free_sources().items()
        # The protocol docstring shows the indexes the schema must carry; that is documentation of
        # a requirement, not SQL this module runs.
        if path.name != "protocols.py" and statements.search(text)
    ]
    assert offenders == []


# ---------------------------------------------------------------------------
# §3 / §23 — nothing upstream can be written
# ---------------------------------------------------------------------------


def _protocol_methods(protocol: type) -> set[str]:
    return {
        name
        for name, _ in inspect.getmembers(protocol, inspect.isfunction)
        if not name.startswith("_")
    }


@pytest.mark.parametrize(
    "port",
    [
        uc01.ConfigurationProvider,
        uc02.QuestionBankProvider,
        downstream.ScoringResultProvider,
        downstream.PassFailResultProvider,
        downstream.FeedbackProvider,
        downstream.CoachingProvider,
    ],
)
def test_upstream_ports_are_read_only(port):
    """No method on any of these could change a configuration, a question, a score or a result."""
    forbidden_prefixes = ("create", "update", "save", "delete", "set", "write", "publish", "put")
    offenders = [
        name
        for name in _protocol_methods(port)
        if name.startswith(forbidden_prefixes)
    ]
    assert offenders == []


def test_the_only_write_onto_uc03_is_creating_a_retake_attempt():
    """§3: a retake adds an attempt. It cannot touch the one it followed."""
    methods = _protocol_methods(uc03.AttemptProvider)
    writes = {name for name in methods if not name.startswith(("get", "list", "count", "find"))}
    assert writes == {"create_retake_attempt"}


def test_there_is_no_port_onto_answers_or_marks():
    """UC-08 never sees an answer or a mark, so it cannot alter one.

    UC-04's confirmed totals cross the boundary for display in attempt history, and nothing else:
    there is no answer type, no option, no correctness flag and no answer key anywhere in the
    module.
    """
    text = "\n".join(_sources().values())
    for forbidden in ("is_correct", "answer_key", "selected_option_id", "correct_position"):
        assert forbidden not in text


def test_the_module_performs_no_scoring_arithmetic():
    """§23: UC-04 owns marks. A percentage computed here would be a second answer to one question."""
    score_fields = set(downstream.AttemptScore.__dataclass_fields__)
    assert {"total_marks", "maximum_marks", "percentage"} <= score_fields
    # The scoring type is read in exactly one place — the history assembler — and only copied.
    history = (MODULE_ROOT / "services/history_service.py").read_text(encoding="utf-8")
    assert "percentage=score.percentage" in history
    assert "/ " not in history.split("def _entry")[1].split("def _read")[0]


# ---------------------------------------------------------------------------
# §18 / §19 — the persistence contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "repository", [protocols.RetakeRequestRepository, protocols.GrantRepository]
)
def test_no_repository_can_delete(repository):
    """Erasure is a separate, audited capability. It will not be built on this one by accident."""
    assert not any(name.startswith(("delete", "remove", "purge")) for name in _protocol_methods(repository))


def test_retake_reads_are_ownership_scoped():
    assert "get_for_learner" in _protocol_methods(protocols.RetakeRequestRepository)


def test_grant_reads_are_scoped_to_learner_course_and_quiz():
    signature = inspect.signature(protocols.GrantRepository.list_for_learner_quiz)
    assert {"learner_id", "course_id", "quiz_id"} <= set(signature.parameters)


# ---------------------------------------------------------------------------
# §20 / §25 — no production frontend, no unnecessary infrastructure
# ---------------------------------------------------------------------------


def test_uc08_ships_no_frontend_of_its_own():
    """The merged system has one test UI. UC-08 added retake screens to it, not a second app.

    Standalone, UC-08 served a single-file console so it could be clicked through before UC-01…
    UC-07 existed. Keeping it would have been precisely the duplication this integration set out
    to remove — a second place deciding what a learner may do next.
    """
    assert not (MODULE_ROOT / "static").exists()
    assert not list(MODULE_ROOT.rglob("*.html"))
    assert not list(MODULE_ROOT.rglob("*.jsx"))
    assert not list(MODULE_ROOT.rglob("*.tsx"))
    assert not list(MODULE_ROOT.rglob("package.json"))


@pytest.mark.parametrize(
    "forbidden", ["import redis", "import kafka", "import celery", "kubernetes", "import boto3"]
)
def test_no_distributed_infrastructure_is_introduced(forbidden):
    """§25: correct business rules and clean boundaries, not a platform."""
    offenders = [path.name for path, text in _sources().items() if forbidden in text]
    assert offenders == []


def test_uc08_added_no_runtime_dependency():
    """§25: correct business rules and clean boundaries, not a platform.

    The merged backend runs on the dependency set UC-01…UC-07 already had. UC-08 introduced no
    package of its own — no queue client, no scheduler, no HTTP client for reaching a capability
    that lives in the same process.
    """
    requirements = (APP_ROOT.parent / "requirements.txt").read_text(encoding="utf-8")
    packages = {
        re.split(r"[>=<\[]", line, maxsplit=1)[0].strip().lower()
        for line in requirements.splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert {"fastapi", "pydantic", "sqlalchemy", "alembic"} <= packages
    assert not packages & {"redis", "celery", "kafka-python", "boto3", "kubernetes"}


# ---------------------------------------------------------------------------
# The unconfigured defaults are honest (§19)
# ---------------------------------------------------------------------------


async def test_an_unwired_module_refuses_rather_than_inventing():
    """Every unbound port reports nothing. An unwired deployment says so."""
    from app.modules.retakes.container import create_container
    from app.modules.retakes.domain.errors import QuestionBankUnavailableError, QuizNotFoundError

    container = create_container()

    with pytest.raises(QuizNotFoundError):
        await container.services.eligibility.check("learner-alice", "quiz-1")

    with pytest.raises(QuestionBankUnavailableError):
        await container.ports.question_bank.find_eligible_questions(
            uc02.QuestionPoolQuery(quiz_id="quiz-1", course_id="course-1")
        )

    history = await container.services.history.for_learner_quiz("learner-alice", "quiz-1")
    assert history.attempt_count == 0
