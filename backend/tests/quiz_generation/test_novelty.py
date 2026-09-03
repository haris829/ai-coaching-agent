"""Every generation must produce questions the course has not been given before.

The company was explicit: *"every time they generate different question not same"*. That is a real
requirement for a system whose whole purpose is producing a fresh paper, and it does not come for
free — a model asked the same thing twice will happily answer the same way twice.

Two mechanisms, and both are tested here because either alone is insufficient:

* the prompt is told what has already been asked and told to test different points — which stops a
  repeat being written, and is far more effective than filtering afterwards;
* anything that comes back anyway is refused and counted — because a model told not to repeat
  itself sometimes does.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

from app.modules.quiz_generation.domain.generation import (
    AVOID_STEM_CHARS,
    MAX_AVOID_STEMS,
    CourseBrief,
    build_prompt,
)
from app.modules.quiz_generation.integration.catalogue import CatalogueLookup
from app.modules.quiz_generation.integration.question_bank import (
    GeneratedHistory,
    NoHistory,
    QuestionBankSink,
    QuestionBankView,
)
from app.modules.quiz_generation.services.generation_service import (
    QuestionGenerationService,
)
from app.modules.quiz_generation.services.quiz_service import GeneratedQuizService

BRIEF = CourseBrief(course_id="LL-1", name="Contract Law")


def _reply(texts: list[str]) -> str:
    return json.dumps(
        {
            "questions": [
                {
                    "question": text,
                    "options": {
                        "A": f"{text} a",
                        "B": f"{text} b",
                        "C": f"{text} c",
                        "D": f"{text} d",
                    },
                    "answer": "B",
                    "explanation": "Because.",
                }
                for text in texts
            ]
        }
    )


class Fixed:
    """Returns the same questions every single time — the worst case for novelty."""

    configured = True

    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.prompts: list[str] = []

    def complete(self, prompt: str, *, max_tokens: int = 0) -> str:  # noqa: ARG002
        self.prompts.append(prompt)
        return _reply(self.texts)


class Sink:
    def __init__(self) -> None:
        self.stored: list[str] = []

    def store(self, question, brief, *, actor=None, topics=()):  # noqa: ANN001, ANN201, ARG002
        self.stored.append(question.question_text)
        return f"id-{len(self.stored)}", None


class Remembers:
    """A history stub, so the service can be tested without a database."""

    def __init__(self, stems: tuple[str, ...] = ()) -> None:
        self.stems = stems
        self.asked: list[dict] = []

    def previous_stems(self, *, course_ref, topic, limit):  # noqa: ANN001, ANN201
        self.asked.append({"course_ref": course_ref, "topic": topic, "limit": limit})
        return self.stems


# ---------------------------------------------------------------------------
# The prompt is told what not to repeat
# ---------------------------------------------------------------------------


class TestThePrompt:
    def test_previously_asked_questions_appear_in_the_prompt(self) -> None:
        prompt = build_prompt(
            BRIEF, 5, avoid=("What is consideration?", "When is an offer revoked?")
        )

        assert "already been asked" in prompt
        assert "What is consideration?" in prompt
        assert "When is an offer revoked?" in prompt

    def test_it_forbids_rephrasing_as_well_as_repeating(self) -> None:
        """A reworded question is still the same question from the learner's side."""
        prompt = build_prompt(BRIEF, 5, avoid=("What is consideration?",))

        assert "Do not rephrase any of these" in prompt
        assert "same rule from a different angle" in prompt

    def test_a_course_with_no_history_gets_no_such_instruction(self) -> None:
        # A first generation has nothing to avoid, and saying so would waste the instruction.
        prompt = build_prompt(BRIEF, 5)

        assert "already been asked" not in prompt

    def test_the_list_is_capped_so_it_cannot_swallow_the_prompt(self) -> None:
        """Otherwise the hundredth generation spends its prompt reciting the first ninety-nine."""
        many = tuple(f"Question number {n} about something?" for n in range(200))

        prompt = build_prompt(BRIEF, 5, avoid=many)

        listed = [line for line in prompt.splitlines() if line.startswith("  - Question number")]
        assert len(listed) == MAX_AVOID_STEMS

    def test_each_entry_is_truncated(self) -> None:
        prompt = build_prompt(BRIEF, 5, avoid=("x" * 4000,))

        longest = max(
            (len(line) for line in prompt.splitlines() if line.startswith("  - ")), default=0
        )
        assert longest <= AVOID_STEM_CHARS + len("  - ")


