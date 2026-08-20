"""UC-11 §26 — the six end-to-end scenarios.

Every other suite in this repository, including the ones in ``tests/integration/``, verifies a
*seam*: two or three capabilities meeting. These six verify a **journey** — one learner, from a
quiz nobody has sat yet through to the certificate, the coaching queue and the administrator's
dashboard — and each one crosses every capability the system has.

That is the coverage no capability owns and no seam test can supply. A seam test proves UC-05 asks
UC-09's gate the right question; only a journey proves that a learner who fails, is granted an extra
attempt, retakes on a fresh paper and passes ends up with exactly one certificate, an untouched
first attempt, and a dashboard that reports both attempts.

WHAT IS DELIBERATELY *NOT* HERE
-------------------------------
Sections whose requirements are already proved end to end, against real rows, by an existing suite:

* §14 certificate gating — ``tests/integration/test_results_chain.py`` (standard) and
  ``test_formal_assessment_chain.py`` (withheld until an assessor approves).
* §15 feedback coverage — ``test_results_chain.py`` and ``tests/global_dod/test_question_types.py``,
  which walks all five types through feedback.
* §16 coaching security — ``test_coaching_chain.py``, including that no answer-key string reaches
  the provider and that no coaching table stores one.
* §18 formal-assessment security — ``test_formal_assessment_chain.py`` and
  ``tests/formal_assessment/test_security_bypass.py``.
* §19 analytics — ``test_analytics_chain.py``, field by field against UC-04's and UC-05's rows.

Restating those here would be the duplication UC-11 exists to detect, not coverage. What this file
adds is the part none of them can see: the same facts holding *along a whole journey*.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from tests.global_dod.conftest import (
    ASSESSMENT_TABLES,
    LEARNER_TOKEN,
    V1,
    answer_payload,
    delivered_question_ids,
    fingerprint,
    sit,
)
from tests.harness import ADMIN_TOKEN, ASSESSOR_TOKEN, Ctx, auth

ADMIN = "/api/admin/analytics"
SYSTEM = "/api/system/formal-assessments"

#: A three-question single-choice paper with a small allowance, drawn from a bank big enough that a
#: retake can be a genuinely fresh paper. The pass mark is 60, so two of three is a pass and none is
#: a fail — which makes "passed" and "failed" facts about the answers rather than about a fixture.
JOURNEY_CONFIGURATION: dict[str, Any] = {
    "questionCount": 3,
    "timeLimitMinutes": 30,
    "passMark": 60,
    "questionTypes": [{"type": "SINGLE_CHOICE"}],
    "randomiseQuestions": False,
    "maxAttempts": 2,
    "deliveryMode": "assessment",
}

#: The same paper, run as a formal assessment. Two questions so a single right answer is 50%.
FORMAL_CONFIGURATION: dict[str, Any] = {
    "questionCount": 2,
    "timeLimitMinutes": 30,
    "passMark": 50,
    "questionTypes": [{"type": "SINGLE_CHOICE"}],
    "randomiseQuestions": False,
    "maxAttempts": 2,
    "deliveryMode": "assessment",
    "isFormalAssessment": True,
    "requiresHumanReview": True,
    "requiresAssessorApproval": True,
}


# ---------------------------------------------------------------------------
# Walking a journey
# ---------------------------------------------------------------------------


def _journey(system: Any, **overrides: Any) -> Ctx:
    from tests import bank

    return system({**JOURNEY_CONFIGURATION, **overrides}, bank.DEFAULT_BANK)


def _score_and_gate(ctx: Ctx, attempt_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Drive UC-04 then UC-05 for one attempt and return ``(result, outcome view)``."""
    scored = ctx.client.post(
        f"{V1}/attempts/{attempt_id}/result", json={}, headers=auth(LEARNER_TOKEN)
    )
    assert scored.status_code in (200, 201), scored.text
    gated = ctx.client.post(
        f"{V1}/attempts/{attempt_id}/outcome", json={}, headers=auth(LEARNER_TOKEN)
    )
    assert gated.status_code in (200, 201), gated.text
    return scored.json(), gated.json()


def _feedback(ctx: Ctx, attempt_id: str) -> dict[str, Any]:
    generated = ctx.client.post(
        f"{V1}/attempts/{attempt_id}/feedback", json={}, headers=auth(LEARNER_TOKEN)
    )
    assert generated.status_code in (200, 201), generated.text
    return generated.json()


