"""Error handling (UC-02 §24), the admin guard, and schema/migration integrity."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests import factories
from tests.factories import API

BACKEND_DIR = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Error envelope
# ---------------------------------------------------------------------------


def test_health_reports_the_database(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    # Both capabilities are mounted on the one API, so health advertises both.
    assert "UC-02 Question Bank Management" in body["modules"]


def test_every_error_uses_the_same_envelope(client: TestClient) -> None:
    responses = [
        client.get(f"{API}/questions/missing"),                              # 404
        client.post(f"{API}/questions", json=factories.single_choice(type="")),  # 422
        client.post(f"{API}/imports", content=b"", headers={"Content-Type": "text/csv"}),  # 400
    ]
    for response in responses:
        body = response.json()
        assert set(body) == {"error"}
        assert {"code", "message"} <= set(body["error"])
        assert isinstance(body["error"]["message"], str) and body["error"]["message"]


def test_errors_never_leak_a_stack_trace(client: TestClient) -> None:
    response = client.post(f"{API}/questions", json=factories.single_choice(type="nonsense"))
    text = response.text
    for leak in ("Traceback", "File \"", "sqlalchemy", ".py\", line"):
        assert leak not in text


def test_malformed_json_body_returns_400(client: TestClient) -> None:
    response = client.post(
        f"{API}/questions",
        content=b"{not json at all",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BAD_REQUEST"


def test_wrong_field_type_returns_400_with_field_detail(client: TestClient) -> None:
    response = client.post(f"{API}/questions", json={"type": "SINGLE_CHOICE", "options": "nope"})
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "BAD_REQUEST"
    assert body["error"]["details"]
    assert any("options" in issue["field"] for issue in body["error"]["details"])


def test_unknown_route_returns_the_error_envelope(client: TestClient) -> None:
    response = client.get(f"{API}/questions/x/y/z/nope")
    assert response.status_code == 404
    assert "error" in response.json()


def test_wrong_method_returns_405(client: TestClient) -> None:
    response = client.put(f"{API}/questions")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_oversized_csv_is_rejected_with_413(client: TestClient, monkeypatch) -> None:
    from app.core import config as config_module
    from app.modules.question_bank.api import imports as imports_module

    monkeypatch.setattr(config_module.settings, "csv_max_bytes", 10, raising=False)
    monkeypatch.setattr(imports_module.settings, "csv_max_bytes", 10, raising=False)

    response = client.post(
        f"{API}/imports",
        content=b"a" * 200,
        headers={"Content-Type": "text/csv"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_invalid_query_parameter_returns_400(client: TestClient) -> None:
    response = client.get(f"{API}/questions", params={"page": 0})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Admin guard
# ---------------------------------------------------------------------------


def test_admin_token_guard_protects_reads_and_writes_when_configured(client: TestClient, monkeypatch) -> None:
    # Patch the settings singleton itself: identity resolution and every guard read the same
    # object, so there is one place to turn the token requirement on.
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "admin_api_token", "s3cret", raising=False)

    unauthenticated = client.post(f"{API}/questions", json=factories.single_choice())
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "UNAUTHORIZED"

    wrong = client.post(
        f"{API}/questions",
        json=factories.single_choice(),
        headers={"Authorization": "Bearer wrong"},
    )
    assert wrong.status_code == 401

    authorised = client.post(
        f"{API}/questions",
        json=factories.single_choice(),
        headers={"Authorization": "Bearer s3cret"},
    )
    assert authorised.status_code == 201

    # Reads are guarded too, and this is the line that changed at merge time.
    #
    # UC-02 shipped with its reads open and said so here: "the platform's real auth decides that
    # policy at merge time". This is that merge, and the policy is that they are not open. The
    # payload carries ``isCorrect`` on every option, ``correctPosition`` for a drag-to-order and
    # ``isPrimary`` on a scenario's sub-questions — that is the answer key. With the learner API
    # and the admin API behind one gateway, an authenticated learner could otherwise have read the
    # answer to every question in the bank before sitting the quiz.
    assert client.get(f"{API}/questions").status_code == 401
    assert (
        client.get(f"{API}/questions", headers={"Authorization": "Bearer s3cret"}).status_code
        == 200
    )


def test_actor_is_recorded_from_the_admin_header(client: TestClient) -> None:
    created = client.post(
        f"{API}/questions",
        json=factories.single_choice(),
        headers={"X-Admin-User": "h.khan"},
    ).json()
    assert created["createdBy"] == "h.khan"
    assert created["updatedBy"] == "h.khan"


# ---------------------------------------------------------------------------
# Schema / migration integrity
# ---------------------------------------------------------------------------


# The whole-schema drift guard lives in ``tests/test_schema_migration.py``. It used to be here,
# as a `compare_metadata` diff; UC-03 brought a table-by-table, constraint-by-constraint comparison
# that is strictly stronger, so the two were merged into the one cross-capability test rather than
# left as two checks of the same thing.


def test_foreign_keys_are_enforced() -> None:
    """SQLite disables FK enforcement per connection; the engine hook must turn it on.

    The historical-data guarantees rely on ON DELETE RESTRICT actually firing.
    """
    from sqlalchemy import text

    from app.db.session import engine

    with engine.connect() as connection:
        enabled = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
    assert enabled == 1


def test_database_refuses_to_delete_a_question_with_usage(client: TestClient, db) -> None:
    """Defence in depth: even bypassing the service layer, the FK blocks the delete.

    The raw SQL runs on the *same* session the request used. A second connection would be the more
    obvious way to bypass the service, but the engine takes its write lock at ``BEGIN`` (see
    ``app/db/session.py``), so two write-capable connections in one test simply block each other —
    which would prove nothing about the constraint.
    """
    from sqlalchemy import text

    question = factories.create(client, factories.single_choice())
    usage = client.post(
        f"{API}/delivery/usages",
        json={"attemptRef": "att-fk", "questionId": question["id"]},
    )
    assert usage.status_code == 201

    try:
        db.execute(text("DELETE FROM qb_questions WHERE id = :id"), {"id": question["id"]})
        db.commit()
        raise AssertionError("the database allowed a question with usage to be deleted")
    except AssertionError:
        raise
    except Exception as exc:  # IntegrityError from the RESTRICT constraint
        db.rollback()
        assert "FOREIGN KEY" in str(exc).upper() or "CONSTRAINT" in str(exc).upper()