# ---------------------------------------------------------------------------
# And a repeat that arrives anyway is refused
# ---------------------------------------------------------------------------


class TestTheFilter:
    def test_a_question_already_asked_for_this_course_is_dropped(self) -> None:
        """The mechanism that makes the guarantee hold even when the model ignores the prompt."""
        generator = Fixed(["What is consideration?", "A genuinely new question?"])
        sink = Sink()

        outcome = QuestionGenerationService(
            generator, sink, Remembers(("What is consideration?",))
        ).generate(BRIEF, count=2)

        assert sink.stored == ["A genuinely new question?"]
        assert outcome.created == 1
        assert outcome.rejected == 1
        assert any("already asked for this course" in r for r in outcome.reasons)

    def test_the_match_ignores_case_and_surrounding_space(self) -> None:
        # "What is consideration?" and "  what IS consideration?  " are the same question.
        generator = Fixed(["  what IS consideration?  "])
        sink = Sink()

        with pytest.raises(Exception):  # noqa: B017 - nothing survived, which is the point
            QuestionGenerationService(
                generator, sink, Remembers(("What is consideration?",))
            ).generate(BRIEF, count=1)

        assert sink.stored == []

    def test_without_a_history_nothing_is_avoided(self) -> None:
        """The default is honest: no history means no guarantee, not a silent one."""
        generator = Fixed(["What is consideration?"])
        sink = Sink()

        QuestionGenerationService(generator, sink, NoHistory()).generate(BRIEF, count=1)

        assert sink.stored == ["What is consideration?"]

    def test_the_history_is_looked_up_once_for_the_whole_run(self) -> None:
        """Every batch avoids the same history, rather than each rediscovering it."""
        history = Remembers()

        QuestionGenerationService(
            Fixed([f"Question {n}?" for n in range(10)]), Sink(), history
        ).generate(BRIEF, count=50)

        assert len(history.asked) == 1
        assert history.asked[0]["course_ref"] == "LL-1"
        assert history.asked[0]["topic"] == "Contract Law"


# ---------------------------------------------------------------------------
# End to end, against the real database
# ---------------------------------------------------------------------------