def _sit_existing(ctx: Ctx, attempt_id: str, *, correct: int) -> None:
    """Answer an attempt that already exists — the shape a retake arrives in.

    UC-08 creates the retake's attempt itself, so a retake is sat from the attempt it handed back
    rather than by asking UC-03 to start another one.
    """
    read = ctx.attempt_questions(attempt_id)
    assert read.status_code == 200, read.text
    for index, question in enumerate(read.json()["questions"]):
        saved = ctx.client.put(
            f"{V1}/attempts/{attempt_id}/questions/{question['questionId']}/answer",
            json={
                "response": answer_payload(ctx, question, correctly=index < correct),
                "source": "MANUAL",
            },
            headers=auth(LEARNER_TOKEN),
        )
        assert saved.status_code == 200, saved.text
    submitted = ctx.submit_attempt(attempt_id)
    assert submitted.status_code == 200, submitted.text


def _sit_fresh(ctx: Ctx, *, correct: int) -> str:
    attempt_id, questions = ctx.start_and_read_questions()
    for index, question in enumerate(questions):
        saved = ctx.client.put(
            f"{V1}/attempts/{attempt_id}/questions/{question['questionId']}/answer",
            json={
                "response": answer_payload(ctx, question, correctly=index < correct),
                "source": "MANUAL",
            },
            headers=auth(LEARNER_TOKEN),
        )
        assert saved.status_code == 200, saved.text
    assert ctx.submit_attempt(attempt_id).status_code == 200
    return attempt_id


def _retake(ctx: Ctx) -> Any:
    return ctx.client.post(
        f"{V1}/quizzes/{ctx.quiz_id}/retakes", json={}, headers=auth(LEARNER_TOKEN)
    )


def _eligibility(ctx: Ctx) -> dict[str, Any]:
    response = ctx.client.get(
        f"{V1}/quizzes/{ctx.quiz_id}/retake-eligibility", headers=auth(LEARNER_TOKEN)
    )
    assert response.status_code == 200, response.text
    return response.json()


def _certificates(ctx: Ctx) -> list[dict[str, Any]]:
    with ctx.session() as session:
        return [
            dict(row)
            for row in session.execute(
                text(
                    "SELECT attempt_id, status, certificate_number FROM qg_certificates "
                    "ORDER BY attempt_id"
                )
            ).mappings()
        ]


# ---------------------------------------------------------------------------
# Scenario A — a learner passes at the first attempt
# ---------------------------------------------------------------------------


def test_scenario_a_a_first_time_pass_produces_one_consistent_record_everywhere(
    system: Any,
) -> None:
    """Configure, sit, submit, score, gate, certify, explain, report — and agree at every step.

    The point is not that each capability answers; each capability's own suite proves that. It is
    that the *same numbers* appear in UC-04's result, UC-05's outcome, UC-06's report and UC-10's
    dashboard. Four independent renderings of one attempt is exactly where a disagreement hides.
    """
    ctx = _journey(system)
    attempt_id = _sit_fresh(ctx, correct=3)

    scored, gated = _score_and_gate(ctx, attempt_id)
    result = scored["result"]
    outcome = gated["outcome"]
    report = _feedback(ctx, attempt_id)

    assert result["status"] == "SCORED"
    assert result["percentage"] == 100.0
    assert outcome["outcome"] == "PASS"
    assert outcome["percentage"] == result["percentage"]
    assert outcome["passMarkPercentage"] == 60.0
    assert report["summary"]["percentage"] == result["percentage"]
    assert report["summary"]["passed"] is True
    assert len(report["items"]) == result["totalQuestions"] == 3

    # UC-05 issued the certificate and logged the CPD activity, because the outcome was a pass.
    assert gated["certificate"]["status"] == "ISSUED"
    assert gated["certificate"]["certificateNumber"]
    assert gated["cpd"]["status"] == "SYNCHRONISED"
    assert gated["cpd"]["scorePercentage"] == result["percentage"]

    # The allowance is now one of two used, so a retake is still possible but not needed.
    assert gated["attemptsUsed"] == 1
    assert gated["attemptsRemaining"] == 1

    # And UC-10 reports the same attempt to the administrator, without being told about it.
    dashboard = ctx.client.get(f"{ADMIN}/overall", headers=auth(ADMIN_TOKEN))
    assert dashboard.status_code == 200, dashboard.text
    figures = dashboard.json()
    assert figures["attempt_volume"] == 1
    assert figures["scored_attempts"] == 1
    assert figures["passed_attempts"] == 1
    assert figures["average_score"] == pytest.approx(result["percentage"], abs=0.01)

    # UC-07 has nothing to coach: there is no wrong answer to review.
    review = ctx.client.get(
        f"{V1}/attempts/{attempt_id}/coaching/review", headers=auth(LEARNER_TOKEN)
    )
    assert review.status_code in (200, 409), review.text
    if review.status_code == 200:
        assert review.json().get("queue", review.json()).get("items", []) == []


