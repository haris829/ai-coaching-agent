"""UC-04's scoring service: persistence, idempotency, immutability and retry.

Runs against a real SQLite database created from the models — triggers included — so the
immutability claims are tested against the schema rather than against the service's intentions.
UC-03 and UC-02 are
faked, because several of these states (a lost answer key, a paper worth zero marks, no questions at
all) are ones the real chain correctly refuses to produce.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import text

from app.core.errors import AppError
from app.modules.scoring.api.presenters import present_result
from app.modules.scoring.domain.enums import (
    AnswerKeySource,
    QuestionOutcome,
    QuestionType,
    ResultStatus,
    ScoreAnomaly,
)
from app.modules.scoring.models import AttemptResult
from tests.support.results_world import (
    CONFIGURATION_VERSION_ID,
    LEARNER_ID,
    OTHER_LEARNER_ID,
    ResultsWorld,
    answer_key,
    delivered,
    elapsed_seconds,
    option,
    order_item,
    submitted_attempt,
)


def _paper(
    world: ResultsWorld,
    *,
    correct: int = 4,
    total: int = 4,
    unanswered: int = 0,
    points: float = 1.0,
    pass_mark: float = 70.0,
    attempt_id: str = "attempt-1",
    attempt_number: int = 1,
    learner_id: str = LEARNER_ID,
    locked: bool = True,
    submitted: bool = True,
    with_keys: bool = True,
    with_frozen_key: bool = True,
) -> list:
    """Seed one submitted attempt of single-choice questions.

    ``correct`` are answered right, the next ``unanswered`` are left blank, and the rest are
    answered wrongly. ``with_frozen_key`` strips the answer key UC-03 froze onto the attempt, which
    — together with ``with_keys=False`` — is the only way to reach a genuinely missing key: UC-03
    always freezes one, and that is exactly why the fallback exists.
    """
    questions = []
    for position in range(1, total + 1):
        if position <= correct:
            response: dict | None = {"type": "SINGLE_CHOICE", "selectedOptionId": "A"}
        elif position <= correct + unanswered:
            response = None
        else:
            response = {"type": "SINGLE_CHOICE", "selectedOptionId": "B"}

        question = delivered(
            QuestionType.SINGLE_CHOICE,
            position=position,
            question_id=f"q{position}",
            points=points,
            prompt=f"Question {position}: what comes first?",
            options=[
                option("A", "Raise the alarm", correct=with_frozen_key),
                option("B", "Finish the task"),
            ],
            response=response,
        )
        questions.append(question)
        if with_keys:
            world.answer_keys.add(answer_key(question, correct_ids=["A"]))

    world.attempts.add(
        submitted_attempt(
            questions,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            learner_id=learner_id,
            pass_mark=pass_mark,
            locked=locked,
            status="SUBMITTED" if locked else "ACTIVE",
            submitted_at=None if not submitted else None if not locked else "2026-03-01T09:12:30Z",
        )
    )
    return questions


class TestScoringASubmittedAttempt:
    def test_a_fully_correct_attempt_scores_the_maximum(self, world: ResultsWorld) -> None:
        _paper(world, correct=4, total=4)

        result = world.score().result

        assert result.status == str(ResultStatus.SCORED)
        assert result.total_marks == result.maximum_marks == 4.0
        assert result.percentage == 100.0
        assert (result.correct_count, result.incorrect_count, result.unanswered_count) == (4, 0, 0)

    def test_a_score_is_persisted_per_question(self, world: ResultsWorld) -> None:
        _paper(world, correct=4, total=4)

        scores = world.score().question_scores

        assert [score.position for score in scores] == [1, 2, 3, 4]
        assert all(score.outcome == str(QuestionOutcome.CORRECT) for score in scores)

    def test_the_question_text_and_answers_are_frozen_onto_the_score(
        self, world: ResultsWorld
    ) -> None:
        """UC-06 builds its report from these rows, so they have to carry the content."""
        _paper(world, correct=1, total=1)

        first = world.score().question_scores[0]

        assert first.question_text == "Question 1: what comes first?"
        assert first.learner_answer == {"type": "SINGLE_CHOICE", "selectedOptionId": "A"}
        assert first.correct_answer_display["optionIds"] == ["A"]
        assert first.explanation is not None

    def test_a_wrong_attempt_scores_zero_but_keeps_the_maximum(self, world: ResultsWorld) -> None:
        _paper(world, correct=0, total=4)

        result = world.score().result

        assert result.total_marks == 0.0
        assert result.maximum_marks == 4.0
        assert result.percentage == 0.0
        assert result.correct_count == 0

    def test_unanswered_questions_are_counted_and_score_nothing(self, world: ResultsWorld) -> None:
        _paper(world, correct=2, unanswered=2, total=4)

        result = world.score().result

        assert (result.correct_count, result.unanswered_count) == (2, 2)
        assert result.total_marks == 2.0
        assert result.percentage == 50.0

    def test_mixed_question_types_are_all_scored(self, world: ResultsWorld) -> None:
        """One of every supported type in one paper, to prove the dispatch covers all five."""
        single = delivered(
            QuestionType.SINGLE_CHOICE,
            position=1,
            options=[option("A", "Right", correct=True), option("B", "Wrong")],
            response={"type": "SINGLE_CHOICE", "selectedOptionId": "A"},
        )
        boolean = delivered(
            QuestionType.TRUE_FALSE,
            position=2,
            options=[option("TRUE", "True", correct=True), option("FALSE", "False")],
            response={"type": "TRUE_FALSE", "value": True},
        )
        multi = delivered(
            QuestionType.MULTI_SELECT,
            position=3,
            points=2.0,
            options=[
                option("A", "One", correct=True),
                option("B", "Two", correct=True),
                option("C", "Three"),
            ],
            response={"type": "MULTI_SELECT", "selectedOptionIds": ["A", "B"]},
        )
        story = delivered(
            QuestionType.SCENARIO,
            position=4,
            question_id="q-scenario",
            options=[option("A", "Evacuate", correct=True), option("B", "Wait")],
            sub_question_ids=["q-scenario:1"],
            response={
                "type": "SCENARIO",
                "responses": [
                    {
                        "subQuestionId": "q-scenario:1",
                        "answer": {"type": "SINGLE_CHOICE", "selectedOptionId": "A"},
                    }
                ],
            },
        )
        ordering = delivered(
            QuestionType.DRAG_TO_ORDER,
            position=5,
            points=3.0,
            options=[
                order_item("S2", "Second", position=2),
                order_item("S1", "First", position=1),
            ],
            response={"type": "DRAG_TO_ORDER", "orderedItemIds": ["S1", "S2"]},
        )

        world.answer_keys.add(answer_key(single, correct_ids=["A"]))
        world.answer_keys.add(answer_key(boolean, correct_ids=["TRUE"]))
        world.answer_keys.add(answer_key(multi, correct_ids=["A", "B"]))
        world.answer_keys.add(answer_key(story, correct_ids=["A"], primary_id="A"))
        world.answer_keys.add(answer_key(ordering, correct_order=["S1", "S2"]))
        world.attempts.add(submitted_attempt([single, boolean, multi, story, ordering]))

        result = world.score().result

        assert result.total_questions == 5
        assert result.correct_count == 5
        assert result.total_marks == result.maximum_marks == 8.0
        assert result.percentage == 100.0

    def test_the_pass_mark_is_frozen_from_the_attempts_own_configuration_version(
        self, world: ResultsWorld
    ) -> None:
        _paper(world, pass_mark=82.0)

        result = world.score().result

        assert result.pass_mark_percentage == 82.0
        assert result.configuration_version_id == CONFIGURATION_VERSION_ID
        assert result.configuration_version_number == 3

    def test_the_time_taken_comes_from_the_attempts_own_timestamps(
        self, world: ResultsWorld
    ) -> None:
        _paper(world)

        assert world.score().result.time_taken_seconds == elapsed_seconds()

    def test_the_answer_key_source_is_recorded_per_question(self, world: ResultsWorld) -> None:
        _paper(world)

        scores = world.score().question_scores

        assert {score.answer_key_source for score in scores} == {
            str(AnswerKeySource.QUESTION_BANK_SNAPSHOT)
        }

    def test_a_missing_bank_snapshot_falls_back_to_the_key_frozen_on_the_attempt(
        self, world: ResultsWorld
    ) -> None:
        """A lost snapshot row must not turn a learner's correct answers into zeros."""
        _paper(world, correct=2, total=2)
        world.answer_keys.drop("q1")

        outcome = world.score()
        first = outcome.question_scores[0]

        assert outcome.result.status == str(ResultStatus.SCORED)
        assert first.answer_key_source == str(AnswerKeySource.ATTEMPT_SNAPSHOT)
        assert first.awarded_marks == first.maximum_marks

    def test_a_whole_bank_outage_still_scores_from_the_frozen_keys(
        self, world: ResultsWorld
    ) -> None:
        _paper(world, correct=4, total=4)
        world.answer_keys.unavailable = True

        result = world.score().result

        assert result.status == str(ResultStatus.SCORED)
        assert result.percentage == 100.0


