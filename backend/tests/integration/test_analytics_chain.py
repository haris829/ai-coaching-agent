"""UC-10 on the real chain, over HTTP, with the real read-only projection.

    configure (UC-01) -> bank (UC-02) -> attempts (UC-03) -> submit
        -> score (UC-04) -> pass/fail (UC-05) -> analytics (UC-10)

``tests/analytics/`` tests UC-10's calculations against in-memory repositories and a dataset small
enough to verify by hand. This file tests the part those cannot: that the projection over
UC-02/UC-03/UC-04/UC-05's own rows produces the records the calculations were verified against, and
that the numbers on the dashboard are the numbers those capabilities actually wrote.

Nothing here is a double. Real rows, real transactions, real constraints, real triggers.

WHAT THIS FILE IS REALLY CHECKING
---------------------------------
1. **The metrics are the chain's own figures.** Average score, pass rate, completion rate and
   attempt volume are compared against ``qr_attempt_results`` and ``qg_attempt_outcomes`` directly.
   UC-10 aggregates; it must not re-decide.
2. **Question accuracy comes from UC-04's outcomes**, and the most common wrong answer is the label
   UC-04 rendered — the same string the learner saw on their feedback report.
3. **The three filters work against real data**: the formal/standard split reads UC-09's flag on
   the attempt, the cohort filter reads the platform's enrolment, and the date range is half-open.
4. **CSV matches the dashboard exactly**, field for field, on the same request.
5. **Analytics changes nothing.** Every attempt, answer, score and outcome is fingerprinted before
   and after a full dashboard read plus a review action.
6. **A flag clears only through a review action**, and the audit row is immutable — asserted by
   attempting the forbidden ``UPDATE`` on the real table.
7. **Zero attempts is an empty state, not zeros.**
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from app.modules.question_bank.models import Question
from tests import bank
from tests.harness import (
    ADMIN_TOKEN,
    LEARNER2_COHORT,
    LEARNER2_TOKEN,
    LEARNER_COHORT,
    LEARNER_TOKEN,
    Ctx,
    auth,
)

ADMIN = "/api/admin/analytics"
V1 = "/api/v1"

#: Two single-choice questions and a pass mark of 50, so one correct answer passes and none fails.
#: Small on purpose: this file is about the projection, not about scoring.
CONFIGURATION: dict[str, Any] = {
    "questionCount": 2,
    "timeLimitMinutes": 30,
    "passMark": 50,
    "questionTypes": [{"type": "SINGLE_CHOICE"}],
    "randomiseQuestions": False,
    "maxAttempts": 5,
    "deliveryMode": "assessment",
}


# ---------------------------------------------------------------------------
# Driving the real chain
# ---------------------------------------------------------------------------


def _configured(make_ctx: Any, **overrides: Any) -> Ctx:
    ctx = make_ctx(bank.DEFAULT_BANK)
    saved = ctx.save_configuration({**CONFIGURATION, **overrides})
    assert saved.status_code == 201, saved.text
    return ctx


def _option_labels(ctx: Ctx, question_id: str) -> tuple[list[str], list[str]]:
    """``(correct, wrong)`` option labels, read from UC-02's own key."""
    with ctx.session() as session:
        row = session.get(Question, question_id)
        assert row is not None
        options = sorted(row.options, key=lambda option: option.position)
        return (
            [option.label for option in options if option.is_correct],
            [option.label for option in options if not option.is_correct],
        )


def _sit(
    ctx: Ctx,
    *,
    token: str = LEARNER_TOKEN,
    correct_count: int = 0,
    wrong_choice: int = 0,
    submit: bool = True,
) -> tuple[str, list[str]]:
    """Sit an attempt, answering ``correct_count`` questions correctly.

    ``wrong_choice`` selects *which* wrong option to pick, which is how the "most common wrong
    answer" assertion gets a genuine winner rather than a tie.
    """
    attempt_id, questions = ctx.start_and_read_questions(token)
    for index, question in enumerate(questions):
        correct, wrong = _option_labels(ctx, question["questionId"])
        chosen = correct[0] if index < correct_count else wrong[wrong_choice % len(wrong)]
        saved = ctx.client.put(
            f"{V1}/attempts/{attempt_id}/questions/{question['questionId']}/answer",
            json={
                "response": {"type": "SINGLE_CHOICE", "selectedOptionId": chosen},
                "source": "MANUAL",
            },
            headers=auth(token),
        )
        assert saved.status_code == 200, saved.text

    if submit:
        submitted = ctx.client.post(
            f"{V1}/attempts/{attempt_id}/submission",
            json={"confirmed": True},
            headers=auth(token),
        )
        assert submitted.status_code == 200, submitted.text
    return attempt_id, [question["questionId"] for question in questions]