# ---------------------------------------------------------------------------
# Scenario B — fail, retake, pass
# ---------------------------------------------------------------------------


def test_scenario_b_a_failed_attempt_is_retaken_on_a_fresh_paper_and_left_untouched(
    system: Any,
) -> None:
    """The retake journey, with the first attempt byte-compared across the whole of it.

    A learner who fails and then passes must end up with one certificate, two visible attempts, and
    a first attempt whose every stored row is exactly as it was — including its score and its
    outcome, which UC-05 has every technical opportunity to overwrite when the second pass arrives.
    """
    ctx = _journey(system)
    first_attempt = _sit_fresh(ctx, correct=0)
    _score_and_gate(ctx, first_attempt)
    _feedback(ctx, first_attempt)

    first_paper = delivered_question_ids(ctx, first_attempt)
    before = fingerprint(ctx, ASSESSMENT_TABLES)

    eligible = _eligibility(ctx)
    assert eligible["state"] == "ELIGIBLE", eligible
    created = _retake(ctx)
    assert created.status_code == 201, created.text
    second_attempt = created.json()["attempt"]["attempt_id"]

    # A retake is a different paper, not a reshuffle — compared as sets, from UC-03's own table.
    second_paper = delivered_question_ids(ctx, second_attempt)
    assert set(second_paper).isdisjoint(first_paper), "the bank is large enough to be fresh"

    _sit_existing(ctx, second_attempt, correct=3)
    _, gated = _score_and_gate(ctx, second_attempt)
    assert gated["outcome"]["outcome"] == "PASS"
    assert gated["outcome"]["attemptNumber"] == 2
    assert gated["certificate"]["status"] == "ISSUED"

    # Exactly one certificate exists, and it belongs to the attempt that passed.
    certificates = _certificates(ctx)
    assert len(certificates) == 1
    assert certificates[0]["attempt_id"] == second_attempt

    # The first attempt is untouched. Whole-table comparison, not a spot check on one column.
    after = fingerprint(ctx, ASSESSMENT_TABLES)
    for table, rows in before.items():
        surviving = [row for row in after[table] if row in rows]
        assert surviving == rows, f"{table}: the first attempt's rows changed"

    # And the learner's history shows both attempts, in the right order, with the right verdicts.
    history = ctx.client.get(
        f"{V1}/quizzes/{ctx.quiz_id}/attempt-history", headers=auth(LEARNER_TOKEN)
    )
    assert history.status_code == 200, history.text
    entries = history.json()["entries"]
    assert history.json()["attempt_count"] == 2
    assert [item["attempt_number"] for item in entries] == [1, 2]
    assert [item["pass_fail_status"] for item in entries] == ["FAILED", "PASSED"]
    assert [item["is_retake"] for item in entries] == [False, True]
    assert entries[1]["retake_of_attempt_id"] == first_attempt


# ---------------------------------------------------------------------------
# Scenario C — the allowance runs out, and an administrator grants one more
# ---------------------------------------------------------------------------


