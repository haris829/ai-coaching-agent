"""Smoke-test the test UI against a live backend through the Vite dev proxy.

Confirms the two halves actually talk to each other: the UI is served, every page module compiles,
and requests made from the browser origin reach the backend and return real data — for the question
bank (UC-02), for quiz configuration and learner rules (UC-01), and for taking a quiz (UC-03).

This is a contract smoke test, not a browser test: it drives Vite's module transform and the dev
proxy rather than clicking through a rendered page.

    python -m scripts.smoke_ui
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
FRONTEND_DIR = ROOT_DIR / "frontend"

BACKEND_URL = "http://127.0.0.1:8124"
UI_URL = "http://127.0.0.1:5174"

ADMIN_TOKEN = "smoke-admin-token"
LEARNER_TOKEN = "smoke-learner-token"

failures: list[str] = []
checks = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if condition:
        print(f"  [PASS] {label}")
    else:
        failures.append(label)
        print(f"  [FAIL] {label}{f' — {detail}' if detail else ''}")


def fetch(url: str, timeout: float = 15.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # connection refused etc.
        return 0, str(exc)


def authed_fetch(url: str, token: str, timeout: float = 15.0) -> tuple[int, str]:
    """`fetch` with a bearer token, for the endpoints that resolve an identity."""
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # connection refused etc.
        return 0, str(exc)


def wait_for(url: str, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, _ = fetch(url, timeout=3)
        if status and status < 500:
            return True
        time.sleep(0.5)
    return False


def post_question(base: str) -> tuple[int, dict]:
    payload = {
        "type": "SINGLE_CHOICE",
        "questionText": "Which layer of the OSI model performs routing?",
        "explanation": "Layer 3, the Network layer.",
        "topics": ["Networking"],
        "options": [
            {"label": "A", "text": "Layer 2", "isCorrect": False},
            {"label": "B", "text": "Layer 3", "isCorrect": True},
            {"label": "C", "text": "Layer 4", "isCorrect": False},
            {"label": "D", "text": "Layer 7", "isCorrect": False},
        ],
        "scoring": {"points": 1, "scoringStrategy": "ALL_OR_NOTHING"},
    }
    request = urllib.request.Request(
        f"{base}/api/question-bank/questions",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "X-Admin-User": "smoke"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def seed_platform_rows(path: Path) -> None:
    """Insert the identity, course and quiz rows the UC-01 screens need.

    Raw SQL because the company's systems will own these rows — there is no API that creates them.
    """
    import sqlite3

    now = "2026-01-01 00:00:00"
    connection = sqlite3.connect(path, timeout=10)
    try:
        connection.execute(
            "INSERT INTO qa_users (email, display_name, role, api_token, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("smoke-admin@example.com", "Smoke Admin", "admin", ADMIN_TOKEN, now),
        )
        connection.execute(
            "INSERT INTO qa_users (email, display_name, role, api_token, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("smoke-learner@example.com", "Smoke Learner", "learner", LEARNER_TOKEN, now),
        )
        connection.execute(
            "INSERT INTO qc_courses (code, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("SMOKE-1", "Smoke Course", now, now),
        )
        course_id = connection.execute(
            "SELECT id FROM qc_courses WHERE code = 'SMOKE-1'"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO qc_quizzes (course_id, slug, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (course_id, "smoke-quiz", "Smoke Quiz", now, now),
        )
        connection.commit()
    finally:
        connection.close()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="qb-smoke-"))
    db_path = tmp / "smoke.db"

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

    print("COURSES QUIZ AGENT - TEST UI SMOKE TEST")
    print("=" * 60)

    migrate = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    check("database schema created", migrate.returncode == 0, migrate.stderr[-200:])
    seed_platform_rows(db_path)

    backend_log = (tmp / "backend.log").open("w", encoding="utf-8")
    ui_log = (tmp / "ui.log").open("w", encoding="utf-8")

    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8124"],
        cwd=BACKEND_DIR,
        env=env,
        stdout=backend_log,
        stderr=subprocess.STDOUT,
    )
    ui = subprocess.Popen(
        [
            str(ROOT_DIR / "node_modules" / ".bin" / ("vite.cmd" if os.name == "nt" else "vite")),
            "--port",
            "5174",
            "--strictPort",
            "--host",
            "127.0.0.1",
        ],
        cwd=FRONTEND_DIR,
        stdout=ui_log,
        stderr=subprocess.STDOUT,
        env={**os.environ, "VITE_BACKEND_PORT": "8124"},
    )

    try:
        print("\nStartup")
        print("-------")
        check("backend responds", wait_for(f"{BACKEND_URL}/api/health"))
        check("vite dev server responds", wait_for(f"{UI_URL}/"))

        print("\nUI is served")
        print("------------")
        status, html = fetch(f"{UI_URL}/")
        check("GET / returns 200", status == 200, str(status))
        check("the app root element is present", 'id="root"' in html)
        check("the entry module is referenced", "/src/main.tsx" in html)

        status, main_tsx = fetch(f"{UI_URL}/src/main.tsx")
        check("the entry module compiles and is served", status == 200, str(status))

        status, app_tsx = fetch(f"{UI_URL}/src/App.tsx")
        check("App.tsx transforms without error", status == 200 and "Routes" in app_tsx, str(status))
        check(
            "no Vite transform error in the served module",
            "Internal server error" not in app_tsx and "Transform failed" not in app_tsx,
        )

        # Every screen the workflow needs, so a syntax error in any one of them fails here rather
        # than in a browser.
        for module in (
            "pages/QuizConfigurationPage.tsx",
            "pages/LearnerRulesPage.tsx",
            "pages/QuestionListPage.tsx",
            "pages/QuestionFormPage.tsx",
            "pages/TopicsPage.tsx",
            "pages/ImportPage.tsx",
            "pages/AttemptReportPage.tsx",
            "pages/AttemptPage.tsx",
            "components/IdentitySwitcher.tsx",
            "components/attempt/QuestionInputs.tsx",
            "components/attempt/AttemptReview.tsx",
            "components/attempt/SubmitPanel.tsx",
            "components/attempt/ResultPanel.tsx",
            "lib/configurationRules.ts",
            "lib/attemptAnswers.ts",
            "lib/attemptTimer.ts",
            "api/client.ts",
            "api/attemptTypes.ts",
            "api/resultTypes.ts",
            "api/session.ts",
        ):
            status, source = fetch(f"{UI_URL}/src/{module}")
            check(
                f"{module} transforms without error",
                status == 200
                and "Internal server error" not in source
                and "Transform failed" not in source,
                str(status),
            )

        print("\nAPI reachable from the browser origin (dev proxy)")
        print("------------------------------------------------")
        created_status, created = post_question(BACKEND_URL)
        check("seed question created via the API", created_status == 201, json.dumps(created)[:200])

        status, body = fetch(f"{UI_URL}/api/question-bank/questions?pageSize=10")
        check("proxied GET /api/... returns 200", status == 200, str(status))
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = {}
        check(
            "the UI receives the question the backend stored",
            any(
                item.get("id") == created.get("id") for item in parsed.get("items", [])
            ),
            body[:200],
        )

        status, template = fetch(f"{UI_URL}/api/question-bank/imports/template")
        check("CSV template downloads through the proxy", status == 200 and "SINGLE_CHOICE" in template)

        status, guide = fetch(f"{UI_URL}/api/question-bank/imports/template/guide")
        check("CSV format guide is available to the UI", status == 200 and "listDelimiter" in guide)

        status, topics = fetch(f"{UI_URL}/api/question-bank/topics")
        check("topics endpoint reachable", status == 200 and "Networking" in topics)

        print("\nUC-01 screens can reach their endpoints")
        print("--------------------------------------")
        status, meta = fetch(f"{UI_URL}/api/meta")
        check(
            "configuration vocabulary reachable",
            status == 200 and "SINGLE_CHOICE" in meta and "deliveryModes" in meta,
            str(status),
        )

        status, session = fetch(f"{UI_URL}/api/session")
        check(
            "the identity switcher can list development identities",
            status == 200 and ADMIN_TOKEN in session and LEARNER_TOKEN in session,
            session[:200],
        )

        status, quizzes = authed_fetch(f"{UI_URL}/api/admin/quizzes", ADMIN_TOKEN)
        check(
            "the configuration screen can list quizzes",
            status == 200 and "Smoke Quiz" in quizzes,
            quizzes[:200],
        )
        quiz_id = json.loads(quizzes)["quizzes"][0]["id"]

        status, bank = authed_fetch(
            f"{UI_URL}/api/admin/quizzes/{quiz_id}/question-bank", ADMIN_TOKEN
        )
        check(
            "the configuration screen can read live bank capacity",
            status == 200 and "availableByType" in bank,
            bank[:200],
        )

        status, configuration = authed_fetch(
            f"{UI_URL}/api/admin/quizzes/{quiz_id}/configuration", ADMIN_TOKEN
        )
        check(
            "an unconfigured quiz reports no configuration rather than failing",
            status == 200 and json.loads(configuration)["configuration"] is None,
            configuration[:200],
        )

        status, rules = authed_fetch(f"{UI_URL}/api/quizzes/{quiz_id}/rules", LEARNER_TOKEN)
        check(
            "the learner screen gets a clear error for an unconfigured quiz",
            status == 409 and "CONFIGURATION_UNAVAILABLE" in rules,
            rules[:200],
        )

        print("\nUC-03 screen can reach its endpoints")
        print("-----------------------------------")
        # The quiz is deliberately left unconfigured here, so what this proves is that the attempt
        # screen's first two calls are reachable through the proxy and answer with the documented
        # envelope rather than a proxy or CORS failure. The full lifecycle is covered by
        # `scripts.verify_e2e` against a configured quiz.
        status, eligibility = authed_fetch(
            f"{UI_URL}/api/v1/quizzes/{quiz_id}/attempt-eligibility", LEARNER_TOKEN
        )
        check(
            "the eligibility check is reachable and answers in the standard shape",
            status in (200, 409, 422) and ("eligibility" in eligibility or "error" in eligibility),
            f"{status} {eligibility[:200]}",
        )

        status, active = authed_fetch(f"{UI_URL}/api/v1/attempts/active?quizId={quiz_id}", LEARNER_TOKEN)
        check(
            "the resume path reports 'no open attempt' rather than failing",
            status == 404 and "NO_ACTIVE_ATTEMPT" in active,
            f"{status} {active[:200]}",
        )

        status, unauthenticated = fetch(f"{UI_URL}/api/v1/attempts/active?quizId={quiz_id}")
        check(
            "an unauthenticated attempt read is refused",
            status == 401 and "UNAUTHENTICATED" in unauthenticated,
            f"{status} {unauthenticated[:200]}",
        )

        print()
        print("UC-04/05/06 screens can reach their endpoints")
        print("--------------------------------------------")
        # As above, the quiz is deliberately unconfigured, so there is no scored attempt to read. What
        # this proves is that the three result endpoints are routed through the proxy and answer in the
        # documented envelope rather than with a proxy, CORS or routing failure. The full chain is
        # covered by `scripts.verify_e2e` against a real submitted attempt.
        for label, path, expected_code in (
            ("the score screen", "result", "ATTEMPT_NOT_FOUND"),
            ("the pass/fail screen", "outcome", "ATTEMPT_NOT_FOUND"),
            ("the feedback screen", "feedback", "ATTEMPT_NOT_FOUND"),
        ):
            status, body = authed_fetch(
                f"{UI_URL}/api/v1/attempts/no-such-attempt/{path}", LEARNER_TOKEN
            )
            check(
                f"{label} is reachable and answers in the standard shape",
                status == 404 and expected_code in body,
                f"{status} {body[:200]}",
            )

        for label, path in (
            ("results", "results"),
            ("outcomes", "outcomes"),
            ("feedback reports", "feedback"),
        ):
            status, body = authed_fetch(f"{UI_URL}/api/v1/{path}", LEARNER_TOKEN)
            check(
                f"the learner's {label} list is reachable and empty",
                status == 200 and '"total":0' in body.replace(" ", ""),
                f"{status} {body[:200]}",
            )

        status, refused = fetch(f"{UI_URL}/api/v1/results")
        check(
            "an unauthenticated result read is refused",
            status == 401,
            f"{status} {refused[:200]}",
        )

        print()
        print("UC-07 coaching screens can reach their endpoints")
        print("------------------------------------------------")
        # Same reasoning as above: there is no submitted attempt here, so what this proves is that the
        # coaching routes are reachable through the proxy and answer in the documented envelope. The
        # conversation itself is covered by `scripts.verify_e2e` and by tests/integration.
        status, body = authed_fetch(
            f"{UI_URL}/api/v1/attempts/no-such-attempt/coaching/eligibility", LEARNER_TOKEN
        )
        check(
            "the coaching eligibility read is reachable and reports a reason, not an error",
            status == 200 and "ATTEMPT_NOT_FOUND" in body,
            f"{status} {body[:200]}",
        )
        status, body = authed_fetch(
            f"{UI_URL}/api/v1/attempts/no-such-attempt/coaching/review", LEARNER_TOKEN
        )
        check(
            "the review queue is reachable and answers in the standard shape",
            status == 404 and "ATTEMPT_NOT_FOUND" in body,
            f"{status} {body[:200]}",
        )
        status, body = authed_fetch(
            f"{UI_URL}/api/v1/coaching/sessions/no-such-session", LEARNER_TOKEN
        )
        check(
            "an unknown coaching session is a 404, not a 403",
            status == 404 and "COACHING_SESSION_NOT_FOUND" in body,
            f"{status} {body[:200]}",
        )
        status, refused = fetch(f"{UI_URL}/api/v1/attempts/no-such-attempt/coaching/eligibility")
        check(
            "an unauthenticated coaching read is refused",
            status == 401,
            f"{status} {refused[:200]}",
        )

        status, liveness = fetch(f"{UI_URL}/api/health/live")
        check("the liveness probe is reachable", status == 200 and '"ok"' in liveness, liveness[:120])

    finally:
        for process in (ui, backend):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        backend_log.close()
        ui_log.close()
        if failures:
            print("\n--- vite log tail ---")
            print((tmp / "ui.log").read_text(encoding="utf-8", errors="replace")[-1500:])
            print("\n--- backend log tail ---")
            print((tmp / "backend.log").read_text(encoding="utf-8", errors="replace")[-1500:])

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
