"""The whole system, end to end, over HTTP with every real adapter.

    configure (UC-01) -> question bank (UC-02) -> attempt (UC-03) -> submit
        -> score (UC-04) -> pass/fail + certificate + CPD (UC-05) -> feedback (UC-06)

Nothing is faked here. The app is the real application, the question bank is a real seeded bank, the
configuration is saved through UC-01's real endpoint, the attempt is started and answered through
UC-03's real endpoints, and the result chain runs where it runs in production: inside UC-03's
submission hand-off. The suites under ``tests/scoring``, ``tests/certification`` and
``tests/feedback`` test the rules and the failure paths; this one tests that the seams line up.

The correct answers are read from the question bank's own rows, because the learner API deliberately
strips them -- which is itself worth having a test walk past.
"""

from __future__ import annotations

from typing import Any

from app.modules.question_bank.models import Question
from tests.harness import ADMIN_TOKEN, LEARNER2_TOKEN, LEARNER_TOKEN, Ctx, auth

# A configuration the seeded test bank can satisfy, across four of the five question types.
MIXED_CONFIGURATION: dict[str, Any] = {
    "questionCount": 4,
    "timeLimitMinutes": 30,
    "passMark": 60,
    "questionTypes": [
        {"type": "SINGLE_CHOICE", "quota": 1},
        {"type": "TRUE_FALSE", "quota": 1},
        {"type": "MULTI_SELECT", "quota": 1},
        {"type": "DRAG_TO_ORDER", "quota": 1},
    ],
    "randomiseQuestions": False,
    "maxAttempts": 3,
    "deliveryMode": "assessment",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _answer_for(ctx: Ctx, question: dict[str, Any], *, correctly: bool) -> Any:
    """Build an answer payload for a delivered question, from the bank's own answer key.

    The delivered question carries no correctness -- UC-03's presenter strips it -- so this reads
    the key from ``qb_question_options``. That is the honest way for a test to know the right
    answer, and it doubles as a check that the learner payload really does not contain one.
    """
    assert "isCorrect" not in str(question), "the learner payload must not carry the answer key"

    question_type = question["questionType"]
    with ctx.session() as session:
        row = session.get(Question, question["questionId"])
        assert row is not None
        options = sorted(row.options, key=lambda option: option.position)
        correct = [option.label for option in options if option.is_correct]
        primary = next(
            (option.label for option in options if option.is_primary),
            correct[0] if correct else None,
        )
        ordered = [
            option.label
            for option in sorted(
                (item for item in options if item.correct_position is not None),
                key=lambda item: item.correct_position or 0,
            )
        ]
        wrong = [option.label for option in options if not option.is_correct]

    if question_type == "SINGLE_CHOICE":
        chosen = correct[0] if correctly else wrong[0]
        return {"type": "SINGLE_CHOICE", "selectedOptionId": chosen}

    if question_type == "TRUE_FALSE":
        truth = correct[0].upper() == "TRUE"
        return {"type": "TRUE_FALSE", "value": truth if correctly else not truth}

    if question_type == "MULTI_SELECT":
        chosen = correct if correctly else wrong[:1]
        return {"type": "MULTI_SELECT", "selectedOptionIds": sorted(chosen)}

    if question_type == "SCENARIO":
        chosen = primary if correctly else wrong[0]
        return {
            "type": "SCENARIO",
            "responses": [
                {
                    "subQuestionId": question["subQuestions"][0]["subQuestionId"],
                    "answer": {"type": "SINGLE_CHOICE", "selectedOptionId": chosen},
                }
            ],
        }

    if question_type == "DRAG_TO_ORDER":
        sequence = ordered if correctly else list(reversed(ordered))
        return {"type": "DRAG_TO_ORDER", "orderedItemIds": sequence}

    raise AssertionError(f"unsupported question type {question_type}")


def _sit_quiz(
    ctx: Ctx, *, correctly: bool = True, token: str = LEARNER_TOKEN
) -> tuple[str, dict[str, Any]]:
    """Start an attempt, answer every question, submit it. Returns the attempt id and the
    response."""
    attempt_id, questions = ctx.start_and_read_questions(token)

    for question in questions:
        saved = ctx.client.put(
            f"/api/v1/attempts/{attempt_id}/questions/{question['questionId']}/answer",
            json={"response": _answer_for(ctx, question, correctly=correctly), "source": "MANUAL"},
            headers=auth(token),
        )
        assert saved.status_code == 200, saved.text

    submitted = ctx.client.post(
        f"/api/v1/attempts/{attempt_id}/submission",
        json={"confirmed": True},
        headers=auth(token),
    )
    assert submitted.status_code == 200, submitted.text
    return attempt_id, submitted.json()


def _result(ctx: Ctx, attempt_id: str, token: str = LEARNER_TOKEN) -> dict[str, Any]:
    response = ctx.client.get(f"/api/v1/attempts/{attempt_id}/result", headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


def _outcome(ctx: Ctx, attempt_id: str, token: str = LEARNER_TOKEN) -> dict[str, Any]:
    response = ctx.client.get(f"/api/v1/attempts/{attempt_id}/outcome", headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


def _feedback(ctx: Ctx, attempt_id: str, token: str = LEARNER_TOKEN) -> dict[str, Any]:
    response = ctx.client.get(f"/api/v1/attempts/{attempt_id}/feedback", headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# The whole flow
# ---------------------------------------------------------------------------


class TestTheCompleteFlow:
    def test_configure_attempt_submit_score_pass_and_feedback(self, ctx: Ctx) -> None:
        """One test that walks the entire documented flow and checks each hand-over."""
        saved = ctx.save_configuration(MIXED_CONFIGURATION)
        assert saved.status_code == 201, saved.text
        version_id = saved.json()["configuration"]["id"]

        attempt_id, submission = _sit_quiz(ctx, correctly=True)

        # UC-03 reports the hand-off; the chain ran inside it.
        assert submission["attempt"]["status"] == "SUBMITTED"

        # ---- UC-04 -------------------------------------------------------
        result = _result(ctx, attempt_id)["result"]
        assert result["status"] == "SCORED"
        assert result["statusLabel"] == "Scored"
        assert result["percentage"] == 100.0
        assert result["totalMarks"] == result["maximumMarks"]
        assert result["correctCount"] == 4
        assert result["incorrectCount"] == 0
        assert result["unansweredCount"] == 0
        assert result["timeTakenSeconds"] is not None
        # The version the attempt locked, not the quiz's current pointer.
        assert result["configurationVersionId"] == str(version_id)

        scores = _result(ctx, attempt_id)["questionScores"]
        assert len(scores) == 4
        assert {score["outcome"] for score in scores} == {"CORRECT"}
        assert all(score["answerKeySource"] == "QUESTION_BANK_SNAPSHOT" for score in scores)

        # ---- UC-05 -------------------------------------------------------
        outcome = _outcome(ctx, attempt_id)
        assert outcome["outcome"]["outcome"] == "PASS"
        assert outcome["outcome"]["passMarkPercentage"] == 60.0
        assert outcome["outcome"]["certificateRequired"] is True
        assert outcome["certificate"]["status"] == "ISSUED"
        assert outcome["certificate"]["certificateNumber"]
        assert outcome["certificate"]["courseName"] == "Test Course"
        assert outcome["cpd"]["status"] == "SYNCHRONISED"
        assert outcome["cpd"]["passed"] is True
        assert outcome["cpd"]["scorePercentage"] == 100.0

        # ---- UC-06 -------------------------------------------------------
        feedback = _feedback(ctx, attempt_id)
        assert feedback["status"] == "GENERATED"
        assert feedback["summary"]["passed"] is True
        assert feedback["summary"]["percentage"] == 100.0
        assert feedback["summary"]["correctCount"] == 4
        assert len(feedback["items"]) == 4

        for item in feedback["items"]:
            assert item["question"], "every item names the question"
            assert item["learnerAnswer"], "every item shows what the learner answered"
            assert item["correctAnswer"], "every item shows the correct answer"
            # The seeded bank authors an explanation and a topic for every question, so no item here
            # should be falling back.
            assert item["explanation"] and not item["explanation"].startswith("No explanation")
            assert item["lessonReference"].startswith("Topic:")
            assert item["questionScore"] == item["maximumMarks"]

        multi = next(item for item in feedback["items"] if item["questionType"] == "MULTI_SELECT")
        assert multi["optionBreakdown"], "a multi-select reports every option"
        assert {entry["correct"] for entry in multi["optionBreakdown"]} == {True, False}
        assert any(entry["markContribution"] > 0 for entry in multi["optionBreakdown"])

    def test_a_failing_attempt_is_reported_as_a_fail_with_attempts_remaining(
        self, ctx: Ctx
    ) -> None:
        assert ctx.save_configuration(MIXED_CONFIGURATION).status_code == 201

        attempt_id, _submission = _sit_quiz(ctx, correctly=False)

        result = _result(ctx, attempt_id)["result"]
        assert result["status"] == "SCORED"
        assert result["percentage"] == 0.0

        outcome = _outcome(ctx, attempt_id)
        assert outcome["outcome"]["outcome"] == "FAIL"
        assert outcome["certificate"] is None
        assert outcome["attemptsUsed"] == 1
        assert outcome["maxAttempts"] == 3
        assert outcome["attemptsRemaining"] == 2
        assert outcome["mayReattempt"] is True

        # A CPD record is still written: it logs the activity, not the achievement.
        assert outcome["cpd"]["status"] == "SYNCHRONISED"
        assert outcome["cpd"]["passed"] is False

        feedback = _feedback(ctx, attempt_id)
        assert feedback["summary"]["passed"] is False
        # Feedback still shows the correct answers, which is the point of a failed attempt's report.
        assert all(item["correctAnswer"] for item in feedback["items"])

    def test_an_unanswered_question_scores_zero_and_is_reported(self, ctx: Ctx) -> None:
        assert ctx.save_configuration(MIXED_CONFIGURATION).status_code == 201
        attempt_id, questions = ctx.start_and_read_questions()

        # Answer all but the last question.
        for question in questions[:-1]:
            saved = ctx.client.put(
                f"/api/v1/attempts/{attempt_id}/questions/{question['questionId']}/answer",
                json={"response": _answer_for(ctx, question, correctly=True), "source": "MANUAL"},
                headers=auth(LEARNER_TOKEN),
            )
            assert saved.status_code == 200, saved.text

        submitted = ctx.client.post(
            f"/api/v1/attempts/{attempt_id}/submission",
            json={"confirmed": True},
            headers=auth(LEARNER_TOKEN),
        )
        assert submitted.status_code == 200, submitted.text

        result = _result(ctx, attempt_id)["result"]
        assert result["unansweredCount"] == 1
        assert result["totalMarks"] < result["maximumMarks"]

        feedback = _feedback(ctx, attempt_id)
        blank = next(item for item in feedback["items"] if not item["answered"])
        assert blank["questionScore"] == 0.0
        assert blank["learnerAnswer"]["summary"] == "No answer given."


# ---------------------------------------------------------------------------
# Idempotency and retry, over HTTP
# ---------------------------------------------------------------------------


class TestIdempotencyOverHttp:
    def test_scoring_the_same_attempt_again_replays_the_stored_result(self, ctx: Ctx) -> None:
        assert ctx.save_configuration(MIXED_CONFIGURATION).status_code == 201
        attempt_id, _ = _sit_quiz(ctx)

        first = ctx.client.post(
            f"/api/v1/attempts/{attempt_id}/result", headers=auth(LEARNER_TOKEN)
        )
        assert first.status_code == 200, first.text
        assert first.json()["replayed"] is True
        assert first.json()["result"]["scoringAttemptCount"] == 1

        second = ctx.client.post(
            f"/api/v1/attempts/{attempt_id}/result", headers=auth(LEARNER_TOKEN)
        )
        assert second.json()["result"]["scoredAt"] == first.json()["result"]["scoredAt"]
        assert ctx.scalar("SELECT COUNT(*) FROM qr_attempt_results") == 1

    def test_determining_the_outcome_again_returns_the_same_verdict(self, ctx: Ctx) -> None:
        assert ctx.save_configuration(MIXED_CONFIGURATION).status_code == 201
        attempt_id, _ = _sit_quiz(ctx)

        again = ctx.client.post(
            f"/api/v1/attempts/{attempt_id}/outcome", headers=auth(LEARNER_TOKEN)
        )
        assert again.status_code == 200, again.text
        assert again.json()["created"] is False
        assert again.json()["outcome"]["outcome"] == "PASS"
        assert ctx.scalar("SELECT COUNT(*) FROM qg_attempt_outcomes") == 1

    def test_generating_feedback_again_replays_the_frozen_report(self, ctx: Ctx) -> None:
        assert ctx.save_configuration(MIXED_CONFIGURATION).status_code == 201
        attempt_id, _ = _sit_quiz(ctx)

        again = ctx.client.post(
            f"/api/v1/attempts/{attempt_id}/feedback", headers=auth(LEARNER_TOKEN)
        )
        assert again.status_code == 200, again.text
        assert again.json()["replayed"] is True
        assert ctx.scalar("SELECT COUNT(*) FROM qf_feedback_reports") == 1
        assert ctx.scalar("SELECT COUNT(*) FROM qf_feedback_items") == 4

    def test_retrying_an_issued_certificate_does_not_issue_a_second(self, ctx: Ctx) -> None:
        assert ctx.save_configuration(MIXED_CONFIGURATION).status_code == 201
        attempt_id, _ = _sit_quiz(ctx)
        issued = _outcome(ctx, attempt_id)["certificate"]["certificateNumber"]

        retried = ctx.client.post(
            f"/api/v1/attempts/{attempt_id}/outcome/certificate/retry", headers=auth(LEARNER_TOKEN)
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["certificate"]["certificateNumber"] == issued
        assert ctx.scalar("SELECT COUNT(*) FROM qg_certificates WHERE status = 'ISSUED'") == 1

    def test_retrying_cpd_does_not_log_a_second_activity(self, ctx: Ctx) -> None:
        assert ctx.save_configuration(MIXED_CONFIGURATION).status_code == 201
        attempt_id, _ = _sit_quiz(ctx)

        retried = ctx.client.post(
            f"/api/v1/attempts/{attempt_id}/outcome/cpd/retry", headers=auth(LEARNER_TOKEN)
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["cpd"]["status"] == "SYNCHRONISED"
        assert ctx.scalar("SELECT COUNT(*) FROM qg_cpd_records") == 1

    def test_passing_a_second_time_does_not_mint_a_second_certificate(self, ctx: Ctx) -> None:
        """One issued certificate per learner and quiz, through the real chain."""
        assert ctx.save_configuration(MIXED_CONFIGURATION).status_code == 201

        first_attempt, _ = _sit_quiz(ctx)
        assert _outcome(ctx, first_attempt)["certificate"]["status"] == "ISSUED"

        second_attempt, _ = _sit_quiz(ctx)
        second = _outcome(ctx, second_attempt)

        assert second["outcome"]["outcome"] == "PASS"
        assert second["certificate"]["status"] == "FAILED"
        assert second["certificate"]["failureCode"] == "CERTIFICATE_ALREADY_ISSUED"
        assert ctx.scalar("SELECT COUNT(*) FROM qg_certificates WHERE status = 'ISSUED'") == 1


# ---------------------------------------------------------------------------
# Historical integrity
# ---------------------------------------------------------------------------


class TestHistoricalIntegrity:
    def test_reconfiguring_the_quiz_cannot_change_a_recorded_result(self, ctx: Ctx) -> None:
        """The pass mark that judged the attempt is the one its own version carried."""
        assert ctx.save_configuration(MIXED_CONFIGURATION).status_code == 201
        attempt_id, _ = _sit_quiz(ctx, correctly=True)

        before_result = _result(ctx, attempt_id)["result"]
        before_outcome = _outcome(ctx, attempt_id)["outcome"]
        assert before_outcome["passMarkPercentage"] == 60.0

        # The administrator raises the pass mark to 100 and shortens the quiz -- a new version.
        reconfigured = ctx.save_configuration({**MIXED_CONFIGURATION, "passMark": 100})
        assert reconfigured.status_code == 201, reconfigured.text
        assert ctx.version_count() == 2

        after_result = _result(ctx, attempt_id)["result"]
        after_outcome = _outcome(ctx, attempt_id)["outcome"]

        assert after_result == before_result
        assert after_outcome == before_outcome
        assert after_outcome["passMarkPercentage"] == 60.0

        # Re-driving each stage changes nothing either.
        assert (
            ctx.client.post(
                f"/api/v1/attempts/{attempt_id}/result", headers=auth(LEARNER_TOKEN)
            ).json()["result"]
            == before_result
        )
        assert (
            ctx.client.post(
                f"/api/v1/attempts/{attempt_id}/outcome", headers=auth(LEARNER_TOKEN)
            ).json()["outcome"]
            == before_outcome
        )

    def test_editing_or_retiring_a_question_cannot_change_a_generated_report(
        self, ctx: Ctx
    ) -> None:
        assert ctx.save_configuration(MIXED_CONFIGURATION).status_code == 201
        attempt_id, _ = _sit_quiz(ctx, correctly=True)

        before = _feedback(ctx, attempt_id)
        delivered_ids = [item["questionId"] for item in before["items"]]

        # Edit one delivered question's explanation and retire another, through UC-02's real API.
        update = ctx.client.patch(
            f"/api/question-bank/questions/{delivered_ids[0]}",
            json={"explanation": "A completely rewritten explanation."},
            headers=auth(ADMIN_TOKEN),
        )
        assert update.status_code == 200, update.text
        # The live question now says something different from what the learner was shown. Whether
        # UC-02 treats an explanation edit as a new version is its business; either way the report
        # below must not move.
        assert update.json()["explanation"] == "A completely rewritten explanation."

        retire = ctx.client.post(
            f"/api/question-bank/questions/{delivered_ids[1]}/retire",
            json={"reason": "superseded"},
            headers=auth(ADMIN_TOKEN),
        )
        assert retire.status_code == 200, retire.text

        after = _feedback(ctx, attempt_id)

        assert after == before, "a generated report is served exactly as it was generated"
        # And re-driving generation replays rather than rebuilding.
        again = ctx.client.post(
            f"/api/v1/attempts/{attempt_id}/feedback", headers=auth(LEARNER_TOKEN)
        )
        assert again.json()["replayed"] is True
        assert again.json()["items"] == before["items"]

    def test_the_result_survives_the_configuration_version_being_superseded(self, ctx: Ctx) -> None:
        """UC-04 froze the pass mark, so nothing has to re-read a superseded version."""
        assert ctx.save_configuration(MIXED_CONFIGURATION).status_code == 201
        attempt_id, _ = _sit_quiz(ctx)
        original = _result(ctx, attempt_id)["result"]

        for pass_mark in (70, 80, 90):
            assert (
                ctx.save_configuration({**MIXED_CONFIGURATION, "passMark": pass_mark}).status_code
                == 201
            )

        assert _result(ctx, attempt_id)["result"] == original
        assert _outcome(ctx, attempt_id)["outcome"]["passMarkPercentage"] == 60.0


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


class TestAccess:
    def test_another_learner_cannot_read_the_result_the_outcome_or_the_feedback(
        self, ctx: Ctx
    ) -> None:
        assert ctx.save_configuration(MIXED_CONFIGURATION).status_code == 201
        attempt_id, _ = _sit_quiz(ctx, token=LEARNER_TOKEN)

        for path in ("result", "outcome", "feedback"):
            response = ctx.client.get(
                f"/api/v1/attempts/{attempt_id}/{path}", headers=auth(LEARNER2_TOKEN)
            )
            assert response.status_code == 404, f"{path}: {response.text}"
            assert response.json()["error"]["code"] in {
                "ATTEMPT_NOT_FOUND",
                "RESULT_NOT_FOUND",
                "OUTCOME_NOT_FOUND",
                "FEEDBACK_NOT_FOUND",
            }

    def test_an_administrator_token_is_not_a_learner(self, ctx: Ctx) -> None:
        assert ctx.save_configuration(MIXED_CONFIGURATION).status_code == 201
        attempt_id, _ = _sit_quiz(ctx)

        response = ctx.client.get(
            f"/api/v1/attempts/{attempt_id}/result", headers=auth(ADMIN_TOKEN)
        )
        assert response.status_code == 403, response.text

    def test_the_learner_can_list_their_results_outcomes_and_reports(self, ctx: Ctx) -> None:
        assert ctx.save_configuration(MIXED_CONFIGURATION).status_code == 201
        _sit_quiz(ctx)

        for path, key in (
            ("/api/v1/results", "results"),
            ("/api/v1/outcomes", "outcomes"),
            ("/api/v1/feedback", "reports"),
        ):
            response = ctx.client.get(path, headers=auth(LEARNER_TOKEN))
            assert response.status_code == 200, response.text
            assert response.json()["total"] == 1
            assert len(response.json()[key]) == 1


# ---------------------------------------------------------------------------
# The lifecycle guards
# ---------------------------------------------------------------------------


class TestLifecycleGuards:
    def test_an_attempt_in_progress_has_no_result_outcome_or_feedback(self, ctx: Ctx) -> None:
        assert ctx.save_configuration(MIXED_CONFIGURATION).status_code == 201
        created = ctx.start_attempt()
        attempt_id = created.json()["attempt"]["attemptId"]

        for path in ("result", "outcome", "feedback"):
            response = ctx.client.get(
                f"/api/v1/attempts/{attempt_id}/{path}", headers=auth(LEARNER_TOKEN)
            )
            assert response.status_code == 404, f"{path}: {response.text}"

        scored = ctx.client.post(
            f"/api/v1/attempts/{attempt_id}/result", headers=auth(LEARNER_TOKEN)
        )
        assert scored.status_code == 409
        assert scored.json()["error"]["code"] == "ATTEMPT_NOT_SUBMITTED"

    def test_the_chain_writes_nothing_for_an_attempt_that_was_never_submitted(
        self, ctx: Ctx
    ) -> None:
        assert ctx.save_configuration(MIXED_CONFIGURATION).status_code == 201
        ctx.start_attempt()

        assert ctx.scalar("SELECT COUNT(*) FROM qr_attempt_results") == 0
        assert ctx.scalar("SELECT COUNT(*) FROM qg_attempt_outcomes") == 0
        assert ctx.scalar("SELECT COUNT(*) FROM qf_feedback_reports") == 0

    def test_a_configuration_that_cannot_be_delivered_never_reaches_the_chain(
        self, ctx: Ctx
    ) -> None:
        """UC-01 refuses the configuration, so there is no attempt and nothing to score."""
        refused = ctx.save_configuration({**MIXED_CONFIGURATION, "questionCount": 500})
        assert refused.status_code == 422, refused.text
        assert ctx.attempt_count() == 0
        assert ctx.scalar("SELECT COUNT(*) FROM qr_attempt_results") == 0


# ---------------------------------------------------------------------------
# One record of each thing
# ---------------------------------------------------------------------------


class TestNoDuplicateRecords:
    def test_the_chain_produces_exactly_one_row_of_each_kind_per_attempt(self, ctx: Ctx) -> None:
        assert ctx.save_configuration(MIXED_CONFIGURATION).status_code == 201
        attempt_id, _ = _sit_quiz(ctx)

        # Re-drive every stage several times, over HTTP.
        for _ in range(3):
            ctx.client.post(f"/api/v1/attempts/{attempt_id}/result", headers=auth(LEARNER_TOKEN))
            ctx.client.post(f"/api/v1/attempts/{attempt_id}/outcome", headers=auth(LEARNER_TOKEN))
            ctx.client.post(f"/api/v1/attempts/{attempt_id}/feedback", headers=auth(LEARNER_TOKEN))

        assert ctx.scalar("SELECT COUNT(*) FROM qr_attempt_results") == 1
        assert ctx.scalar("SELECT COUNT(*) FROM qr_question_scores") == 4
        assert ctx.scalar("SELECT COUNT(*) FROM qg_attempt_outcomes") == 1
        assert ctx.scalar("SELECT COUNT(*) FROM qg_certificates") == 1
        assert ctx.scalar("SELECT COUNT(*) FROM qg_cpd_records") == 1
        assert ctx.scalar("SELECT COUNT(*) FROM qf_feedback_reports") == 1
        assert ctx.scalar("SELECT COUNT(*) FROM qf_feedback_items") == 4
        # And UC-03 still has exactly one attempt and one submission.
        assert ctx.attempt_count() == 1
        assert ctx.scalar("SELECT COUNT(*) FROM qd_attempt_submissions") == 1