def test_scenario_c_an_exhausted_learner_is_granted_one_more_attempt_and_the_quiz_is_unchanged(
    system: Any,
) -> None:
    """The grant is for one learner. Everything else about the quiz stays exactly where it was.

    This is the journey where a mistake would be least visible and most damaging: raising the
    course-wide maximum to let one learner through would silently give it to everybody, and nothing
    in the learner's own view would show it.
    """
    ctx = _journey(system)
    _score_and_gate(ctx, _sit_fresh(ctx, correct=0))
    first_retake = _retake(ctx)
    assert first_retake.status_code == 201, first_retake.text
    _sit_existing(ctx, first_retake.json()["attempt"]["attempt_id"], correct=0)
    _score_and_gate(ctx, first_retake.json()["attempt"]["attempt_id"])

    assert _eligibility(ctx)["state"] == "EXHAUSTED"
    refused = _retake(ctx)
    assert refused.status_code == 409, refused.text

    version_id = ctx.active_version_id()
    maximum_before = ctx.scalar(
        "SELECT max_attempts FROM qc_configuration_versions WHERE id = :id", id=version_id
    )
    versions_before = ctx.version_count()

    granted = ctx.client.post(
        "/api/admin/retakes/grants",
        json={
            "learner_id": str(ctx.learner_id),
            "course_id": str(ctx.course_id),
            "quiz_id": str(ctx.quiz_id),
            "additional_attempts": 1,
            "reason": "Approved by the course lead after a documented technical fault.",
        },
        headers={**auth(ADMIN_TOKEN), "Idempotency-Key": "uc11-scenario-c"},
    )
    assert granted.status_code == 201, granted.text

    assert (
        ctx.scalar(
            "SELECT max_attempts FROM qc_configuration_versions WHERE id = :id", id=version_id
        )
        == maximum_before
    ), "a grant must never edit the published configuration"
    assert ctx.version_count() == versions_before, "and must not publish a version to do it"

    after = _eligibility(ctx)
    assert after["state"] == "ADDITIONAL_ATTEMPT_AVAILABLE"
    assert after["allowance"]["granted_attempts"] == 1

    third = _retake(ctx)
    assert third.status_code == 201, third.text
    third_attempt = third.json()["attempt"]["attempt_id"]
    _sit_existing(ctx, third_attempt, correct=3)
    _, gated = _score_and_gate(ctx, third_attempt)
    assert gated["outcome"]["outcome"] == "PASS"
    assert gated["outcome"]["attemptNumber"] == 3
    assert gated["certificate"]["status"] == "ISSUED"

    # The grant is spent, not standing. One extra attempt means one.
    assert _eligibility(ctx)["state"] == "EXHAUSTED"


# ---------------------------------------------------------------------------
# Scenario D — a formal assessment is passed, held, and released by an assessor
# ---------------------------------------------------------------------------


def _start_formal(ctx: Ctx) -> tuple[str, str, str]:
    """The whole pre-start sequence, as a real learner walks it."""
    conditions = ctx.client.get(
        f"{V1}/quizzes/{ctx.quiz_id}/formal-conditions", headers=auth(LEARNER_TOKEN)
    )
    assert conditions.status_code == 200, conditions.text
    codes = [item["code"] for item in conditions.json()["conditions"]]

    acknowledged = ctx.client.post(
        f"{V1}/quizzes/{ctx.quiz_id}/conditions-acknowledgement",
        json={"acknowledged_condition_codes": codes},
        headers=auth(LEARNER_TOKEN),
    )
    assert acknowledged.status_code in (200, 201), acknowledged.text
    formal_attempt_id = acknowledged.json()["formal_attempt_id"]

    identity = ctx.client.post(
        f"{V1}/quizzes/{ctx.quiz_id}/identity-confirmation",
        json={"full_name": "Test Learner", "email": "learner@test.local"},
        headers=auth(LEARNER_TOKEN),
    )
    assert identity.status_code == 200, identity.text

    started = ctx.client.post(
        f"{V1}/quizzes/{ctx.quiz_id}/formal-attempts",
        json={"device": {"fingerprint": "uc11-device", "platform": "test"}},
        headers=auth(LEARNER_TOKEN),
    )
    assert started.status_code in (200, 201), started.text
    body = started.json()
    return formal_attempt_id, body["session"]["session_token"], body["attempt_id"]


def _formal_autosave(
    ctx: Ctx,
    formal_attempt_id: str,
    session_token: str,
    attempt_id: str,
    *,
    correct: int,
    answer_only: int | None = None,
) -> None:
    """Autosave through UC-09's own endpoint.

    ``correct`` is how many of the answered questions are right; ``answer_only`` limits how many
    are answered at all, which is how a scenario arranges a half-finished paper without reaching
    behind the API to make one.
    """
    read = ctx.attempt_questions(attempt_id)
    assert read.status_code == 200, read.text
    questions = read.json()["questions"]
    if answer_only is not None:
        questions = questions[:answer_only]
    saved = ctx.client.post(
        f"{V1}/formal-attempts/{formal_attempt_id}/autosave",
        json={
            "answers": [
                {
                    "question_id": question["questionId"],
                    "response": answer_payload(ctx, question, correctly=index < correct),
                }
                for index, question in enumerate(questions)
            ]
        },
        headers={**auth(LEARNER_TOKEN), "X-Formal-Session": session_token},
    )
    assert saved.status_code == 200, saved.text


