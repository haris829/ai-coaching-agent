"""Finding a course by what the caller actually has, and asking for it more than once.

Two requirements from the company, both about how their platform will really call this:

* **They send a course name, not our code.** Nobody integrating against us knows that Medical Law MA
  is ``LL-45165``. If only the code resolved, every one of their requests would generate from a bare
  string and the course's description and level would never be used.
* **They ask repeatedly, with different counts.** Ten questions now, twenty later, same course. Each
  call must produce a quiz, and must not repeat what the earlier call already asked.
"""

from __future__ import annotations

import itertools
import json
import threading

import pytest
from sqlalchemy.orm import Session

from app.modules.quiz_configuration.models import Course
from app.modules.quiz_generation.integration.catalogue import CatalogueLookup
from app.modules.quiz_generation.integration.question_bank import (
    GeneratedHistory,
    QuestionBankSink,
    QuestionBankView,
)
from app.modules.quiz_generation.services.quiz_service import GeneratedQuizService


class Unique:
    """A model that never repeats itself, so any repeat seen in a test is the service's doing."""

    configured = True

    #: Shared across instances on purpose. Each `_service(...)` call builds a fresh generator, and
    #: if numbering restarted per instance every "run" would return the same questions — the test
    #: would then be measuring the stub, not the deduplication it is meant to exercise.
    _counter = itertools.count()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.prompts: list[str] = []

    def complete(self, prompt: str, *, max_tokens: int = 0) -> str:  # noqa: ARG002
        with self._lock:
            self.prompts.append(prompt)
        return json.dumps(
            {
                "questions": [
                    {
                        "question": f"Distinct question {next(Unique._counter)}?",
                        "options": {
                            "A": f"opt a {i}",
                            "B": f"opt b {i}",
                            "C": f"opt c {i}",
                            "D": f"opt d {i}",
                        },
                        "answer": "ABCD"[i % 4],
                        "explanation": "Because.",
                    }
                    for i in range(40)
                ]
            }
        )


@pytest.fixture
def catalogue(db: Session) -> CatalogueLookup:
    db.add_all(
        [
            Course(
                code="LL-45165",
                title="Medical Law MA (Postgraduate)",
                description="Consent, capacity, and clinical negligence.",
                rqf_level=7,
            ),
            Course(code="LL-52720", title="International Law (Postgraduate)"),
            Course(code="LL-52728", title="International Trade Law (Postgraduate)"),
            Course(code="SEC-101", title="Information Security Fundamentals"),
            Course(code="ODD-1", title="100% Practical_Skills"),
        ]
    )
    db.commit()
    return CatalogueLookup(db)


def _service(db: Session, generator: Unique) -> GeneratedQuizService:
    return GeneratedQuizService(
        db,
        generator=generator,
        sink=QuestionBankSink(db),
        view=QuestionBankView(db),
        courses=CatalogueLookup(db),
        history=GeneratedHistory(db),
    )


# ---------------------------------------------------------------------------
# Resolving a course
# ---------------------------------------------------------------------------


class TestFindingACourse:
    def test_the_catalogue_code_resolves(self, catalogue: CatalogueLookup) -> None:
        brief = catalogue.find("LL-45165")

        assert brief is not None
        assert brief.name == "Medical Law MA (Postgraduate)"
        assert brief.rqf_level == 7

    def test_the_course_name_resolves(self, catalogue: CatalogueLookup) -> None:
        """The one that matters: their platform sends names, not our codes."""
        brief = catalogue.find("Medical Law MA (Postgraduate)")

        assert brief is not None
        assert brief.course_id == "LL-45165"
        assert brief.description.startswith("Consent, capacity")

    @pytest.mark.parametrize(
        "reference",
        [
            "medical law ma (postgraduate)",
            "MEDICAL LAW MA (POSTGRADUATE)",
            "  Medical Law MA (Postgraduate)  ",
            "Medical   Law  MA (Postgraduate)",
            "ll-45165",
        ],
    )
    def test_case_and_spacing_do_not_matter(
        self, catalogue: CatalogueLookup, reference: str
    ) -> None:
        # A name arriving from another system carries whatever spacing and case it carries.
        brief = catalogue.find(reference)

        assert brief is not None
        assert brief.course_id == "LL-45165"

    def test_a_unique_partial_name_resolves(self, catalogue: CatalogueLookup) -> None:
        brief = catalogue.find("Information Security")

        assert brief is not None
        assert brief.course_id == "SEC-101"

    def test_an_ambiguous_partial_resolves_to_nothing(
        self, catalogue: CatalogueLookup
    ) -> None:
        """Two courses start with "International". Guessing between them is worse than not matching.

        A wrong match generates a plausible quiz for a course the caller did not ask for, and gives
        no sign that it did. No match falls back to generating from the name, which is honest.
        """
        assert catalogue.find("International") is None

    def test_a_very_short_reference_is_not_partially_matched(
        self, catalogue: CatalogueLookup
    ) -> None:
        # "Law" is in most of the catalogue; matching on it is a coin toss dressed up as a lookup.
        assert catalogue.find("Law") is None
        assert catalogue.find("MA") is None

    def test_like_wildcards_in_the_reference_are_escaped(
        self, catalogue: CatalogueLookup
    ) -> None:
        """`%` and `_` are LIKE wildcards, and a course title legitimately contains them.

        Unescaped, searching for "%" would match every course and the ambiguity check would be the
        only thing standing between the caller and a random quiz.
        """
        assert catalogue.find("100% Practical_Skills") is not None
        assert catalogue.find("%%%%%") is None
        assert catalogue.find("_____") is None

    def test_an_unknown_reference_resolves_to_nothing(
        self, catalogue: CatalogueLookup
    ) -> None:
        assert catalogue.find("Underwater Basket Weaving") is None
        assert catalogue.find("") is None


