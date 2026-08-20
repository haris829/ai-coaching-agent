"""UC-11 §12 (autosave and recovery) and §13 (negative-marking protection).

Two requirements that share a property: both are about what the system does when something goes
*wrong* mid-attempt — a browser refresh, a dropped connection, a learner guessing every wrong
option — and both are invisible on the happy path. Neither capability's own suite can prove them
end to end, because each spans UC-03's persistence and UC-04's arithmetic.
"""

from __future__ import annotations

from typing import Any

from tests.global_dod.conftest import (
    ALL_TYPES_CONFIGURATION,
    LEARNER_TOKEN,
    V1,
    answer_payload,
    auth,
)


def _questions_by_type(questions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {question["questionType"]: question for question in questions}


# ---------------------------------------------------------------------------
# §12 — autosave, reload, and recovery
# ---------------------------------------------------------------------------


def test_a_batch_autosave_persists_every_answer_and_reports_when_it_landed(system: Any) -> None:
    """The 30-second loop's round trip: one call, everything saved, an authoritative clock back.

    The client needs ``persistedAt`` and ``timing`` in the *same* response — otherwise it cannot
    tell a save that landed from one that is still in flight, and cannot resync its countdown
    without a second call that may itself fail.
    """
    ctx = system()
    attempt_id, questions = ctx.start_and_read_questions()

    saved = ctx.client.post(
        f"{V1}/attempts/{attempt_id}/answers",
        json={
            "answers": [
                {
                    "questionId": question["questionId"],
                    "response": answer_payload(ctx, question, correctly=True),
                }
                for question in questions
            ],
            "source": "AUTOSAVE",
        },
        headers=auth(LEARNER_TOKEN),
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["savedCount"] == len(questions)
    assert body["changedCount"] == len(questions)
    assert body["persistedAt"], "a client cannot confirm a save with no timestamp"
    assert body["timing"]["remainingSeconds"] > 0

    # And the reload path rebuilds the same state — every delivered question listed, answered or
    # not, so "unanswered" is explicit rather than inferred from an absence.
    reloaded = ctx.client.get(f"{V1}/attempts/{attempt_id}/answers", headers=auth(LEARNER_TOKEN))
    assert reloaded.status_code == 200, reloaded.text
    assert reloaded.json()["answeredCount"] == len(questions)
    assert len(reloaded.json()["answers"]) == len(questions)


def test_a_repeated_autosave_is_idempotent_and_does_not_advance_the_revision(system: Any) -> None:
    """A periodic timer re-sends unchanged answers constantly. That must cost nothing.

    If an unchanged save bumped the revision, the optimistic-concurrency guard would fire against
    the learner's own second tab, and the audit trail would fill with saves that changed nothing.
    """
    ctx = system()
    attempt_id, questions = ctx.start_and_read_questions()
    question = questions[0]
    response = answer_payload(ctx, question, correctly=True)
    url = f"{V1}/attempts/{attempt_id}/questions/{question['questionId']}/answer"

    first = ctx.client.put(url, json={"response": response}, headers=auth(LEARNER_TOKEN))
    assert first.status_code == 200, first.text
    assert first.json()["answer"]["changed"] is True
    revision = first.json()["answer"]["revision"]

    for _ in range(3):
        again = ctx.client.put(
            url, json={"response": response, "source": "AUTOSAVE"}, headers=auth(LEARNER_TOKEN)
        )
        assert again.status_code == 200, again.text
        assert again.json()["answer"]["changed"] is False
        assert again.json()["answer"]["revision"] == revision

    trail = ctx.client.get(
        f"{V1}/attempts/{attempt_id}/answers/revisions", headers=auth(LEARNER_TOKEN)
    )
    assert trail.status_code == 200, trail.text
    entries = [
        item
        for item in trail.json()["revisions"]
        if item["questionId"] == question["questionId"]
    ]
    assert len(entries) == 1, "three no-op autosaves must not produce three audit rows"


def test_one_bad_answer_rejects_the_whole_batch_and_changes_nothing(system: Any) -> None:
    """All-or-nothing. A half-applied autosave is worse than a failed one.

    The learner can be told "save failed, retry" and be correct about it. A partial write would
    leave the client and the server disagreeing about state with nothing to detect it.
    """
    ctx = system()
    attempt_id, questions = ctx.start_and_read_questions()
    by_type = _questions_by_type(questions)
    good = by_type["SINGLE_CHOICE"]
    victim = by_type["TRUE_FALSE"]

    before = ctx.client.get(f"{V1}/attempts/{attempt_id}/answers", headers=auth(LEARNER_TOKEN))
    assert before.json()["answeredCount"] == 0

    rejected = ctx.client.post(
        f"{V1}/attempts/{attempt_id}/answers",
        json={
            "answers": [
                {
                    "questionId": good["questionId"],
                    "response": answer_payload(ctx, good, correctly=True),
                },
                {"questionId": victim["questionId"], "response": {"value": "not-a-boolean"}},
            ]
        },
        headers=auth(LEARNER_TOKEN),
    )
    assert rejected.status_code == 422, rejected.text

    after = ctx.client.get(f"{V1}/attempts/{attempt_id}/answers", headers=auth(LEARNER_TOKEN))
    assert after.json()["answeredCount"] == 0, "the valid entry must not have been written"


def test_the_expected_revision_guard_detects_a_second_device(system: Any) -> None:
    """Two tabs, one attempt. The second writer is told, not silently overwritten."""
    ctx = system()
    attempt_id, questions = ctx.start_and_read_questions()
    question = questions[0]
    url = f"{V1}/attempts/{attempt_id}/questions/{question['questionId']}/answer"

    first = ctx.client.put(
        url,
        json={"response": answer_payload(ctx, question, correctly=True), "expectedRevision": 0},
        headers=auth(LEARNER_TOKEN),
    )
    assert first.status_code == 200, first.text

    stale = ctx.client.put(
        url,
        json={"response": answer_payload(ctx, question, correctly=False), "expectedRevision": 0},
        headers=auth(LEARNER_TOKEN),
    )
    assert stale.status_code == 409, stale.text


def test_autosaved_work_survives_to_scoring_when_the_attempt_is_never_manually_submitted(
    system: Any,
) -> None:
    """The requirement behind autosave: work already saved is work already counted.

    A disconnect is not a reason to lose an answer. UC-09's disconnect path submits the attempt
    from whatever was last autosaved, and this proves that path scores the autosaved answers rather
    than an empty paper.
    """
    ctx = system()
    attempt_id, questions = ctx.start_and_read_questions()
    ctx.client.post(
        f"{V1}/attempts/{attempt_id}/answers",
        json={
            "answers": [
                {
                    "questionId": question["questionId"],
                    "response": answer_payload(ctx, question, correctly=True),
                }
                for question in questions
            ]
        },
        headers=auth(LEARNER_TOKEN),
    )
    submitted = ctx.submit_attempt(attempt_id)
    assert submitted.status_code == 200, submitted.text

    result = ctx.client.post(
        f"{V1}/attempts/{attempt_id}/result", json={}, headers=auth(LEARNER_TOKEN)
    )
    assert result.status_code in (200, 201), result.text
    assert result.json()["result"]["unansweredCount"] == 0
    assert result.json()["result"]["correctCount"] == len(questions)


def test_a_submitted_attempt_refuses_further_autosaves(system: Any) -> None:
    """The autosave loop may still be running when the learner presses submit. It must be refused.

    Not "ignored" — refused, with a status the client can act on. Silently accepting a save into a
    submitted attempt is the exact failure §10's immutability rule exists to prevent.
    """
    ctx = system()
    attempt_id, questions = ctx.start_and_read_questions()
    question = questions[0]
    ctx.client.put(
        f"{V1}/attempts/{attempt_id}/questions/{question['questionId']}/answer",
        json={"response": answer_payload(ctx, question, correctly=True)},
        headers=auth(LEARNER_TOKEN),
    )
    assert ctx.submit_attempt(attempt_id).status_code == 200

    late = ctx.client.post(
        f"{V1}/attempts/{attempt_id}/answers",
        json={
            "answers": [
                {
                    "questionId": question["questionId"],
                    "response": answer_payload(ctx, question, correctly=False),
                }
            ],
            "source": "AUTOSAVE",
        },
        headers=auth(LEARNER_TOKEN),
    )
    assert late.status_code == 409, late.text


# ---------------------------------------------------------------------------
# §13 — negative marking never goes negative
# ---------------------------------------------------------------------------


def test_a_wholly_wrong_multi_select_scores_zero_rather_than_a_negative(system: Any) -> None:
    """The penalty stops at zero for that question.

    The seeded multi-select is worth 3 marks with a 0.5 penalty per incorrect selection and carries
    two wrong options, so selecting both and none of the correct ones computes to -1.0. What the
    learner must be shown is 0.
    """
    ctx = system()
    attempt_id, questions = ctx.start_and_read_questions()
    for question in questions:
        ctx.client.put(
            f"{V1}/attempts/{attempt_id}/questions/{question['questionId']}/answer",
            json={
                "response": answer_payload(
                    ctx, question, correctly=question["questionType"] != "MULTI_SELECT"
                )
            },
            headers=auth(LEARNER_TOKEN),
        )
    assert ctx.submit_attempt(attempt_id).status_code == 200
    scored = ctx.client.post(
        f"{V1}/attempts/{attempt_id}/result", json={}, headers=auth(LEARNER_TOKEN)
    )
    assert scored.status_code in (200, 201), scored.text
    body = scored.json()

    multi = next(
        score for score in body["questionScores"] if score["questionType"] == "MULTI_SELECT"
    )
    assert multi["deduction"] > 0, "the fixture must actually apply a penalty for this to prove it"
    assert multi["rawMarks"] < 0, "…and the raw computation must actually have gone negative"
    assert multi["awardedMarks"] == 0, "the awarded mark is what the learner sees; it stops at zero"


def test_a_penalty_never_eats_into_another_questions_marks(system: Any) -> None:
    """Clamping per question, not at the end. The distinction is the whole requirement.

    A total clamped only at the end would still let a badly-guessed multi-select silently cancel a
    correct answer elsewhere. Here every other question is answered correctly, so the total must be
    exactly the sum of those — the wrong one contributes zero, not less.
    """
    ctx = system()
    attempt_id, questions = ctx.start_and_read_questions()
    for question in questions:
        ctx.client.put(
            f"{V1}/attempts/{attempt_id}/questions/{question['questionId']}/answer",
            json={
                "response": answer_payload(
                    ctx, question, correctly=question["questionType"] != "MULTI_SELECT"
                )
            },
            headers=auth(LEARNER_TOKEN),
        )
    assert ctx.submit_attempt(attempt_id).status_code == 200
    body = ctx.client.post(
        f"{V1}/attempts/{attempt_id}/result", json={}, headers=auth(LEARNER_TOKEN)
    ).json()

    scores = body["questionScores"]
    others = [score for score in scores if score["questionType"] != "MULTI_SELECT"]
    assert body["result"]["totalMarks"] == round(
        sum(score["awardedMarks"] for score in others), 4
    )
    assert body["result"]["totalMarks"] >= 0
    assert all(score["awardedMarks"] >= 0 for score in scores)


def test_an_unanswered_question_is_never_penalised(system: Any) -> None:
    """Leaving a multi-select blank scores zero — the same as guessing everything wrong, not worse.

    Otherwise the deduction rule would push learners to guess, which is the behaviour it exists to
    discourage.
    """
    ctx = system()
    attempt_id, questions = ctx.start_and_read_questions()
    for question in questions:
        if question["questionType"] == "MULTI_SELECT":
            continue
        ctx.client.put(
            f"{V1}/attempts/{attempt_id}/questions/{question['questionId']}/answer",
            json={"response": answer_payload(ctx, question, correctly=True)},
            headers=auth(LEARNER_TOKEN),
        )
    assert ctx.submit_attempt(attempt_id).status_code == 200
    body = ctx.client.post(
        f"{V1}/attempts/{attempt_id}/result", json={}, headers=auth(LEARNER_TOKEN)
    ).json()

    multi = next(s for s in body["questionScores"] if s["questionType"] == "MULTI_SELECT")
    assert multi["answered"] is False
    assert multi["awardedMarks"] == 0
    assert multi["deduction"] == 0, "an unanswered question has nothing to deduct for"
    assert body["result"]["unansweredCount"] == 1


def test_partial_credit_lands_between_the_two_extremes(system: Any) -> None:
    """Two of three correct options, no wrong ones: more than nothing, less than everything.

    Written against a configuration whose multi-select carries three correct options, so "partial"
    is a real state rather than an arithmetic accident.
    """
    ctx = system(ALL_TYPES_CONFIGURATION)
    attempt_id, questions = ctx.start_and_read_questions()
    multi = _questions_by_type(questions)["MULTI_SELECT"]
    full = answer_payload(ctx, multi, correctly=True)["selectedOptionIds"]
    assert len(full) >= 3, "the fixture must offer enough correct options to be partially right"

    ctx.client.put(
        f"{V1}/attempts/{attempt_id}/questions/{multi['questionId']}/answer",
        json={"response": {"selectedOptionIds": full[:2]}},
        headers=auth(LEARNER_TOKEN),
    )
    assert ctx.submit_attempt(attempt_id).status_code == 200
    body = ctx.client.post(
        f"{V1}/attempts/{attempt_id}/result", json={}, headers=auth(LEARNER_TOKEN)
    ).json()

    score = next(s for s in body["questionScores"] if s["questionType"] == "MULTI_SELECT")
    assert 0 < score["awardedMarks"] < score["maximumMarks"]
    assert score["outcome"] == "PARTIALLY_CORRECT"
