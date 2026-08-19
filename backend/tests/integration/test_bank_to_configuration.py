"""UC-01 + UC-02 as one workflow.

    Question bank
          ↓
    Available question counts   (question bank's deliverable query)
          ↓
    Quiz configuration
          ↓
    Configuration validation    (capacity)
          ↓
    Immutable configuration version
          ↓
    Ready for UC-03

Each test drives the real HTTP API of both capabilities. Nothing here reaches into a service to
shortcut a step, because the point is to prove the seam holds end to end.
"""

from __future__ import annotations

from app.core.question_types import QuestionType
from tests import factories
from tests.harness import ADMIN_TOKEN, LEARNER_TOKEN, Ctx, auth, valid_configuration

QB = "/api/question-bank"


class TestTheWholeChain:
    def test_an_empty_bank_cannot_satisfy_any_configuration(self, make_ctx) -> None:
        ctx: Ctx = make_ctx({})

        assert ctx.get_question_bank().json()["availableByType"] == {
            item.value: 0 for item in QuestionType
        }

        response = ctx.save_configuration(valid_configuration())
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "QUESTION_BANK_INSUFFICIENT"
        assert ctx.version_count() == 0

    def test_questions_created_through_the_question_bank_api_unlock_a_configuration(
        self, make_ctx
    ) -> None:
        """The full chain, one step at a time, through the public API only."""
        ctx: Ctx = make_ctx({})

        # 1. Author questions in the bank.
        for index in range(4):
            payload = factories.single_choice(
                questionText=f"Integration single-choice question {index}."
            )
            created = ctx.client.post(f"{QB}/questions", json=payload, headers=auth(ADMIN_TOKEN))
            assert created.status_code == 201, created.text

        # 2. The configuration screen sees them.
        assert ctx.get_question_bank().json()["availableByType"]["SINGLE_CHOICE"] == 4

        # 3. A configuration for more than exists is refused...
        too_big = ctx.save_configuration(
            valid_configuration(
                questionCount=5, questionTypes=[{"type": "SINGLE_CHOICE", "quota": 5}]
            )
        )
        assert too_big.status_code == 422
        assert too_big.json()["error"]["capacity"]["breakdown"][0]["shortfall"] == 1

        # 4. ...and one the bank can satisfy becomes immutable version 1.
        ok = ctx.save_configuration(
            valid_configuration(
                questionCount=4, questionTypes=[{"type": "SINGLE_CHOICE", "quota": 4}]
            )
        )
        assert ok.status_code == 201
        version = ok.json()["configuration"]
        assert version["versionNumber"] == 1
        assert version["isActive"] is True

        # 5. UC-03 can now start an attempt, and it gets exactly those questions.
        _attempt_id, questions = ctx.start_and_read_questions()
        assert len(questions) == 4

    def test_a_csv_import_feeds_capacity(self, make_ctx) -> None:
        """Bulk-imported questions are ordinary bank questions as far as UC-01 is concerned."""
        ctx: Ctx = make_ctx({})

        template = ctx.client.get(f"{QB}/imports/template")
        assert template.status_code == 200

        # The documented CSV format (see docs/CSV_IMPORT.md): TRUE_FALSE may leave `options`
        # blank because the TRUE/FALSE pair is implied.
        rows = [
            "type,question_text,options,correct_answers,explanation,topics",
            *[
                f"TRUE_FALSE,Imported statement number {index} is correct.,,TRUE,"
                f"Because it is.,Imported"
                for index in range(1, 4)
            ],
        ]
        upload = ctx.client.post(
            f"{QB}/imports",
            files={"file": ("bank.csv", "\n".join(rows).encode("utf-8"), "text/csv")},
            headers=auth(ADMIN_TOKEN),
        )
        assert upload.status_code in (200, 201), upload.text
        assert upload.json()["importedRows"] == 3, upload.text

        assert ctx.get_question_bank().json()["availableByType"]["TRUE_FALSE"] == 3

        saved = ctx.save_configuration(
            valid_configuration(
                questionCount=3, questionTypes=[{"type": "TRUE_FALSE", "quota": 3}]
            )
        )
        assert saved.status_code == 201, saved.text


