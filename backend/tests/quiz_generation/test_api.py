"""The four routes, over HTTP.

The service tests cover the marking arithmetic. These cover the things only a real request can show:
who is allowed to call what, what the wire format looks like, and — the one that matters most —
**that the answer key never leaves the server on the two routes a learner uses**.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app
from app.modules.identity.models import User
from app.modules.identity.principal import Role
from app.modules.quiz_configuration.models import Course
from app.modules.quiz_generation.api.dependencies import get_quiz_service
from app.modules.quiz_generation.integration.catalogue import CatalogueLookup
from app.modules.quiz_generation.integration.question_bank import (
    QuestionBankSink,
    QuestionBankView,
)
from app.modules.quiz_generation.services.quiz_service import GeneratedQuizService
from tests.harness import ADMIN_TOKEN, LEARNER_TOKEN, auth

QUESTION_COUNT = 4


def _reply(count: int = QUESTION_COUNT) -> str:
    return json.dumps(
        {
            "questions": [
                {
                    "question": f"Question number {index}?",
                    "options": {
                        "A": f"first option {index}",
                        "B": f"second option {index}",
                        "C": f"third option {index}",
                        "D": f"fourth option {index}",
                    },
                    "answer": "ABCD"[index % 4],
                    "explanation": f"Because of reason {index}.",
                }
                for index in range(count)
            ]
        }
    )


class StubGenerator:
    configured = True

    def __init__(self, reply: str) -> None:
        self.reply = reply

    def complete(self, prompt: str, *, max_tokens: int = 0) -> str:  # noqa: ARG002
        return self.reply


@pytest.fixture
def api(db: Session) -> Iterator[TestClient]:
    """A client whose generation service uses a stub model and the test's own session.

    The model is the only thing replaced. The question bank, the catalogue and the ``qz_`` tables
    are all real, so an answer key that leaked would leak from real code.
    """
    with db.begin_nested():
        db.add_all(
            [
                User(
                    email="admin@test.local",
                    display_name="Test Admin",
                    role=Role.ADMIN.value,
                    api_token=ADMIN_TOKEN,
                ),
                User(
                    email="learner@test.local",
                    display_name="Test Learner",
                    role=Role.LEARNER.value,
                    api_token=LEARNER_TOKEN,
                ),
                Course(
                    code="LL-900",
                    title="Anti-Money Laundering for Fee Earners",
                    description="Client due diligence and reporting a suspicion.",
                    rqf_level=6,
                ),
            ]
        )
    db.commit()

    app = create_app()

    def _db() -> Iterator[Session]:
        yield db

    def _service() -> Iterator[GeneratedQuizService]:
        yield GeneratedQuizService(
            db,
            generator=StubGenerator(_reply()),
            sink=QuestionBankSink(db),
            view=QuestionBankView(db),
            courses=CatalogueLookup(db),
        )

    from app.core.deps import get_db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_quiz_service] = _service
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
    app.dependency_overrides.clear()


def _generate(api: TestClient, **payload: object) -> dict:
    body = {"topic": "Anti-money laundering", "count": QUESTION_COUNT} | payload
    response = api.post("/api/v1/generated-quizzes", json=body, headers=auth(ADMIN_TOKEN))
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------


class TestGenerate:
    def test_it_returns_the_quiz_its_questions_and_the_keys(self, api: TestClient) -> None:
        body = _generate(api)

        assert body["quizId"]
        assert body["passMark"] == 50
        assert body["requestedCount"] == QUESTION_COUNT
        assert body["questionCount"] == QUESTION_COUNT
        assert len(body["questions"]) == QUESTION_COUNT
        first = body["questions"][0]
        assert first["sequence"] == 1
        assert first["answer"] in {"A", "B", "C", "D"}
        assert [option["label"] for option in first["options"]] == ["A", "B", "C", "D"]

    def test_the_wire_format_is_camel_case(self, api: TestClient) -> None:
        body = _generate(api)

        assert "passMark" in body and "pass_mark" not in body
        assert "questionId" in body["questions"][0]

    def test_a_learner_may_not_generate(self, api: TestClient) -> None:
        """Generation spends money and writes into the question bank. Both are admin acts."""
        response = api.post(
            "/api/v1/generated-quizzes",
            json={"topic": "Anti-money laundering", "count": 2},
            headers=auth(LEARNER_TOKEN),
        )

        assert response.status_code == 403

    def test_an_anonymous_caller_may_not_generate(self, api: TestClient) -> None:
        response = api.post(
            "/api/v1/generated-quizzes", json={"topic": "Anti-money laundering", "count": 2}
        )

        assert response.status_code == 401

    def test_a_count_above_the_ceiling_is_refused_before_a_model_is_called(
        self, api: TestClient
    ) -> None:
        response = api.post(
            "/api/v1/generated-quizzes",
            json={"topic": "Anti-money laundering", "count": 5000},
            headers=auth(ADMIN_TOKEN),
        )

        # 400, not FastAPI's default 422: the application maps a malformed *request* to
        # BadRequestError (400) and reserves 422 for a request it understood but whose content is
        # invalid. See app/core/errors.py.
        assert response.status_code == 400

    def test_a_course_ref_is_echoed_back(self, api: TestClient) -> None:
        body = _generate(api, courseRef="LL-900")

        assert body["courseRef"] == "LL-900"


# ---------------------------------------------------------------------------
# The answer key — the part worth being strict about
# ---------------------------------------------------------------------------


class TestTheAnswerKeyStaysOnTheServer:
    def test_the_quiz_a_learner_sits_carries_no_answers(self, api: TestClient) -> None:
        quiz_id = _generate(api)["quizId"]

        response = api.get(f"/api/v1/generated-quizzes/{quiz_id}", headers=auth(LEARNER_TOKEN))

        assert response.status_code == 200
        body = response.json()
        assert len(body["questions"]) == QUESTION_COUNT
        for question in body["questions"]:
            assert "answer" not in question
            assert "explanation" not in question
        # Belt and braces: no key anywhere in the serialised body, under any field name.
        assert '"answer"' not in response.text

    def test_the_marking_response_never_says_what_the_right_answer_was(
        self, api: TestClient
    ) -> None:
        """Otherwise this route is an answer-key oracle.

        Submit rubbish, read the corrections, submit again and pass. `isCorrect` is all a marking
        response needs.
        """
        quiz_id = _generate(api)["quizId"]

        response = api.post(
            f"/api/v1/generated-quizzes/{quiz_id}/results",
            json={"answers": {"Q1": "A", "Q2": "A", "Q3": "A", "Q4": "A"}},
            headers=auth(LEARNER_TOKEN),
        )

        assert response.status_code == 200
        for answer in response.json()["answers"]:
            assert set(answer) == {"sequence", "questionId", "given", "isCorrect"}
        assert '"correct":"' not in response.text.replace(" ", "")

    def test_a_learner_may_not_read_the_answer_key(self, api: TestClient) -> None:
        quiz_id = _generate(api)["quizId"]

        response = api.get(
            f"/api/v1/generated-quizzes/{quiz_id}/answers", headers=auth(LEARNER_TOKEN)
        )

        assert response.status_code == 403

    def test_an_administrator_may_read_the_answer_key_back(self, api: TestClient) -> None:
        generated = _generate(api)

        response = api.get(
            f"/api/v1/generated-quizzes/{generated['quizId']}/answers",
            headers=auth(ADMIN_TOKEN),
        )

        assert response.status_code == 200
        keys = [question["answer"] for question in response.json()["questions"]]
        assert keys == [question["answer"] for question in generated["questions"]]


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


class TestResults:
    def test_all_correct_is_a_pass(self, api: TestClient) -> None:
        generated = _generate(api)
        answers = {
            f"Q{question['sequence']}": question["answer"]
            for question in generated["questions"]
        }

        body = api.post(
            f"/api/v1/generated-quizzes/{generated['quizId']}/results",
            json={"answers": answers},
            headers=auth(LEARNER_TOKEN),
        ).json()

        assert body["outcome"] == "PASS"
        assert body["passed"] is True
        assert body["correct"] == QUESTION_COUNT
        assert body["percentage"] == 100.0

    def test_all_wrong_is_a_fail(self, api: TestClient) -> None:
        generated = _generate(api)
        answers = {
            f"Q{question['sequence']}": next(
                label for label in "ABCD" if label != question["answer"]
            )
            for question in generated["questions"]
        }

        body = api.post(
            f"/api/v1/generated-quizzes/{generated['quizId']}/results",
            json={"answers": answers},
            headers=auth(LEARNER_TOKEN),
        ).json()

        assert body["outcome"] == "FAIL"
        assert body["correct"] == 0

    def test_exactly_half_right_passes_at_the_default_pass_mark(
        self, api: TestClient
    ) -> None:
        """The boundary the brief left open. 50% passes, as it does in UC-05."""
        generated = _generate(api)
        half = generated["questions"][: QUESTION_COUNT // 2]
        answers = {f"Q{question['sequence']}": question["answer"] for question in half}

        body = api.post(
            f"/api/v1/generated-quizzes/{generated['quizId']}/results",
            json={"answers": answers},
            headers=auth(LEARNER_TOKEN),
        ).json()

        assert body["percentage"] == 50.0
        assert body["outcome"] == "PASS"

    def test_an_empty_submission_is_a_fail_not_an_error(self, api: TestClient) -> None:
        generated = _generate(api)

        response = api.post(
            f"/api/v1/generated-quizzes/{generated['quizId']}/results",
            json={"answers": {}},
            headers=auth(LEARNER_TOKEN),
        )

        assert response.status_code == 200
        assert response.json()["outcome"] == "FAIL"

    def test_an_unknown_quiz_is_a_404(self, api: TestClient) -> None:
        response = api.post(
            "/api/v1/generated-quizzes/no-such-quiz/results",
            json={"answers": {"Q1": "A"}},
            headers=auth(LEARNER_TOKEN),
        )

        assert response.status_code == 404

    def test_an_anonymous_caller_may_not_submit(self, api: TestClient) -> None:
        generated = _generate(api)

        response = api.post(
            f"/api/v1/generated-quizzes/{generated['quizId']}/results",
            json={"answers": {"Q1": "A"}},
        )

        assert response.status_code == 401