def test_scenario_d_a_passed_formal_assessment_waits_for_an_assessor_before_certifying(
    system: Any,
) -> None:
    """Pass is not release. The certificate exists only once a named assessor decides.

    The journey matters because the withholding has to survive every later step: UC-05's issue
    path, UC-05's retry endpoint, and the learner's own outcome view must all agree that there is
    no certificate yet, and then all agree that there is one.
    """
    from tests import bank

    ctx = system(FORMAL_CONFIGURATION, bank.DEFAULT_BANK)
    formal_attempt_id, session_token, attempt_id = _start_formal(ctx)
    _formal_autosave(ctx, formal_attempt_id, session_token, attempt_id, correct=2)

    submitted = ctx.client.post(
        f"{V1}/formal-attempts/{formal_attempt_id}/submission",
        json={},
        headers={**auth(LEARNER_TOKEN), "X-Formal-Session": session_token},
    )
    assert submitted.status_code == 200, submitted.text

    _, gated = _score_and_gate(ctx, attempt_id)
    assert gated["outcome"]["outcome"] == "PASS"
    assert gated["certificate"] is None or gated["certificate"]["status"] != "ISSUED", (
        "a formal pass must not certify before an assessor has decided"
    )
    assert _certificates(ctx) == [] or all(
        row["status"] != "ISSUED" for row in _certificates(ctx)
    )

    # Retrying the certificate does not smuggle one out either — the gate is asked every time.
    retried = ctx.client.post(
        f"{V1}/attempts/{attempt_id}/outcome/certificate/retry",
        json={},
        headers=auth(LEARNER_TOKEN),
    )
    assert retried.status_code in (200, 201, 409), retried.text
    assert all(row["status"] != "ISSUED" for row in _certificates(ctx))

    # The assessor picks the review up, decides, and only then does the certificate exist.
    pending = ctx.client.get("/api/assessor/pending-reviews", headers=auth(ASSESSOR_TOKEN))
    assert pending.status_code == 200, pending.text
    reviews = pending.json()["reviews"]
    assert len(reviews) == 1, reviews
    review_id = reviews[0]["review_id"]

    started_review = ctx.client.post(
        f"/api/assessor/reviews/{review_id}/review-start",
        json={},
        headers=auth(ASSESSOR_TOKEN),
    )
    assert started_review.status_code in (200, 201), started_review.text

    decided = ctx.client.post(
        f"/api/assessor/reviews/{review_id}/decision",
        json={"decision": "APPROVED", "notes": "Verified against the recorded session."},
        headers=auth(ASSESSOR_TOKEN),
    )
    assert decided.status_code in (200, 201), decided.text

    workflow = ctx.client.post(
        f"/api/assessor/reviews/{review_id}/certificate-workflow",
        json={},
        headers=auth(ASSESSOR_TOKEN),
    )
    assert workflow.status_code in (200, 201), workflow.text

    issued = [row for row in _certificates(ctx) if row["status"] == "ISSUED"]
    assert len(issued) == 1, _certificates(ctx)
    assert issued[0]["attempt_id"] == attempt_id
    assert issued[0]["certificate_number"]


# ---------------------------------------------------------------------------
# Scenario E — the connection drops mid formal assessment
# ---------------------------------------------------------------------------


def test_scenario_e_a_disconnect_submits_the_autosaved_work_rather_than_losing_it(
    system: Any,
) -> None:
    """A dropped connection ends the attempt. It does not discard what was already saved.

    Two facts have to hold together for that to be true, and they live in different capabilities:
    UC-09 must terminate the attempt through UC-03's submit-on-disconnect path, and UC-04 must then
    score the answers UC-03 had already persisted. Either alone looks correct in isolation.
    """
    from tests import bank

    ctx = system(FORMAL_CONFIGURATION, bank.DEFAULT_BANK)
    formal_attempt_id, session_token, attempt_id = _start_formal(ctx)
    # One question answered correctly; the learner never reached the second.
    _formal_autosave(
        ctx, formal_attempt_id, session_token, attempt_id, correct=1, answer_only=1
    )

    dropped = ctx.client.post(
        f"{V1}/formal-attempts/{formal_attempt_id}/disconnect",
        json={"reason": "NETWORK_LOSS"},
        headers={**auth(LEARNER_TOKEN), "X-Formal-Session": session_token},
    )
    assert dropped.status_code in (200, 201), dropped.text

    state = ctx.get_attempt(attempt_id)
    assert state.status_code == 200, state.text
    assert state.json()["attempt"]["status"] == "SUBMITTED"

    result, gated = _score_and_gate(ctx, attempt_id)
    assert result["result"]["status"] == "SCORED"
    assert result["result"]["correctCount"] == 1, "the autosaved answer was scored"
    assert result["result"]["unansweredCount"] == 1, "and nothing was invented for the rest"
    assert gated["outcome"]["percentage"] == 50.0