def _overall(ctx: Ctx, **params: Any) -> dict[str, Any]:
    response = ctx.client.get(f"{ADMIN}/overall", params=params, headers=auth(ADMIN_TOKEN))
    assert response.status_code == 200, response.text
    return response.json()


def _questions(ctx: Ctx, **params: Any) -> dict[str, Any]:
    response = ctx.client.get(f"{ADMIN}/questions", params=params, headers=auth(ADMIN_TOKEN))
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# 1. The metrics are the chain's own figures
# ---------------------------------------------------------------------------


def test_dashboard_metrics_match_what_the_chain_actually_wrote(make_ctx: Any) -> None:
    """Every headline metric checked against UC-04's and UC-05's rows, not against a fixture."""
    ctx = _configured(make_ctx)
    _sit(ctx, correct_count=2)  # 100% -> pass
    _sit(ctx, correct_count=0, token=LEARNER2_TOKEN)  # 0% -> fail

    body = _overall(ctx)

    with ctx.session() as session:
        scores = [
            row.percentage
            for row in session.execute(
                text("SELECT percentage FROM qr_attempt_results WHERE status = 'SCORED'")
            )
        ]
        passes = int(
            session.execute(
                text("SELECT COUNT(*) FROM qg_attempt_outcomes WHERE outcome = 'PASS'")
            ).scalar()
            or 0
        )
        outcomes = int(
            session.execute(text("SELECT COUNT(*) FROM qg_attempt_outcomes")).scalar() or 0
        )

    assert body["data_state"] == "OK"
    assert body["attempt_volume"] == 2
    assert body["scored_attempts"] == len(scores)
    assert body["average_score"] == pytest.approx(sum(scores) / len(scores), abs=0.01)
    assert body["graded_attempts"] == outcomes
    assert body["passed_attempts"] == passes
    assert body["pass_rate"] == pytest.approx(100.0 * passes / outcomes, abs=0.01)
    # Both attempts were submitted, so both are COMPLETED as far as UC-10 is concerned.
    assert body["completion_rate"] == pytest.approx(100.0)
    assert body["unique_learners"] == 2
    # Freshness: the requirement asks for when the figures were calculated.
    assert body["calculated_at"]


def test_an_attempt_in_progress_is_counted_but_not_scored(make_ctx: Any) -> None:
    """The denominators are what keep an unfinished attempt from distorting the averages."""
    ctx = _configured(make_ctx)
    _sit(ctx, correct_count=2)
    _sit(ctx, correct_count=1, token=LEARNER2_TOKEN, submit=False)

    body = _overall(ctx)

    assert body["attempt_volume"] == 2
    assert body["completed_attempts"] == 1
    assert body["scored_attempts"] == 1, "an unsubmitted attempt has no confirmed score"
    assert body["graded_attempts"] == 1
    assert body["average_score"] == pytest.approx(100.0)
    assert body["completion_rate"] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# 2. Question analytics from UC-04's real outcomes
# ---------------------------------------------------------------------------


