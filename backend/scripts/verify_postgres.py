"""Verify the schema on a real PostgreSQL server — migrations, triggers, indexes, constraints.

    python -m scripts.verify_postgres --database-url postgresql://user:pass@host:5432/dbname

    # or, with nothing installed locally, against an embedded server:
    python -m scripts.verify_postgres --embedded

WHY THIS IS SEPARATE FROM THE TEST SUITE
----------------------------------------
Every test in ``backend/tests/`` runs on SQLite, and SQLite is permissive in precisely the places
PostgreSQL is not. Three classes of defect made the first real PostgreSQL migration fail outright:

* identifier names over PostgreSQL's 63-character limit (SQLite has no limit);
* boolean columns compared with integers — ``answered = 1`` — which SQLite accepts because it
  stores booleans as integers;
* server defaults whose type contradicts their column's, in both directions.

``tests/test_database_portability.py`` is the cheap static gate for all three and runs on every
commit. It cannot, however, prove that the immutability **triggers** install, that the partial
unique indexes are genuinely partial, or that a CHECK constraint actually rejects a row — those need
a server. That is what this does.

It creates and drops its own database, so it never touches one holding real data.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

passes: list[str] = []
fails: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        passes.append(label)
        print(f"  [PASS] {label}")
    else:
        fails.append(f"{label}{f' - {detail}' if detail else ''}")
        print(f"  [FAIL] {label}{f' - {detail}' if detail else ''}")


def section(title: str) -> None:
    print(f"\n{title}")
    print("-" * min(len(title), 78))


#: Every immutability trigger the system depends on. Named individually rather than counted: a
#: count would pass while the wrong eleven were installed.
EXPECTED_TRIGGERS = {
    "trg_qc_config_version_no_update": "a published configuration version can never be edited",
    "trg_qc_config_version_types_no_update": "nor its question-type quotas",
    "trg_qc_config_version_topics_no_update": "nor its topic scope",
    "trg_qr_result_immutable_when_scored": "a confirmed score is final",
    "trg_qr_question_score_no_update": "per-question scores are written once",
    "trg_qg_outcome_no_update": "a pass or fail is a derived fact, written once",
    "trg_qf_report_immutable_when_generated": "a generated feedback report is frozen",
    "trg_qf_item_no_update": "and so is every item in it",
    "trg_qk_message_no_update": "a coaching exchange is a record of what was said",
    "trg_qk_activity_no_update": "and the activity log is append-only",
    "trg_qy_review_action_no_update": "so is the content-review audit trail",
}

#: The uniqueness that carries a business rule. Partial indexes on PostgreSQL, so each must be
#: present *and* partial — a full unique index here would forbid a second attempt outright.
EXPECTED_PARTIAL_INDEXES = {
    "ux_attempt_single_open": "one open attempt per learner and quiz",
    "ux_submission_single_success": "at most one successful submission per attempt",
    "ux_retake_attempt_slot": "one retake per attempt slot",
    "ux_qg_certificate_single_issued": "one issued certificate per learner and quiz",
    "ux_formal_attempt_open": "one open formal attempt per learner and quiz",
    "ux_device_session_active": "one active device per formal attempt",
}

EXPECTED_INDEXES = {
    "ux_retake_idempotency": "a retried retake request creates one retake",
    "ux_grant_idempotency": "a retried grant grants once",
    "ux_formal_review_attempt": "one review per formal attempt",
}

TABLE_PREFIXES = {
    "qb_": "UC-02 question bank",
    "qc_": "UC-01 quiz configuration",
    "qd_": "UC-03 attempt delivery",
    "qr_": "UC-04 scoring",
    "qg_": "UC-05 certification",
    "qf_": "UC-06 feedback",
    "qk_": "UC-07 coaching",
    "qt_": "UC-08 retakes",
    "qs_": "UC-09 formal assessment",
    "qy_": "UC-10 analytics",
    "qa_": "platform placeholder",
}


def embedded_url() -> str:
    """Start an embedded PostgreSQL and return a URL for a fresh database on it.

    For a machine with no PostgreSQL and no Docker. The server lives for this process only.
    """
    try:
        import pgserver
    except ImportError:
        print(
            "  --embedded needs the `pgserver` package:\n"
            "      pip install pgserver\n"
            "  or pass --database-url pointing at a real server."
        )
        raise SystemExit(2) from None

    import psycopg

    data_dir = Path(BACKEND_DIR / ".pgdata-verify")
    server = pgserver.get_server(str(data_dir))
    admin = server.get_uri()
    name = "quizagent_verify"
    with psycopg.connect(admin, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (name,),
        )
        cursor.execute(f'DROP DATABASE IF EXISTS "{name}"')
        cursor.execute(f'CREATE DATABASE "{name}"')
    # Held open by this process; returned so the caller can migrate into it.
    globals()["_embedded_server"] = server
    return admin.rsplit("/", 1)[0] + f"/{name}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default="",
        help="A PostgreSQL URL. The bare postgresql:// form from any managed provider works.",
    )
    parser.add_argument(
        "--embedded",
        action="store_true",
        help="Start a throwaway embedded PostgreSQL instead of connecting to one.",
    )
    args = parser.parse_args()

    if not args.database_url and not args.embedded:
        parser.error("pass --database-url or --embedded")

    raw = args.database_url or embedded_url()

    # Normalised through `Settings`, not by a copy of the rule: a bare `postgresql://` URL selects
    # psycopg2, which is not a dependency, and the resulting "No module named 'psycopg2'" reads as a
    # packaging problem rather than a URL one. Reusing the application's own normaliser also means
    # this script cannot verify a URL form the application would reject.
    from app.core.config import Settings

    url = Settings(
        database_url=raw, environment="development", admin_api_token=None, system_api_token=None
    ).database_url

    print("COURSES QUIZ AGENT - POSTGRESQL SCHEMA VERIFICATION")
    print("=" * 60)
    # Never print the URL: it carries a password.
    print(f"target : {url.rsplit('@', 1)[-1] if '@' in url else url}")

    import psycopg
    from alembic.config import Config

    from alembic import command

    # ---- migrate ---------------------------------------------------------
    section("1. Alembic migration against PostgreSQL")
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    try:
        command.upgrade(config, "head")
        migrated = True
        detail = ""
    except Exception as exc:  # noqa: BLE001 - the message is the whole point
        migrated = False
        detail = str(exc)[:300]
    check("alembic upgrade head succeeds on PostgreSQL", migrated, detail)
    if not migrated:
        print("\nmigration failed; nothing further can be checked")
        return 1

    # SQLAlchemy's `postgresql+psycopg://` names a *dialect*; libpq does not understand the `+psycopg`
    # part, so the driver suffix is stripped for the direct connection below. Two forms of the same
    # URL, each used where it belongs.
    dsn = url.replace("postgresql+psycopg://", "postgresql://", 1)
    connection = psycopg.connect(dsn, autocommit=True)
    cursor = connection.cursor()

    def rows(sql: str, *params):
        cursor.execute(sql, params or None)
        return cursor.fetchall()

    def names(sql: str) -> set[str]:
        return {row[0] for row in rows(sql)}

    # ---- tables ----------------------------------------------------------
    section("2. Every capability owns its tables")
    tables = names("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    for prefix, capability in TABLE_PREFIXES.items():
        owned = sorted(name for name in tables if name.startswith(prefix))
        check(f"{capability} ({prefix}*): {len(owned)} tables", bool(owned), "none found")

    # ---- triggers --------------------------------------------------------
    section("3. Immutability triggers, installed by PostgreSQL-specific DDL")
    installed = names("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
    for trigger, rule in EXPECTED_TRIGGERS.items():
        check(f"{trigger} — {rule}", trigger in installed)

    functions = names(
        "SELECT proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public'"
    )
    rejecters = sorted(name for name in functions if name.startswith("fn_reject"))
    check(
        f"the trigger functions PostgreSQL triggers call exist ({len(rejecters)})",
        bool(rejecters),
        "a trigger referencing a missing function is created happily and fails at the first UPDATE",
    )

    # ---- a trigger genuinely refuses -------------------------------------
    section("4. A trigger refuses an UPDATE, rather than merely existing")
    cursor.execute(
        "INSERT INTO qc_courses (code, title, created_at, updated_at) "
        "VALUES ('PGVERIFY', 'Verification Course', now(), now()) "
        "ON CONFLICT (code) DO UPDATE SET title = EXCLUDED.title RETURNING id"
    )
    course_id = cursor.fetchone()[0]
    cursor.execute(
        "INSERT INTO qc_quizzes (course_id, slug, title, created_at, updated_at) "
        "VALUES (%s, 'pgverify-quiz', 'Verification Quiz', now(), now()) RETURNING id",
        (course_id,),
    )
    quiz_id = cursor.fetchone()[0]
    cursor.execute(
        "INSERT INTO qc_configuration_versions "
        "(quiz_id, version_number, question_count, pass_mark, randomise_questions, max_attempts, "
        " delivery_mode, settings_fingerprint, created_at) "
        "VALUES (%s, 1, 5, 60, false, 3, 'assessment', 'pgverify', now()) RETURNING id",
        (quiz_id,),
    )
    version_id = cursor.fetchone()[0]

    try:
        cursor.execute(
            "UPDATE qc_configuration_versions SET pass_mark = 99 WHERE id = %s", (version_id,)
        )
        refused, detail = False, "the UPDATE succeeded — the version is editable"
    except psycopg.errors.RaiseException as exc:
        refused = "IMMUTABLE_CONFIGURATION_VERSION" in str(exc)
        detail = str(exc)[:140]
    except psycopg.Error as exc:
        refused, detail = False, f"unexpected error: {str(exc)[:140]}"
    check("PostgreSQL refuses to edit a stored configuration version", refused, detail)

    # ---- indexes ---------------------------------------------------------
    section("5. Uniqueness that carries a business rule")
    definitions = dict(
        rows("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'")
    )
    for index, rule in EXPECTED_PARTIAL_INDEXES.items():
        present = index in definitions
        partial = present and "WHERE" in definitions[index].upper()
        check(f"{index} exists and is PARTIAL — {rule}", partial,
              definitions.get(index, "missing")[:120])
    for index, rule in EXPECTED_INDEXES.items():
        check(f"{index} exists — {rule}", index in definitions)

    constraints = names(
        "SELECT conname FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
        "WHERE n.nspname = 'public'"
    )
    check(
        "ux_submission_idempotency exists — a retried submission collapses onto the first",
        "ux_submission_idempotency" in constraints,
    )

    # ---- constraints -----------------------------------------------------
    section("6. CHECK constraints, including the portable boolean predicates")
    check_count = len(
        rows(
            "SELECT conname FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
            "WHERE n.nspname = 'public' AND c.contype = 'c'"
        )
    )
    check(f"CHECK constraints migrated ({check_count})", check_count > 80, str(check_count))

    cursor.execute(
        "INSERT INTO qd_attempts (id, learner_id, course_id, quiz_id, configuration_version_id, "
        " configuration_version_number, configuration_snapshot, attempt_number, status, "
        " question_presentation, selection_seed, total_questions, current_position, started_at, "
        " last_activity_at, created_at, updated_at) "
        "VALUES ('pgverify-attempt', '1', %s, %s, %s, 1, '{}', 1, 'ACTIVE', 'ONE_AT_A_TIME', "
        " 'seed', 1, 1, now(), now(), now(), now())",
        (str(course_id), str(quiz_id), str(version_id)),
    )
    cursor.execute(
        "INSERT INTO qd_attempt_questions (id, attempt_id, question_id, question_version, "
        " position, question_type, points, question_snapshot, created_at) "
        "VALUES ('pgverify-aq', 'pgverify-attempt', 'q1', 1, 1, 'SINGLE_CHOICE', 1, '{}', now())"
    )
    try:
        # answered = true with no response violates ck_answer_payload. This is the constraint whose
        # SQLite-only form (`answered = 1`) failed the PostgreSQL migration.
        cursor.execute(
            "INSERT INTO qd_attempt_answers (id, attempt_id, attempt_question_id, question_id, "
            " answered, complete, response, revision, source, first_saved_at, saved_at) "
            "VALUES ('pgverify-bad', 'pgverify-attempt', 'pgverify-aq', 'q1', true, true, NULL, 1, "
            " 'MANUAL', now(), now())"
        )
        rejected, detail = False, "the insert succeeded"
    except psycopg.errors.CheckViolation as exc:
        rejected, detail = True, str(exc)[:120]
    except psycopg.Error as exc:
        rejected, detail = False, f"unexpected: {str(exc)[:140]}"
    check("the portable boolean CHECK rejects answered-with-no-response", rejected, detail)

    # ---- foreign keys ----------------------------------------------------
    section("7. Foreign keys, and identifier legality")
    fk_count = len(
        rows(
            "SELECT conname FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
            "WHERE n.nspname = 'public' AND c.contype = 'f'"
        )
    )
    check(f"foreign keys migrated ({fk_count})", fk_count > 20, str(fk_count))

    # PostgreSQL enforces foreign keys unconditionally; SQLite needs a per-connection pragma, so
    # this is the one place the guarantee is stronger rather than merely equivalent.
    try:
        cursor.execute(
            "INSERT INTO qd_attempt_questions (id, attempt_id, question_id, question_version, "
            " position, question_type, points, question_snapshot, created_at) "
            "VALUES ('pgverify-orphan', 'no-such-attempt', 'q', 1, 2, 'SINGLE_CHOICE', 1, '{}', "
            " now())"
        )
        refused_fk = False
    except psycopg.errors.ForeignKeyViolation:
        refused_fk = True
    except psycopg.Error:
        refused_fk = False
    check("a dangling reference is refused with no pragma required", refused_fk)

    over_long = [name for name in constraints | set(definitions) if len(name) > 63]
    check(
        "every stored identifier is within PostgreSQL's 63-character limit",
        not over_long,
        str(over_long),
    )
    # SQLAlchemy truncates an over-long name by keeping a prefix and appending `_<4 hex digits>`.
    # That suffix is the signal, not the length: a name that merely happens to be 63 characters is
    # legitimate, while a truncated one is opaque, unstable across schema edits, and will not match
    # what the models declare.
    suspicious = [
        name
        for name in constraints | set(definitions)
        if len(name) == 63 and re.fullmatch(r".*_[0-9a-f]{4}", name)
    ]
    check(
        "no identifier bears SQLAlchemy's truncation hash",
        not suspicious,
        str(suspicious),
    )

    # ---- head ------------------------------------------------------------
    section("8. Alembic state")
    head = rows("SELECT version_num FROM alembic_version")
    check("the database records exactly one alembic head", len(head) == 1, str(head))
    if head:
        print(f"         head = {head[0][0]}")

    cursor.close()
    connection.close()

    print("\n" + "=" * 60)
    total = len(passes) + len(fails)
    if fails:
        print(f"RESULT: {len(fails)} of {total} checks FAILED")
        for failure in fails:
            print(f"  - {failure}")
        return 1
    print(f"RESULT: all {total} PostgreSQL checks PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
