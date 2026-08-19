"""End-to-end verification against a LIVE uvicorn server and a real database file.

The pytest suite uses an in-process ASGI client. This script goes further: it boots a real uvicorn
server on a real SQLite file created by the real Alembic migration, drives the documented workflows
of **both** capabilities over HTTP, and — for the persistence claims — re-opens the database file
with a separate connection to confirm the data is genuinely on disk rather than in a session.

Sections 1–15 cover UC-02 (question bank). Sections 16–19 cover UC-01 (quiz configuration and
rules). Sections 20–24 cover UC-03 (attempt delivery) and the integration of all three: bank counts →
configuration → validation → immutable version → eligibility → attempt locked to a version → answer →
autosave → review → confirmed submission → historical report.

    python -m scripts.verify_e2e
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


@contextmanager
def readonly_db(path: Path) -> Iterator[sqlite3.Connection]:
    """Independent connection used to prove data is really on disk.

    ``with sqlite3.connect(...)`` commits but does NOT close, and a lingering read transaction
    against a WAL database blocks the server's writes — so the close is explicit here.
    """
    connection = sqlite3.connect(path, timeout=10)
    try:
        yield connection
    finally:
        connection.close()


def _rejects_update(path: Path, statement: str, parameter: str, marker: str) -> bool:
    """True when the database refused an UPDATE with the expected immutability message.

    The triggers are the load-bearing part of "a confirmed score, a determined outcome and a generated
    report are never edited", and only a live database can prove they are actually installed -- which is
    exactly what this script is for.
    """
    connection = sqlite3.connect(path, timeout=10)
    try:
        connection.execute(statement, (parameter,))
        connection.commit()
        return False
    except sqlite3.DatabaseError as exc:
        return marker in str(exc)
    finally:
        connection.close()

BACKEND_DIR = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8123"
API = f"{BASE}/api/question-bank"
QC = f"{BASE}/api"
#: UC-03 versions its routes, so its base differs from the other two.
V1 = f"{BASE}/api/v1"

#: Development credentials inserted directly into the placeholder identity table below. The
#: company system will own these rows, which is exactly why they are set up out-of-band here.
ADMIN_TOKEN = "e2e-admin-token"
LEARNER_TOKEN = "e2e-learner-token"

failures: list[str] = []
checks = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if condition:
        print(f"  [PASS] {label}")
    else:
        failures.append(f"{label}{f' — {detail}' if detail else ''}")
        print(f"  [FAIL] {label}{f' — {detail}' if detail else ''}")


def call(
    method: str,
    path: str,
    body: Any = None,
    *,
    raw: bytes | None = None,
    content_type: str | None = None,
    token: str | None = None,
) -> tuple[int, Any]:
    url = path if path.startswith("http") else f"{API}{path}"
    data: bytes | None = None
    headers = {"X-Admin-User": "verify-script"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    if raw is not None:
        data = raw
        headers["Content-Type"] = content_type or "text/csv"
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
            try:
                return response.status, json.loads(payload)
            except ValueError:
                return response.status, payload.decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            return exc.code, json.loads(payload)
        except ValueError:
            return exc.code, payload.decode("utf-8", "replace")


def wait_for_server(timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE}/api/health", timeout=3) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def answer_payload(question: dict[str, Any], *, variant: int = 0) -> Any:
    """A valid answer for a delivered question, per its type.

    `variant` picks a different-but-valid answer, which is how the idempotency and revision checks
    distinguish "saved again unchanged" from "genuinely updated".
    """
    # A sub-question reports its type as `type`; a delivered question as `questionType`.
    kind = question.get("questionType") or question["type"]

    if kind == "SINGLE_CHOICE":
        options = question["options"]
        return {"selectedOptionId": options[variant % len(options)]["optionId"]}
    if kind == "TRUE_FALSE":
        return {"value": variant % 2 == 0}
    if kind == "MULTI_SELECT":
        options = question["options"]
        return {"selectedOptionIds": [option["optionId"] for option in options[: 1 + (variant % 2)]]}
    if kind == "DRAG_TO_ORDER":
        items = [item["itemId"] for item in question["orderItems"]]
        return {"orderedItemIds": list(reversed(items)) if variant % 2 else items}
    if kind == "SCENARIO":
        return {
            "responses": [
                {"subQuestionId": sub["subQuestionId"], "answer": answer_payload(sub, variant=variant)}
                for sub in question["subQuestions"]
            ]
        }
    raise AssertionError(f"Unsupported question type in the verification script: {kind}")


def section(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def single_choice(variant: str = "") -> dict[str, Any]:
    """A valid single-choice payload. ``variant`` makes it a distinct question.

    The bank rejects duplicate content, so anything that needs several questions has to vary the
    text rather than post the same payload twice.
    """
    return {
        "type": "SINGLE_CHOICE",
        "questionText": (
            "Which HTTP status code indicates a conflict with the current state?" + variant
        ),
        "explanation": "409 Conflict signals the request clashes with the resource's state.",
        "topics": ["HTTP", "APIs"],
        "difficulty": "MEDIUM",
        "options": [
            {"label": "A", "text": "400 Bad Request", "isCorrect": False},
            {"label": "B", "text": "404 Not Found", "isCorrect": False},
            {"label": "C", "text": "409 Conflict", "isCorrect": True},
            {"label": "D", "text": "500 Internal Server Error", "isCorrect": False},
        ],
        "scoring": {"points": 1, "scoringStrategy": "ALL_OR_NOTHING"},
    }


def drag_to_order() -> dict[str, Any]:
    return {
        "type": "DRAG_TO_ORDER",
        "questionText": "Order the phases of a database migration release.",
        "explanation": "Back up, apply, verify, then switch traffic.",
        "topics": ["Databases"],
        "options": [
            {"label": "A", "text": "Take a backup", "position": 1, "correctPosition": 1},
            {"label": "B", "text": "Apply the migration", "position": 2, "correctPosition": 2},
            {"label": "C", "text": "Verify the schema", "position": 3, "correctPosition": 3},
            {"label": "D", "text": "Switch traffic", "position": 4, "correctPosition": 4},
        ],
        "scoring": {"points": 4, "scoringStrategy": "PARTIAL_CREDIT"},
    }


CSV_MIXED = """type,question_text,scenario_text,options,correct_answers,correct_order,explanation,topics,points,scoring_strategy,penalty_per_incorrect,difficulty,external_ref
TRUE_FALSE,"HTTP 201 means Created",,,TRUE,,"201 is returned when a resource is created.",HTTP,1,,,EASY,E2E-1
SINGLE_CHOICE,"Bad type row",,"A:1|B:2|C:3|D:4",A,,"Explanation.",HTTP,1,,,,E2E-2
MULTI_SELECT,"Which are 2xx status codes?",,"A:200|B:201|C:404|D:500",A|B,,"200 and 201 are success codes.",HTTP,2,PARTIAL_CREDIT,,MEDIUM,E2E-3
badtype,"This row has an invalid type",,"A:1|B:2",A,,"Explanation.",HTTP,1,,,,E2E-4
SINGLE_CHOICE,"Which one references a missing option?",,"A:1|B:2|C:3|D:4",Z,,"Explanation.",HTTP,1,,,,E2E-5
DRAG_TO_ORDER,"Order these steps",,"A:First|B:Second|C:Third",,A|B,"Explanation.",Process,3,,,,E2E-6
"""


def seed_platform_rows(path: Path) -> None:
    """Insert the identities, course and quiz UC-01 needs.

    Written with raw SQL on purpose: these are the rows the company's own systems will own, so
    there is no API that creates them. Everything the verification actually exercises goes over
    HTTP.
    """
    now = "2026-01-01 00:00:00"
    with readonly_db(path) as conn:
        conn.executemany(
            "INSERT INTO qa_users (email, display_name, role, api_token, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("e2e-admin@example.com", "E2E Admin", "admin", ADMIN_TOKEN, now),
                ("e2e-learner@example.com", "E2E Learner", "learner", LEARNER_TOKEN, now),
            ],
        )
        conn.execute(
            "INSERT INTO qc_courses (code, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("E2E-101", "End-to-end Course", now, now),
        )
        course_id = conn.execute("SELECT id FROM qc_courses WHERE code = 'E2E-101'").fetchone()[0]
        conn.execute(
            "INSERT INTO qc_quizzes (course_id, slug, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (course_id, "e2e-quiz", "End-to-end Quiz", now, now),
        )
        # UC-03 refuses to create an attempt for a learner who is not enrolled on the course, so
        # without this the seeded world would look configured but be unusable. The enrolment is
        # keyed by string because UC-03 treats both ids as opaque.
        learner_id = conn.execute(
            "SELECT id FROM qa_users WHERE email = 'e2e-learner@example.com'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO qa_enrolments (learner_id, course_id, status, enrolled_at) "
            "VALUES (?, ?, ?, ?)",
            (str(learner_id), str(course_id), "ACTIVE", now),
        )
        conn.commit()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="qb-e2e-"))
    db_path = tmp / "e2e.db"

    env = os.environ.copy()
    env.update(
        {
            "ENVIRONMENT": "development",
            "LOG_LEVEL": "WARNING",
            "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
            "ADMIN_API_TOKEN": "",
            "PYTHONPATH": str(BACKEND_DIR),
        }
    )

    print("COURSES QUIZ AGENT - END-TO-END VERIFICATION (UC-01 ... UC-07)")
    print("=" * 60)
    print(f"database : {db_path}")

    section("0. Schema created by the real Alembic migration")
    migrate = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    check("alembic upgrade head succeeds", migrate.returncode == 0, migrate.stderr[-300:])
    with readonly_db(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    for table in (
        "qb_questions",
        "qb_question_options",
        "qb_topics",
        "qb_question_topics",
        "qb_question_snapshots",
        "qb_question_usages",
        "qb_question_imports",
        "qb_question_import_errors",
        "qa_users",
        "qc_courses",
        "qc_quizzes",
        "qc_configuration_versions",
        "qc_configuration_version_question_types",
        "qc_configuration_version_topics",
        # UC-03 owns attempts. UC-01's own `qc_attempts` table was dropped when the two merged, so
        # that there is exactly one record of an attempt in the system.
        "qd_attempts",
        "qd_attempt_questions",
        "qd_attempt_answers",
        "qd_attempt_answer_revisions",
        "qd_attempt_question_flags",
        "qd_attempt_submissions",
        "qa_enrolments",
        # UC-07 owns the coaching conversation and what it records about it.
        "qk_coaching_sessions",
        "qk_coaching_messages",
        "qk_knowledge_gaps",
        "qk_coaching_activity",
    ):
        check(f"table {table} exists", table in tables)

    with readonly_db(db_path) as conn:
        triggers = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        }
    for trigger in (
        "trg_qc_config_version_no_update",
        "trg_qc_config_version_types_no_update",
        "trg_qc_config_version_topics_no_update",
        # UC-07's append-only guarantees. Checked here as well as in pytest because a
        # batch_alter_table after their creation would silently drop them, and only a run
        # against a genuinely migrated database can catch that.
        "trg_qk_message_no_update",
        "trg_qk_activity_no_update",
    ):
        # The migration must ship the integrity triggers, not only `create_all`.
        check(f"trigger {trigger} exists", trigger in triggers)

    seed_platform_rows(db_path)
    check("platform rows (identities, course, quiz) inserted", True)

    # Server output goes to a file rather than a pipe: an unread pipe fills up and blocks the
    # server mid-run, which is very hard to diagnose.
    server_log = tmp / "server.log"
    log_handle = server_log.open("w", encoding="utf-8")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8123"],
        cwd=BACKEND_DIR,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        section("1. Live server health")
        if not wait_for_server():
            print("  [FAIL] server did not start")
            log_handle.flush()
            print(server_log.read_text(encoding="utf-8", errors="replace")[-3000:])
            return 1
        status, health = call("GET", f"{BASE}/api/health")
        check("GET /api/health returns 200", status == 200)
        check("health reports the database reachable", health.get("database") == "ok", str(health))

        # -------------------------------------------------------------- create
        section("2. Create question -> persist")
        status, created = call("POST", "/questions", single_choice())
        check("POST /questions returns 201", status == 201, json.dumps(created)[:300])
        question_id = created["id"]
        reference = created["reference"]
        check("a human-readable reference was allocated", reference.startswith("Q-"), reference)
        check("correct answer stored", created["correctLabels"] == ["C"], str(created["correctLabels"]))
        check("topics stored relationally", len(created["topics"]) == 2)

        # Independent connection: proves it is really on disk.
        with readonly_db(db_path) as conn:
            row = conn.execute(
                "SELECT question_text, status, version FROM qb_questions WHERE id = ?", (question_id,)
            ).fetchone()
            options = conn.execute(
                "SELECT COUNT(*) FROM qb_question_options WHERE question_id = ?", (question_id,)
            ).fetchone()[0]
            topic_links = conn.execute(
                "SELECT COUNT(*) FROM qb_question_topics WHERE question_id = ?", (question_id,)
            ).fetchone()[0]
            snapshots = conn.execute(
                "SELECT COUNT(*) FROM qb_question_snapshots WHERE question_id = ?", (question_id,)
            ).fetchone()[0]
        check("question row present in the database file", row is not None)
        check("4 option rows persisted", options == 4, str(options))
        check("2 topic link rows persisted", topic_links == 2, str(topic_links))
        check("version-1 snapshot persisted", snapshots == 1, str(snapshots))

        # -------------------------------------------------------------- read
        section("3. View question")
        status, fetched = call("GET", f"/questions/{question_id}")
        check("GET by id returns 200", status == 200)
        status, by_ref = call("GET", f"/questions/{reference}")
        check("GET by reference returns the same question", by_ref.get("id") == question_id)
        status, listing = call("GET", "/questions?pageSize=50")
        check("question appears in the list", any(i["id"] == question_id for i in listing["items"]))

        # -------------------------------------------------------------- validation
        section("4. Backend validation rejects invalid input")
        bad = single_choice()
        bad["questionText"] = ""
        status, response = call("POST", "/questions", bad)
        check("empty question text rejected with 422", status == 422, str(status))
        codes = {issue["code"] for issue in response.get("error", {}).get("details", [])}
        check("field-level code returned", "QUESTION_TEXT_REQUIRED" in codes, str(codes))

        bad2 = single_choice()
        bad2["type"] = "multiplechoicee"
        status, response = call("POST", "/questions", bad2)
        check("invalid question type rejected with 422", status == 422)
        check(
            "error message names the invalid value",
            "multiplechoicee" in json.dumps(response),
            json.dumps(response)[:200],
        )

        with readonly_db(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM qb_questions").fetchone()[0]
        check("no invalid question was persisted", count == 1, f"{count} rows")

        # -------------------------------------------------------------- edit
        section("5. Edit -> new version, history preserved")
        status, updated = call(
            "PATCH",
            f"/questions/{question_id}",
            {"questionText": "Which HTTP status code signals a state conflict?"},
        )
        check("PATCH returns 200", status == 200, json.dumps(updated)[:200])
        check("version bumped to 2", updated.get("version") == 2, str(updated.get("version")))
        status, versions = call("GET", f"/questions/{question_id}/versions")
        check("two frozen versions exist", len(versions) == 2, str(len(versions)))
        check(
            "version 1 still holds the original text",
            versions[0]["questionText"].endswith("with the current state?"),
            versions[0]["questionText"],
        )

        # -------------------------------------------------------------- topics
        section("6. Manage topics")
        status, tagged = call("POST", f"/questions/{question_id}/topics", {"topicNames": ["Status Codes"]})
        check("topic assigned", status == 200 and len(tagged["topics"]) == 3, str(status))
        topic_id = next(t["id"] for t in tagged["topics"] if t["name"] == "Status Codes")
        status, untagged = call("DELETE", f"/questions/{question_id}/topics/{topic_id}")
        check("topic removed", status == 200 and len(untagged["topics"]) == 2, str(status))
        status, topics = call("GET", "/topics")
        check("topics listed with question counts", status == 200 and len(topics) >= 2)

        # -------------------------------------------------------------- deliver + complete
        section("7. Deliver to an attempt and complete it")
        status, pool = call("GET", "/delivery/pool?limit=50")
        check("question is in the delivery pool", any(i["id"] == question_id for i in pool["items"]))
        check(
            "the delivery pool withholds the answer key",
            all("correctLabels" not in item for item in pool["items"]),
        )

        status, usage = call(
            "POST",
            "/delivery/usages",
            {"attemptRef": "E2E-ATTEMPT-1", "questionId": question_id, "learnerRef": "learner-1"},
        )
        check("usage recorded with 201", status == 201, json.dumps(usage)[:200])
        check("snapshot pinned at the current version", usage.get("snapshotVersion") == 2, str(usage))

        status, answered = call(
            "PATCH",
            f"/delivery/usages/{usage['id']}",
            {"selectedLabels": ["C"], "attemptStatus": "COMPLETED"},
        )
        check("response graded correct", answered.get("isCorrect") is True, json.dumps(answered)[:200])
        check("points awarded", answered.get("awardedPoints") == 1.0, str(answered.get("awardedPoints")))

        # -------------------------------------------------------------- retire
        section("8. Retire -> excluded from delivery, history intact")
        status, retired = call(
            "POST", f"/questions/{question_id}/retire", {"reason": "Verified by the e2e script"}
        )
        check("retire returns 200", status == 200)
        check("status is RETIRED", retired.get("status") == "RETIRED")
        check("isDeliverable is false", retired.get("isDeliverable") is False)
        check("retirement reason recorded", retired.get("retiredReason") == "Verified by the e2e script")

        status, pool_after = call("GET", "/delivery/pool?limit=50")
        check(
            "retired question is gone from the delivery pool",
            all(item["id"] != question_id for item in pool_after["items"]),
        )
        status, blocked = call(
            "POST", "/delivery/usages", {"attemptRef": "E2E-ATTEMPT-2", "questionId": question_id}
        )
        check("delivering a retired question is refused with 409", status == 409, str(status))

        status, still_readable = call("GET", f"/questions/{question_id}")
        check("retired question is still readable", status == 200)
        check("its identity is unchanged", still_readable.get("reference") == reference)

        status, retire_again = call("POST", f"/questions/{question_id}/retire", {})
        check("retiring twice returns 409", status == 409, str(status))

        # -------------------------------------------------------------- historical report
        section("9. Historical attempt report survives retirement")
        status, report = call("GET", "/reporting/attempts/E2E-ATTEMPT-1")
        check("report returns 200 after retirement", status == 200, json.dumps(report)[:200])
        item = report["items"][0] if report.get("items") else {}
        check("question text preserved", bool(item.get("questionText")))
        check("question type preserved", item.get("type") == "SINGLE_CHOICE")
        check("options preserved", len(item.get("options", [])) == 4)
        check("correct answer preserved", item.get("correctLabels") == ["C"])
        check(
            "learner response preserved",
            (item.get("learnerResponse") or {}).get("selectedLabels") == ["C"],
            json.dumps(item.get("learnerResponse")),
        )
        check("score preserved", item.get("awardedPoints") == 1.0)
        check("original identity preserved", item.get("questionReference") == reference)
        check("live status reported as context", item.get("currentQuestionStatus") == "RETIRED")

        # -------------------------------------------------------------- delete guard
        section("10. Hard delete refused once history exists")
        status, refused = call("DELETE", f"/questions/{question_id}")
        check("DELETE returns 409", status == 409, str(status))
        check(
            "error code is QUESTION_HAS_HISTORY",
            refused.get("error", {}).get("code") == "QUESTION_HAS_HISTORY",
            json.dumps(refused)[:200],
        )
        with readonly_db(db_path) as conn:
            survived = conn.execute(
                "SELECT COUNT(*) FROM qb_questions WHERE id = ?", (question_id,)
            ).fetchone()[0]
        check("the question is still in the database", survived == 1)

        # -------------------------------------------------------------- drag-to-order
        section("11. Drag-to-order keeps presentation and answer order separate")
        status, ordering = call("POST", "/questions", drag_to_order())
        check("drag-to-order created", status == 201, json.dumps(ordering)[:200])
        check("correct order preserved", ordering.get("correctOrder") == ["A", "B", "C", "D"])
        check("no isCorrect flags used", ordering.get("correctLabels") == [])

        status, ord_usage = call(
            "POST",
            "/delivery/usages",
            {
                "attemptRef": "E2E-ATTEMPT-ORDER",
                "questionId": ordering["id"],
                # Shown to the learner in a shuffled order.
                "presentationOrder": ["C", "A", "D", "B"],
            },
        )
        check("usage records the shuffled presentation order", status == 201)
        status, ord_answer = call(
            "PATCH",
            f"/delivery/usages/{ord_usage['id']}",
            {"orderedLabels": ["A", "B", "C", "D"], "attemptStatus": "COMPLETED"},
        )
        check(
            "graded against the correct order, not the display order",
            ord_answer.get("isCorrect") is True,
            json.dumps(ord_answer)[:200],
        )
        check("full marks awarded", ord_answer.get("awardedPoints") == 4.0)
        check(
            "presentation order kept separately",
            ord_answer.get("presentationOrder") == ["C", "A", "D", "B"],
            str(ord_answer.get("presentationOrder")),
        )

        # -------------------------------------------------------------- CSV template
        section("12. CSV template")
        status, template = call("GET", "/imports/template")
        check("template downloads", status == 200)
        check(
            "template covers all five types",
            all(
                t in template
                for t in ("SINGLE_CHOICE", "TRUE_FALSE", "MULTI_SELECT", "SCENARIO", "DRAG_TO_ORDER")
            ),
        )

        # -------------------------------------------------------------- CSV import
        section("13. CSV import: valid rows imported, invalid rows reported")
        status, result = call(
            "POST", "/imports", raw=CSV_MIXED.encode("utf-8"), content_type="text/csv"
        )
        check("import returns 201", status == 201, json.dumps(result)[:300])
        check("6 data rows counted", result.get("totalRows") == 6, str(result.get("totalRows")))
        check("3 rows imported", result.get("importedRows") == 3, str(result.get("importedRows")))
        check("3 rows rejected", result.get("rejectedRows") == 3, str(result.get("rejectedRows")))
        check(
            "counts reconcile",
            result.get("importedRows", 0) + result.get("rejectedRows", 0) == result.get("totalRows"),
        )

        rejected_codes = {
            row["rowNumber"]: {issue["code"] for issue in row["errors"]}
            for row in result.get("rejected", [])
        }
        check(
            "invalid question type reported on its row",
            any("INVALID_QUESTION_TYPE" in codes for codes in rejected_codes.values()),
            str(rejected_codes),
        )
        check(
            "unknown option reference reported",
            any(
                "CORRECT_ANSWER_REFERENCES_UNKNOWN_OPTION" in codes
                for codes in rejected_codes.values()
            ),
            str(rejected_codes),
        )
        check(
            "incomplete ordering data reported",
            any("DRAG_TO_ORDER_MISSING_POSITIONS" in codes for codes in rejected_codes.values()),
            str(rejected_codes),
        )
        check(
            "every rejected row carries a row number and a message",
            all(
                row["rowNumber"] > 1 and all(issue["message"] for issue in row["errors"])
                for row in result.get("rejected", [])
            ),
        )

        with readonly_db(db_path) as conn:
            imported = conn.execute(
                "SELECT COUNT(*) FROM qb_questions WHERE import_id = ?", (result["id"],)
            ).fetchone()[0]
            errors = conn.execute(
                "SELECT COUNT(*) FROM qb_question_import_errors WHERE import_id = ?",
                (result["id"],),
            ).fetchone()[0]
        check("imported rows persisted with provenance", imported == 3, str(imported))
        check("row errors persisted for later re-reading", errors >= 3, str(errors))

        status, reread = call("GET", f"/imports/{result['id']}")
        check("import report can be re-read", status == 200 and reread["rejectedRows"] == 3)

        section("14. CSV whole-file failure imports nothing")
        status, failure = call(
            "POST", "/imports", raw=b"type,question_text\nSINGLE_CHOICE,Hi\n", content_type="text/csv"
        )
        check("missing headers rejected with 400", status == 400, str(status))
        check(
            "the message names the missing columns",
            "options" in json.dumps(failure),
            json.dumps(failure)[:200],
        )
        status, runs = call("GET", "/imports")
        check(
            "the failed run is recorded as FAILED",
            any(run["status"] == "FAILED" for run in runs["items"]),
        )

        # ------------------------------------------------------------------
        # UC-01 - quiz configuration & rules, and the integration with UC-02
        # ------------------------------------------------------------------

        section("16. Identity resolves across both capabilities")
        status, session = call("GET", f"{QC}/session", token=ADMIN_TOKEN)
        check("GET /api/session returns 200", status == 200)
        check(
            "the admin token resolves to the admin role",
            (session.get("user") or {}).get("role") == "admin",
            str(session.get("user")),
        )
        status, _ = call("GET", f"{QC}/admin/quizzes")
        check("admin endpoints reject an unauthenticated call", status == 401)
        status, _ = call("GET", f"{QC}/admin/quizzes", token=LEARNER_TOKEN)
        check("admin endpoints reject a learner", status == 403)
        status, learner_write = call(
            "POST", f"{API}/questions", single_choice(), token=LEARNER_TOKEN
        )
        check("a learner cannot write to the question bank", status == 403, str(learner_write))

        status, quizzes = call("GET", f"{QC}/admin/quizzes", token=ADMIN_TOKEN)
        check("GET /api/admin/quizzes returns 200", status == 200)
        quiz_id = quizzes["quizzes"][0]["id"] if quizzes.get("quizzes") else None
        check("a quiz is available to configure", quiz_id is not None)

        section("17. Configuration vocabulary and live bank capacity")
        status, meta = call("GET", f"{QC}/meta")
        check("GET /api/meta returns 200", status == 200)
        check(
            "meta publishes exactly the five question types",
            [item["value"] for item in meta["questionTypes"]]
            == ["SINGLE_CHOICE", "TRUE_FALSE", "MULTI_SELECT", "SCENARIO", "DRAG_TO_ORDER"],
            str(meta.get("questionTypes")),
        )
        check(
            "meta says only ACTIVE questions are deliverable",
            meta["deliverableQuestionStatuses"] == ["ACTIVE"],
        )

        status, availability = call(
            "GET", f"{QC}/admin/quizzes/{quiz_id}/question-bank", token=ADMIN_TOKEN
        )
        check("GET .../question-bank returns 200", status == 200)
        counts = availability["availableByType"]
        check(
            "every question type is reported",
            set(counts) == {item["value"] for item in meta["questionTypes"]},
            str(sorted(counts)),
        )

        # Cross-check the capacity count against the question bank's own deliverable pool: if the
        # two ever disagree, a quiz could be configured but not started.
        status, pool = call("GET", "/delivery/pool?limit=200")
        check("GET /delivery/pool returns 200", status == 200)
        check(
            "capacity counts and the deliverable pool agree",
            sum(counts.values()) == pool["totalAvailable"],
            f"capacity={sum(counts.values())} pool={pool['totalAvailable']}",
        )

        section("18. Configuration validation is backend-authoritative")
        # Earlier sections retired and deleted questions, so stock a known number of eligible
        # single-choice questions rather than depending on what happens to be left.
        for index in range(4):
            created_status, _ = call(
                "POST", "/questions", single_choice(f" (config fixture {index})"), token=ADMIN_TOKEN
            )
            check(f"configuration fixture question {index + 1} created", created_status == 201)

        status, availability = call(
            "GET", f"{QC}/admin/quizzes/{quiz_id}/question-bank", token=ADMIN_TOKEN
        )
        check(
            "the new questions are immediately eligible",
            availability["availableByType"]["SINGLE_CHOICE"] >= 4,
            str(availability["availableByType"]),
        )

        base_config = {
            "questionCount": 2,
            "timeLimitMinutes": 20,
            "passMark": 60,
            "maxAttempts": 2,
            "deliveryMode": "assessment",
            "randomiseQuestions": False,
            "questionTypes": [{"type": "SINGLE_CHOICE", "quota": 2}],
        }
        config_url = f"{QC}/admin/quizzes/{quiz_id}/configuration"

        status, invalid = call(
            "PUT", config_url, {**base_config, "passMark": 0, "maxAttempts": 0}, token=ADMIN_TOKEN
        )
        check("an invalid configuration is rejected with 422", status == 422)
        check("the error code is VALIDATION_FAILED", invalid["error"]["code"] == "VALIDATION_FAILED")
        fields = {issue["field"] for issue in invalid["error"]["details"]}
        check("every bad field is reported at once", {"passMark", "maxAttempts"} <= fields, str(fields))
        check(
            "each field error carries a machine-readable code",
            all(issue.get("code") for issue in invalid["error"]["details"]),
        )

        status, legacy = call(
            "PUT",
            config_url,
            {**base_config, "questionTypes": [{"type": "mcq", "quota": 2}]},
            token=ADMIN_TOKEN,
        )
        check("the pre-merge question-type vocabulary is rejected", status == 422)
        check(
            "the reason is INVALID_QUESTION_TYPE",
            any(i["code"] == "INVALID_QUESTION_TYPE" for i in legacy["error"]["details"]),
        )

        # 100 is the largest question count the field rules allow, so this reaches the capacity
        # gate rather than being turned away as out of range.
        impossible = {
            **base_config,
            "questionCount": 100,
            "questionTypes": [{"type": "SINGLE_CHOICE", "quota": 100}],
        }
        status, refused = call("PUT", config_url, impossible, token=ADMIN_TOKEN)
        check("a configuration the bank cannot satisfy is rejected", status == 422)
        check(
            "the error code is QUESTION_BANK_INSUFFICIENT",
            refused["error"]["code"] == "QUESTION_BANK_INSUFFICIENT",
        )
        check(
            "the shortfall is itemised per type",
            refused["error"]["capacity"]["breakdown"][0]["shortfall"] > 0,
        )

        with readonly_db(db_path) as conn:
            versions_after_failures = conn.execute(
                "SELECT COUNT(*) FROM qc_configuration_versions"
            ).fetchone()[0]
        check("no version was written by any rejected save", versions_after_failures == 0)

        section("19. Immutable versioning")
        status, v1 = call("PUT", config_url, base_config, token=ADMIN_TOKEN)
        check("the first valid save returns 201", status == 201, str(v1))
        check("it creates version 1", v1["configuration"]["versionNumber"] == 1)
        check("version 1 is active", v1["configuration"]["isActive"] is True)
        version1_id = v1["configuration"]["id"]

        status, unchanged = call("PUT", config_url, base_config, token=ADMIN_TOKEN)
        check("an unchanged re-save returns 200, not 201", status == 200)
        check("no new version is created", unchanged["created"] is False)

        status, v2 = call("PUT", config_url, {**base_config, "passMark": 75}, token=ADMIN_TOKEN)
        check(
            "a real change creates version 2",
            status == 201 and v2["configuration"]["versionNumber"] == 2,
            str(v2),
        )

        status, history = call(
            "GET", f"{QC}/admin/quizzes/{quiz_id}/configuration/versions", token=ADMIN_TOKEN
        )
        check(
            "version history lists both, newest first",
            [v["versionNumber"] for v in history["versions"]] == [2, 1],
        )
        stored_v1 = next(v for v in history["versions"] if v["versionNumber"] == 1)
        check("version 1 kept its original pass mark", stored_v1["passMark"] == 60)
        check("version 1 is no longer active", stored_v1["isActive"] is False)

        with readonly_db(db_path) as conn:
            check(
                "both versions are on disk",
                conn.execute("SELECT COUNT(*) FROM qc_configuration_versions").fetchone()[0] == 2,
            )
            try:
                conn.execute(
                    "UPDATE qc_configuration_versions SET pass_mark = 99 WHERE id = ?",
                    (version1_id,),
                )
                conn.commit()
                check("the database refuses to edit a stored version", False, "the UPDATE succeeded")
            except sqlite3.Error as exc:
                check(
                    "the database refuses to edit a stored version",
                    "IMMUTABLE_CONFIGURATION_VERSION" in str(exc),
                    str(exc),
                )

        section("20. Learner rules (UC-01) create nothing")
        status, rules = call("GET", f"{QC}/quizzes/{quiz_id}/rules", token=LEARNER_TOKEN)
        check("GET .../rules returns 200", status == 200, str(rules))
        check("the rules come from the active version", rules["configurationVersionNumber"] == 2)
        check("the pass mark is the active one", rules["passMark"] == 75)
        check("the learner has their full allowance", rules["remainingAttempts"] == 2)
        check("the learner can start", rules["canStart"] is True, str(rules.get("blockedReason")))

        for _ in range(3):
            call("GET", f"{QC}/quizzes/{quiz_id}/rules", token=LEARNER_TOKEN)
        with readonly_db(db_path) as conn:
            after_reads = conn.execute("SELECT COUNT(*) FROM qd_attempts").fetchone()[0]
        check("viewing the rules creates no attempt", after_reads == 0)

        # UC-01 reports the attempt counts, but reads them from UC-03 through a port rather than
        # keeping its own copy. This is the check that the two agree.
        status, eligibility = call(
            "GET", f"{V1}/quizzes/{quiz_id}/attempt-eligibility", token=LEARNER_TOKEN
        )
        check("UC-03 eligibility returns 200", status == 200, str(eligibility))
        report = eligibility["eligibility"]
        check("UC-03 agrees the learner is eligible", report["eligible"] is True, str(report["reasons"]))
        check("UC-03 agrees on the enrolment", report["enrolled"] is True)
        check(
            "UC-03 and UC-01 report the same remaining attempts",
            report["attemptsRemaining"] == rules["remainingAttempts"],
            f'uc03={report["attemptsRemaining"]} uc01={rules["remainingAttempts"]}',
        )
        with readonly_db(db_path) as conn:
            check(
                "checking eligibility creates no attempt either",
                conn.execute("SELECT COUNT(*) FROM qd_attempts").fetchone()[0] == 0,
            )

        section("21. UC-03 delivers an attempt locked to one configuration version")
        status, created = call("POST", f"{V1}/attempts", {"quizId": str(quiz_id)}, token=LEARNER_TOKEN)
        check("POST /api/v1/attempts returns 201", status == 201, str(created)[:300])
        attempt = created["attempt"]
        attempt_id = attempt["attemptId"]

        check(
            "the attempt is locked to the active version, not the first one",
            attempt["configurationVersionId"] != str(version1_id),
            f'locked to {attempt["configurationVersionId"]}, v1 was {version1_id}',
        )
        check(
            "the locked snapshot carries the active version's pass mark",
            attempt["configuration"]["passMarkPercentage"] == 75,
            str(attempt["configuration"]),
        )
        check(
            "it reports where to fetch the paper, given the locked presentation",
            created["delivery"]["questionsUrl"].endswith("/questions"),
            created["delivery"]["questionsUrl"],
        )
        check(
            "the delivery descriptor reports the configured count",
            created["delivery"]["totalQuestions"] == 2,
            str(created["delivery"]["totalQuestions"]),
        )

        # The paper is a separate read: creation returns a descriptor, so a client cannot accidentally
        # fetch the whole paper for a one-at-a-time attempt.
        status, paper = call("GET", f"{V1}/attempts/{attempt_id}/questions", token=LEARNER_TOKEN)
        check("the questions read returns 200", status == 200, str(paper)[:200])
        questions = paper["questions"]
        check("it received the configured number of questions", len(questions) == 2, str(len(questions)))
        check(
            "each question carries the version it was delivered at",
            all(question["questionVersion"] >= 1 for question in questions),
        )
        body_text = json.dumps(paper)
        check(
            "the learner view contains no answer key",
            not any(
                key in body_text
                for key in ("isCorrect", "correctLabels", "correctPosition", "correct_position")
            ),
        )

        status, second = call("POST", f"{V1}/attempts", {"quizId": str(quiz_id)}, token=LEARNER_TOKEN)
        check("a second concurrent attempt is refused", status == 409, str(second)[:200])
        check(
            "the reason is ACTIVE_ATTEMPT_EXISTS",
            second["error"]["code"] == "ACTIVE_ATTEMPT_EXISTS",
            str(second["error"]["code"]),
        )

        with readonly_db(db_path) as conn:
            check(
                "exactly one attempt is on disk, with a locked version",
                conn.execute(
                    "SELECT COUNT(*) FROM qd_attempts WHERE configuration_version_id IS NOT NULL"
                ).fetchone()[0]
                == 1,
            )
            check(
                "its questions are frozen as snapshots on disk",
                conn.execute(
                    "SELECT COUNT(*) FROM qd_attempt_questions WHERE attempt_id = ?", (attempt_id,)
                ).fetchone()[0]
                == 2,
            )
            # UC-03 reports the delivery to UC-02 using the attempt id as the bank's opaque
            # `attempt_ref`. This is what keeps UC-02's usage counts, its delete-refusal and its
            # historical report working for real attempts.
            pinned = conn.execute(
                "SELECT COUNT(*) FROM qb_question_usages WHERE attempt_ref = ?", (attempt_id,)
            ).fetchone()[0]
        check("the drawn questions are recorded against the bank", pinned == 2, str(pinned))
        usage_ref = attempt_id

        # The resume path: a client that reloads finds the same attempt rather than starting a new one.
        status, resumed = call(
            "GET", f"{V1}/attempts/active?quizId={quiz_id}", token=LEARNER_TOKEN
        )
        check("the open attempt is resumable", status == 200 and resumed["attempt"]["attemptId"] == attempt_id)

        section("22. Answering, autosaving and reviewing")
        first, secondq = questions[0], questions[1]

        answer = answer_payload(first)
        status, saved = call(
            "PUT",
            f'{V1}/attempts/{attempt_id}/questions/{first["questionId"]}/answer',
            {"response": answer, "source": "MANUAL"},
            token=LEARNER_TOKEN,
        )
        check("saving an answer returns 200", status == 200, str(saved)[:200])
        stored = saved["answer"]
        check("it is recorded as changed", stored["changed"] is True)
        check("it is recorded as complete", stored["complete"] is True, str(stored))
        check(
            "the response carries fresh authoritative timing",
            saved["timing"]["remainingSeconds"] > 0,
            str(saved["timing"]),
        )
        revision = stored["revision"]

        # Idempotent re-save: the autosave loop repeats this constantly and must not churn revisions.
        status, again = call(
            "PUT",
            f'{V1}/attempts/{attempt_id}/questions/{first["questionId"]}/answer',
            {"response": answer, "source": "AUTOSAVE"},
            token=LEARNER_TOKEN,
        )
        check("re-saving the same answer is accepted", status == 200)
        check("it reports no change", again["answer"]["changed"] is False)
        check(
            "the revision did not advance",
            again["answer"]["revision"] == revision,
            str(again["answer"]["revision"]),
        )

        # Batch autosave, which is what the UI actually sends.
        status, batch = call(
            "POST",
            f"{V1}/attempts/{attempt_id}/answers",
            {
                "answers": [{"questionId": secondq["questionId"], "response": answer_payload(secondq)}],
                "source": "AUTOSAVE",
            },
            token=LEARNER_TOKEN,
        )
        check("batch autosave returns 200", status == 200, str(batch)[:200])
        check("it saved the entry", batch["savedCount"] == 1 and batch["changedCount"] == 1)

        # A stale expectedRevision must be refused, not silently overwrite another device's answer.
        status, conflict = call(
            "PUT",
            f'{V1}/attempts/{attempt_id}/questions/{first["questionId"]}/answer',
            {"response": answer_payload(first, variant=1), "source": "MANUAL", "expectedRevision": 0},
            token=LEARNER_TOKEN,
        )
        check("a stale expectedRevision is refused", status == 409, str(conflict)[:200])
        check(
            "the reason is ANSWER_REVISION_CONFLICT",
            conflict["error"]["code"] == "ANSWER_REVISION_CONFLICT",
        )

        status, flagged = call(
            "PUT",
            f'{V1}/attempts/{attempt_id}/questions/{secondq["questionId"]}/flag',
            {"flagged": True},
            token=LEARNER_TOKEN,
        )
        check("a question can be flagged", status == 200, str(flagged)[:200])

        status, state = call("GET", f"{V1}/attempts/{attempt_id}/state", token=LEARNER_TOKEN)
        check("the review state returns 200", status == 200)
        outline = state["state"]
        check("it reports both questions complete", outline["completeCount"] == 2, str(outline))
        check("it reports the flag", outline["flaggedCount"] == 1)
        check(
            "it reports authoritative timing",
            outline["timing"]["timed"] is True and outline["timing"]["remainingSeconds"] > 0,
            str(outline["timing"]),
        )

        # The reload path: answers come back from the server, not from client memory.
        status, sheet = call("GET", f"{V1}/attempts/{attempt_id}/answers", token=LEARNER_TOKEN)
        check("the answer sheet returns 200", status == 200)
        check("every delivered question is listed", len(sheet["answers"]) == 2)
        check(
            "the saved answers survive a fresh read",
            all(entry["response"] is not None for entry in sheet["answers"]),
        )

        with readonly_db(db_path) as conn:
            check(
                "the answers are genuinely on disk",
                conn.execute(
                    "SELECT COUNT(*) FROM qd_attempt_answers WHERE attempt_id = ? AND answered = 1",
                    (attempt_id,),
                ).fetchone()[0]
                == 2,
            )
            check(
                "every accepted save left an audit record",
                conn.execute(
                    "SELECT COUNT(*) FROM qd_attempt_answer_revisions WHERE attempt_id = ?",
                    (attempt_id,),
                ).fetchone()[0]
                >= 2,
            )

        section("23. A configuration change cannot alter a running attempt")
        status, v3 = call(
            "PUT", config_url, {**base_config, "passMark": 90, "timeLimitMinutes": 5}, token=ADMIN_TOKEN
        )
        check("the administrator can still create version 3", status == 201, str(v3))

        status, reloaded = call("GET", f"{V1}/attempts/{attempt_id}", token=LEARNER_TOKEN)
        check("the open attempt still reads 200", status == 200)
        locked = reloaded["attempt"]
        check("its version is unchanged", locked["configurationVersionId"] == attempt["configurationVersionId"])
        check("its pass mark is unchanged", locked["configuration"]["passMarkPercentage"] == 75)
        check(
            "its time limit is unchanged",
            locked["configuration"]["timeLimitSeconds"] == 20 * 60,
            str(locked["configuration"]["timeLimitSeconds"]),
        )
        status, still = call("GET", f"{V1}/attempts/{attempt_id}/questions", token=LEARNER_TOKEN)
        check(
            "its questions are unchanged",
            [q["questionId"] for q in still["questions"]]
            == [q["questionId"] for q in questions],
        )

        section("24. Confirmed submission, then the historical report")
        status, preview = call(
            "GET", f"{V1}/attempts/{attempt_id}/submission/preview", token=LEARNER_TOKEN
        )
        check("the submission preview returns 200", status == 200, str(preview)[:200])
        summary = preview["preview"]
        check("it may be submitted", summary["canSubmit"] is True, str(summary["blockers"]))
        check("it requires confirmation", summary["requiresConfirmation"] is True)
        check("it warns about the outstanding flag", any(
            warning["code"] == "FLAGGED_QUESTIONS" for warning in summary["warnings"]
        ), str(summary["warnings"]))

        with readonly_db(db_path) as conn:
            check(
                "previewing submits nothing",
                conn.execute(
                    "SELECT COUNT(*) FROM qd_attempt_submissions WHERE attempt_id = ?", (attempt_id,)
                ).fetchone()[0]
                == 0,
            )

        status, unconfirmed = call(
            "POST", f"{V1}/attempts/{attempt_id}/submission", {"confirmed": False}, token=LEARNER_TOKEN
        )
        check("an unconfirmed submission is refused", status == 400, str(unconfirmed)[:200])
        check(
            "the reason is SUBMISSION_NOT_CONFIRMED",
            unconfirmed["error"]["code"] == "SUBMISSION_NOT_CONFIRMED",
        )

        key = summary["suggestedIdempotencyKey"]
        status, submitted = call(
            "POST",
            f"{V1}/attempts/{attempt_id}/submission",
            {"confirmed": True, "idempotencyKey": key},
            token=LEARNER_TOKEN,
        )
        check("a confirmed submission returns 200", status == 200, str(submitted)[:300])

        # The double-click case: the same key must collapse into one submission.
        status, repeat = call(
            "POST",
            f"{V1}/attempts/{attempt_id}/submission",
            {"confirmed": True, "idempotencyKey": key},
            token=LEARNER_TOKEN,
        )
        check("re-submitting with the same key is accepted", status == 200, str(repeat)[:200])
        with readonly_db(db_path) as conn:
            check(
                "and produced exactly one submission",
                conn.execute(
                    "SELECT COUNT(*) FROM qd_attempt_submissions WHERE attempt_id = ?", (attempt_id,)
                ).fetchone()[0]
                == 1,
            )

        status, closed = call("GET", f"{V1}/attempts/{attempt_id}", token=LEARNER_TOKEN)
        check("the attempt is now submitted", closed["attempt"]["status"] == "SUBMITTED", str(closed["attempt"]["status"]))
        status, refused = call(
            "PUT",
            f'{V1}/attempts/{attempt_id}/questions/{first["questionId"]}/answer',
            {"response": answer_payload(first, variant=1), "source": "MANUAL"},
            token=LEARNER_TOKEN,
        )
        check("answers can no longer be changed", status == 409, str(refused)[:200])

        status, freed = call(
            "GET", f"{V1}/quizzes/{quiz_id}/attempt-eligibility", token=LEARNER_TOKEN
        )
        check(
            "the submitted attempt consumed one of the allowance",
            freed["eligibility"]["attemptsUsed"] == 1,
            str(freed["eligibility"]["attemptsUsed"]),
        )
        status, uc01_after = call("GET", f"{QC}/quizzes/{quiz_id}/rules", token=LEARNER_TOKEN)
        check(
            "UC-01 sees the same count through its port",
            uc01_after["attemptsUsed"] == 1,
            str(uc01_after["attemptsUsed"]),
        )

        # Retire every question the attempt was given, then confirm the history still renders.
        for question in questions:
            call(
                "POST",
                f"/questions/{question['questionId']}/retire",
                {"reason": "e2e"},
                token=ADMIN_TOKEN,
            )
        status, after_retire = call("GET", f"{V1}/attempts/{attempt_id}/questions", token=LEARNER_TOKEN)
        check("the submitted attempt still renders its questions", status == 200, str(after_retire)[:200])
        check(
            "with the wording it was delivered, not the retired live row",
            [q["questionId"] for q in after_retire["questions"]]
            == [q["questionId"] for q in questions],
        )

        if usage_ref:
            status, report = call("GET", f"/reporting/attempts/{usage_ref}")
            check("the UC-02 attempt report survives retiring every question", status == 200, str(report)[:200])
            check("it still shows every question", report["questionCount"] == 2)
            check(
                "each item reports the live status as RETIRED but keeps its content",
                all(
                    item["currentQuestionStatus"] == "RETIRED" and item["questionText"]
                    for item in report["items"]
                ),
            )

        section("25. UC-04 scores the submitted attempt")
        status, scored = call("GET", f"{V1}/attempts/{attempt_id}/result", token=LEARNER_TOKEN)
        check("the result endpoint returns 200", status == 200, str(scored)[:300])
        result = scored["result"]
        check(
            "the attempt is SCORED",
            result["status"] == "SCORED",
            f'{result["status"]} {result.get("failureCode")}',
        )
        check("its label reads Scored", result["statusLabel"] == "Scored", result["statusLabel"])
        check(
            "the marks are within the maximum",
            0 <= result["totalMarks"] <= result["maximumMarks"] and result["maximumMarks"] > 0,
            f'{result["totalMarks"]}/{result["maximumMarks"]}',
        )
        check(
            "the percentage matches the marks",
            abs(result["percentage"] - (result["totalMarks"] / result["maximumMarks"] * 100)) < 0.02,
            str(result["percentage"]),
        )
        check(
            "it was scored against the version the attempt locked",
            result["configurationVersionId"] == attempt["configurationVersionId"],
            f'{result["configurationVersionId"]} vs {attempt["configurationVersionId"]}',
        )
        check("time taken was recorded", result["timeTakenSeconds"] is not None)
        check(
            "every delivered question has a score",
            len(scored["questionScores"]) == len(questions),
            str(len(scored["questionScores"])),
        )
        check(
            "each score names its answer key source",
            all(item["answerKeySource"] for item in scored["questionScores"]),
        )
        check(
            "scoring survived every delivered question being retired",
            all(item["questionText"] for item in scored["questionScores"]),
        )

        with readonly_db(db_path) as conn:
            row = conn.execute(
                "SELECT status, percentage, scored_at FROM qr_attempt_results WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            check("the result is on disk", row is not None and row[0] == "SCORED", str(row))
            check("with the instant it was scored", row is not None and row[2] is not None)
            check(
                "and exactly one result row for the attempt",
                conn.execute(
                    "SELECT COUNT(*) FROM qr_attempt_results WHERE attempt_id = ?", (attempt_id,)
                ).fetchone()[0]
                == 1,
            )

        status, replayed = call("POST", f"{V1}/attempts/{attempt_id}/result", token=LEARNER_TOKEN)
        check("re-scoring is accepted", status == 200, str(replayed)[:200])
        check("and replays the confirmed score", replayed["replayed"] is True)
        check(
            "without changing it",
            replayed["result"]["scoredAt"] == result["scoredAt"]
            and replayed["result"]["percentage"] == result["percentage"],
        )
        with readonly_db(db_path) as conn:
            check(
                "a confirmed score cannot be edited, even directly",
                _rejects_update(
                    db_path,
                    "UPDATE qr_attempt_results SET percentage = 1 WHERE attempt_id = ?",
                    attempt_id,
                    "IMMUTABLE_ATTEMPT_RESULT",
                ),
            )

        section("26. UC-05 determines pass/fail and gates the certificate")
        status, outcome = call("GET", f"{V1}/attempts/{attempt_id}/outcome", token=LEARNER_TOKEN)
        check("the outcome endpoint returns 200", status == 200, str(outcome)[:300])
        verdict = outcome["outcome"]
        check("the verdict is PASS or FAIL", verdict["outcome"] in {"PASS", "FAIL"}, verdict["outcome"])
        check(
            "it was judged against the attempt's own pass mark",
            verdict["passMarkPercentage"] == float(attempt["configuration"]["passMarkPercentage"]),
            f'{verdict["passMarkPercentage"]} vs {attempt["configuration"]["passMarkPercentage"]}',
        )
        check(
            "the verdict follows the arithmetic",
            (verdict["outcome"] == "PASS")
            == (verdict["percentage"] >= verdict["passMarkPercentage"]),
            f'{verdict["percentage"]} vs {verdict["passMarkPercentage"]}',
        )
        check(
            "a certificate is required exactly for a pass",
            verdict["certificateRequired"] == (verdict["outcome"] == "PASS"),
        )
        if verdict["outcome"] == "PASS":
            check(
                "the certificate was issued",
                outcome["certificate"] is not None
                and outcome["certificate"]["status"] == "ISSUED",
                str(outcome["certificate"]),
            )
            check(
                "with a certificate number and the frozen course name",
                outcome["certificate"]["certificateNumber"]
                and outcome["certificate"]["courseName"],
            )
        else:
            check("no certificate was issued for a fail", outcome["certificate"] is None)
            check(
                "and the remaining attempts are reported",
                outcome["attemptsRemaining"] is None or outcome["attemptsRemaining"] >= 0,
                str(outcome["attemptsRemaining"]),
            )

        check(
            "the CPD record was synchronised",
            outcome["cpd"] is not None and outcome["cpd"]["status"] == "SYNCHRONISED",
            str(outcome["cpd"]),
        )
        check(
            "with the four agreed fields",
            outcome["cpd"]["attemptDate"]
            and outcome["cpd"]["courseName"]
            and isinstance(outcome["cpd"]["passed"], bool)
            and isinstance(outcome["cpd"]["scorePercentage"], (int, float)),
            str(outcome["cpd"]),
        )

        status, again = call("POST", f"{V1}/attempts/{attempt_id}/outcome", token=LEARNER_TOKEN)
        check("re-determining is accepted", status == 200, str(again)[:200])
        check("and does not create a second outcome", again["created"] is False)
        check(
            "the verdict is unchanged",
            again["outcome"]["outcome"] == verdict["outcome"]
            and again["outcome"]["determinedAt"] == verdict["determinedAt"],
        )
        status, retried = call(
            "POST", f"{V1}/attempts/{attempt_id}/outcome/certificate/retry", token=LEARNER_TOKEN
        )
        check(
            "retrying the certificate is accepted and mints nothing new",
            status in {200, 409},
            str(retried)[:200],
        )
        with readonly_db(db_path) as conn:
            check(
                "exactly one outcome row",
                conn.execute(
                    "SELECT COUNT(*) FROM qg_attempt_outcomes WHERE attempt_id = ?", (attempt_id,)
                ).fetchone()[0]
                == 1,
            )
            check(
                "at most one issued certificate for this learner and quiz",
                conn.execute(
                    "SELECT COUNT(*) FROM qg_certificates WHERE status = 'ISSUED'"
                ).fetchone()[0]
                <= 1,
            )
            check(
                "exactly one CPD record",
                conn.execute(
                    "SELECT COUNT(*) FROM qg_cpd_records WHERE attempt_id = ?", (attempt_id,)
                ).fetchone()[0]
                == 1,
            )
        check(
            "a determined outcome cannot be edited, even directly",
            _rejects_update(
                db_path,
                "UPDATE qg_attempt_outcomes SET outcome = 'FAIL' WHERE attempt_id = ?",
                attempt_id,
                "IMMUTABLE_ATTEMPT_OUTCOME",
            ),
        )

        section("27. UC-06 reports detailed feedback, frozen")
        status, feedback = call("GET", f"{V1}/attempts/{attempt_id}/feedback", token=LEARNER_TOKEN)
        check("the feedback endpoint returns 200", status == 200, str(feedback)[:300])
        check(
            "the report is generated",
            feedback["status"] == "GENERATED",
            f'{feedback["status"]} {feedback.get("failureCode")}',
        )
        summary_block = feedback["summary"]
        check(
            "it leads with the score, the percentage and the verdict",
            summary_block["totalScore"] == result["totalMarks"]
            and summary_block["percentage"] == result["percentage"]
            and summary_block["passed"] == (verdict["outcome"] == "PASS"),
            str(summary_block),
        )
        check("and the time taken", summary_block["timeTakenSeconds"] is not None)
        check(
            "the counts add up to the questions delivered",
            summary_block["correctCount"]
            + summary_block["incorrectCount"]
            + summary_block["unansweredCount"]
            == summary_block["totalQuestions"],
            str(summary_block),
        )
        check(
            "there is one item per delivered question",
            len(feedback["items"]) == len(questions),
            str(len(feedback["items"])),
        )
        for item in feedback["items"]:
            check(
                f'item {item["position"]} names the question, both answers, an explanation and a lesson',
                bool(item["question"])
                and item["learnerAnswer"] is not None
                and item["correctAnswer"] is not None
                and bool(item["explanation"])
                and bool(item["lessonReference"]),
                str(item)[:200],
            )

        with readonly_db(db_path) as conn:
            check(
                "the report and its items are on disk",
                conn.execute(
                    "SELECT COUNT(*) FROM qf_feedback_reports WHERE attempt_id = ?", (attempt_id,)
                ).fetchone()[0]
                == 1
                and conn.execute("SELECT COUNT(*) FROM qf_feedback_items").fetchone()[0]
                == len(questions),
            )
        check(
            "a generated report cannot be edited, even directly",
            _rejects_update(
                db_path,
                "UPDATE qf_feedback_reports SET percentage = 1 WHERE attempt_id = ?",
                attempt_id,
                "IMMUTABLE_FEEDBACK_REPORT",
            ),
        )
        status, regenerated = call(
            "POST", f"{V1}/attempts/{attempt_id}/feedback", token=LEARNER_TOKEN
        )
        check("re-requesting feedback is accepted", status == 200, str(regenerated)[:200])
        check("and replays the frozen report", regenerated["replayed"] is True)
        check(
            "with the same items, after every delivered question was retired",
            regenerated["items"] == feedback["items"],
        )

        section("28. UC-07 offers coaching on the wrong answers, and refuses to invent it")
        status, coaching_check = call(
            "GET", f"{V1}/attempts/{attempt_id}/coaching/eligibility", token=LEARNER_TOKEN
        )
        check("the coaching eligibility endpoint returns 200", status == 200, str(coaching_check)[:300])
        # No AI provider is configured for this run -- which is what a stock deployment looks like --
        # so coaching must report itself unavailable rather than answering with invented text.
        check(
            "with no provider bound, coaching reports itself unavailable",
            coaching_check["coachingAvailable"] is False
            and coaching_check["reason"] == "SERVICE_UNAVAILABLE",
            str(coaching_check)[:200],
        )
        check("and says the refusal is worth retrying later", coaching_check["retryable"] is True)

        status, coaching_health = call("GET", f"{BASE}/api/health")
        check(
            "readiness reports that no coaching provider is bound",
            coaching_health.get("coachingProvider", {}).get("configured") is False,
            str(coaching_health.get("coachingProvider")),
        )
        check(
            "and lists UC-07 among the modules",
            "UC-07 AI Coaching Review Mode" in coaching_health.get("modules", []),
        )

        # The review queue does not need the AI to be up: a learner can always see which questions
        # they got wrong. Only the conversation itself is refused.
        status, review = call(
            "GET", f"{V1}/attempts/{attempt_id}/coaching/review", token=LEARNER_TOKEN
        )
        check("the review queue is readable during an outage", status == 200, str(review)[:300])
        incorrect_positions = sorted(
            item["position"] for item in scored["questionScores"] if item["outcome"] != "CORRECT"
        )
        check(
            "it contains exactly the questions UC-04 marked incorrect",
            review["totalIncorrect"] == len(incorrect_positions)
            and [item["position"] for item in review["items"]] == incorrect_positions,
            f'{review["totalIncorrect"]} vs {len(incorrect_positions)}',
        )
        review_text = json.dumps(review)
        check(
            "and nothing in it carries a correct answer",
            "correctAnswer" not in review_text and "isCorrect" not in review_text,
        )

        if review["items"]:
            first_incorrect = review["items"][0]["questionId"]
            status, refused = call(
                "POST",
                f"{V1}/attempts/{attempt_id}/coaching/questions/{first_incorrect}",
                token=LEARNER_TOKEN,
            )
            check(
                "starting coaching without a provider is refused with 503",
                status == 503,
                f"{status} {str(refused)[:200]}",
            )
            check(
                "with a retryable code and no invented coaching text",
                refused["error"]["code"] == "COACHING_SERVICE_UNAVAILABLE"
                and refused["error"]["retryable"] is True,
                str(refused["error"])[:200],
            )
            with readonly_db(db_path) as conn:
                check(
                    "and no coaching session was opened",
                    conn.execute(
                        "SELECT COUNT(*) FROM qk_coaching_sessions WHERE attempt_id = ?",
                        (attempt_id,),
                    ).fetchone()[0]
                    == 0,
                )

        # A correctly answered question is never coachable, whatever the provider is doing.
        correct_question = next(
            (
                item["questionId"]
                for item in scored["questionScores"]
                if item["outcome"] == "CORRECT"
            ),
            None,
        )
        if correct_question is not None:
            status, not_incorrect = call(
                "POST",
                f"{V1}/attempts/{attempt_id}/coaching/questions/{correct_question}",
                token=LEARNER_TOKEN,
            )
            check(
                "a correctly answered question cannot be coached",
                status == 409 and not_incorrect["error"]["code"] == "QUESTION_NOT_INCORRECT",
                f"{status} {str(not_incorrect)[:160]}",
            )
            check(
                "and that refusal is permanent, not retryable",
                not_incorrect["error"]["retryable"] is False,
            )

        status, unauthenticated = call("GET", f"{V1}/attempts/{attempt_id}/coaching/eligibility")
        check(
            "an unauthenticated caller cannot read coaching eligibility at all",
            status == 401,
            f"{status} {str(unauthenticated)[:160]}",
        )

        # The score, the verdict and the report are exactly as they were before coaching was asked
        # for anything. UC-07 cannot touch a learner's result, and this proves it on disk.
        with readonly_db(db_path) as conn:
            check(
                "nothing coaching did changed the score, the outcome or the report",
                conn.execute(
                    "SELECT status FROM qr_attempt_results WHERE attempt_id = ?", (attempt_id,)
                ).fetchone()[0]
                == "SCORED"
                and conn.execute(
                    "SELECT COUNT(*) FROM qg_attempt_outcomes WHERE attempt_id = ?", (attempt_id,)
                ).fetchone()[0]
                == 1
                and conn.execute(
                    "SELECT status FROM qf_feedback_reports WHERE attempt_id = ?", (attempt_id,)
                ).fetchone()[0]
                == "GENERATED",
            )

        section("29. Errors never leak internals")
        status, not_found = call("GET", "/questions/definitely-not-a-real-id")
        check("unknown question returns 404", status == 404)
        check("error envelope is consistent", "error" in not_found and "code" in not_found["error"])
        check(
            "no stack trace in the response",
            "Traceback" not in json.dumps(not_found) and ".py" not in json.dumps(not_found),
        )

    except Exception:
        import traceback

        print("\n  [FAIL] the verification script itself raised:")
        traceback.print_exc()
        failures.append("verification script raised an exception")
        log_handle.flush()
        print("\n--- server log tail ---")
        print(server_log.read_text(encoding="utf-8", errors="replace")[-2000:])
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        log_handle.close()

    print("\n" + "=" * 60)
    if failures:
        print(f"RESULT: {len(failures)} of {checks} checks FAILED")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print(f"RESULT: all {checks} checks PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