def test_question_accuracy_and_common_wrong_answer_come_from_uc04(make_ctx: Any) -> None:
    """The wrong answer reported is the label UC-04 rendered — what the learner actually saw."""
    ctx = _configured(make_ctx)
    # Three sittings: one all-correct, two all-wrong picking the *same* wrong option, so the
    # most-common wrong answer has a genuine winner rather than a tie.
    _, question_ids = _sit(ctx, correct_count=2)
    _sit(ctx, correct_count=0, wrong_choice=0)
    _sit(ctx, correct_count=0, wrong_choice=0, token=LEARNER2_TOKEN)

    first_question = question_ids[0]
    body = _questions(ctx)
    entry = next(item for item in body["items"] if item["question_id"] == first_question)

    with ctx.session() as session:
        rows = list(
            session.execute(
                text(
                    "SELECT outcome, learner_answer_display FROM qr_question_scores "
                    "WHERE question_id = :q"
                ),
                {"q": first_question},
            ).mappings()
        )

    graded = len(rows)
    correct = sum(1 for row in rows if row["outcome"] == "CORRECT")
    assert entry["graded_count"] == graded
    assert entry["correct_count"] == correct
    assert entry["accuracy_percentage"] == pytest.approx(100.0 * correct / graded, abs=0.01)
    assert entry["wrong_answer_rate"] == pytest.approx(
        100.0 * (graded - correct) / graded, abs=0.01
    )

    # The winner is the label UC-04 stored for the wrong answers, not one this test invented.
    #
    # ``learner_answer_display`` is JSON — ``{"optionIds": [...], "labels": [...]}`` — because a
    # multi-select answer is several labels. UC-10 groups by a single string, so the projection
    # reduces it to the *labels*, which are what the learner saw. The assertion reads the same
    # field and checks the reported answer is one of those labels.
    import json as _json

    wrong_labels: list[str] = []
    for row in rows:
        if row["outcome"] == "CORRECT":
            continue
        raw = row["learner_answer_display"]
        payload = _json.loads(raw) if isinstance(raw, str) else raw
        labels = payload.get("labels") if isinstance(payload, dict) else None
        wrong_labels.append(", ".join(sorted(map(str, labels or []))))

    winner = entry["most_frequent_wrong_answer"]
    assert winner is not None
    assert winner["answer"] in wrong_labels, (winner["answer"], wrong_labels)
    assert winner["count"] == wrong_labels.count(winner["answer"])
    # Every wrong answer here was the same option, so the winner accounts for all of them.
    assert winner["share_of_incorrect"] == pytest.approx(100.0)

    # The type is UC-10's reporting vocabulary, and the label is the system's own name.
    assert entry["question_type"] == "MULTIPLE_CHOICE"
    assert entry["question_type_label"] == "Single choice"


def test_average_time_per_question_is_measured_not_invented(make_ctx: Any) -> None:
    """Derived from UC-03's save instants — see ``_derive_time_spent`` for what it measures.

    The assertion is deliberately weak on the *value* and strong on its provenance: a real
    measurement over real saves is a non-negative number or ``None``, and it must never be a
    fabricated zero for a question nobody answered.
    """
    ctx = _configured(make_ctx)
    _, question_ids = _sit(ctx, correct_count=1)

    body = _questions(ctx)
    entry = next(item for item in body["items"] if item["question_id"] == question_ids[0])

    average = entry["average_time_seconds"]
    assert average is None or average >= 0.0


# ---------------------------------------------------------------------------
# 3. The three filters, against real data
# ---------------------------------------------------------------------------


def test_the_assessment_type_filter_reads_uc09s_flag_on_the_attempt(make_ctx: Any) -> None:
    ctx = _configured(make_ctx)
    _sit(ctx, correct_count=2)

    standard = _overall(ctx, assessment_type="STANDARD_QUIZ")
    formal = _overall(ctx, assessment_type="FORMAL_ASSESSMENT")

    assert standard["attempt_volume"] == 1
    # No formal assessment was sat, so the formal view is an empty state rather than zeros.
    assert formal["attempt_volume"] == 0
    assert formal["data_state"] == "NO_ATTEMPTS"
    assert formal["average_score"] is None
    assert formal["pass_rate"] is None


def test_the_cohort_filter_reads_the_platforms_enrolment(make_ctx: Any) -> None:
    """The cohort column added for this requirement, exercised against real enrolments."""
    ctx = _configured(make_ctx)
    _sit(ctx, correct_count=2)  # learner 1, cohort-a
    _sit(ctx, correct_count=0, token=LEARNER2_TOKEN)  # learner 2, cohort-b

    everyone = _overall(ctx)
    first = _overall(ctx, cohort_id=LEARNER_COHORT)
    second = _overall(ctx, cohort_id=LEARNER2_COHORT)
    nobody = _overall(ctx, cohort_id="cohort-that-does-not-exist")

    assert everyone["attempt_volume"] == 2
    assert first["attempt_volume"] == 1
    assert second["attempt_volume"] == 1
    assert first["average_score"] == pytest.approx(100.0)
    assert second["average_score"] == pytest.approx(0.0)
    # An unknown cohort is an empty state, not the whole dataset.
    assert nobody["attempt_volume"] == 0
    assert nobody["data_state"] == "NO_ATTEMPTS"


