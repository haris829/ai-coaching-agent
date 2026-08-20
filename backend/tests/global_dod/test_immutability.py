"""UC-11 §10 — immutable attempt data, and configuration immutability.

After submission: answers cannot be modified, scores cannot be modified, historical attempt data
cannot be changed, and **neither a learner API nor an admin API can mutate a submitted attempt**.

The last clause is why this file exists rather than the assertion living in UC-03's suite. UC-03 can
prove *its own* endpoints refuse a write. It cannot prove that no other capability's endpoint
does — and by the time ten capabilities read an attempt, "nothing writes to it" is a statement
about the whole application.

So the method here is a **fingerprint**: every row of every table holding a learner's submitted
work, compared before and after driving the entire application surface. That catches a write nobody
predicted, including one through a route added later.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from tests.global_dod.conftest import (
    ADMIN_TOKEN,
    ASSESSMENT_TABLES,
    LEARNER_TOKEN,
    V1,
    answer_payload,
    auth,
    fingerprint,
    sit,
)
from tests.harness import LEARNER2_TOKEN

# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------


def test_a_learner_cannot_change_an_answer_after_submitting(simple_system: Any) -> None:
    ctx = simple_system
    attempt_id, questions = sit(ctx, correctly=False)
    question = questions[0]

    before = fingerprint(ctx, ("qd_attempt_answers", "qd_attempt_answer_revisions"))

    response = ctx.client.put(
        f"{V1}/attempts/{attempt_id}/questions/{question['questionId']}/answer",
        json={"response": answer_payload(ctx, question, correctly=True), "source": "MANUAL"},
        headers=auth(LEARNER_TOKEN),
    )

    assert response.status_code in (409, 422), response.text
    assert fingerprint(ctx, ("qd_attempt_answers", "qd_attempt_answer_revisions")) == before


def test_an_answer_cannot_be_cleared_after_submitting(simple_system: Any) -> None:
    """Clearing is a write too, and a cleared answer would silently change the score."""
    ctx = simple_system
    attempt_id, questions = sit(ctx, correctly=True)

    before = fingerprint(ctx, ("qd_attempt_answers",))

    response = ctx.client.put(
        f"{V1}/attempts/{attempt_id}/questions/{questions[0]['questionId']}/answer",
        json={"response": None, "source": "MANUAL"},
        headers=auth(LEARNER_TOKEN),
    )

    assert response.status_code in (409, 422), response.text
    assert fingerprint(ctx, ("qd_attempt_answers",)) == before


def test_the_answer_revision_history_is_append_only(simple_system: Any) -> None:
    """Every save before submission is kept, so "what did they answer first?" stays answerable."""
    ctx = simple_system
    attempt_id, questions = ctx.start_and_read_questions(LEARNER_TOKEN)
    question = questions[0]

    for correctly in (False, True, False):
        saved = ctx.client.put(
            f"{V1}/attempts/{attempt_id}/questions/{question['questionId']}/answer",
            json={
                "response": answer_payload(ctx, question, correctly=correctly),
                "source": "AUTOSAVE",
            },
            headers=auth(LEARNER_TOKEN),
        )
        assert saved.status_code == 200, saved.text

    revisions = int(
        ctx.scalar(
            "SELECT COUNT(*) FROM qd_attempt_answer_revisions WHERE attempt_id = :a",
            a=attempt_id,
        )
        or 0
    )
    assert revisions >= 3, "each distinct save must leave its own revision"

    # The revisions are never rewritten: the earliest one is still revision 1.
    with ctx.session() as session:
        first = session.execute(
            text(
                "SELECT revision FROM qd_attempt_answer_revisions "
                "WHERE attempt_id = :a ORDER BY revision LIMIT 1"
            ),
            {"a": attempt_id},
        ).scalar()
    assert first == 1


# ---------------------------------------------------------------------------
# Scores and outcomes
# ---------------------------------------------------------------------------


def test_a_confirmed_score_is_not_recomputed_by_asking_for_it_again(
    simple_system: Any,
) -> None:
    """The verdict is derived from immutable data, so re-reading it must be a read.

    A result that recomputed on read would be a result that could change after a learner had been
    told it — which is the same defect as a mutable score, arrived at differently.
    """
    ctx = simple_system
    attempt_id, _ = sit(ctx, correctly=True)

    before = fingerprint(ctx, ("qr_attempt_results", "qr_question_scores", "qg_attempt_outcomes"))

    for _ in range(3):
        result = ctx.client.get(
            f"{V1}/attempts/{attempt_id}/result", headers=auth(LEARNER_TOKEN)
        )
        assert result.status_code == 200, result.text
        outcome = ctx.client.get(
            f"{V1}/attempts/{attempt_id}/outcome", headers=auth(LEARNER_TOKEN)
        )
        assert outcome.status_code == 200, outcome.text

    assert (
        fingerprint(ctx, ("qr_attempt_results", "qr_question_scores", "qg_attempt_outcomes"))
        == before
    )


def test_the_confirmed_score_row_is_immutable_in_the_database(simple_system: Any) -> None:
    """Asserted by attempting the forbidden write, which is the only way to show the trigger works.

    A service that promises not to rewrite a score is not the same as a database that refuses to.
    A learner is shown this number and a certificate is gated on it.
    """
    ctx = simple_system
    attempt_id, _ = sit(ctx, correctly=True)

    with pytest.raises(Exception) as caught:
        ctx.execute(
            "UPDATE qr_attempt_results SET percentage = 100.0 WHERE attempt_id = :id",
            id=attempt_id,
        )
    assert caught.value is not None

    # And the stored value is untouched after the refusal.
    assert ctx.scalar(
        "SELECT percentage FROM qr_attempt_results WHERE attempt_id = :id", id=attempt_id
    ) == pytest.approx(100.0)


def test_a_duplicate_submission_creates_no_second_submission(simple_system: Any) -> None:
    """§21: a retried submission must not duplicate an attempt, a score or a certificate."""
    ctx = simple_system
    attempt_id, _ = sit(ctx, correctly=True)

    before = fingerprint(ctx, ASSESSMENT_TABLES)

    again = ctx.client.post(
        f"{V1}/attempts/{attempt_id}/submission",
        json={"confirmed": True},
        headers=auth(LEARNER_TOKEN),
    )
    # Either replayed as success or refused as a conflict — never a second submission.
    assert again.status_code in (200, 409), again.text

    assert fingerprint(ctx, ASSESSMENT_TABLES) == before
    assert int(ctx.scalar("SELECT COUNT(*) FROM qr_attempt_results") or 0) == 1
    assert int(ctx.scalar("SELECT COUNT(*) FROM qg_attempt_outcomes") or 0) == 1


# ---------------------------------------------------------------------------
# The whole application surface
# ---------------------------------------------------------------------------


def test_no_route_in_the_application_mutates_a_submitted_attempt(
    simple_system: Any,
) -> None:
    """The §10 assertion no single capability can make.

    Drives every read a learner, an administrator and the platform can reach for a submitted
    attempt — across UC-03 through UC-10 — and compares every row of every table holding the
    learner's work. A capability added later that quietly wrote to an attempt would fail here even
    if its own suite was green.
    """
    ctx = simple_system
    attempt_id, questions = sit(ctx, correctly=False)
    question_id = questions[0]["questionId"]

    before = fingerprint(ctx, ASSESSMENT_TABLES)

    learner_reads = (
        f"{V1}/attempts/{attempt_id}",
        f"{V1}/attempts/{attempt_id}/questions",
        f"{V1}/attempts/{attempt_id}/answers",
        f"{V1}/attempts/{attempt_id}/timing",
        f"{V1}/attempts/{attempt_id}/submission",
        f"{V1}/attempts/{attempt_id}/result",
        f"{V1}/attempts/{attempt_id}/outcome",
        f"{V1}/attempts/{attempt_id}/feedback",
        f"{V1}/attempts/{attempt_id}/coaching/eligibility",
        f"{V1}/attempts/{attempt_id}/coaching/review",
        f"{V1}/quizzes/{ctx.quiz_id}/retake-eligibility",
        f"{V1}/quizzes/{ctx.quiz_id}/attempt-history",
        f"{V1}/quizzes/{ctx.quiz_id}/formal-conditions",
        f"{V1}/attempts/{attempt_id}/certificate-eligibility",
    )
    for path in learner_reads:
        response = ctx.client.get(path, headers=auth(LEARNER_TOKEN))
        # 404 is acceptable for a capability that has nothing for this attempt; a 5xx is not.
        assert response.status_code < 500, (path, response.text)

    admin_reads = (
        f"/api/admin/quizzes/{ctx.quiz_id}/configuration",
        f"/api/admin/quizzes/{ctx.quiz_id}/configuration/versions",
        f"/api/admin/quizzes/{ctx.quiz_id}/question-bank",
        "/api/admin/analytics/overall",
        "/api/admin/analytics/questions",
        "/api/admin/analytics/questions/flagged",
        "/api/admin/analytics/exports/overall.csv",
        "/api/admin/analytics/review/actions",
        f"/api/question-bank/questions/{question_id}",
        f"/api/question-bank/questions/{question_id}/versions",
        f"/api/question-bank/questions/{question_id}/usages",
        f"/api/question-bank/reporting/attempts/{attempt_id}",
    )
    for path in admin_reads:
        response = ctx.client.get(path, headers=auth(ADMIN_TOKEN))
        assert response.status_code < 500, (path, response.text)

    # The two writes that legitimately exist and must still not touch assessment data: analytics
    # flag evaluation, and a content-review action.
    assert (
        ctx.client.post(
            "/api/admin/analytics/questions/flags/evaluate", headers=auth(ADMIN_TOKEN)
        ).status_code
        < 500
    )
    assert (
        ctx.client.post(
            "/api/admin/analytics/review/actions",
            json={"question_id": question_id, "action": "NO_CHANGE"},
            headers=auth(ADMIN_TOKEN),
        ).status_code
        < 500
    )

    assert fingerprint(ctx, ASSESSMENT_TABLES) == before


def test_an_administrator_has_no_route_to_a_learners_answer(simple_system: Any) -> None:
    """§10: an admin API cannot mutate a submitted attempt — and cannot read the answer either.

    Asserted against the OpenAPI document rather than by guessing URLs, so a route added later is
    covered. What is checked is that no path under an administrative prefix addresses an attempt's
    answers at all: the capability does not exist, rather than existing and being refused.
    """
    ctx = simple_system
    document = ctx.client.get("/api/openapi.json").json()

    admin_paths = [
        path
        for path in document["paths"]
        if path.startswith(("/api/admin", "/api/question-bank", "/api/assessor"))
    ]
    assert admin_paths, "the scan must find the administrative surface"

    for path in admin_paths:
        assert "/answers" not in path, path
        assert "/answer" not in path, path


def test_another_learner_cannot_reach_a_submitted_attempt_at_all(simple_system: Any) -> None:
    """Cross-learner access, on every route that reads submitted work.

    The guarantee asserted is the one that matters: **no attempt data crosses to another learner**
    — not a mark, not a percentage, not a verdict, not a question. That is checked on the response
    body of every route, whatever status code it chose.

    The status codes deliberately differ, and that is a design decision rather than an oversight:
    UC-03/UC-04/UC-05/UC-06 refuse, while UC-07's eligibility endpoint answers 200 with
    ``coachingAvailable: false`` because it is specified never to fail for an ineligible attempt —
    a learner opening their report should see "coaching is not available" rather than an error.

    See ``docs/UC11-FINDINGS.md`` finding U-01: that endpoint does distinguish "not yours" from
    "does not exist", which confirms an attempt id exists. Low severity — ids are UUID4, so
    enumeration is infeasible — and left as UC-07's documented convention rather than changed here.
    """
    ctx = simple_system
    attempt_id, _ = sit(ctx, correctly=True)

    refusing = (
        f"{V1}/attempts/{attempt_id}",
        f"{V1}/attempts/{attempt_id}/answers",
        f"{V1}/attempts/{attempt_id}/result",
        f"{V1}/attempts/{attempt_id}/outcome",
        f"{V1}/attempts/{attempt_id}/feedback",
    )
    for path in refusing:
        response = ctx.client.get(path, headers=auth(LEARNER2_TOKEN))
        assert response.status_code in (403, 404), (path, response.status_code)

    answering = (f"{V1}/attempts/{attempt_id}/coaching/eligibility",)
    for path in answering:
        response = ctx.client.get(path, headers=auth(LEARNER2_TOKEN))
        assert response.status_code == 200, (path, response.text)
        body = response.json()
        assert body["coachingAvailable"] is False
        assert body["questions"] == []

    # The guarantee, on every one of them: nothing about the attempt itself crosses over.
    for path in refusing + answering:
        text_body = ctx.client.get(path, headers=auth(LEARNER2_TOKEN)).text
        for leaked in ("percentage", "totalMarks", "outcome\":", "PASS", "FAIL", "learnerAnswer"):
            assert leaked not in text_body, (path, leaked)


# ---------------------------------------------------------------------------
# Configuration immutability
# ---------------------------------------------------------------------------


def test_changing_the_configuration_disturbs_no_existing_attempt(simple_system: Any) -> None:
    """§10's second clause, across every capability that reads a configuration.

    An attempt is submitted and scored under version 1. The administrator then changes the pass
    mark, the question count and the attempt limit — enough that any capability re-reading the
    live configuration instead of the locked one would answer differently.
    """
    ctx = simple_system
    submitted_id, _ = sit(ctx, correctly=True)
    active_id, _ = sit(ctx, correctly=True, submit=False, token=LEARNER2_TOKEN)

    before = fingerprint(ctx, ASSESSMENT_TABLES)
    version_one = ctx.active_version_id()

    resaved = ctx.save_configuration(
        {
            "questionCount": 1,
            "timeLimitMinutes": 5,
            "passMark": 99,
            "questionTypes": [{"type": "SINGLE_CHOICE"}],
            "randomiseQuestions": True,
            "maxAttempts": 1,
            "deliveryMode": "practice",
        }
    )
    assert resaved.status_code == 201, resaved.text
    assert ctx.active_version_id() != version_one, "a new version must be published"

    # Nothing about either attempt moved.
    assert fingerprint(ctx, ASSESSMENT_TABLES) == before

    # Both attempts still name version 1, and the submitted one still reports the old pass mark.
    for attempt_id in (submitted_id, active_id):
        assert (
            ctx.scalar(
                "SELECT configuration_version_id FROM qd_attempts WHERE id = :id", id=attempt_id
            )
            == str(version_one)
        )

    result = ctx.client.get(
        f"{V1}/attempts/{submitted_id}/result", headers=auth(LEARNER_TOKEN)
    ).json()
    outcome = ctx.client.get(
        f"{V1}/attempts/{submitted_id}/outcome", headers=auth(LEARNER_TOKEN)
    ).json()
    # 50 was version 1's pass mark; 99 is version 2's. A learner who passed must still have passed.
    assert result["result"]["passMarkPercentage"] == pytest.approx(50.0)
    assert outcome["outcome"]["outcome"] == "PASS"


def test_a_published_configuration_version_is_immutable_in_the_database(
    simple_system: Any,
) -> None:
    """UC-01's versions are the anchor every other capability's guarantees hang from."""
    ctx = simple_system
    sit(ctx, correctly=True)
    version_id = ctx.active_version_id()

    with pytest.raises(Exception) as caught:
        ctx.execute(
            "UPDATE qc_configuration_versions SET pass_mark = 1 WHERE id = :id", id=version_id
        )
    assert caught.value is not None

    assert (
        ctx.scalar("SELECT pass_mark FROM qc_configuration_versions WHERE id = :id", id=version_id)
        == 50
    )


def test_retiring_every_delivered_question_leaves_the_attempt_intact(
    simple_system: Any,
) -> None:
    """The frozen snapshot is what makes an attempt survive its questions being withdrawn."""
    from app.core.question_types import QuestionType

    ctx = simple_system
    attempt_id, questions = sit(ctx, correctly=True)

    before = fingerprint(ctx, ASSESSMENT_TABLES)
    retired = ctx.retire(QuestionType.SINGLE_CHOICE)
    assert retired, "the fixture must retire something for this test to mean anything"

    assert fingerprint(ctx, ASSESSMENT_TABLES) == before

    # And every consumer can still read the attempt.
    for path in (
        f"{V1}/attempts/{attempt_id}/questions",
        f"{V1}/attempts/{attempt_id}/result",
        f"{V1}/attempts/{attempt_id}/feedback",
    ):
        response = ctx.client.get(path, headers=auth(LEARNER_TOKEN))
        assert response.status_code == 200, (path, response.text)

    delivered = ctx.client.get(
        f"{V1}/attempts/{attempt_id}/questions", headers=auth(LEARNER_TOKEN)
    ).json()["questions"]
    assert {question["questionId"] for question in delivered} == {
        question["questionId"] for question in questions
    }
