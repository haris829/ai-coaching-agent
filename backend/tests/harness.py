"""Shared harness for the quiz-configuration and integration tests.

``Ctx`` bundles what a configuration test needs: an HTTP client driving the real app, a real
question bank stocked to a known shape, and direct database access for asserting on committed
state and for proving database-level rules with raw SQL.

The client uses the app's **own** request-scoped sessions rather than sharing the test's session.
That matters here: these tests assert that a failed save left nothing behind, which is only
meaningful if the request really committed or really rolled back.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.question_types import QuestionType
from app.db.session import SessionLocal, engine
from app.modules.identity.enums import EnrolmentStatus
from app.modules.identity.models import Enrolment, User
from app.modules.identity.principal import Role
from app.modules.quiz_configuration.models import Course, Quiz
from tests import bank

ADMIN_TOKEN = "test-admin-token"
LEARNER_TOKEN = "test-learner-token"
LEARNER2_TOKEN = "test-learner2-token"
#: UC-09's third audience. A distinct role, not a flavour of admin: an assessor signs off on one
#: learner's result and is named on the review record, while an administrator configures quizzes.
ASSESSOR_TOKEN = "test-assessor-token"
#: The cohorts the two learners are enrolled in, so a test can filter by one and get one.
LEARNER_COHORT = "cohort-a"
LEARNER2_COHORT = "cohort-b"
#: The service credential UC-09's system endpoints require — the session monitor, the certificate
#: service, the recovery sweep. Deliberately not reachable from a browser.
SYSTEM_TOKEN = "test-system-token"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Ctx:
    """Everything a UC-01 test needs."""

    client: TestClient
    admin_id: int
    learner_id: int
    learner2_id: int
    assessor_id: int
    course_id: int
    quiz_id: int
    questions: dict[QuestionType, list[str]] = field(default_factory=dict)

    # --- direct database access ---------------------------------------------
    @contextmanager
    def session(self) -> Iterator[Session]:
        """A short-lived session, so assertions always see freshly committed state."""
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    def scalar(self, sql: str, **params: Any) -> Any:
        with engine.connect() as connection:
            return connection.execute(text(sql), params).scalar()

    def execute(self, sql: str, **params: Any) -> Any:
        """Run raw SQL in its own transaction — used to prove database-level rules."""
        with engine.begin() as connection:
            return connection.execute(text(sql), params)

    # --- convenience counters -----------------------------------------------
    def attempt_count(self) -> int:
        """Attempts in UC-03, the single owner of them."""
        return int(self.scalar("SELECT COUNT(*) FROM qd_attempts") or 0)

    def delivered_question_count(self) -> int:
        """Questions frozen onto attempts — UC-03's record of what a learner was given."""
        return int(self.scalar("SELECT COUNT(*) FROM qd_attempt_questions") or 0)

    def version_count(self) -> int:
        return int(self.scalar("SELECT COUNT(*) FROM qc_configuration_versions") or 0)

    def question_type_row_count(self) -> int:
        return int(
            self.scalar(
                "SELECT COUNT(*) FROM qc_configuration_version_question_types"
            )
            or 0
        )

    def bank_usage_count(self) -> int:
        """Rows in UC-02's own delivery-seam table.

        UC-03 does **not** write these: it freezes its own question snapshots. See the note in
        docs/INTEGRATION.md about the two records of "which questions did this attempt get".
        """
        return int(self.scalar("SELECT COUNT(*) FROM qb_question_usages") or 0)

    def active_version_id(self) -> int | None:
        value = self.scalar(
            "SELECT active_configuration_version_id FROM qc_quizzes WHERE id = :id",
            id=self.quiz_id,
        )
        return None if value is None else int(value)

    # --- API helpers ---------------------------------------------------------
    def save_configuration(
        self, payload: Any, token: str = ADMIN_TOKEN, quiz_id: int | None = None
    ):
        target = self.quiz_id if quiz_id is None else quiz_id
        return self.client.put(
            f"/api/admin/quizzes/{target}/configuration", json=payload, headers=auth(token)
        )

    def get_configuration(self, token: str = ADMIN_TOKEN):
        return self.client.get(
            f"/api/admin/quizzes/{self.quiz_id}/configuration", headers=auth(token)
        )

    def get_versions(self, token: str = ADMIN_TOKEN):
        return self.client.get(
            f"/api/admin/quizzes/{self.quiz_id}/configuration/versions", headers=auth(token)
        )

    def get_question_bank(self, token: str = ADMIN_TOKEN, **params: Any):
        return self.client.get(
            f"/api/admin/quizzes/{self.quiz_id}/question-bank",
            headers=auth(token),
            params=params or None,
        )

    def get_rules(self, token: str = LEARNER_TOKEN):
        """UC-01's read-only rules summary."""
        return self.client.get(f"/api/quizzes/{self.quiz_id}/rules", headers=auth(token))

    # --- attempts: UC-03 owns these ------------------------------------------
    def eligibility(self, token: str = LEARNER_TOKEN):
        return self.client.get(
            f"/api/v1/quizzes/{self.quiz_id}/attempt-eligibility", headers=auth(token)
        )

    def start_attempt(self, token: str = LEARNER_TOKEN):
        return self.client.post(
            "/api/v1/attempts", json={"quizId": str(self.quiz_id)}, headers=auth(token)
        )

    def get_attempt(self, attempt_id: str, token: str = LEARNER_TOKEN):
        return self.client.get(f"/api/v1/attempts/{attempt_id}", headers=auth(token))

    def attempt_questions(self, attempt_id: str, token: str = LEARNER_TOKEN):
        return self.client.get(f"/api/v1/attempts/{attempt_id}/questions", headers=auth(token))

    def submit_attempt(self, attempt_id: str, token: str = LEARNER_TOKEN):
        return self.client.post(
            f"/api/v1/attempts/{attempt_id}/submission",
            json={"confirmed": True},
            headers=auth(token),
        )

    def list_attempts(self, token: str = LEARNER_TOKEN):
        return self.client.get(
            "/api/v1/attempts", params={"quizId": str(self.quiz_id)}, headers=auth(token)
        )

    def start_and_read_questions(self, token: str = LEARNER_TOKEN) -> tuple[str, list[dict]]:
        """Start an attempt and return ``(attempt_id, delivered questions)``.

        The two calls are separate in UC-03's API — creation returns the attempt, the paper is
        fetched from its own endpoint — so this keeps the integration tests readable.
        """
        created = self.start_attempt(token)
        assert created.status_code == 201, created.text
        attempt_id = created.json()["attempt"]["attemptId"]
        questions = self.attempt_questions(attempt_id, token)
        assert questions.status_code == 200, questions.text
        return attempt_id, questions.json()["questions"]

    # --- question bank helpers ----------------------------------------------
    def retire(self, question_type: QuestionType, count: int | None = None) -> list[str]:
        """Retire questions of one type through the real API. Returns the retired ids."""
        ids = self.questions.get(question_type, [])
        targets = ids if count is None else ids[:count]
        for question_id in targets:
            response = self.client.post(
                f"/api/question-bank/questions/{question_id}/retire",
                json={"reason": "test"},
                headers=auth(ADMIN_TOKEN),
            )
            assert response.status_code == 200, response.text
        return list(targets)


