"""UC-08 on the real chain, over HTTP, with every real adapter.

    configure (UC-01) -> bank (UC-02) -> attempt (UC-03) -> submit
        -> score (UC-04) -> gate (UC-05) -> feedback (UC-06) -> retake (UC-08)

``tests/retakes/`` tests UC-08's rules against port fakes, for the reasons its conftest sets out.
This file tests the part those cannot: that the adapters onto UC-01, UC-02, UC-03, UC-04, UC-05 and
UC-06 line up with what those capabilities actually wrote, and that the ``qt_`` tables satisfy the
contracts the in-memory repositories were standing in for.

Nothing here is a double. Real rows, real transactions, real constraints.

WHAT THIS FILE IS REALLY CHECKING
---------------------------------
Six claims that only a real chain can support:

1. **A retake is genuinely a different paper.** The question ids delivered by the retake are
   compared with the ids UC-03 froze onto the first attempt — read from ``qd_attempt_questions``,
   not from a fake that agrees with itself. A reordering would fail this.
2. **A small bank reuses, and says so.** With a bank too small to avoid it, the retake is still
   delivered in full and the reuse is recorded rather than the retake being refused.
3. **The previous attempt is untouched.** Its row, its frozen questions, its answers, its score and
   its outcome are byte-compared before and after. This is UC-08's central promise and the only
   way to check it is against real rows.
4. **The allowance is the real one.** UC-03's attempt count, the locked configuration's maximum,
   and a real administrator grant — through the real admin endpoint — combine into the eligibility
   UC-08 serves.
5. **A grant does not change the quiz.** The configuration version's ``max_attempts`` is read from
   UC-01's own table after the grant and must be unchanged.
6. **Idempotency and the slot are the database's.** A repeated retake request produces one row in
   ``qt_retake_requests`` and one new attempt, enforced by the unique indexes rather than by a
   check in a service.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.question_bank.models import Question
from tests import bank
from tests.harness import ADMIN_TOKEN, LEARNER_TOKEN, Ctx, auth

#: Three questions drawn freely from a bank far larger than the paper, so a retake has room to be
#: completely fresh. ``maxAttempts`` of 2 is what makes the allowance arithmetic observable.
RETAKEABLE_CONFIGURATION: dict[str, Any] = {
    "questionCount": 3,
    "timeLimitMinutes": 30,
    "passMark": 60,
    "questionTypes": [{"type": "SINGLE_CHOICE"}],
    "randomiseQuestions": False,
    "maxAttempts": 2,
    "deliveryMode": "assessment",
}


# ---------------------------------------------------------------------------
# Driving the real chain
# ---------------------------------------------------------------------------


def _wrong_answer(ctx: Ctx, question: dict[str, Any]) -> dict[str, Any]:
    """A deliberately incorrect answer, built from the bank's own key.

    Every attempt in this file is failed on purpose: a passing learner has no reason to retake,
    and UC-05 recording a failure is part of what the history assertions read.
    """
    with ctx.session() as session:
        row = session.get(Question, question["questionId"])
        assert row is not None
        wrong = [option.label for option in row.options if not option.is_correct]
    return {"type": "SINGLE_CHOICE", "selectedOptionId": wrong[0]}


def _fail_existing(ctx: Ctx, attempt_id: str, token: str = LEARNER_TOKEN) -> list[str]:
    """Answer an *existing* attempt wrongly and submit it.

    A retake's attempt is created by UC-08, not by UC-03's start endpoint, so sitting one has to
    begin from the attempt that already exists. Calling ``start_attempt`` again would correctly be
    refused — the learner already has an open attempt — and that refusal is not what these tests
    are about.
    """
    read = ctx.attempt_questions(attempt_id, token)
    assert read.status_code == 200, read.text
    questions = read.json()["questions"]

    for question in questions:
        saved = ctx.client.put(
            f"/api/v1/attempts/{attempt_id}/questions/{question['questionId']}/answer",
            json={"response": _wrong_answer(ctx, question), "source": "MANUAL"},
            headers=auth(token),
        )
        assert saved.status_code == 200, saved.text

    submitted = ctx.client.post(
        f"/api/v1/attempts/{attempt_id}/submission",
        json={"confirmed": True},
        headers=auth(token),
    )
    assert submitted.status_code == 200, submitted.text
    return [question["questionId"] for question in questions]


def _sit_and_fail(ctx: Ctx, token: str = LEARNER_TOKEN) -> tuple[str, list[str]]:
    """Start an attempt, answer everything wrongly, submit. Returns ``(attempt_id, question ids)``."""
    attempt_id, questions = ctx.start_and_read_questions(token)
    for question in questions:
        saved = ctx.client.put(
            f"/api/v1/attempts/{attempt_id}/questions/{question['questionId']}/answer",
            json={"response": _wrong_answer(ctx, question), "source": "MANUAL"},
            headers=auth(token),
        )
        assert saved.status_code == 200, saved.text

    submitted = ctx.client.post(
        f"/api/v1/attempts/{attempt_id}/submission",
        json={"confirmed": True},
        headers=auth(token),
    )
    assert submitted.status_code == 200, submitted.text
    return attempt_id, [question["questionId"] for question in questions]


def _delivered_ids(ctx: Ctx, attempt_id: str) -> list[str]:
    """The question ids frozen onto an attempt, straight from UC-03's table, in delivery order."""
    with ctx.session() as session:
        rows = session.execute(
            __import__("sqlalchemy").text(
                "SELECT question_id FROM qd_attempt_questions "
                "WHERE attempt_id = :id ORDER BY position"
            ),
            {"id": attempt_id},
        ).scalars()
        return list(rows)


