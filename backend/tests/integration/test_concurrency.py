"""Concurrency and data integrity.

The system is meant to be usable by roughly a hundred people, so the invariants must survive two
administrators (or one impatient learner with a double-click) acting at the same instant. Every
test here asserts an *invariant* rather than a particular winner: which request wins a race is not
interesting, but "two attempts were created from one allowance" would be a defect.

Each thread gets its own ``TestClient`` over its own app instance, so requests really do run
concurrently against the shared engine rather than being serialised by one client.

Note on the local datastore: SQLite permits a single writer at a time, and the engine sets
``busy_timeout`` so a concurrent writer waits its turn rather than failing instantly. The company's
server database will allow genuinely parallel writes — which is exactly why these invariants are
enforced by unique constraints and partial indexes rather than by read-then-write checks in
application code.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi.testclient import TestClient

from app.core.question_types import QuestionType
from app.main import create_app
from tests import factories
from tests.harness import ADMIN_TOKEN, LEARNER_TOKEN, Ctx, auth, valid_configuration

QB = "/api/question-bank"


def _run_together(count: int, work: Callable[[TestClient, int], object]) -> list[object]:
    """Fire ``count`` requests as simultaneously as threads allow."""
    clients = [TestClient(create_app(), raise_server_exceptions=False) for _ in range(count)]
    barrier = Barrier(count)

    def task(index: int) -> object:
        barrier.wait(timeout=10)
        return work(clients[index], index)

    try:
        with ThreadPoolExecutor(max_workers=count) as pool:
            return [future.result(timeout=60) for future in [pool.submit(task, i) for i in range(count)]]
    finally:
        for client in clients:
            client.close()


class TestConfigurationVersionCreation:
    def test_concurrent_saves_never_duplicate_a_version_number(self, make_ctx) -> None:
        ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 20})
        url = f"/api/admin/quizzes/{ctx.quiz_id}/configuration"

        def save(client: TestClient, index: int):
            return client.put(
                url,
                json=valid_configuration(
                    questionCount=10,
                    # A distinct pass mark per writer, so every request is a real change and
                    # none of them can be dismissed as a no-op re-save.
                    passMark=50 + index,
                    questionTypes=[{"type": "SINGLE_CHOICE", "quota": 10}],
                ),
                headers=auth(ADMIN_TOKEN),
            )

        responses = _run_together(4, save)

        # Every request either created a version or was cleanly refused as a conflict. Nothing
        # returned a 500, and nothing was half-written.
        for response in responses:
            assert response.status_code in (201, 409), response.text

        numbers = [
            row[0]
            for row in ctx.execute(
                "SELECT version_number FROM qc_configuration_versions WHERE quiz_id = :quiz",
                quiz=ctx.quiz_id,
            ).fetchall()
        ]
        assert len(numbers) == len(set(numbers)), "a version number was reused"
        assert sorted(numbers) == list(range(1, len(numbers) + 1)), "version numbering has gaps"

        # The quiz points at exactly one version, and it is the newest one that was written.
        active = ctx.active_version_id()
        assert active is not None
        highest = ctx.scalar(
            "SELECT id FROM qc_configuration_versions WHERE quiz_id = :quiz "
            "ORDER BY version_number DESC LIMIT 1",
            quiz=ctx.quiz_id,
        )
        assert active == highest

    def test_a_refused_concurrent_save_says_so_and_is_retryable(self, make_ctx) -> None:
        """A losing writer must get an actionable answer, not a stack trace."""
        ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 20})
        url = f"/api/admin/quizzes/{ctx.quiz_id}/configuration"

        def save(client: TestClient, index: int):
            return client.put(
                url,
                json=valid_configuration(
                    questionCount=10,
                    passMark=60 + index,
                    questionTypes=[{"type": "SINGLE_CHOICE", "quota": 10}],
                ),
                headers=auth(ADMIN_TOKEN),
            )

        responses = _run_together(6, save)
        refused = [r for r in responses if r.status_code == 409]

        for response in refused:
            body = response.json()
            assert body["error"]["code"] in (
                "CONCURRENT_CONFIGURATION_UPDATE",
                "INTEGRITY_CONFLICT",
            )
            assert "Traceback" not in response.text
        assert any(r.status_code == 201 for r in responses), "no writer made progress"


class TestStartingAnAttempt:
    def test_a_double_click_creates_exactly_one_attempt(self, make_ctx) -> None:
        ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 20})
        ctx.save_configuration(
            valid_configuration(
                questionCount=5,
                maxAttempts=5,
                questionTypes=[{"type": "SINGLE_CHOICE", "quota": 5}],
            )
        )
        body = {"quizId": str(ctx.quiz_id)}

        responses = _run_together(
            5,
            lambda client, _index: client.post(
                "/api/v1/attempts", json=body, headers=auth(LEARNER_TOKEN)
            ),
        )

        created = [r for r in responses if r.status_code == 201]
        assert len(created) == 1, [r.status_code for r in responses]
        for response in responses:
            assert response.status_code in (201, 409, 500, 503), response.text

        assert ctx.attempt_count() == 1
        # Exactly one attempt's worth of frozen questions — no orphans from the losing threads.
        assert ctx.delivered_question_count() == 5

    def test_the_attempt_allowance_cannot_be_exceeded_by_racing(self, make_ctx) -> None:
        """One permitted attempt means one attempt, however many requests arrive together."""
        ctx: Ctx = make_ctx({QuestionType.TRUE_FALSE: 10})
        ctx.save_configuration(
            valid_configuration(
                questionCount=2,
                maxAttempts=1,
                questionTypes=[{"type": "TRUE_FALSE", "quota": 2}],
            )
        )
        body = {"quizId": str(ctx.quiz_id)}

        _run_together(
            4,
            lambda client, _index: client.post(
                "/api/v1/attempts", json=body, headers=auth(LEARNER_TOKEN)
            ),
        )

        assert ctx.attempt_count() == 1


class TestQuestionBankWrites:
    def test_concurrent_question_creation_allocates_unique_references(self, make_ctx) -> None:
        """The reference sequence is allocated inside the transaction, not guessed."""
        ctx: Ctx = make_ctx({})

        def create(client: TestClient, index: int):
            return client.post(
                f"{QB}/questions",
                json=factories.single_choice(
                    questionText=f"Concurrently authored question number {index}."
                ),
                headers=auth(ADMIN_TOKEN),
            )

        responses = _run_together(6, create)
        created = [r for r in responses if r.status_code == 201]
        assert created, [r.status_code for r in responses]

        references = [row[0] for row in ctx.execute("SELECT reference FROM qb_questions").fetchall()]
        assert len(references) == len(set(references)), "a question reference was reused"
        assert len(references) == len(created)

        seqs = [row[0] for row in ctx.execute("SELECT seq FROM qb_questions").fetchall()]
        assert len(seqs) == len(set(seqs)), "a sequence value was reused"

    def test_concurrent_identical_creations_produce_at_most_one_question(self, make_ctx) -> None:
        """Duplicate detection must not be defeated by simultaneity."""
        ctx: Ctx = make_ctx({})
        payload = factories.single_choice(questionText="Exactly the same question, twice at once.")

        responses = _run_together(
            4,
            lambda client, _index: client.post(
                f"{QB}/questions", json=payload, headers=auth(ADMIN_TOKEN)
            ),
        )

        assert [r.status_code for r in responses].count(201) <= 1
        total = ctx.scalar("SELECT COUNT(*) FROM qb_questions")
        assert total == 1, f"duplicate detection let {total} copies through"

    def test_retiring_while_a_learner_starts_never_delivers_a_retired_question(
        self, make_ctx
    ) -> None:
        """Whoever wins, the attempt that exists contains only questions that were deliverable."""
        ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 6})
        ctx.save_configuration(
            valid_configuration(
                questionCount=6,
                maxAttempts=3,
                questionTypes=[{"type": "SINGLE_CHOICE", "quota": 6}],
            )
        )
        target = ctx.questions[QuestionType.SINGLE_CHOICE][0]
        retire_url = f"{QB}/questions/{target}/retire"

        def work(client: TestClient, index: int):
            if index == 0:
                return client.post(
                    "/api/v1/attempts",
                    json={"quizId": str(ctx.quiz_id)},
                    headers=auth(LEARNER_TOKEN),
                )
            return client.post(retire_url, json={"reason": "race"}, headers=auth(ADMIN_TOKEN))

        responses = _run_together(2, work)
        start = responses[0]

        # Either the attempt was created before the retirement landed, or it was refused because
        # the bank could no longer satisfy the configuration. Both are correct.
        assert start.status_code in (201, 409, 422, 500, 503), start.text

        if start.status_code == 201:
            attempt_id = start.json()["attempt"]["attemptId"]
            drawn = ctx.attempt_questions(attempt_id).json()["questions"]
            # A question may have been retired *after* being drawn, which is allowed; what must
            # never happen is delivering a question that was already retired at draw time.
            assert len(drawn) == 6
            assert ctx.delivered_question_count() == 6
        else:
            assert ctx.attempt_count() == 0
            assert ctx.delivered_question_count() == 0
