"""UC-11 §11 — question-type regression across all five types.

Each of the five types is taken through **delivery, answer persistence, scoring, feedback and a
retake** in one flow, because that is the chain a type can break anywhere along. UC-02 tests that a
type is authored and validated correctly; UC-03 that it is delivered; UC-04 that it is scored. None
of them can tell you that a ``DRAG_TO_ORDER`` question survives all five stages with the same
meaning at each one.

Malformed and unanswered cases are included, because those are the two shapes a type-specific
handler is most likely to get wrong and least likely to be exercised by a happy-path flow.

No arithmetic here: every expected mark is read from the delivered snapshot or from UC-04's own row.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from tests.global_dod.conftest import (
    LEARNER_TOKEN,
    V1,
    answer_payload,
    auth,
    sit,
)

#: The five types the system supports. Named here so a sixth type added to the kernel makes this
#: file fail rather than silently going untested.
ALL_TYPES = ("SINGLE_CHOICE", "TRUE_FALSE", "MULTI_SELECT", "SCENARIO", "DRAG_TO_ORDER")


def _delivered_by_type(questions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {question["questionType"]: question for question in questions}


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def test_every_type_is_delivered_with_the_structure_it_needs(system: Any) -> None:
    """A type whose payload lacks its own structure cannot be answered at all."""
    ctx = system()
    _, questions = sit(ctx, submit=False)
    by_type = _delivered_by_type(questions)

    assert set(by_type) == set(ALL_TYPES), (
        "the configured paper must contain one of every type; got " + str(sorted(by_type))
    )

    assert by_type["SINGLE_CHOICE"]["options"]
    assert by_type["TRUE_FALSE"]["questionType"] == "TRUE_FALSE"
    assert len(by_type["MULTI_SELECT"]["options"]) >= 2
    assert by_type["DRAG_TO_ORDER"]["orderItems"]
    scenario = by_type["SCENARIO"]
    assert scenario["scenarioText"]
    assert scenario["subQuestions"], "a scenario with no sub-question cannot be answered"


def test_no_delivered_question_of_any_type_carries_the_answer_key(system: Any) -> None:
    """§16's guarantee, checked at the *delivery* boundary for every type.

    The strongest place to assert it: if the key never reaches the learner's payload, no amount of
    client-side inspection finds it, and every downstream consumer is reading a paper that never
    had it.
    """
    ctx = system()
    _, questions = sit(ctx, submit=False)

    rendered = str(questions)
    for forbidden in (
        "isCorrect",
        "is_correct",
        "correctPosition",
        "correct_position",
        "isPrimary",
        "is_primary",
        "answerKey",
        "answer_key",
    ):
        assert forbidden not in rendered, forbidden


# ---------------------------------------------------------------------------
# Answer persistence and scoring
# ---------------------------------------------------------------------------


def test_a_correct_answer_of_every_type_earns_the_questions_full_marks(system: Any) -> None:
    """The marks compared are the ones the *delivered snapshot* froze, not a number in this file."""
    ctx = system()
    attempt_id, _ = sit(ctx, correctly=True)

    with ctx.session() as session:
        rows = list(
            session.execute(
                text(
                    "SELECT s.question_type, s.awarded_marks, s.maximum_marks, s.outcome "
                    "FROM qr_question_scores s "
                    "JOIN qr_attempt_results r ON r.id = s.result_id "
                    "WHERE r.attempt_id = :id"
                ),
                {"id": attempt_id},
            ).mappings()
        )

    assert {row["question_type"] for row in rows} == set(ALL_TYPES)
    for row in rows:
        assert row["outcome"] == "CORRECT", row["question_type"]
        assert row["awarded_marks"] == pytest.approx(row["maximum_marks"]), row["question_type"]


def test_an_incorrect_answer_of_every_type_earns_nothing_and_never_less(system: Any) -> None:
    """§13's floor applies to every type, not only to the penalty-scored multi-select."""
    ctx = system()
    attempt_id, _ = sit(ctx, correctly=False)

    with ctx.session() as session:
        rows = list(
            session.execute(
                text(
                    "SELECT s.question_type, s.awarded_marks, s.raw_marks, s.outcome "
                    "FROM qr_question_scores s "
                    "JOIN qr_attempt_results r ON r.id = s.result_id "
                    "WHERE r.attempt_id = :id"
                ),
                {"id": attempt_id},
            ).mappings()
        )
        total = session.execute(
            text("SELECT total_marks, percentage FROM qr_attempt_results WHERE attempt_id = :id"),
            {"id": attempt_id},
        ).mappings().one()

    assert {row["question_type"] for row in rows} == set(ALL_TYPES)
    for row in rows:
        assert row["awarded_marks"] >= 0.0, row["question_type"]
        assert row["outcome"] in {"INCORRECT", "PARTIALLY_CORRECT"}, row["question_type"]

    # And the total cannot be negative however the per-question raw marks fell.
    assert total["total_marks"] >= 0.0
    assert total["percentage"] >= 0.0