class TestRetirementAcrossTheBoundary:
    def test_retirement_reduces_capacity_and_blocks_a_new_start(self, make_ctx) -> None:
        ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 6})
        ctx.save_configuration(
            valid_configuration(
                questionCount=6,
                maxAttempts=5,
                questionTypes=[{"type": "SINGLE_CHOICE", "quota": 6}],
            )
        )
        assert ctx.get_rules().json()["canStart"] is True

        ctx.retire(QuestionType.SINGLE_CHOICE, count=1)

        rules = ctx.get_rules().json()
        assert rules["canStart"] is False
        assert rules["blockedReason"] == "question_bank_insufficient"

        refused = ctx.start_attempt()
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["code"] == "INSUFFICIENT_QUESTIONS"
        assert ctx.attempt_count() == 0

    def test_a_retired_question_is_never_drawn_for_a_new_attempt(self, make_ctx) -> None:
        ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 10})
        retired = set(ctx.retire(QuestionType.SINGLE_CHOICE, count=5))

        ctx.save_configuration(
            valid_configuration(
                questionCount=5,
                maxAttempts=5,
                randomiseQuestions=True,
                questionTypes=[{"type": "SINGLE_CHOICE", "quota": 5}],
            )
        )

        # Randomised draws, repeated: a retired question must never appear.
        for _ in range(4):
            attempt_id, questions = ctx.start_and_read_questions()
            drawn = {question["questionId"] for question in questions}
            assert drawn.isdisjoint(retired)
            # Submitting closes the attempt so the next one may start (one open attempt per quiz).
            assert ctx.submit_attempt(attempt_id).status_code == 200

    def test_a_submitted_attempt_survives_retiring_every_question_it_used(self, make_ctx) -> None:
        """Historical integrity, guaranteed by UC-03's own frozen snapshots.

        UC-03 freezes each delivered question onto ``qd_attempt_questions``, so the attempt is
        self-contained: retiring — or even editing — every question it used cannot change what the
        learner saw.

        It *also* reports the delivery to UC-02, which keeps its own usage row. The two are not
        duplicates: UC-03's snapshot answers "what did this learner see", UC-02's usage row answers
        "has this question of mine ever been used" — and the second is what drives its usage counts,
        its refusal to hard-delete used content, and its own historical report. The test below
        covers
        that record; this one covers the learner's paper.
        """
        ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 3})
        ctx.save_configuration(
            valid_configuration(
                questionCount=3, questionTypes=[{"type": "SINGLE_CHOICE", "quota": 3}]
            )
        )

        attempt_id, questions = ctx.start_and_read_questions()
        before = [(q["questionId"], q["prompt"]) for q in questions]
        assert ctx.submit_attempt(attempt_id).status_code == 200

        # Retire every question the attempt was given, and edit one of them for good measure.
        ctx.retire(QuestionType.SINGLE_CHOICE)
        edited = ctx.client.patch(
            f"{QB}/questions/{before[0][0]}",
            json={"questionText": "Rewritten long after the attempt was submitted."},
            headers=auth(ADMIN_TOKEN),
        )
        assert edited.status_code in (200, 409), edited.text

        reloaded = ctx.attempt_questions(attempt_id)
        assert reloaded.status_code == 200, reloaded.text
        after = [(q["questionId"], q["prompt"]) for q in reloaded.json()["questions"]]
        assert after == before

    def test_delivering_a_question_protects_it_from_hard_deletion(self, make_ctx) -> None:
        """UC-02's own rule, now reachable for a real attempt.

        The bank refuses to hard-delete a question it has a usage record for
        (``QUESTION_HAS_HISTORY``). That rule was unreachable while nothing reported deliveries to
        UC-02: an administrator could permanently destroy a question a learner had just been asked.
        UC-03 now reports every delivery, so the rule fires.
        """
        ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 2})
        ctx.save_configuration(
            valid_configuration(
                questionCount=2, questionTypes=[{"type": "SINGLE_CHOICE", "quota": 2}]
            )
        )
        attempt_id, questions = ctx.start_and_read_questions()
        before = [(q["questionId"], q["prompt"]) for q in questions]

        deleted = ctx.client.delete(f"{QB}/questions/{before[0][0]}", headers=auth(ADMIN_TOKEN))
        assert deleted.status_code == 409, deleted.text
        assert deleted.json()["error"]["code"] == "QUESTION_HAS_HISTORY"

        # And the attempt is untouched either way: its questions come from its own snapshots.
        reloaded = ctx.attempt_questions(attempt_id)
        assert reloaded.status_code == 200, reloaded.text
        assert [(q["questionId"], q["prompt"]) for q in reloaded.json()["questions"]] == before

    def test_an_attempt_is_recorded_against_the_bank_that_supplied_it(self, make_ctx) -> None:
        """The delivery record UC-02's reporting and usage counts depend on.

        Asserted through UC-02's own API rather than by reading the table, because the point is that
        UC-02 can *see* the delivery — a row nothing can read would be no better than no row.
        """
        ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 3})
        ctx.save_configuration(
            valid_configuration(
                questionCount=3, questionTypes=[{"type": "SINGLE_CHOICE", "quota": 3}]
            )
        )
        attempt_id, questions = ctx.start_and_read_questions()

        for position, question in enumerate(questions, start=1):
            usages = ctx.client.get(
                f"{QB}/questions/{question['questionId']}/usages", headers=auth(ADMIN_TOKEN)
            )
            assert usages.status_code == 200, usages.text
            rows = usages.json()
            mine = [row for row in rows if row["attemptRef"] == attempt_id]
            assert len(mine) == 1, f"expected one usage for {question['questionId']}, got {rows}"
            # The bank is told the delivered position, so its report can render the paper in order.
            assert mine[0]["deliveryPosition"] == position

        # UC-02's historical report is rendered from those rows, and is now reachable.
        report = ctx.client.get(f"{QB}/reporting/attempts/{attempt_id}", headers=auth(ADMIN_TOKEN))
        assert report.status_code == 200, report.text
        assert report.json()["questionCount"] == 3

    def test_the_delivery_record_is_not_double_counted(self, make_ctx) -> None:
        """Two attempts produce two usages per question, and one attempt produces one.

        The guard that matters is idempotency per attempt: clients retry attempt creation, and a
        double-counted usage would silently corrupt UC-02's reporting.
        """
        ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 2})
        ctx.save_configuration(
            valid_configuration(
                questionCount=2,
                maxAttempts=2,
                questionTypes=[{"type": "SINGLE_CHOICE", "quota": 2}],
            )
        )

        first_id, questions = ctx.start_and_read_questions()
        # A refused duplicate creation must not add a second set of usages.
        assert ctx.start_attempt().status_code == 409
        assert ctx.submit_attempt(first_id).status_code == 200

        question_id = questions[0]["questionId"]
        after_one = ctx.client.get(
            f"{QB}/questions/{question_id}/usages", headers=auth(ADMIN_TOKEN)
        ).json()
        assert len([row for row in after_one if row["attemptRef"] == first_id]) == 1

        second_id, _ = ctx.start_and_read_questions()
        after_two = ctx.client.get(
            f"{QB}/questions/{question_id}/usages", headers=auth(ADMIN_TOKEN)
        ).json()
        assert {row["attemptRef"] for row in after_two} == {first_id, second_id}