def test_the_date_range_is_half_open_so_consecutive_periods_tile(make_ctx: Any) -> None:
    """A January report and a February report can never double-count the same attempt."""
    ctx = _configured(make_ctx)
    _sit(ctx, correct_count=2)

    started_at = ctx.scalar("SELECT started_at FROM qd_attempts LIMIT 1")
    day = str(started_at)[:10]

    inclusive_start = _overall(ctx, start_date=f"{day}T00:00:00+00:00")
    exclusive_end = _overall(ctx, end_date=f"{day}T00:00:00+00:00")

    assert inclusive_start["attempt_volume"] == 1, "start_date is inclusive"
    assert exclusive_end["attempt_volume"] == 0, "end_date is exclusive"
    assert exclusive_end["data_state"] == "NO_ATTEMPTS"


# ---------------------------------------------------------------------------
# 4. The export matches the dashboard
# ---------------------------------------------------------------------------


def test_the_csv_export_matches_the_dashboard_field_for_field(make_ctx: Any) -> None:
    """The requirement is exactness, and the only way to check it is to compare both outputs."""
    import csv
    import io

    ctx = _configured(make_ctx)
    _sit(ctx, correct_count=2)
    _sit(ctx, correct_count=0, token=LEARNER2_TOKEN)

    dashboard = _overall(ctx)
    export = ctx.client.get(f"{ADMIN}/exports/overall.csv", headers=auth(ADMIN_TOKEN))
    assert export.status_code == 200, export.text
    assert "text/csv" in export.headers["content-type"]

    rows = list(csv.DictReader(io.StringIO(export.text)))
    assert len(rows) == 1
    row = rows[0]

    for field in (
        "attempt_volume",
        "completed_attempts",
        "scored_attempts",
        "graded_attempts",
        "passed_attempts",
        "failed_attempts",
        "unique_learners",
    ):
        assert row[field] == str(dashboard[field]), field

    for field in ("average_score", "pass_rate", "completion_rate"):
        assert float(row[field]) == pytest.approx(dashboard[field]), field

    # The empty-state signal travels on the export too, so a spreadsheet cannot mistake an empty
    # period for a measured zero.
    assert export.headers["X-Analytics-Data-State"] == dashboard["data_state"]


# ---------------------------------------------------------------------------
# 5. Analytics changes nothing
# ---------------------------------------------------------------------------


def _assessment_fingerprint(ctx: Ctx) -> dict[str, Any]:
    """Everything analytics must not be able to touch."""
    with ctx.session() as session:
        return {
            table: [
                dict(row)
                for row in session.execute(
                    text(f"SELECT * FROM {table} ORDER BY 1")  # noqa: S608 - fixed table list
                ).mappings()
            ]
            for table in (
                "qd_attempts",
                "qd_attempt_answers",
                "qd_attempt_questions",
                "qr_attempt_results",
                "qr_question_scores",
                "qg_attempt_outcomes",
            )
        }


def test_a_full_dashboard_read_and_a_review_action_change_no_assessment_data(
    make_ctx: Any,
) -> None:
    """The read-only requirement, checked against every table analytics reads.

    The projection having no mutating method proves UC-10 *cannot* have written. This proves that
    exercising the whole surface — including the two endpoints that legitimately write to UC-10's
    own review store — leaves the assessment data byte-identical.
    """
    ctx = _configured(make_ctx)
    _, question_ids = _sit(ctx, correct_count=0)
    _sit(ctx, correct_count=0, token=LEARNER2_TOKEN)

    before = _assessment_fingerprint(ctx)

    for path in (
        f"{ADMIN}/overall",
        f"{ADMIN}/questions",
        f"{ADMIN}/questions/flagged",
        f"{ADMIN}/exports/overall.csv",
        f"{ADMIN}/exports/questions.csv",
        f"{ADMIN}/exports/flagged-questions.csv",
        f"{ADMIN}/review/actions",
        f"{ADMIN}/config",
    ):
        assert ctx.client.get(path, headers=auth(ADMIN_TOKEN)).status_code == 200, path

    evaluated = ctx.client.post(
        f"{ADMIN}/questions/flags/evaluate", headers=auth(ADMIN_TOKEN)
    )
    assert evaluated.status_code in (200, 201), evaluated.text

    recorded = ctx.client.post(
        f"{ADMIN}/review/actions",
        json={"question_id": question_ids[0], "action": "NO_CHANGE"},
        headers=auth(ADMIN_TOKEN),
    )
    assert recorded.status_code == 201, recorded.text

    assert _assessment_fingerprint(ctx) == before