def test_an_unanswered_question_of_every_type_still_appears_in_the_result(system: Any) -> None:
    """A skipped question must be scored as zero, not omitted — or the maximum drifts.

    Omitting it would silently reduce the denominator, so a learner who skipped half the paper
    would score the same percentage as one who answered it all correctly.
    """
    ctx = system(
        {
            # Incomplete submission has to be permitted for a learner to skip anything.
            "questionCount": 5,
            "timeLimitMinutes": 30,
            "passMark": 60,
            "questionTypes": [
                {"type": "SINGLE_CHOICE", "quota": 1},
                {"type": "TRUE_FALSE", "quota": 1},
                {"type": "MULTI_SELECT", "quota": 1},
                {"type": "SCENARIO", "quota": 1},
                {"type": "DRAG_TO_ORDER", "quota": 1},
            ],
            "randomiseQuestions": False,
            "maxAttempts": 3,
            "deliveryMode": "assessment",
            "allowIncompleteSubmission": True,
        }
    )

    attempt_id, questions = ctx.start_and_read_questions(LEARNER_TOKEN)
    # Answer exactly one question and submit, leaving four of the five untouched.
    first = questions[0]
    ctx.client.put(
        f"{V1}/attempts/{attempt_id}/questions/{first['questionId']}/answer",
        json={"response": answer_payload(ctx, first, correctly=True), "source": "MANUAL"},
        headers=auth(LEARNER_TOKEN),
    )
    submitted = ctx.client.post(
        f"{V1}/attempts/{attempt_id}/submission",
        json={"confirmed": True},
        headers=auth(LEARNER_TOKEN),
    )
    assert submitted.status_code == 200, submitted.text

    with ctx.session() as session:
        rows = list(
            session.execute(
                text(
                    "SELECT s.question_type, s.answered, s.awarded_marks, s.maximum_marks "
                    "FROM qr_question_scores s "
                    "JOIN qr_attempt_results r ON r.id = s.result_id "
                    "WHERE r.attempt_id = :id"
                ),
                {"id": attempt_id},
            ).mappings()
        )
        result = session.execute(
            text(
                "SELECT unanswered_count, total_questions, maximum_marks "
                "FROM qr_attempt_results WHERE attempt_id = :id"
            ),
            {"id": attempt_id},
        ).mappings().one()

    assert len(rows) == 5, "every delivered question must be scored, answered or not"
    assert result["total_questions"] == 5
    assert result["unanswered_count"] == 4
    # The maximum counts every question, so the denominator is the whole paper.
    assert result["maximum_marks"] == pytest.approx(sum(row["maximum_marks"] for row in rows))
    for row in rows:
        if not row["answered"]:
            assert row["awarded_marks"] == pytest.approx(0.0), row["question_type"]