def build_ctx(
    plan: dict[QuestionType, int] | None = None,
    *,
    topics: list[str] | None = None,
    coaching_llm: Any | None = None,
) -> Ctx:
    """Build a configured course, a stocked bank and a client onto the real app.

    ``coaching_llm`` binds an AI coach for UC-07. Left unbound — the default, and what a stock
    deployment runs — coaching honestly reports itself unavailable, so a test that wants to hold a
    conversation has to supply a coach. That asymmetry is deliberate: it means no test can
    accidentally pass because something invented a reply.
    """
    from app.main import create_app
    from app.modules.coaching.container import CoachingAppContext

    with SessionLocal() as session:
        admin = User(
            email="admin@test.local",
            display_name="Test Admin",
            role=Role.ADMIN.value,
            api_token=ADMIN_TOKEN,
        )
        learner = User(
            email="learner@test.local",
            display_name="Test Learner",
            role=Role.LEARNER.value,
            api_token=LEARNER_TOKEN,
        )
        learner2 = User(
            email="learner2@test.local",
            display_name="Second Learner",
            role=Role.LEARNER.value,
            api_token=LEARNER2_TOKEN,
        )
        assessor = User(
            email="assessor@test.local",
            display_name="Test Assessor",
            role=Role.ASSESSOR.value,
            api_token=ASSESSOR_TOKEN,
        )
        course = Course(code="TEST-1", title="Test Course")
        session.add_all([admin, learner, learner2, assessor, course])
        session.flush()

        quiz = Quiz(course_id=course.id, slug="test-quiz", title="Test Quiz")
        session.add(quiz)
        session.flush()

        # UC-03 refuses to create an attempt for a learner who is not enrolled, so the platform
        # placeholder is stocked here alongside the identities.
        #
        # The two learners are put in *different* cohorts, which is what lets UC-10's cohort filter
        # be tested against real data rather than against a fixture that agrees with itself.
        for learner_row, cohort in ((learner, "cohort-a"), (learner2, "cohort-b")):
            session.add(
                Enrolment(
                    learner_id=str(learner_row.id),
                    course_id=str(course.id),
                    cohort_id=cohort,
                    status=EnrolmentStatus.ACTIVE.value,
                )
            )
        session.flush()

        ids = {
            "admin_id": admin.id,
            "learner_id": learner.id,
            "learner2_id": learner2.id,
            "assessor_id": assessor.id,
            "course_id": course.id,
            "quiz_id": quiz.id,
        }
        session.commit()

        seeded = bank.seed_bank(session, plan, topics=topics)
        questions = {
            question_type: [question.id for question in items]
            for question_type, items in seeded.items()
        }

    coaching = (
        CoachingAppContext(session_factory=SessionLocal, llm=coaching_llm)
        if coaching_llm is not None
        else None
    )
    client = TestClient(
        create_app(coaching_context=coaching), raise_server_exceptions=False
    )
    return Ctx(client=client, questions=questions, **ids)


def valid_configuration(**overrides: Any) -> dict[str, Any]:
    """A configuration the default test bank can satisfy."""
    payload: dict[str, Any] = {
        "questionCount": 10,
        "timeLimitMinutes": 30,
        "passMark": 60,
        "questionTypes": [
            {"type": "SINGLE_CHOICE", "quota": None},
            {"type": "TRUE_FALSE", "quota": None},
        ],
        "randomiseQuestions": False,
        "maxAttempts": 3,
        "deliveryMode": "assessment",
    }
    payload.update(overrides)
    return payload


def field_errors(body: dict[str, Any]) -> dict[str, list[str]]:
    """Group a validation response's field-level details by field name."""
    grouped: dict[str, list[str]] = {}
    for item in body["error"].get("details", []):
        grouped.setdefault(item["field"], []).append(item["message"])
    return grouped


def error_codes(body: dict[str, Any]) -> set[str]:
    return {item["code"] for item in body["error"].get("details", [])}