# ---------------------------------------------------------------------------
# Scenario F — the quiz is reconfigured while an attempt is in flight
# ---------------------------------------------------------------------------


def test_scenario_f_a_configuration_change_cannot_reach_an_attempt_already_running(
    system: Any,
) -> None:
    """The version an attempt locked at its start is the version it is judged by. Forever.

    The dangerous case is the one here: the change is published *after* the paper was drawn but
    *before* it was submitted, so a system that read the quiz's current pointer at scoring time
    would look correct on every other path and be wrong only on this one.
    """
    ctx = _journey(system)
    attempt_id, questions = ctx.start_and_read_questions()
    locked_version = ctx.active_version_id()
    locked_paper = delivered_question_ids(ctx, attempt_id)

    # A different pass mark and a different paper size, published mid-flight.
    republished = ctx.save_configuration(
        {**JOURNEY_CONFIGURATION, "passMark": 90, "questionCount": 2}
    )
    assert republished.status_code == 201, republished.text
    new_version = ctx.active_version_id()
    assert new_version != locked_version, "the fixture must really have published a new version"

    # The in-flight paper is unchanged: same questions, same count.
    assert delivered_question_ids(ctx, attempt_id) == locked_paper
    assert len(locked_paper) == 3

    for index, question in enumerate(questions):
        ctx.client.put(
            f"{V1}/attempts/{attempt_id}/questions/{question['questionId']}/answer",
            json={
                "response": answer_payload(ctx, question, correctly=index < 2),
                "source": "MANUAL",
            },
            headers=auth(LEARNER_TOKEN),
        )
    assert ctx.submit_attempt(attempt_id).status_code == 200

    scored, gated = _score_and_gate(ctx, attempt_id)
    # Two of three is 66.7%: a pass under the locked 60, a fail under the published 90.
    assert scored["result"]["configurationVersionId"] == str(locked_version)
    assert gated["outcome"]["passMarkPercentage"] == 60.0
    assert gated["outcome"]["outcome"] == "PASS"
    assert gated["outcome"]["configurationVersionId"] == str(locked_version)

    # And the report the learner is shown agrees with the version the attempt ran under.
    report = _feedback(ctx, attempt_id)
    assert report["summary"]["passMarkPercentage"] == 60.0
    assert report["summary"]["passed"] is True

    # The *next* attempt gets the new configuration — the change was published, not lost.
    retake = _retake(ctx)
    assert retake.status_code == 201, retake.text
    next_attempt = retake.json()["attempt"]["attempt_id"]
    assert len(delivered_question_ids(ctx, next_attempt)) == 2, (
        "a new attempt must run under the currently published version"
    )
    _sit_existing(ctx, next_attempt, correct=2)
    _, next_gated = _score_and_gate(ctx, next_attempt)
    assert next_gated["outcome"]["passMarkPercentage"] == 90.0
    assert next_gated["outcome"]["outcome"] == "PASS"


def test_the_journeys_leave_the_shared_kernel_alone(system: Any) -> None:
    """A sanity check on the suite itself, not on the system.

    ``sit`` is the vocabulary the rest of this package is written in. If it stopped submitting, or
    stopped answering, every assertion above would still pass while proving nothing. This asserts
    the helper does what its name says.
    """
    ctx = _journey(system)
    attempt_id, questions = sit(ctx, correctly=True)
    assert len(questions) == 3
    state = ctx.get_attempt(attempt_id)
    assert state.json()["attempt"]["status"] == "SUBMITTED"
    result, _ = _score_and_gate(ctx, attempt_id)
    assert result["result"]["correctCount"] == 3
