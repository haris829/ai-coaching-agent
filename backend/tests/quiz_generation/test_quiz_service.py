"""Generating a quiz and marking it — against the real question bank, not a fake one.

The domain tests cover the prompt and the parser. These cover what happens either side of them: a
generated question really going through UC-02's validator into a real row, and answers really being
marked against what the database holds.

The model is faked — a stub returning a fixed JSON payload — because the point here is the wiring,
and a real call would make these tests slow, costly and non-deterministic. Everything downstream of
the model is real: real session, real ``create_question``, real ``qz_`` rows.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationError
from app.modules.question_bank.models import Question
from app.modules.quiz_configuration.models import Course
from app.modules.quiz_generation.integration.catalogue import CatalogueLookup
from app.modules.quiz_generation.integration.llm import (
    QuestionGenerationFailedError,
)
from app.modules.quiz_generation.integration.question_bank import (
    QuestionBankSink,
    QuestionBankView,
)
from app.modules.quiz_generation.models import GeneratedQuiz
from app.modules.quiz_generation.services.quiz_service import GeneratedQuizService

# ---------------------------------------------------------------------------
# Fakes for the one collaborator worth faking
# ---------------------------------------------------------------------------


def _payload(count: int) -> str:
    """``count`` well-formed questions, each distinguishable from the others."""
    return json.dumps(
        {
            "questions": [
                {
                    "question": f"Question number {index}?",
                    "options": {
                        "A": f"first option {index}",
                        "B": f"second option {index}",
                        "C": f"third option {index}",
                        "D": f"fourth option {index}",
                    },
                    # A rotating key, so a test that always answers "A" cannot pass by accident.
                    "answer": "ABCD"[index % 4],
                    "explanation": f"Because of reason {index}.",
                }
                for index in range(count)
            ]
        }
    )


class StubGenerator:
    """A model that returns exactly what it was told to, and records the prompt it was given."""

    configured = True

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str, *, max_tokens: int = 0) -> str:  # noqa: ARG002
        self.prompts.append(prompt)
        return self.reply


@pytest.fixture
def service(db: Session) -> GeneratedQuizService:
    return GeneratedQuizService(
        db,
        generator=StubGenerator(_payload(3)),
        sink=QuestionBankSink(db),
        view=QuestionBankView(db),
        courses=CatalogueLookup(db),
    )


def _service(db: Session, reply: str) -> GeneratedQuizService:
    return GeneratedQuizService(
        db,
        generator=StubGenerator(reply),
        sink=QuestionBankSink(db),
        view=QuestionBankView(db),
        courses=CatalogueLookup(db),
    )


# ---------------------------------------------------------------------------
# Generating
# ---------------------------------------------------------------------------


class TestGenerating:
    def test_the_questions_land_in_the_question_bank_as_draft(
        self, service: GeneratedQuizService, db: Session
    ) -> None:
        """The property that keeps a model's output away from a learner.

        UC-02 delivers only ACTIVE questions, so DRAFT means an administrator has to read a
        generated question before anybody is assessed on it.
        """
        view = service.create(topic="Contract formation", count=3)

        stored = db.query(Question).all()
        assert len(stored) == 3
        assert {question.status for question in stored} == {"DRAFT"}
        assert len(view.questions) == 3

    def test_every_stored_question_has_four_options_and_exactly_one_key(
        self, service: GeneratedQuizService, db: Session
    ) -> None:
        # Not a restatement of the parser's test: this asserts what UC-02's validator accepted and
        # wrote, which is the thing a learner would actually be shown.
        service.create(topic="Contract formation", count=3)

        for question in db.query(Question).all():
            assert len(question.options) == 4
            assert sum(option.is_correct for option in question.options) == 1

    def test_the_quiz_remembers_which_questions_it_asked_and_in_what_order(
        self, service: GeneratedQuizService, db: Session
    ) -> None:
        view = service.create(topic="Contract formation", count=3)

        quiz = db.get(GeneratedQuiz, view.quiz_id)
        assert quiz is not None
        assert [link.sequence for link in quiz.questions] == [1, 2, 3]
        assert [question.sequence for question in view.questions] == [1, 2, 3]

    def test_the_pass_mark_is_frozen_onto_the_quiz(
        self, service: GeneratedQuizService, db: Session
    ) -> None:
        """A quiz already sat must not be re-marked against a threshold changed afterwards."""
        view = service.create(topic="Contract formation", count=3, pass_mark=80)

        assert view.pass_mark == 80
        assert db.get(GeneratedQuiz, view.quiz_id).pass_mark == 80

    def test_the_default_pass_mark_is_the_fifty_percent_in_the_brief(
        self, service: GeneratedQuizService
    ) -> None:
        assert service.create(topic="Contract formation", count=3).pass_mark == 50

    def test_a_named_course_reaches_the_prompt_with_its_level_and_description(
        self, db: Session
    ) -> None:
        """The reason the catalogue lookup exists at all."""
        db.add(
            Course(
                code="LL-900",
                title="Anti-Money Laundering for Fee Earners",
                description="Client due diligence and reporting a suspicion.",
                rqf_level=6,
            )
        )
        db.commit()
        generator = StubGenerator(_payload(2))
        instance = GeneratedQuizService(
            db,
            generator=generator,
            sink=QuestionBankSink(db),
            view=QuestionBankView(db),
            courses=CatalogueLookup(db),
        )

        instance.create(topic="AML", count=2, course_ref="LL-900")

        prompt = generator.prompts[0]
        assert "Anti-Money Laundering for Fee Earners" in prompt
        assert "RQF 6" in prompt
        assert "Client due diligence" in prompt

    def test_an_unknown_course_code_is_not_an_error(
        self, service: GeneratedQuizService
    ) -> None:
        """The code is a hint. If it does not resolve, the topic the caller typed still stands."""
        view = service.create(topic="Contract formation", count=3, course_ref="LL-NOPE")

        assert len(view.questions) == 3
        assert view.course_ref == "LL-NOPE"

    def test_a_blank_topic_is_refused_before_a_model_is_called(
        self, service: GeneratedQuizService
    ) -> None:
        with pytest.raises(ValidationError):
            service.create(topic="   ", count=3)

    def test_a_pass_mark_outside_zero_to_a_hundred_is_refused(
        self, service: GeneratedQuizService
    ) -> None:
        with pytest.raises(ValidationError):
            service.create(topic="Contract formation", count=3, pass_mark=140)

    def test_a_malformed_reply_fails_the_request_and_stores_nothing(
        self, db: Session
    ) -> None:
        """Nothing is repaired, and nothing half-made is left behind.

        The failure is raised rather than returned as an empty success: a caller who asked for
        twenty questions and silently got none would have no way to tell that from a quiz that
        genuinely has none. The reason travels with the error, so a low yield is diagnosable.
        """
        with pytest.raises(QuestionGenerationFailedError) as failure:
            _service(db, "I am not JSON.").create(topic="Contract formation", count=3)

        # The reason goes to `log_context`, not `context`: an operator needs to know the model
        # returned prose, and a caller only needs to know the request failed and may be retried.
        assert "not JSON" in str(failure.value.log_context)
        assert failure.value.status_code == 502
        assert db.query(Question).count() == 0
        assert db.query(GeneratedQuiz).count() == 0, "no quiz row for a failed generation"

    def test_one_unusable_question_costs_only_itself(self, db: Session) -> None:
        reply = json.dumps(
            {
                "questions": [
                    json.loads(_payload(1))["questions"][0],
                    {
                        "question": "A broken one?",
                        "options": {"A": "one", "B": "two", "C": "three", "D": "four"},
                        "answer": "Z",
                    },
                    {
                        "question": "A good one?",
                        "options": {"A": "w", "B": "x", "C": "y", "D": "z"},
                        "answer": "C",
                        "explanation": "Because.",
                    },
                ]
            }
        )
        view = _service(db, reply).create(topic="Contract formation", count=3)

        assert len(view.questions) == 2
        assert view.rejected == 1


# ---------------------------------------------------------------------------
# Marking
# ---------------------------------------------------------------------------


class TestMarking:
    @staticmethod
    def _correct_answers(view) -> dict[str, str]:
        return {str(question.sequence): question.answer for question in view.questions}

    def test_all_correct_passes(self, service: GeneratedQuizService) -> None:
        view = service.create(topic="Contract formation", count=3)

        result = service.mark(view.quiz_id, self._correct_answers(view))

        assert result.correct == 3
        assert result.percentage == 100.0
        assert result.passed is True

    def test_all_wrong_fails(self, service: GeneratedQuizService) -> None:
        view = service.create(topic="Contract formation", count=3)
        wrong = {
            str(question.sequence): next(
                label for label in "ABCD" if label != question.answer
            )
            for question in view.questions
        }

        result = service.mark(view.quiz_id, wrong)

        assert result.correct == 0
        assert result.passed is False

    def test_a_missing_answer_is_marked_wrong_not_skipped(
        self, service: GeneratedQuizService
    ) -> None:
        """Otherwise a caller improves their percentage by omitting what they were unsure of."""
        view = service.create(topic="Contract formation", count=3)
        answers = self._correct_answers(view)
        answers.pop("3")

        result = service.mark(view.quiz_id, answers)

        assert result.total == 3
        assert result.correct == 2
        assert result.percentage == pytest.approx(66.67, abs=0.01)
        assert result.answers[2].given is None
        assert result.answers[2].is_correct is False

    def test_fifty_percent_passes(self, db: Session) -> None:
        """The boundary the brief left open, resolved the same way UC-05 resolves it."""
        view = _service(db, _payload(4)).create(topic="Contract formation", count=4)
        instance = _service(db, _payload(4))
        answers = {str(q.sequence): q.answer for q in view.questions[:2]}

        result = instance.mark(view.quiz_id, answers)

        assert result.percentage == 50.0
        assert result.pass_mark == 50.0
        assert result.passed is True, "50% must pass, as it does in UC-05"

    def test_just_under_the_pass_mark_fails(self, db: Session) -> None:
        view = _service(db, _payload(4)).create(
            topic="Contract formation", count=4, pass_mark=75
        )
        answers = {str(q.sequence): q.answer for q in view.questions[:2]}

        result = _service(db, _payload(4)).mark(view.quiz_id, answers)

        assert result.percentage == 50.0
        assert result.passed is False

    def test_answers_may_be_keyed_by_q1_style_labels(
        self, service: GeneratedQuizService
    ) -> None:
        # The company's sketch wrote them as "Q1-Q20", so that form has to work.
        view = service.create(topic="Contract formation", count=3)
        answers = {f"Q{question.sequence}": question.answer for question in view.questions}

        assert service.mark(view.quiz_id, answers).correct == 3

    def test_answers_may_be_keyed_by_question_id(
        self, service: GeneratedQuizService
    ) -> None:
        view = service.create(topic="Contract formation", count=3)
        answers = {question.question_id: question.answer for question in view.questions}

        assert service.mark(view.quiz_id, answers).correct == 3

    def test_a_lower_case_answer_letter_is_accepted(
        self, service: GeneratedQuizService
    ) -> None:
        view = service.create(topic="Contract formation", count=3)
        answers = {
            str(question.sequence): question.answer.lower() for question in view.questions
        }

        assert service.mark(view.quiz_id, answers).correct == 3

    def test_marking_an_unknown_quiz_is_a_not_found(
        self, service: GeneratedQuizService
    ) -> None:
        with pytest.raises(NotFoundError):
            service.mark("no-such-quiz", {"1": "A"})

    def test_marking_uses_the_database_not_the_submitted_payload(
        self, service: GeneratedQuizService, db: Session
    ) -> None:
        """The property that makes a pass mean something.

        A caller can send anything. The key comes from ``qb_question_options``, so rewriting the
        options in a submission cannot change what is correct.
        """
        view = service.create(topic="Contract formation", count=3)
        first = view.questions[0]
        db.execute(
            text(
                "UPDATE qb_question_options SET is_correct = 0 "
                "WHERE question_id = :qid"
            ),
            {"qid": first.question_id},
        )
        db.execute(
            text(
                "UPDATE qb_question_options SET is_correct = 1 "
                "WHERE question_id = :qid AND label = :label"
            ),
            {"qid": first.question_id, "label": "D"},
        )
        db.commit()

        result = service.mark(view.quiz_id, {"1": first.answer})

        # The answer that was correct at generation is no longer the key in the database, and the
        # database wins.
        if first.answer != "D":
            assert result.answers[0].is_correct is False


# ---------------------------------------------------------------------------
# Reading it back
# ---------------------------------------------------------------------------


class TestReading:
    def test_find_returns_the_quiz_with_its_questions(
        self, service: GeneratedQuizService
    ) -> None:
        created = service.create(topic="Contract formation", count=3)

        found = service.find(created.quiz_id)

        assert found.quiz_id == created.quiz_id
        assert found.topic == "Contract formation"
        assert len(found.questions) == 3
        assert found.questions[0].options.keys() == {"A", "B", "C", "D"}

    def test_finding_an_unknown_quiz_is_a_not_found(
        self, service: GeneratedQuizService
    ) -> None:
        with pytest.raises(NotFoundError):
            service.find("no-such-quiz")