# ---------------------------------------------------------------------------
# The topic itself is tried as a course reference
# ---------------------------------------------------------------------------


class TestTheTopicIsTriedToo:
    def test_a_topic_that_names_a_course_picks_up_its_brief(self, db: Session) -> None:
        """A caller sending only a topic has still named a course. Use what we know about it."""
        db.add(
            Course(
                code="LL-45165",
                title="Medical Law MA",
                description="Consent, capacity, and clinical negligence.",
                rqf_level=7,
            )
        )
        db.commit()
        generator = Unique()

        _service(db, generator).create(topic="Medical Law MA", count=2)

        prompt = generator.prompts[0]
        assert "RQF 7" in prompt
        assert "Consent, capacity" in prompt

    def test_a_topic_that_names_no_course_still_generates(self, db: Session) -> None:
        # The catalogue is a source of extra detail, never a gate on what may be asked.
        generator = Unique()

        view = _service(db, generator).create(topic="Underwater Basket Weaving", count=2)

        assert len(view.questions) == 2
        assert "Underwater Basket Weaving" in generator.prompts[0]


# ---------------------------------------------------------------------------
# Asking again, with a different count
# ---------------------------------------------------------------------------


class TestAskingAgain:
    def test_ten_then_twenty_for_the_same_course(self, db: Session) -> None:
        """Their stated pattern: ten now, twenty later, same course.

        Both must succeed, and the second must not re-ask what the first already asked.
        """
        first = _service(db, Unique()).create(topic="Contract Law", count=10)
        second = _service(db, Unique()).create(topic="Contract Law", count=20)

        assert len(first.questions) == 10
        assert len(second.questions) == 20
        assert first.quiz_id != second.quiz_id

        a = {q.question.strip().casefold() for q in first.questions}
        b = {q.question.strip().casefold() for q in second.questions}
        assert not (a & b), "the second request re-asked something from the first"

    def test_the_second_request_is_told_what_the_first_asked(self, db: Session) -> None:
        _service(db, Unique()).create(topic="Contract Law", count=10)
        generator = Unique()

        _service(db, generator).create(topic="Contract Law", count=20)

        assert "already been asked" in generator.prompts[0]

    def test_each_request_is_its_own_quiz_with_its_own_pass_mark(
        self, db: Session
    ) -> None:
        first = _service(db, Unique()).create(topic="Contract Law", count=10, pass_mark=50)
        second = _service(db, Unique()).create(topic="Contract Law", count=20, pass_mark=75)

        assert first.pass_mark == 50
        assert second.pass_mark == 75
        assert first.requested_count == 10
        assert second.requested_count == 20

    def test_a_third_and_fourth_request_still_work(self, db: Session) -> None:
        """History accumulates. It must narrow the questions, not choke the request."""
        seen: set[str] = set()
        for count in (5, 10, 15, 20):
            view = _service(db, Unique()).create(topic="Contract Law", count=count)
            assert len(view.questions) == count, f"asked for {count}"
            stems = {q.question.strip().casefold() for q in view.questions}
            assert not (stems & seen), f"the run of {count} repeated an earlier question"
            seen |= stems

        assert len(seen) == 50

    def test_the_matched_course_is_recorded_even_when_only_a_name_was_sent(
        self, db: Session
    ) -> None:
        """A caller must be able to tell a match from a silent miss.

        Without this the response reports no course for a request that did resolve one, and the
        one case worth seeing — the name matched nothing, so the quiz came from a bare string
        rather than a syllabus — looks identical to the case where everything worked.
        """
        db.add(Course(code="LL-45165", title="Medical Law MA", description="Consent."))
        db.commit()

        view = _service(db, Unique()).create(topic="Medical Law MA", count=2)

        assert view.course_ref == "LL-45165"

    def test_an_unmatched_name_records_no_course(self, db: Session) -> None:
        view = _service(db, Unique()).create(topic="Underwater Basket Weaving", count=2)

        assert view.course_ref is None
