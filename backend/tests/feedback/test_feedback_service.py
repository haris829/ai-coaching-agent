"""UC-06: the report's content, its fallbacks, its immutability and its retry path.

The chain in front of it is real -- UC-04 scores into its own tables, UC-05 determines the outcome,
and UC-06 reads both through the real adapters. Only the boundaries outside the chain are faked:
UC-03's attempt, UC-02's answer keys, and UC-02's authored explanations and lesson references.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.modules.certification.domain.enums import Outcome
from app.modules.feedback.domain import errors
from app.modules.feedback.domain.enums import ReportStatus
from app.modules.feedback.domain.fallbacks import NO_EXPLANATION, NO_LESSON_REFERENCE
from app.modules.scoring.domain.answer_key import MarkingPolicy
from app.modules.scoring.domain.enums import QuestionType
from tests.support.results_world import (
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


def _single_choice_paper(
    world: ResultsWorld,
    *,
    correct: int = 3,
    total: int = 4,
    with_content: bool = True,
    attempt_id: str = "attempt-1",
    pass_mark: float = 70.0,
):
    questions = []
    for position in range(1, total + 1):
        selected = "A" if position <= correct else "B"
        question = delivered(
            QuestionType.SINGLE_CHOICE,
            position=position,
            question_id=f"q{position}",
            prompt=f"Question {position}: what comes first?",
            options=[option("A", "Raise the alarm", correct=True), option("B", "Finish the task")],
            response={"type": "SINGLE_CHOICE", "selectedOptionId": selected},
        )
        questions.append(question)
        world.answer_keys.add(answer_key(question, correct_ids=["A"]))
        if with_content:
            world.content.add(
                question.question_id,
                explanation=f"Explanation for question {position}.",
                lesson_reference=f"Topic: Lesson {position}",
            )
    world.attempts.add(submitted_attempt(questions, attempt_id=attempt_id, pass_mark=pass_mark))
    return questions


def _run_chain(world: ResultsWorld, attempt_id: str = "attempt-1"):
    world.score(attempt_id)
    world.determine(attempt_id)
    return world.generate_feedback(attempt_id)


class TestReportContent:
    def test_it_reports_the_totals_and_the_pass_fail_result(self, world: ResultsWorld) -> None:
        _single_choice_paper(world, correct=3, total=4, pass_mark=70.0)

        outcome = _run_chain(world)
        report = outcome.report

        assert report.status == str(ReportStatus.GENERATED)
        assert report.total_marks == 3.0
        assert report.maximum_marks == 4.0
        assert report.percentage == 75.0
        assert report.pass_mark_percentage == 70.0
        assert report.passed is True
        assert report.total_questions == 4
        assert report.correct_count == 3
        assert report.incorrect_count == 1
        assert report.unanswered_count == 0
        assert report.time_taken_seconds == elapsed_seconds()

    def test_it_reports_a_fail_as_a_fail(self, world: ResultsWorld) -> None:
        _single_choice_paper(world, correct=1, total=4, pass_mark=70.0)

        report = _run_chain(world).report

        assert report.passed is False
        assert report.percentage == 25.0

    def test_every_question_carries_the_six_required_fields(self, world: ResultsWorld) -> None:
        _single_choice_paper(world, correct=1, total=2)

        items = _run_chain(world).items

        assert len(items) == 2
        first = items[0]
        # question · learner answer · correct answer · explanation · score · lesson reference
        assert first.question_text.startswith("Question 1")
        assert first.learner_answer["labels"] == ["Raise the alarm"]
        assert first.correct_answer["labels"] == ["Raise the alarm"]
        assert first.explanation == "Explanation for question 1."
        assert first.question_score == 1.0
        assert first.maximum_marks == 1.0
        assert first.lesson_reference == "Topic: Lesson 1"

    def test_items_are_ordered_by_the_position_the_learner_saw(self, world: ResultsWorld) -> None:
        _single_choice_paper(world, correct=2, total=4)

        items = _run_chain(world).items

        assert [item.position for item in items] == [1, 2, 3, 4]

    def test_an_unanswered_question_is_reported_as_such(self, world: ResultsWorld) -> None:
        question = delivered(
            QuestionType.SINGLE_CHOICE,
            options=[option("A", "Raise the alarm", correct=True), option("B", "Wait")],
            response=None,
        )
        world.answer_keys.add(answer_key(question, correct_ids=["A"]))
        world.content.add(question.question_id)
        world.attempts.add(submitted_attempt([question]))

        items = _run_chain(world).items

        assert items[0].answered is False
        assert items[0].question_score == 0.0
        assert items[0].learner_answer["summary"] == "No answer given."
        # The correct answer is still shown -- that is the point of feedback.
        assert items[0].correct_answer["labels"] == ["Raise the alarm"]

    def test_a_multi_select_reports_every_option_and_its_contribution(
        self, world: ResultsWorld
    ) -> None:
        question = delivered(
            QuestionType.MULTI_SELECT,
            points=4.0,
            options=[
                option("A", "Raise the alarm", correct=True),
                option("B", "Use the exit", correct=True),
                option("C", "Use the lift"),
            ],
            response={"type": "MULTI_SELECT", "selectedOptionIds": ["A", "C"]},
        )
        world.answer_keys.add(
            answer_key(
                question,
                correct_ids=["A", "B"],
                policy=MarkingPolicy.PARTIAL_WITH_DEDUCTION,
                deduction=1.0,
                points=4.0,
            )
        )
        world.content.add(question.question_id)
        world.attempts.add(submitted_attempt([question]))

        item = _run_chain(world).items[0]

        breakdown = {entry["optionId"]: entry for entry in item.option_breakdown}
        assert breakdown["A"]["selected"] is True and breakdown["A"]["correct"] is True
        assert breakdown["A"]["markContribution"] == 2.0
        assert breakdown["B"]["selected"] is False and breakdown["B"]["correct"] is True
        assert breakdown["C"]["selected"] is True and breakdown["C"]["correct"] is False
        assert breakdown["C"]["markContribution"] == -1.0
        assert item.question_score == 1.0
        assert item.deduction == 1.0

    def test_authored_per_option_feedback_is_included_when_the_bank_has_it(
        self, world: ResultsWorld
    ) -> None:
        question = delivered(
            QuestionType.SINGLE_CHOICE,
            options=[option("A", "Raise the alarm", correct=True), option("B", "Wait")],
            response={"type": "SINGLE_CHOICE", "selectedOptionId": "B"},
        )
        world.answer_keys.add(answer_key(question, correct_ids=["A"]))
        world.content.add(
            question.question_id,
            option_feedback=[("B", "Waiting costs time you do not have.")],
        )
        world.attempts.add(submitted_attempt([question]))

        item = _run_chain(world).items[0]

        chosen = next(entry for entry in item.option_breakdown if entry["optionId"] == "B")
        assert chosen["feedback"] == "Waiting costs time you do not have."

    def test_a_drag_to_order_report_shows_both_sequences(self, world: ResultsWorld) -> None:
        question = delivered(
            QuestionType.DRAG_TO_ORDER,
            points=3.0,
            options=[
                order_item("A", "Raise the alarm", position=1),
                order_item("B", "Evacuate", position=2),
                order_item("C", "Assemble", position=3),
            ],
            response={"type": "DRAG_TO_ORDER", "orderedItemIds": ["A", "C", "B"]},
        )
        world.answer_keys.add(answer_key(question, correct_order=["A", "B", "C"], points=3.0))
        world.content.add(question.question_id)
        world.attempts.add(submitted_attempt([question]))

        item = _run_chain(world).items[0]

        assert item.learner_answer["orderedItemIds"] == ["A", "C", "B"]
        assert item.correct_answer["orderedItemIds"] == ["A", "B", "C"]
        assert item.question_score == 0.0

    def test_the_report_payload_is_stored_as_it_was_rendered(self, world: ResultsWorld) -> None:
        _single_choice_paper(world, correct=2, total=2)

        report = _run_chain(world).report

        assert report.payload is not None
        assert report.payload["summary"]["percentage"] == 100.0
        assert len(report.payload["items"]) == 2
        assert report.payload["items"][0]["lessonReference"] == "Topic: Lesson 1"


class TestFallbacks:
    def test_a_missing_explanation_uses_the_defined_fallback(self, world: ResultsWorld) -> None:
        question = delivered(
            QuestionType.SINGLE_CHOICE,
            options=[option("A", "Raise the alarm", correct=True), option("B", "Wait")],
            response={"type": "SINGLE_CHOICE", "selectedOptionId": "A"},
        )
        # No explanation anywhere: not on the bank's content, not on the answer key.
        world.answer_keys.add(answer_key(question, correct_ids=["A"], explanation=None))
        world.content.add(question.question_id, explanation=None, lesson_reference="Topic: Lesson")
        world.attempts.add(submitted_attempt([question]))

        item = _run_chain(world).items[0]

        assert item.explanation == NO_EXPLANATION

    def test_a_missing_lesson_reference_uses_the_defined_fallback(
        self, world: ResultsWorld
    ) -> None:
        question = delivered(
            QuestionType.SINGLE_CHOICE,
            options=[option("A", "Raise the alarm", correct=True), option("B", "Wait")],
            response={"type": "SINGLE_CHOICE", "selectedOptionId": "A"},
        )
        world.answer_keys.add(answer_key(question, correct_ids=["A"]))
        world.content.add(question.question_id, lesson_reference=None)
        world.attempts.add(submitted_attempt([question]))

        item = _run_chain(world).items[0]

        assert item.lesson_reference == NO_LESSON_REFERENCE

    def test_a_question_with_no_authored_content_at_all_still_produces_an_item(
        self, world: ResultsWorld
    ) -> None:
        """Missing content degrades two fields of one item; it never costs the learner their
        feedback."""
        _single_choice_paper(world, correct=1, total=1, with_content=False)
        world.answer_keys.add(
            answer_key(
                world.attempts.get_attempt("attempt-1").questions[0],
                correct_ids=["A"],
                explanation=None,
            )
        )

        outcome = _run_chain(world)

        assert outcome.report.status == str(ReportStatus.GENERATED)
        assert outcome.items[0].explanation == NO_EXPLANATION
        assert outcome.items[0].lesson_reference == NO_LESSON_REFERENCE

    def test_the_explanation_captured_at_scoring_is_used_when_the_bank_has_none_now(
        self, world: ResultsWorld
    ) -> None:
        """Both sources are frozen copies of the same authored text; either will do."""
        _single_choice_paper(world, correct=1, total=1, with_content=False)

        item = _run_chain(world).items[0]

        # The answer key carried an explanation, and UC-04 froze it onto the score row, so the
        # report shows authored text rather than the fallback.
        assert item.explanation != NO_EXPLANATION
        assert item.explanation == world.built[0].key.explanation if world.built else True


class TestIdempotencyAndImmutability:
    def test_generating_twice_replays_the_same_report(self, world: ResultsWorld) -> None:
        _single_choice_paper(world, correct=2, total=4)
        first = _run_chain(world)

        second = world.generate_feedback()

        assert second.replayed is True
        assert second.report.id == first.report.id
        assert second.report.generated_at == first.report.generated_at

    def test_there_is_exactly_one_report_per_attempt(self, world: ResultsWorld) -> None:
        _single_choice_paper(world, correct=2, total=4)
        _run_chain(world)
        world.generate_feedback()
        world.generate_feedback()

        with world.session() as session:
            reports = session.execute(text("SELECT COUNT(*) FROM qf_feedback_reports")).scalar()
            items = session.execute(text("SELECT COUNT(*) FROM qf_feedback_items")).scalar()
        assert reports == 1
        assert items == 4

    def test_the_database_refuses_to_update_a_generated_report(self, world: ResultsWorld) -> None:
        _single_choice_paper(world, correct=2, total=4)
        _run_chain(world)

        with world.session() as session, pytest.raises(Exception) as caught:
            session.execute(text("UPDATE qf_feedback_reports SET percentage = 100"))
            session.commit()

        assert "IMMUTABLE_FEEDBACK_REPORT" in str(caught.value)

    def test_the_database_refuses_to_update_a_feedback_item(self, world: ResultsWorld) -> None:
        _single_choice_paper(world, correct=2, total=4)
        _run_chain(world)

        with world.session() as session, pytest.raises(Exception) as caught:
            session.execute(text("UPDATE qf_feedback_items SET explanation = 'edited'"))
            session.commit()

        assert "IMMUTABLE_FEEDBACK_ITEM" in str(caught.value)

    def test_a_generated_report_does_not_change_when_the_authored_content_does(
        self, world: ResultsWorld
    ) -> None:
        """Historical feedback stays consistent even after the question bank is edited."""
        _single_choice_paper(world, correct=1, total=1)
        first = _run_chain(world)
        assert first.items[0].explanation == "Explanation for question 1."

        # The question bank is edited: a new explanation and a new lesson reference.
        world.content.add(
            "q1", explanation="A completely rewritten explanation.", lesson_reference="Topic: New"
        )

        again = world.generate_feedback()

        assert again.replayed is True
        assert again.items[0].explanation == "Explanation for question 1."
        assert again.items[0].lesson_reference == "Topic: Lesson 1"
        assert again.report.payload["items"][0]["explanation"] == "Explanation for question 1."


class TestFailureAndRetry:
    def test_feedback_cannot_be_generated_from_a_pending_score(self, world: ResultsWorld) -> None:
        question = delivered(
            QuestionType.SINGLE_CHOICE,
            options=[option("A", "Right"), option("B", "Wrong")],
            response={"type": "SINGLE_CHOICE", "selectedOptionId": "A"},
        )
        world.attempts.add(submitted_attempt([question]))
        world.score()

        with pytest.raises(errors.AppError) as caught:
            world.generate_feedback()

        assert caught.value.code == "SCORE_NOT_CONFIRMED"
        assert caught.value.status == 409
        assert caught.value.retryable is True

    def test_a_generation_failure_leaves_the_score_and_the_outcome_intact(
        self, world: ResultsWorld
    ) -> None:
        _single_choice_paper(world, correct=3, total=4)
        world.score()
        world.determine()

        class BrokenContent:
            def find_content(self, refs):  # noqa: ANN001, ANN201 - test double
                raise RuntimeError("Simulated content-store outage.")

        world.context.ports = type(world.context.ports)(
            **{
                **{
                    field: getattr(world.context.ports, field)
                    for field in world.context.ports.__dataclass_fields__
                },
                "content": lambda _session: BrokenContent(),
            }
        )

        with pytest.raises(errors.AppError) as caught:
            world.generate_feedback()

        assert caught.value.code == "FEEDBACK_GENERATION_FAILED"
        assert caught.value.retryable is True

        with world.unit_of_work() as ctx:
            # The score and the verdict are exactly as they were.
            assert ctx.scoring.find_result("attempt-1").percentage == 75.0
            assert ctx.certification.find_outcome("attempt-1").outcome.outcome == str(Outcome.PASS)
            report, _items = ctx.feedback.find_report("attempt-1")
        assert report.status == str(ReportStatus.PENDING)
        assert report.failure_code == "FEEDBACK_ASSEMBLY_FAILED"

    def test_a_pending_report_is_generated_on_retry(self, world: ResultsWorld) -> None:
        _single_choice_paper(world, correct=3, total=4)
        world.score()
        world.determine()

        broken = {"calls": 0}
        real_content = world.content

        class FlakyContent:
            def find_content(self, refs):  # noqa: ANN001, ANN201 - test double
                broken["calls"] += 1
                if broken["calls"] == 1:
                    raise RuntimeError("Simulated content-store outage.")
                return real_content.find_content(refs)

        flaky = FlakyContent()
        world.context.ports = type(world.context.ports)(
            **{
                **{
                    field: getattr(world.context.ports, field)
                    for field in world.context.ports.__dataclass_fields__
                },
                "content": lambda _session: flaky,
            }
        )

        with pytest.raises(errors.AppError):
            world.generate_feedback()

        retried = world.generate_feedback()

        assert retried.report.status == str(ReportStatus.GENERATED)
        assert retried.report.generation_attempt_count == 2
        assert retried.report.failure_code is None
        assert len(retried.items) == 4

    def test_the_pipeline_variant_does_not_raise(self, world: ResultsWorld) -> None:
        """The submission pipeline must never fail because feedback could not be built."""
        _single_choice_paper(world, correct=3, total=4)
        world.score()
        world.determine()

        class BrokenContent:
            def find_content(self, refs):  # noqa: ANN001, ANN201 - test double
                raise RuntimeError("Simulated content-store outage.")

        world.context.ports = type(world.context.ports)(
            **{
                **{
                    field: getattr(world.context.ports, field)
                    for field in world.context.ports.__dataclass_fields__
                },
                "content": lambda _session: BrokenContent(),
            }
        )

        outcome = world.generate_feedback(raise_on_failure=False)

        assert outcome.report.status == str(ReportStatus.PENDING)
        assert outcome.generated is False


class TestAccess:
    def test_an_unknown_attempt_is_not_found(self, world: ResultsWorld) -> None:
        with pytest.raises(errors.AppError) as caught:
            world.generate_feedback("no-such-attempt")

        assert caught.value.code == "ATTEMPT_NOT_FOUND"

    def test_another_learners_report_is_not_found(self, world: ResultsWorld) -> None:
        _single_choice_paper(world, correct=3, total=4)
        _run_chain(world)

        with world.unit_of_work() as ctx, pytest.raises(errors.AppError) as caught:
            ctx.feedback.find_report("attempt-1", learner_id=OTHER_LEARNER_ID)

        assert caught.value.code == "ATTEMPT_NOT_FOUND"

    def test_an_ungenerated_report_is_not_found(self, world: ResultsWorld) -> None:
        _single_choice_paper(world, correct=3, total=4)
        world.score()

        with world.unit_of_work() as ctx, pytest.raises(errors.AppError) as caught:
            ctx.feedback.find_report("attempt-1")

        assert caught.value.code == "FEEDBACK_NOT_FOUND"

    def test_a_learner_can_list_their_own_reports(self, world: ResultsWorld) -> None:
        _single_choice_paper(world, correct=4, total=4, attempt_id="attempt-1")
        _run_chain(world, "attempt-1")

        with world.unit_of_work() as ctx:
            reports = ctx.feedback.list_reports(LEARNER_ID)

        assert [report.attempt_id for report in reports] == ["attempt-1"]