class TestIdempotency:
    def test_scoring_twice_replays_the_stored_score(self, world: ResultsWorld) -> None:
        _paper(world)

        first = world.score()
        second = world.score()

        assert second.replayed is True
        assert second.created is False
        assert second.result.id == first.result.id
        assert second.result.scored_at == first.result.scored_at

    def test_only_one_result_row_exists_however_often_scoring_runs(
        self, world: ResultsWorld
    ) -> None:
        _paper(world)
        for _ in range(4):
            world.score()

        with world.session() as session:
            assert session.scalar(text("SELECT COUNT(*) FROM qr_attempt_results")) == 1

    def test_a_replay_does_not_count_as_another_scoring_run(self, world: ResultsWorld) -> None:
        _paper(world)
        world.score()
        world.score()

        assert world.score().result.scoring_attempt_count == 1

    def test_question_scores_are_not_duplicated_by_a_repeat(self, world: ResultsWorld) -> None:
        _paper(world, total=4)
        world.score()
        world.score()

        with world.session() as session:
            assert session.scalar(text("SELECT COUNT(*) FROM qr_question_scores")) == 4


class TestImmutability:
    def test_a_confirmed_score_cannot_be_updated_even_directly(self, world: ResultsWorld) -> None:
        """The trigger, not the service, is what makes this true for every future caller."""
        _paper(world)
        result_id = world.score().result.id

        with pytest.raises(Exception) as failure, world.session() as session:
            session.execute(
                text("UPDATE qr_attempt_results SET percentage = 1 WHERE id = :id"),
                {"id": result_id},
            )
            session.commit()

        assert "IMMUTABLE_ATTEMPT_RESULT" in str(failure.value)

    def test_a_question_score_cannot_be_updated_even_directly(self, world: ResultsWorld) -> None:
        _paper(world)
        score_id = world.score().question_scores[0].id

        with pytest.raises(Exception) as failure, world.session() as session:
            session.execute(
                text("UPDATE qr_question_scores SET awarded_marks = 99 WHERE id = :id"),
                {"id": score_id},
            )
            session.commit()

        assert "IMMUTABLE_QUESTION_SCORE" in str(failure.value)

    def test_changing_the_answer_key_afterwards_cannot_change_a_confirmed_score(
        self, world: ResultsWorld
    ) -> None:
        questions = _paper(world, correct=4, total=4)
        original = world.score().result.percentage
        assert original == 100.0

        # The bank is edited so that what was right is now wrong.
        rekeyed = answer_key(questions[0], correct_ids=["B"])
        world.answer_keys.add(rekeyed)

        assert world.score().result.percentage == original

    def test_a_confirmed_score_is_never_recomputed_even_if_the_answers_change(
        self, world: ResultsWorld
    ) -> None:
        _paper(world, correct=4, total=4)
        assert world.score().result.percentage == 100.0

        # Changing an answer after submission is impossible through UC-03. Even if it happened, the
        # confirmed score stands.
        attempt = world.attempts.get_attempt("attempt-1")
        assert attempt is not None
        world.attempts.replace(
            replace(
                attempt,
                questions=tuple(
                    replace(question, response={"type": "SINGLE_CHOICE", "selectedOptionId": "B"})
                    for question in attempt.questions
                ),
            )
        )

        assert world.score().result.percentage == 100.0