def _eligibility(ctx: Ctx, token: str = LEARNER_TOKEN) -> dict[str, Any]:
    response = ctx.client.get(
        f"/api/v1/quizzes/{ctx.quiz_id}/retake-eligibility", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


def _create_retake(ctx: Ctx, token: str = LEARNER_TOKEN, **body: Any):
    return ctx.client.post(
        f"/api/v1/quizzes/{ctx.quiz_id}/retakes", json=body, headers=auth(token)
    )


def _configured(make_ctx: Any, plan: dict | None = None, **overrides: Any) -> Ctx:
    ctx = make_ctx(plan or bank.DEFAULT_BANK)
    saved = ctx.save_configuration({**RETAKEABLE_CONFIGURATION, **overrides})
    assert saved.status_code == 201, saved.text
    return ctx


# ---------------------------------------------------------------------------
# 1. A retake is a genuinely different paper
# ---------------------------------------------------------------------------


def test_a_retake_delivers_different_questions_from_the_real_bank(make_ctx: Any) -> None:
    """The ids are compared, not the order — a reshuffle of the same paper is not a retake."""
    ctx = _configured(make_ctx)
    first_id, _ = _sit_and_fail(ctx)
    first_questions = _delivered_ids(ctx, first_id)

    created = _create_retake(ctx)
    assert created.status_code == 201, created.text
    body = created.json()

    retake_attempt_id = body["attempt"]["attempt_id"]
    retake_questions = _delivered_ids(ctx, retake_attempt_id)

    assert len(retake_questions) == 3
    # The claim, stated as sets so a different presentation order cannot satisfy it.
    assert set(retake_questions).isdisjoint(set(first_questions))
    assert body["question_set_difference"]["repeated_question_ids"] == []
    assert retake_attempt_id != first_id


def test_the_retake_is_an_independent_attempt_with_its_own_number(make_ctx: Any) -> None:
    ctx = _configured(make_ctx)
    first_id, _ = _sit_and_fail(ctx)

    created = _create_retake(ctx)
    assert created.status_code == 201, created.text
    attempt = created.json()["attempt"]

    assert attempt["attempt_number"] == 2
    assert attempt["status"] == "ACTIVE"
    # Lineage recorded on UC-03's own row, which is what attempt history and analytics read.
    lineage = ctx.scalar(
        "SELECT retake_of_attempt_id FROM qd_attempts WHERE id = :id",
        id=attempt["attempt_id"],
    )
    assert lineage == first_id


# ---------------------------------------------------------------------------
# 2. A bank too small to avoid reuse
# ---------------------------------------------------------------------------


def test_a_bank_too_small_reuses_questions_and_records_that_it_had_to(make_ctx: Any) -> None:
    """Reuse is preferable to a short paper, to a retired question, and to a refusal.

    Four eligible single-choice questions and a three-question paper: at most one of the retake's
    three can be new, so reuse is arithmetically unavoidable. The retake still happens.
    """
    from app.core.question_types import QuestionType

    ctx = _configured(make_ctx, plan={QuestionType.SINGLE_CHOICE: 4})
    first_id, _ = _sit_and_fail(ctx)
    first_questions = set(_delivered_ids(ctx, first_id))

    created = _create_retake(ctx)
    assert created.status_code == 201, created.text
    body = created.json()

    retake_questions = _delivered_ids(ctx, body["attempt"]["attempt_id"])
    assert len(retake_questions) == 3, "a small bank must not produce a short paper"

    reused = set(retake_questions) & first_questions
    assert reused, "with four questions and a three-question paper, reuse is unavoidable"
    # Recorded rather than silent: "why did I see that question again?" stays answerable.
    assert set(body["question_set_difference"]["repeated_question_ids"]) == reused
    assert body["question_plan"]["exclusion_scope"] in {
        "ALL_PREVIOUS_ATTEMPTS",
        "PREVIOUS_ATTEMPT_ONLY",
        "NONE",
    }


def test_a_retired_question_is_never_reached_for_to_avoid_reuse(make_ctx: Any) -> None:
    """§8: reuse is preferable to delivering a retired question.

    The bank holds exactly enough to fill two fresh papers, then half of it is retired through
    UC-02's real API. The retake must reuse rather than reach for anything retired.
    """
    from app.core.question_types import QuestionType

    ctx = _configured(make_ctx, plan={QuestionType.SINGLE_CHOICE: 6})
    first_id, _ = _sit_and_fail(ctx)
    first_questions = set(_delivered_ids(ctx, first_id))

    retired = set(ctx.retire(QuestionType.SINGLE_CHOICE, 3))
    assert retired, "the retirement fixture must actually retire something"

    created = _create_retake(ctx)
    assert created.status_code == 201, created.text
    retake_questions = set(_delivered_ids(ctx, created.json()["attempt"]["attempt_id"]))

    assert retake_questions.isdisjoint(retired), "a retired question was delivered"
    assert len(retake_questions) == 3
    del first_questions  # the assertion above is the point; freshness is not, with a shrunk bank


# ---------------------------------------------------------------------------
# 3. The previous attempt is untouched
# ---------------------------------------------------------------------------


def _attempt_fingerprint(ctx: Ctx, attempt_id: str) -> dict[str, Any]:
    """Everything about an attempt that a retake must not be able to change."""
    with ctx.session() as session:
        sa = __import__("sqlalchemy")
        row = session.execute(
            sa.text(
                "SELECT status, attempt_number, configuration_version_id, submitted_at, "
                "finalised_at, total_questions FROM qd_attempts WHERE id = :id"
            ),
            {"id": attempt_id},
        ).mappings().one()
        answers = session.execute(
            sa.text(
                "SELECT question_id, response FROM qd_attempt_answers "
                "WHERE attempt_id = :id ORDER BY question_id"
            ),
            {"id": attempt_id},
        ).mappings().all()
        result = session.execute(
            sa.text(
                "SELECT status, total_marks, maximum_marks, percentage "
                "FROM qr_attempt_results WHERE attempt_id = :id"
            ),
            {"id": attempt_id},
        ).mappings().one_or_none()
        outcome = session.execute(
            sa.text("SELECT outcome, percentage FROM qg_attempt_outcomes WHERE attempt_id = :id"),
            {"id": attempt_id},
        ).mappings().one_or_none()

    return {
        "attempt": dict(row),
        "questions": _delivered_ids(ctx, attempt_id),
        "answers": [dict(item) for item in answers],
        "result": dict(result) if result else None,
        "outcome": dict(outcome) if outcome else None,
    }


def test_creating_a_retake_changes_nothing_about_the_attempt_it_follows(make_ctx: Any) -> None:
    """UC-08's central promise, checked against real rows rather than a port that has no writes.

    The port having no write method proves UC-08 *cannot* have written. This proves nothing else
    in the chain did either — that creating a second attempt does not disturb the first through
    UC-03's own numbering, UC-04's scoring or UC-05's remaining-attempts arithmetic.
    """
    ctx = _configured(make_ctx)
    first_id, _ = _sit_and_fail(ctx)
    before = _attempt_fingerprint(ctx, first_id)
    assert before["result"] is not None, "the chain must have scored the first attempt"
    assert before["outcome"] is not None, "the chain must have decided pass/fail"

    created = _create_retake(ctx)
    assert created.status_code == 201, created.text

    after = _attempt_fingerprint(ctx, first_id)
    assert after == before


# ---------------------------------------------------------------------------
# 4 & 5. The real allowance, and what a grant does not change
# ---------------------------------------------------------------------------


def test_eligibility_is_computed_from_the_real_attempt_count_and_locked_maximum(
    make_ctx: Any,
) -> None:
    ctx = _configured(make_ctx)
    _sit_and_fail(ctx)

    eligibility = _eligibility(ctx)
    assert eligibility["state"] == "ELIGIBLE"
    assert eligibility["can_retake"] is True
    assert eligibility["allowance"]["maximum_attempts"] == 2
    assert eligibility["allowance"]["attempts_used"] == 1
    assert eligibility["allowance"]["available_attempts"] == 1
    assert eligibility["next_attempt_number"] == 2


def test_a_spent_allowance_refuses_the_retake_and_offers_contact_guidance(
    make_ctx: Any,
) -> None:
    ctx = _configured(make_ctx)
    _sit_and_fail(ctx)
    created = _create_retake(ctx)
    assert created.status_code == 201, created.text
    # Sit and fail the retake too, spending the allowance.
    _fail_existing(ctx, created.json()["attempt"]["attempt_id"])

    eligibility = _eligibility(ctx)
    assert eligibility["state"] == "EXHAUSTED"
    assert eligibility["can_retake"] is False
    assert "administrator" in (eligibility["guidance"] or "")

    refused = _create_retake(ctx)
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    # Deliberately UC-03's code, not a UC-08-only one: the two mean the same thing, so a client
    # that already handles "you have no attempts left" from a fresh start handles it from a
    # retake without a second branch. See app/modules/retakes/domain/errors.py.
    assert error["code"] == "MAX_ATTEMPTS_REACHED"
    assert error["context"]["available_attempts"] == 0
    assert "administrator" in error["context"]["guidance"]


def test_an_administrator_grant_raises_one_learner_without_changing_the_quiz(
    make_ctx: Any,
) -> None:
    """§11: the course-wide maximum is read back from UC-01's own table and must be unchanged."""
    ctx = _configured(make_ctx)
    _sit_and_fail(ctx)
    retake = _create_retake(ctx)
    assert retake.status_code == 201, retake.text
    _fail_existing(ctx, retake.json()["attempt"]["attempt_id"])
    assert _eligibility(ctx)["state"] == "EXHAUSTED"

    version_id = ctx.active_version_id()
    maximum_before = ctx.scalar(
        "SELECT max_attempts FROM qc_configuration_versions WHERE id = :id", id=version_id
    )

    granted = ctx.client.post(
        "/api/admin/retakes/grants",
        json={
            "learner_id": str(ctx.learner_id),
            "course_id": str(ctx.course_id),
            "quiz_id": str(ctx.quiz_id),
            "additional_attempts": 1,
            "reason": "Approved by the course lead.",
        },
        headers={**auth(ADMIN_TOKEN), "Idempotency-Key": "ticket-4471"},
    )
    assert granted.status_code == 201, granted.text

    maximum_after = ctx.scalar(
        "SELECT max_attempts FROM qc_configuration_versions WHERE id = :id", id=version_id
    )
    assert maximum_after == maximum_before, "a grant must not change the quiz configuration"
    # And no new version was published behind the administrator's back.
    assert ctx.version_count() == 1

    after = _eligibility(ctx)
    assert after["state"] == "ADDITIONAL_ATTEMPT_AVAILABLE"
    assert after["allowance"]["granted_attempts"] == 1
    assert after["allowance"]["available_attempts"] == 1

    allowed = _create_retake(ctx)
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["attempt"]["attempt_number"] == 3


def test_a_grant_applies_to_one_learner_only(make_ctx: Any) -> None:
    """A second learner's entitlement is unaffected by the first learner's grant."""
    from tests.harness import LEARNER2_TOKEN

    ctx = _configured(make_ctx)
    _sit_and_fail(ctx)
    _sit_and_fail(ctx, token=LEARNER2_TOKEN)

    granted = ctx.client.post(
        "/api/admin/retakes/grants",
        json={
            "learner_id": str(ctx.learner_id),
            "course_id": str(ctx.course_id),
            "quiz_id": str(ctx.quiz_id),
            "additional_attempts": 2,
        },
        headers={**auth(ADMIN_TOKEN), "Idempotency-Key": "ticket-9001"},
    )
    assert granted.status_code == 201, granted.text

    assert _eligibility(ctx)["allowance"]["granted_attempts"] == 2
    assert _eligibility(ctx, token=LEARNER2_TOKEN)["allowance"]["granted_attempts"] == 0


# ---------------------------------------------------------------------------
# 6. Idempotency and the reservation, enforced by the database
# ---------------------------------------------------------------------------


def test_a_repeated_retake_request_produces_one_row_and_one_attempt(make_ctx: Any) -> None:
    """The key is derived, so a client retry after a timeout is a read rather than a second attempt."""
    ctx = _configured(make_ctx)
    _sit_and_fail(ctx)

    first = _create_retake(ctx)
    second = _create_retake(ctx)

    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    assert second.json()["replayed"] is True
    assert first.json()["attempt"]["attempt_id"] == second.json()["attempt"]["attempt_id"]

    assert int(ctx.scalar("SELECT COUNT(*) FROM qt_retake_requests") or 0) == 1
    assert ctx.attempt_count() == 2


def test_the_retake_record_is_persisted_with_its_lineage(make_ctx: Any) -> None:
    """The ``qt_`` row is real, and carries what the in-memory repository used to hold."""
    ctx = _configured(make_ctx)
    first_id, _ = _sit_and_fail(ctx)
    created = _create_retake(ctx)
    assert created.status_code == 201, created.text
    retake = created.json()["retake"]

    with ctx.session() as session:
        row = session.execute(
            __import__("sqlalchemy").text(
                "SELECT status, previous_attempt_id, attempt_id, attempt_number, "
                "configuration_version_id, configuration_version_source, idempotency_key "
                "FROM qt_retake_requests WHERE id = :id"
            ),
            {"id": retake["retake_id"]},
        ).mappings().one()

    assert row["status"] == "COMPLETED"
    assert row["previous_attempt_id"] == first_id
    assert row["attempt_id"] == created.json()["attempt"]["attempt_id"]
    assert row["attempt_number"] == 2
    assert row["configuration_version_source"] == "CARRIED_FORWARD"
    assert first_id in row["idempotency_key"]


def test_the_attempt_slot_index_refuses_a_second_holder(make_ctx: Any) -> None:
    """The reservation is a partial unique index, not a check in a service.

    Asserted by attempting the forbidden insert directly, which is the only way to show the
    guarantee survives a code path nobody wrote.
    """
    ctx = _configured(make_ctx)
    _sit_and_fail(ctx)
    created = _create_retake(ctx)
    assert created.status_code == 201, created.text

    with pytest.raises(Exception) as caught:
        ctx.execute(
            "INSERT INTO qt_retake_requests ("
            "  id, idempotency_key, learner_id, course_id, quiz_id, previous_attempt_id,"
            "  attempt_number, configuration_version_id, configuration_version_source, status,"
            "  requested_at, updated_at, attempt_count"
            ") VALUES ("
            "  'forged', 'forged-key', :learner, :course, :quiz, 'whatever',"
            "  2, '1', 'CARRIED_FORWARD', 'RESERVED',"
            "  '2026-01-01 00:00:00', '2026-01-01 00:00:00', 1"
            ")",
            learner=str(ctx.learner_id),
            course=str(ctx.course_id),
            quiz=str(ctx.quiz_id),
        )
    assert "unique" in str(caught.value).lower()


# ---------------------------------------------------------------------------
# Attempt history, assembled from every capability at once
# ---------------------------------------------------------------------------


def test_attempt_history_reads_the_real_score_outcome_and_feedback(make_ctx: Any) -> None:
    """Nothing in the history is recomputed; each figure is the owning capability's own."""
    ctx = _configured(make_ctx)
    first_id, _ = _sit_and_fail(ctx)
    _create_retake(ctx)

    response = ctx.client.get(
        f"/api/v1/quizzes/{ctx.quiz_id}/attempt-history", headers=auth(LEARNER_TOKEN)
    )
    assert response.status_code == 200, response.text
    history = response.json()

    assert history["attempt_count"] == 2
    entries = history["entries"]
    assert [entry["attempt_number"] for entry in entries] == [1, 2]

    first = entries[0]
    assert first["attempt_id"] == first_id
    assert first["status"] == "SUBMITTED"
    assert first["score_available"] is True
    assert first["pass_fail_available"] is True
    assert first["pass_fail_status"] == "FAILED"

    # The figures must equal UC-04's and UC-05's own rows, not a recomputation.
    with ctx.session() as session:
        sa = __import__("sqlalchemy")
        result = session.execute(
            sa.text(
                "SELECT total_marks, maximum_marks, percentage FROM qr_attempt_results "
                "WHERE attempt_id = :id"
            ),
            {"id": first_id},
        ).mappings().one()
    assert first["total_marks"] == pytest.approx(result["total_marks"])
    assert first["maximum_marks"] == pytest.approx(result["maximum_marks"])
    assert first["percentage"] == pytest.approx(result["percentage"])

    # The retake, still in progress, is present with its gaps labelled rather than filled in.
    second = entries[1]
    assert second["status"] == "ACTIVE"
    assert second["score_available"] is False
    assert second["total_marks"] is None
    assert second["pass_fail_status"] is None
    assert second["is_retake"] is True
    assert second["retake_of_attempt_id"] == first_id


def test_history_is_scoped_to_the_requesting_learner(make_ctx: Any) -> None:
    from tests.harness import LEARNER2_TOKEN

    ctx = _configured(make_ctx)
    _sit_and_fail(ctx)
    _sit_and_fail(ctx, token=LEARNER2_TOKEN)

    mine = ctx.client.get(
        f"/api/v1/quizzes/{ctx.quiz_id}/attempt-history", headers=auth(LEARNER_TOKEN)
    ).json()
    theirs = ctx.client.get(
        f"/api/v1/quizzes/{ctx.quiz_id}/attempt-history", headers=auth(LEARNER2_TOKEN)
    ).json()

    assert mine["attempt_count"] == 1
    assert theirs["attempt_count"] == 1
    mine_ids = {entry["attempt_id"] for entry in mine["entries"]}
    theirs_ids = {entry["attempt_id"] for entry in theirs["entries"]}
    assert mine_ids.isdisjoint(theirs_ids)


def test_an_unauthenticated_retake_request_is_refused(make_ctx: Any) -> None:
    ctx = _configured(make_ctx)
    _sit_and_fail(ctx)
    response = ctx.client.post(f"/api/v1/quizzes/{ctx.quiz_id}/retakes", json={})
    assert response.status_code == 401
    assert "error" in response.json()