class TestAgainstTheDatabase:
    @staticmethod
    def _service(db: Session, texts: list[str]) -> GeneratedQuizService:
        return GeneratedQuizService(
            db,
            generator=Fixed(texts),
            sink=QuestionBankSink(db),
            view=QuestionBankView(db),
            courses=CatalogueLookup(db),
            history=GeneratedHistory(db),
        )

    def test_a_second_generation_for_the_same_topic_repeats_nothing(
        self, db: Session
    ) -> None:
        """The company's requirement, end to end, with a model that always says the same thing."""
        texts = [f"Question about consideration {n}?" for n in range(3)]

        first = self._service(db, texts).create(topic="Contract Law", count=3)
        assert len(first.questions) == 3

        with pytest.raises(Exception):  # noqa: B017
            # Every question it offers has already been asked, so nothing survives — which is
            # correct, and is reported rather than silently returning a duplicate paper.
            self._service(db, texts).create(topic="Contract Law", count=3)

        stems = [q.question for q in first.questions]
        assert len(set(stems)) == 3

    def test_a_second_generation_keeps_only_what_is_new(self, db: Session) -> None:
        first = self._service(db, ["Old one?", "Also old?"]).create(
            topic="Contract Law", count=2
        )
        assert len(first.questions) == 2

        second = self._service(db, ["Old one?", "Brand new one?"]).create(
            topic="Contract Law", count=2
        )

        assert [q.question for q in second.questions] == ["Brand new one?"]
        assert second.rejected == 1

    def test_history_is_matched_on_the_course_code_too(self, db: Session) -> None:
        """A caller who names the course on one run and not the next gets no repeats either."""
        from app.modules.quiz_configuration.models import Course

        db.add(Course(code="LL-900", title="Anti-Money Laundering"))
        db.commit()

        self._service(db, ["A CDD question?"]).create(
            topic="AML", count=1, course_ref="LL-900"
        )

        with pytest.raises(Exception):  # noqa: B017
            self._service(db, ["A CDD question?"]).create(
                topic="Something else entirely", count=1, course_ref="LL-900"
            )

    def test_the_history_filter_is_scoped_to_one_course(self) -> None:
        """This module's own filter asks only about the course in hand.

        Asserted at the port rather than end to end, because UC-02 imposes a *stronger* rule on top
        of it — see the next test.
        """
        history = Remembers()

        QuestionGenerationService(Fixed(["A question?"]), Sink(), history).generate(
            CourseBrief(course_id="LL-77", name="Commercial Law"), count=1
        )

        assert history.asked[0]["course_ref"] == "LL-77"
        assert history.asked[0]["topic"] == "Commercial Law"

    def test_the_question_bank_refuses_an_equivalent_question_across_all_courses(
        self, db: Session
    ) -> None:
        """A constraint that comes from UC-02, not from here, and is worth knowing about.

        The question bank holds one copy of a question, full stop. So a point genuinely shared by
        two courses — "what is consideration" belongs to both contract law and commercial law —
        can only be stored once, and the second course's generation has it refused with the bank's
        own ConflictError rather than with this module's "already asked" reason.

        For generation that is mostly helpful: it is a second, stricter net under the per-course
        filter. It is recorded here because it is a *product* decision someone may want to revisit
        — a shared question arguably ought to be askable on both courses — and because the reason
        a caller sees in that case comes from UC-02 and reads differently.
        """
        first = self._service(db, ["What is consideration?"]).create(
            topic="Contract Law", count=1
        )
        assert len(first.questions) == 1

        second = self._service(db, ["What is consideration?"]).create(
            topic="Commercial Law", count=1
        )

        assert second.questions == ()
        assert second.rejected == 1
        assert any("already exists in the bank" in r for r in second.reasons)


# ---------------------------------------------------------------------------
# The course list they asked for
# ---------------------------------------------------------------------------


class TestTheCourseList:
    def test_it_lists_courses_by_title_with_what_is_known_about_each(
        self, db: Session
    ) -> None:
        """"First they see the course which is already present, loaded by name and title."""
        from app.modules.quiz_configuration.models import Course

        db.add_all(
            [
                Course(code="LL-2", title="Zebra Law", description="A brief.", rqf_level=7),
                Course(code="LL-1", title="Alpha Law"),
            ]
        )
        db.commit()

        courses = CatalogueLookup(db).list_all()

        # Ordered by title, because a person choosing a course reads the name.
        assert [c.title for c in courses] == ["Alpha Law", "Zebra Law"]
        assert courses[0].has_brief is False, "no description to generate from"
        assert courses[1].has_brief is True
        assert courses[1].rqf_level == 7

    def test_it_reports_how_many_quizzes_a_course_has_already_had(
        self, db: Session
    ) -> None:
        from app.modules.quiz_configuration.models import Course

        db.add(Course(code="LL-5", title="Some Law"))
        db.commit()
        GeneratedQuizService(
            db,
            generator=Fixed(["A question?"]),
            sink=QuestionBankSink(db),
            view=QuestionBankView(db),
            courses=CatalogueLookup(db),
            history=GeneratedHistory(db),
        ).create(topic="Some Law", count=1, course_ref="LL-5")

        courses = {c.code: c for c in CatalogueLookup(db).list_all()}

        assert courses["LL-5"].generated_count == 1