class TestPendingScore:
    def test_a_missing_answer_key_leaves_the_result_pending(self, world: ResultsWorld) -> None:
        _paper(world, correct=1, total=1, with_keys=False, with_frozen_key=False)

        result = world.score().result

        assert result.status == str(ResultStatus.PENDING_SCORE)
        assert result.failure_code == str(ScoreAnomaly.MISSING_ANSWER_KEY)
        assert result.scored_at is None
        assert [item["code"] for item in result.anomalies] == [str(ScoreAnomaly.MISSING_ANSWER_KEY)]

    def test_a_pending_result_is_labelled_submitted_pending_score(
        self, world: ResultsWorld
    ) -> None:
        _paper(world, correct=1, total=1, with_keys=False, with_frozen_key=False)

        payload = present_result(world.score().result)

        assert payload["status"] == "PENDING_SCORE"
        assert payload["statusLabel"] == "Submitted — Pending Score"

    def test_a_pending_result_stores_no_question_scores(self, world: ResultsWorld) -> None:
        """Nobody is shown a per-question mark that was computed from broken data."""
        _paper(world, correct=1, total=1, with_keys=False, with_frozen_key=False)
        world.score()

        with world.session() as session:
            assert session.scalar(text("SELECT COUNT(*) FROM qr_question_scores")) == 0

    def test_a_zero_maximum_leaves_the_result_pending(self, world: ResultsWorld) -> None:
        _paper(world, correct=1, total=1, points=0.0)

        result = world.score().result

        assert result.status == str(ResultStatus.PENDING_SCORE)
        assert result.failure_code == str(ScoreAnomaly.ZERO_MAXIMUM_MARKS)

    def test_an_attempt_with_no_questions_is_pending_rather_than_a_perfect_score(
        self, world: ResultsWorld
    ) -> None:
        world.attempts.add(submitted_attempt([]))

        result = world.score().result

        assert result.status == str(ResultStatus.PENDING_SCORE)
        assert result.failure_code == str(ScoreAnomaly.NO_QUESTIONS_DELIVERED)
        assert result.percentage == 0.0

    def test_the_submission_is_untouched_by_a_scoring_failure(self, world: ResultsWorld) -> None:
        _paper(world, correct=1, total=1, with_keys=False, with_frozen_key=False)
        world.score()

        attempt = world.attempts.get_attempt("attempt-1")
        assert attempt is not None
        assert attempt.status == "SUBMITTED"