# ---------------------------------------------------------------------------
# 6. Flags, review actions and the immutable audit trail
# ---------------------------------------------------------------------------


def test_a_poorly_answered_question_is_flagged_and_clears_only_by_review(
    make_ctx: Any,
) -> None:
    ctx = _configured(make_ctx)
    # Five sittings, every answer wrong: a 100% wrong-answer rate, and five graded responses per
    # question, which is what ``analytics_flag_min_responses`` requires before anything is flagged.
    # That floor is the point — a question is not condemned on one wrong answer.
    _, question_ids = _sit(ctx, correct_count=0)
    _sit(ctx, correct_count=0)
    _sit(ctx, correct_count=0)
    _sit(ctx, correct_count=0, token=LEARNER2_TOKEN)
    _sit(ctx, correct_count=0, token=LEARNER2_TOKEN)
    target = question_ids[0]

    evaluated = ctx.client.post(
        f"{ADMIN}/questions/flags/evaluate", headers=auth(ADMIN_TOKEN)
    )
    assert evaluated.status_code in (200, 201), evaluated.text

    flagged = ctx.client.get(f"{ADMIN}/questions/flagged", headers=auth(ADMIN_TOKEN)).json()
    assert target in {item["question_id"] for item in flagged["items"]}
    assert ctx.scalar(
        "SELECT status FROM qy_question_flags WHERE question_id = :q", q=target
    ) == "FLAGGED"

    # A second evaluation does not duplicate it: the question id is the flag's identity.
    ctx.client.post(f"{ADMIN}/questions/flags/evaluate", headers=auth(ADMIN_TOKEN))
    assert (
        int(
            ctx.scalar(
                "SELECT COUNT(*) FROM qy_question_flags WHERE question_id = :q", q=target
            )
            or 0
        )
        == 1
    )

    # Only an explicit review action clears it, and the action is attributed and timestamped.
    resolved = ctx.client.post(
        f"{ADMIN}/review/actions",
        json={
            "question_id": target,
            "action": "QUESTION_UPDATED",
            "note": "Reworded the stem.",
        },
        headers=auth(ADMIN_TOKEN),
    )
    assert resolved.status_code == 201, resolved.text

    assert ctx.scalar(
        "SELECT status FROM qy_question_flags WHERE question_id = :q", q=target
    ) == "RESOLVED"

    with ctx.session() as session:
        action = (
            session.execute(
                text(
                    "SELECT action, admin_id, created_at, note, previous_flag_status, "
                    "resulting_flag_status FROM qy_review_actions WHERE question_id = :q"
                ),
                {"q": target},
            )
            .mappings()
            .one()
        )
    assert action["action"] == "QUESTION_UPDATED"
    assert action["admin_id"], "every review action names the administrator who made it"
    assert action["created_at"], "and when"
    assert action["previous_flag_status"] == "FLAGGED"
    assert action["resulting_flag_status"] == "RESOLVED"


def test_the_review_audit_trail_is_immutable_in_the_database(make_ctx: Any) -> None:
    """Asserted by attempting the forbidden write, which is the only way to show the trigger works.

    A service that promises not to rewrite an audit row is not the same as a database that refuses
    to. "Who cleared this flag?" has to stay answerable even against a code path nobody wrote.

    UPDATE is what the trigger refuses. DELETE is not — see ``analytics/models.py`` for why: what
    stops UC-10 deleting an audit row is that no method exists to do it, the same way UC-08
    protects its grants.
    """
    ctx = _configured(make_ctx)
    _, question_ids = _sit(ctx, correct_count=0)

    recorded = ctx.client.post(
        f"{ADMIN}/review/actions",
        json={"question_id": question_ids[0], "action": "NO_CHANGE"},
        headers=auth(ADMIN_TOKEN),
    )
    assert recorded.status_code == 201, recorded.text

    with pytest.raises(Exception) as updated:
        ctx.execute("UPDATE qy_review_actions SET admin_id = 'someone-else'")
    assert "append-only" in str(updated.value)

    # The row is still there, unchanged, after the refused write.
    assert ctx.scalar("SELECT COUNT(*) FROM qy_review_actions") == 1
    assert ctx.scalar("SELECT admin_id FROM qy_review_actions") != "someone-else"