class TestAttemptDeliveryAcrossTheBoundary:
    """UC-03 running on UC-01's configuration and UC-02's bank."""

    def test_an_attempt_locks_uc01s_active_version_and_every_rule_on_it(self, make_ctx) -> None:
        ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 4, QuestionType.TRUE_FALSE: 4})
        saved = ctx.save_configuration(
            valid_configuration(
                questionCount=4,
                timeLimitMinutes=25,
                passMark=65,
                maxAttempts=2,
                deliveryMode="assessment",
                randomiseQuestions=True,
                questionPresentation="ALL_AT_ONCE",
                questionTypes=[
                    {"type": "SINGLE_CHOICE", "quota": 2},
                    {"type": "TRUE_FALSE", "quota": 2},
                ],
            )
        )
        assert saved.status_code == 201, saved.text
        version_id = saved.json()["configuration"]["id"]

        created = ctx.start_attempt()
        assert created.status_code == 201, created.text
        attempt = created.json()["attempt"]

        # The version UC-01 made active is the one frozen onto the attempt.
        assert attempt["configurationVersionId"] == str(version_id)
        assert attempt["learnerId"] == str(ctx.learner_id)
        assert attempt["quizId"] == str(ctx.quiz_id)
        assert attempt["totalQuestions"] == 4

        # Every rule UC-03 obeys came from UC-01, translated by the adapter.
        configuration = attempt["configuration"]
        assert configuration["timeLimitSeconds"] == 25 * 60      # UC-01 configures minutes
        assert configuration["passMarkPercentage"] == 65.0
        assert configuration["maxAttempts"] == 2
        assert configuration["randomiseQuestionOrder"] is True
        assert attempt["questionPresentation"] == "ALL_AT_ONCE"
        assert attempt["expiresAt"] is not None

        # UC-01's own delivery mode is carried through unused, so the attempt keeps a faithful
        # record of the version it ran under. It is deliberately absent from the learner-facing
        # response — the presenter copies by allow-list — so it is asserted where it is stored.
        snapshot = ctx.scalar(
            "SELECT configuration_snapshot FROM qd_attempts WHERE id = :id",
            id=attempt["attemptId"],
        )
        assert '"uc01DeliveryMode": "assessment"' in snapshot or (
            '"uc01DeliveryMode":"assessment"' in snapshot
        ), snapshot[:300]

    def test_the_paper_is_real_uc02_content_with_no_answer_key(self, make_ctx) -> None:
        ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 3})
        ctx.save_configuration(
            valid_configuration(
                questionCount=3, questionTypes=[{"type": "SINGLE_CHOICE", "quota": 3}]
            )
        )
        attempt_id, questions = ctx.start_and_read_questions()

        assert [q["position"] for q in questions] == [1, 2, 3]
        for question in questions:
            assert question["questionType"] == "SINGLE_CHOICE"
            assert question["questionVersion"] == 1
            # UC-02 authored these, so the prompt is the bank's text and options carry its labels.
            assert "OSI layer" in question["prompt"]
            assert {option["optionId"] for option in question["options"]} == {"A", "B", "C", "D"}

        raw = ctx.attempt_questions(attempt_id).text
        for leak in ("isCorrect", "correctPosition", "isPrimary"):
            assert leak not in raw

    def test_a_configuration_change_cannot_disturb_a_running_attempt(self, make_ctx) -> None:
        ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 6})
        ctx.save_configuration(
            valid_configuration(
                questionCount=3,
                passMark=50,
                timeLimitMinutes=30,
                questionTypes=[{"type": "SINGLE_CHOICE", "quota": 3}],
            )
        )
        attempt_id, questions = ctx.start_and_read_questions()
        drawn = [q["questionId"] for q in questions]

        # The administrator publishes a materially different version mid-attempt.
        changed = ctx.save_configuration(
            valid_configuration(
                questionCount=6,
                passMark=95,
                timeLimitMinutes=5,
                questionTypes=[{"type": "SINGLE_CHOICE", "quota": 6}],
            )
        )
        assert changed.status_code == 201, changed.text

        reloaded = ctx.get_attempt(attempt_id)
        assert reloaded.status_code == 200
        attempt = reloaded.json()["attempt"]
        assert attempt["configuration"]["passMarkPercentage"] == 50.0
        assert attempt["configuration"]["timeLimitSeconds"] == 30 * 60
        assert attempt["totalQuestions"] == 3
        assert [q["questionId"] for q in ctx.attempt_questions(attempt_id).json()["questions"]] == drawn

    def test_uc01_counts_uc03s_attempts_without_owning_them(self, make_ctx) -> None:
        """The AttemptStatisticsPort: one attempt store, two capabilities reading it."""
        ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 4})
        ctx.save_configuration(
            valid_configuration(
                questionCount=2,
                maxAttempts=3,
                questionTypes=[{"type": "SINGLE_CHOICE", "quota": 2}],
            )
        )

        before = ctx.get_rules().json()
        assert (before["attemptsUsed"], before["remainingAttempts"]) == (0, 3)

        attempt_id, _ = ctx.start_and_read_questions()

        during = ctx.get_rules().json()
        assert during["attemptsUsed"] == 1
        assert during["remainingAttempts"] == 2
        assert during["canStart"] is False
        assert during["blockedReason"] == "attempt_in_progress"
        assert during["attemptInProgress"]["id"] == attempt_id

        assert ctx.submit_attempt(attempt_id).status_code == 200
        after = ctx.get_rules().json()
        assert after["canStart"] is True
        assert after["attemptInProgress"] is None

        # And UC-01's version history attributes the attempt to the version it locked onto.
        versions = ctx.get_versions().json()["versions"]
        assert [v["attemptCount"] for v in versions] == [1]

    def test_uc03_refuses_a_quiz_uc01_has_not_configured(self, make_ctx) -> None:
        ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 4})
        eligibility = ctx.eligibility()
        assert eligibility.status_code == 200
        body = eligibility.json()["eligibility"]
        assert body["eligible"] is False
        codes = {reason["code"] for reason in body["reasons"]}
        assert "QUIZ_NOT_AVAILABLE" in codes or "CONFIGURATION_VERSION_UNAVAILABLE" in codes
        assert ctx.start_attempt().status_code in (409, 422)
        assert ctx.attempt_count() == 0