class TestRetry:
    def test_retrying_a_pending_result_confirms_it_once_the_data_is_fixed(
        self, world: ResultsWorld
    ) -> None:
        questions = _paper(world, correct=2, total=2, with_keys=False, with_frozen_key=False)

        first = world.score().result
        assert first.status == str(ResultStatus.PENDING_SCORE)

        # The bank's snapshots are restored, which is what a real fix looks like.
        for question in questions:
            world.answer_keys.add(answer_key(question, correct_ids=["A"]))

        second = world.score().result

        assert second.id == first.id
        assert second.status == str(ResultStatus.SCORED)
        assert second.failure_code is None
        assert second.anomalies in (None, [])
        assert second.percentage == 100.0

    def test_each_scoring_run_is_counted(self, world: ResultsWorld) -> None:
        _paper(world, correct=1, total=1, with_keys=False, with_frozen_key=False)
        world.score()
        world.score()

        result = world.score().result

        assert result.scoring_attempt_count == 3
        assert result.status == str(ResultStatus.PENDING_SCORE)

    def test_a_retry_reuses_the_same_result_row(self, world: ResultsWorld) -> None:
        _paper(world, correct=1, total=1, with_keys=False, with_frozen_key=False)
        first = world.score().result.id
        world.score()

        with world.session() as session:
            assert session.scalar(text("SELECT COUNT(*) FROM qr_attempt_results")) == 1
            assert session.get(AttemptResult, first) is not None


class TestGuards:
    def test_an_unknown_attempt_is_not_found(self, world: ResultsWorld) -> None:
        with pytest.raises(AppError) as failure:
            world.score("no-such-attempt")

        assert failure.value.code == "ATTEMPT_NOT_FOUND"
        assert failure.value.status_code == 404

    def test_an_attempt_still_in_progress_cannot_be_scored(self, world: ResultsWorld) -> None:
        world.attempts.add(submitted_attempt([], locked=False, status="ACTIVE", submitted_at=None))

        with pytest.raises(AppError) as failure:
            world.score()

        assert failure.value.code == "ATTEMPT_NOT_SUBMITTED"
        assert failure.value.status_code == 409

    def test_a_learner_cannot_score_another_learners_attempt(self, world: ResultsWorld) -> None:
        _paper(world)

        with pytest.raises(AppError) as failure:
            world.score(learner_id=OTHER_LEARNER_ID)

        assert failure.value.code == "ATTEMPT_NOT_FOUND"

    def test_a_learner_cannot_read_another_learners_result(self, world: ResultsWorld) -> None:
        _paper(world)
        world.score()

        with world.unit_of_work() as ctx, pytest.raises(AppError) as failure:
            ctx.scoring.find_result("attempt-1", learner_id=OTHER_LEARNER_ID)

        assert failure.value.code == "ATTEMPT_NOT_FOUND"

    def test_reading_a_result_that_does_not_exist_is_not_found(self, world: ResultsWorld) -> None:
        _paper(world)

        with world.unit_of_work() as ctx, pytest.raises(AppError) as failure:
            ctx.scoring.find_result("attempt-1")

        assert failure.value.code == "RESULT_NOT_FOUND"


class TestListing:
    def test_results_are_listed_newest_attempt_first(self, world: ResultsWorld) -> None:
        _paper(world, attempt_id="attempt-1", attempt_number=1)
        world.score("attempt-1")
        _paper(world, attempt_id="attempt-2", attempt_number=2)
        world.score("attempt-2")

        with world.unit_of_work() as ctx:
            results = ctx.scoring.list_results(LEARNER_ID)

        assert [result.attempt_number for result in results] == [2, 1]

    def test_results_can_be_filtered_to_one_quiz(self, world: ResultsWorld) -> None:
        _paper(world)
        world.score()

        with world.unit_of_work() as ctx:
            assert ctx.scoring.list_results(LEARNER_ID, quiz_id="quiz-fire-safety-final")
            assert ctx.scoring.list_results(LEARNER_ID, quiz_id="another-quiz") == []