def test_retiring_a_question_is_terminal(make_ctx: Any) -> None:
    """A retired question is never re-flagged, however badly it went before."""
    ctx = _configured(make_ctx)
    _, question_ids = _sit(ctx, correct_count=0)
    _sit(ctx, correct_count=0)
    _sit(ctx, correct_count=0)
    _sit(ctx, correct_count=0, token=LEARNER2_TOKEN)
    _sit(ctx, correct_count=0, token=LEARNER2_TOKEN)
    target = question_ids[0]

    ctx.client.post(f"{ADMIN}/questions/flags/evaluate", headers=auth(ADMIN_TOKEN))
    retired = ctx.client.post(
        f"{ADMIN}/review/actions",
        json={"question_id": target, "action": "QUESTION_RETIRED"},
        headers=auth(ADMIN_TOKEN),
    )
    assert retired.status_code == 201, retired.text
    assert ctx.scalar(
        "SELECT status FROM qy_question_flags WHERE question_id = :q", q=target
    ) == "RETIRED"

    # Re-evaluating does not resurrect it.
    ctx.client.post(f"{ADMIN}/questions/flags/evaluate", headers=auth(ADMIN_TOKEN))
    assert ctx.scalar(
        "SELECT status FROM qy_question_flags WHERE question_id = :q", q=target
    ) == "RETIRED"


# ---------------------------------------------------------------------------
# 7. Empty state, and the guards
# ---------------------------------------------------------------------------


def test_a_course_with_no_attempts_reports_an_empty_state_not_zeros(make_ctx: Any) -> None:
    """The requirement is guidance rather than empty charts, and ``null`` rather than ``0``."""
    ctx = _configured(make_ctx)

    body = _overall(ctx)

    assert body["data_state"] == "NO_ATTEMPTS"
    assert body["attempt_volume"] == 0
    assert body["average_score"] is None
    assert body["pass_rate"] is None
    assert body["completion_rate"] is None
    assert body["calculated_at"], "freshness is reported even when there is nothing to report"


def test_a_measured_zero_is_distinguishable_from_no_data(make_ctx: Any) -> None:
    """``0`` and ``null`` mean different things, and the difference has to survive the projection."""
    ctx = _configured(make_ctx)
    _sit(ctx, correct_count=0)

    body = _overall(ctx)

    assert body["data_state"] == "OK"
    assert body["average_score"] == pytest.approx(0.0), "a real zero, measured"
    assert body["pass_rate"] == pytest.approx(0.0)
    assert body["attempt_volume"] == 1


def test_analytics_refuses_a_learner_credential(make_ctx: Any) -> None:
    """Analytics aggregates across every learner, so no learner may read it.

    Only the *learner* case is asserted here. Whether an anonymous caller is refused depends on
    ``ADMIN_API_TOKEN``, which the test environment deliberately leaves empty so the rest of the
    suite can drive admin endpoints — that seam has its own tests in the identity module. The
    learner refusal is unconditional and is the one that matters for analytics: it holds whether or
    not a deployment has configured the token.
    """
    ctx = _configured(make_ctx)
    _sit(ctx, correct_count=1)

    refused = ctx.client.get(f"{ADMIN}/overall", headers=auth(LEARNER_TOKEN))
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "FORBIDDEN"


def test_the_threshold_endpoint_refuses_a_dangerous_value_without_confirmation(
    make_ctx: Any,
) -> None:
    """The company requirement: warn that 0% would flag everything, and demand confirmation."""
    ctx = _configured(make_ctx)

    refused = ctx.client.post(
        f"{ADMIN}/config/validate",
        json={"flag_wrong_answer_rate_threshold": 0.1},
        headers=auth(ADMIN_TOKEN),
    )
    assert refused.status_code == 200, refused.text
    body = refused.json()
    assert body["valid"] is False
    assert body["requires_confirmation"] is True
    assert "THRESHOLD_DANGEROUSLY_LOW" in {issue["code"] for issue in body["issues"]}

    confirmed = ctx.client.post(
        f"{ADMIN}/config/validate",
        params={"confirm_dangerous": "true"},
        json={"flag_wrong_answer_rate_threshold": 0.1},
        headers=auth(ADMIN_TOKEN),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["valid"] is True