class TestMetaContract:
    def test_meta_publishes_the_single_question_type_vocabulary(self, ctx: Ctx) -> None:
        body = ctx.client.get("/api/meta").json()
        assert [item["value"] for item in body["questionTypes"]] == [
            item.value for item in QuestionType
        ]
        assert body["deliverableQuestionStatuses"] == ["ACTIVE"]

    def test_both_capabilities_share_one_error_envelope(self, ctx: Ctx) -> None:
        responses = [
            ctx.save_configuration(valid_configuration(passMark=0)),
            ctx.client.post(
                f"{QB}/questions",
                json=factories.single_choice(options=[]),
                headers=auth(ADMIN_TOKEN),
            ),
        ]
        for response in responses:
            body = response.json()
            assert set(body) == {"error"}
            assert body["error"]["code"] == "VALIDATION_FAILED"
            for issue in body["error"]["details"]:
                assert set(issue) == {"field", "code", "message"}

    def test_one_admin_token_works_across_both_capabilities(self, ctx: Ctx) -> None:
        assert ctx.save_configuration(valid_configuration()).status_code == 201
        created = ctx.client.post(
            f"{QB}/questions", json=factories.scenario(), headers=auth(ADMIN_TOKEN)
        )
        assert created.status_code == 201
        # The audit trail records the resolved identity, not a self-declared header.
        assert created.json()["createdBy"] == "admin@test.local"

    def test_a_learner_token_cannot_modify_the_question_bank(self, ctx: Ctx) -> None:
        from tests.harness import LEARNER_TOKEN

        response = ctx.client.post(
            f"{QB}/questions", json=factories.single_choice(), headers=auth(LEARNER_TOKEN)
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"


class TestQuestionPresentationAcrossTheBoundary:
    """UC-01 authors the presentation; UC-03 honours it.

    This is the seam the two "delivery mode" concepts collided on, so it is worth exercising through
    the real adapter rather than only against a fake: UC-01 writes `question_presentation` on a
    version, the adapter carries it across, and UC-03 locks it onto the attempt and refuses the
    wrong
    read shape. The learner UI's second mode depends entirely on that chain.
    """

    def _configure(self, ctx: Ctx, presentation: str) -> None:
        saved = ctx.save_configuration(
            valid_configuration(
                questionCount=3,
                questionTypes=[{"type": "SINGLE_CHOICE", "quota": 3}],
                questionPresentation=presentation,
            )
        )
        assert saved.status_code == 201, saved.text

    def test_one_at_a_time_is_locked_onto_the_attempt_and_enforced(self, make_ctx) -> None:
        ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 4})
        self._configure(ctx, "ONE_AT_A_TIME")

        created = ctx.start_attempt()
        assert created.status_code == 201, created.text
        attempt = created.json()["attempt"]
        assert attempt["questionPresentation"] == "ONE_AT_A_TIME"
        attempt_id = attempt["attemptId"]

        # The descriptor points a client at the right endpoint for the locked presentation, which is
        # how the UI avoids asking for the whole paper in the first place.
        assert created.json()["delivery"]["questionsUrl"].endswith("/questions/current")

        # And asking anyway is refused, rather than quietly handing over every question.
        whole_paper = ctx.attempt_questions(attempt_id)
        assert whole_paper.status_code == 409, whole_paper.text
        assert whole_paper.json()["error"]["code"] == "QUESTION_PRESENTATION_VIOLATION"

        current = ctx.client.get(
            f"/api/v1/attempts/{attempt_id}/questions/current", headers=auth(LEARNER_TOKEN)
        )
        assert current.status_code == 200, current.text
        assert current.json()["question"]["position"] == 1

    def test_the_cursor_survives_a_reload(self, make_ctx) -> None:
        """The resume position is the server's, so a refresh returns to the same question."""
        ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 4})
        self._configure(ctx, "ONE_AT_A_TIME")
        attempt_id = ctx.start_attempt().json()["attempt"]["attemptId"]

        moved = ctx.client.put(
            f"/api/v1/attempts/{attempt_id}/cursor",
            json={"position": 3},
            headers=auth(LEARNER_TOKEN),
        )
        assert moved.status_code == 200, moved.text

        # A "reload": the client throws its own state away and reads the attempt back.
        resumed = ctx.client.get(
            "/api/v1/attempts/active", params={"quizId": str(ctx.quiz_id)}, headers=auth(LEARNER_TOKEN)
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["attempt"]["currentPosition"] == 3

        current = ctx.client.get(
            f"/api/v1/attempts/{attempt_id}/questions/current", headers=auth(LEARNER_TOKEN)
        )
        assert current.json()["question"]["position"] == 3

    def test_all_at_once_hands_over_the_whole_paper(self, make_ctx) -> None:
        ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 4})
        self._configure(ctx, "ALL_AT_ONCE")

        created = ctx.start_attempt()
        assert created.json()["attempt"]["questionPresentation"] == "ALL_AT_ONCE"
        assert created.json()["delivery"]["questionsUrl"].endswith("/questions")

        attempt_id = created.json()["attempt"]["attemptId"]
        paper = ctx.attempt_questions(attempt_id)
        assert paper.status_code == 200, paper.text
        assert [question["position"] for question in paper.json()["questions"]] == [1, 2, 3]

    def test_changing_the_presentation_does_not_move_a_running_attempt(self, make_ctx) -> None:
        """The presentation is locked like every other rule on the version."""
        ctx: Ctx = make_ctx({QuestionType.SINGLE_CHOICE: 4})
        self._configure(ctx, "ALL_AT_ONCE")
        attempt_id = ctx.start_attempt().json()["attempt"]["attemptId"]

        self._configure(ctx, "ONE_AT_A_TIME")

        reloaded = ctx.get_attempt(attempt_id)
        assert reloaded.json()["attempt"]["questionPresentation"] == "ALL_AT_ONCE"
        # Still readable as a whole paper, because that is what this attempt was started under.
        assert ctx.attempt_questions(attempt_id).status_code == 200