@pytest.mark.parametrize(
    ("question_type", "malformed"),
    [
        ("SINGLE_CHOICE", {"selectedOptionId": "no-such-option"}),
        ("TRUE_FALSE", {"value": "yes"}),
        ("MULTI_SELECT", {"selectedOptionIds": "not-a-list"}),
        ("DRAG_TO_ORDER", {"orderedItemIds": ["only-one"]}),
        ("SCENARIO", {"responses": [{"subQuestionId": "nope", "answer": {"value": True}}]}),
    ],
)
def test_a_malformed_answer_of_every_type_is_refused_at_the_delivery_boundary(
    system: Any, question_type: str, malformed: dict[str, Any]
) -> None:
    """Refused where the shape is known, so nothing downstream has to defend against it.

    The scorer, the feedback report and the coach all read stored answers. If a malformed one could
    be stored, each of them would need its own defence — and one of them would eventually not have
    it.
    """
    ctx = system()
    attempt_id, questions = ctx.start_and_read_questions(LEARNER_TOKEN)
    question = _delivered_by_type(questions)[question_type]

    response = ctx.client.put(
        f"{V1}/attempts/{attempt_id}/questions/{question['questionId']}/answer",
        json={"response": malformed, "source": "MANUAL"},
        headers=auth(LEARNER_TOKEN),
    )

    assert response.status_code in (400, 422), (question_type, response.text)

    # And nothing was stored: a rejected answer must leave the previous state exactly as it was.
    stored = ctx.scalar(
        "SELECT COUNT(*) FROM qd_attempt_answers WHERE attempt_id = :a AND question_id = :q",
        a=attempt_id,
        q=question["questionId"],
    )
    assert int(stored or 0) == 0


# ---------------------------------------------------------------------------
# Feedback and retake
# ---------------------------------------------------------------------------


def test_every_type_appears_in_the_feedback_report_with_both_answers(system: Any) -> None:
    """§15: the learner's answer and the correct answer, for every type.

    This is the one place the answer key is *supposed* to be visible — after submission, on the
    learner's own report — so it is also the place to check that it arrives for every type rather
    than only the simple ones.
    """
    ctx = system()
    attempt_id, _ = sit(ctx, correctly=False)

    response = ctx.client.get(
        f"{V1}/attempts/{attempt_id}/feedback", headers=auth(LEARNER_TOKEN)
    )
    assert response.status_code == 200, response.text
    items = response.json()["report"]["items"]

    assert {item["questionType"] for item in items} == set(ALL_TYPES)
    for item in items:
        # Both answers are present for every type. ``correctAnswer`` is the answer *key*, and this
        # is the one place it is meant to be visible — after submission, on the learner's own
        # report — so checking every type arrives here is the counterpart to checking none of them
        # carried it at delivery.
        assert item["learnerAnswer"], item["questionType"]
        assert item["correctAnswer"], item["questionType"]
        assert item["explanation"] is not None, item["questionType"]
        assert item["lessonReference"] is not None, item["questionType"]
        assert item["outcome"] in {"INCORRECT", "PARTIALLY_CORRECT"}, item["questionType"]


def test_a_retake_redelivers_every_type_and_scores_it_again(system: Any) -> None:
    """§17 crossed with §11: a retake is a new paper, and every type still works on it."""
    ctx = system()
    first_id, _ = sit(ctx, correctly=False)

    created = ctx.client.post(
        f"{V1}/quizzes/{ctx.quiz_id}/retakes", json={}, headers=auth(LEARNER_TOKEN)
    )
    assert created.status_code == 201, created.text
    retake_attempt_id = created.json()["attempt"]["attempt_id"]

    read = ctx.attempt_questions(retake_attempt_id, LEARNER_TOKEN)
    assert read.status_code == 200, read.text
    questions = read.json()["questions"]
    assert {question["questionType"] for question in questions} == set(ALL_TYPES)

    for question in questions:
        saved = ctx.client.put(
            f"{V1}/attempts/{retake_attempt_id}/questions/{question['questionId']}/answer",
            json={
                "response": answer_payload(ctx, question, correctly=True),
                "source": "MANUAL",
            },
            headers=auth(LEARNER_TOKEN),
        )
        assert saved.status_code == 200, saved.text

    submitted = ctx.client.post(
        f"{V1}/attempts/{retake_attempt_id}/submission",
        json={"confirmed": True},
        headers=auth(LEARNER_TOKEN),
    )
    assert submitted.status_code == 200, submitted.text

    assert ctx.scalar(
        "SELECT percentage FROM qr_attempt_results WHERE attempt_id = :id", id=retake_attempt_id
    ) == pytest.approx(100.0)
    # The first attempt's own result is untouched by any of it.
    assert ctx.scalar(
        "SELECT percentage FROM qr_attempt_results WHERE attempt_id = :id", id=first_id
    ) == pytest.approx(0.0)
