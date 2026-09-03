"""The prompt and the parser — the two things generation gets wrong if anything does.

The parser is where a model's output stops being text and starts being a question a professional
will be certified against, so most of these tests are about what it **refuses**. A parser that
accepts a malformed question has not saved a question; it has hidden a defect inside a certificate.
"""

from __future__ import annotations

import json

from app.modules.question_bank.domain.enums import (
    SINGLE_CHOICE_OPTION_COUNT as BANK_OPTION_COUNT,
)
from app.modules.quiz_generation.domain.generation import (
    MAX_QUESTIONS_PER_REQUEST,
    OPTION_LABELS,
    SINGLE_CHOICE_OPTION_COUNT,
    CourseBrief,
    build_prompt,
    parse_questions,
)

BRIEF = CourseBrief(
    course_id="course-1",
    name="Anti-Money Laundering for Fee Earners",
    description="Client due diligence, reporting a suspicion, and record keeping.",
    rqf_level=6,
    subject_area="Regulatory compliance",
    modules=("Customer Due Diligence", "Reporting a Suspicion"),
)


def _reply(*questions: dict) -> str:
    return json.dumps({"questions": list(questions)})


def _question(
    text: str = "When must client identity be verified?",
    answer: str = "B",
    **options: str,
) -> dict:
    chosen = options or {
        "A": "After the matter concludes",
        "B": "Before the retainer begins",
        "C": "Only if the client is overseas",
        "D": "Annually, regardless of matter",
    }
    return {
        "question": text,
        "options": chosen,
        "answer": answer,
        "explanation": "Verification precedes acting for the client.",
    }


# ---------------------------------------------------------------------------
# The constant that must not drift
# ---------------------------------------------------------------------------


def test_the_option_count_matches_the_question_banks_own_rule() -> None:
    """Generation declares four options; UC-02 requires four.

    The generator's domain may not import the question bank — a domain package reaching into another
    capability is the boundary the architecture tests forbid — so the constant is declared twice.
    This is the test that stops the two copies parting company: if UC-02 ever moves to five options,
    this fails rather than generation quietly producing questions the validator will reject.
    """
    assert SINGLE_CHOICE_OPTION_COUNT == BANK_OPTION_COUNT
    assert len(OPTION_LABELS) == BANK_OPTION_COUNT


# ---------------------------------------------------------------------------
# The prompt
# ---------------------------------------------------------------------------


class TestThePrompt:
    def test_it_carries_the_course_the_level_and_the_modules(self) -> None:
        prompt = build_prompt(BRIEF, 20)

        assert "Anti-Money Laundering for Fee Earners" in prompt
        assert "RQF 6" in prompt
        assert "Customer Due Diligence" in prompt
        assert "Write 20 multiple-choice questions" in prompt

    def test_it_asks_for_json_rather_than_prose(self) -> None:
        # The company's own example produced prose, which is guesswork to parse. See the module
        # docstring in domain/generation.py.
        prompt = build_prompt(BRIEF, 5)

        assert '"questions"' in prompt
        assert "Return ONLY a JSON object" in prompt

    def test_it_states_the_rules_the_parser_actually_enforces(self) -> None:
        # A rule the prompt asks for but nothing checks is a wish. These three are all checked.
        prompt = build_prompt(BRIEF, 5)

        assert "exactly 4 options" in prompt
        assert "exactly one option is correct" in prompt
        assert "no two questions may test the same point" in prompt

    def test_a_course_with_nothing_but_a_name_still_produces_a_prompt(self) -> None:
        # Most of their catalogue has no description. Generation must degrade, not fail.
        prompt = build_prompt(CourseBrief(course_id="c", name="Contract Law"), 3)

        assert "Contract Law" in prompt
        assert "RQF" not in prompt


# ---------------------------------------------------------------------------
# What the parser accepts
# ---------------------------------------------------------------------------


class TestWhatIsAccepted:
    def test_a_well_formed_question_survives_with_its_answer_key(self) -> None:
        report = parse_questions(_reply(_question()), wanted=5)

        assert report.count == 1
        assert report.rejected == 0
        question = report.accepted[0]
        assert question.answer_label == "B"
        assert [option.label for option in question.options] == list(OPTION_LABELS)
        assert sum(option.is_correct for option in question.options) == 1
        assert question.explanation == "Verification precedes acting for the client."

    def test_a_fenced_code_block_is_tolerated(self) -> None:
        # Models wrap JSON in ```json fences often enough that refusing it would waste real calls.
        fenced = f"```json\n{_reply(_question())}\n```"

        assert parse_questions(fenced, wanted=5).count == 1

    def test_prose_around_the_json_is_tolerated(self) -> None:
        noisy = f"Certainly! Here are your questions:\n{_reply(_question())}\nHope this helps."

        assert parse_questions(noisy, wanted=5).count == 1

    def test_a_lower_case_answer_letter_is_accepted(self) -> None:
        report = parse_questions(_reply(_question(answer="c")), wanted=5)

        assert report.count == 1
        assert report.accepted[0].answer_label == "C"

    def test_it_never_returns_more_than_was_asked_for(self) -> None:
        # A model that returns thirty when asked for two has not followed instructions; keeping the
        # extras would make quiz length unpredictable.
        many = _reply(*[_question(text=f"Question number {index}?") for index in range(30)])

        assert parse_questions(many, wanted=2).count == 2


# ---------------------------------------------------------------------------
# What the parser refuses — the part that matters
# ---------------------------------------------------------------------------


class TestWhatIsRefused:
    def test_a_reply_that_is_not_json_yields_nothing(self) -> None:
        report = parse_questions("Q1. What is a retainer? A. ... Answer: B", wanted=5)

        assert report.count == 0
        assert "the reply was not JSON" in report.reasons

    def test_an_empty_reply_yields_nothing(self) -> None:
        assert parse_questions("", wanted=5).count == 0
        assert parse_questions("   ", wanted=5).count == 0

    def test_a_question_with_three_options_is_dropped(self) -> None:
        report = parse_questions(
            _reply(_question(A="one", B="two", C="three")), wanted=5
        )

        assert report.count == 0
        assert report.rejected == 1
        assert any("missing option D" in reason for reason in report.reasons)

    def test_an_answer_outside_a_to_d_is_dropped(self) -> None:
        report = parse_questions(_reply(_question(answer="E")), wanted=5)

        assert report.count == 0
        assert report.rejected == 1
        assert any("not one of A-D" in reason for reason in report.reasons)

    def test_duplicate_option_text_is_dropped(self) -> None:
        # Two identical options make the question unanswerable.
        report = parse_questions(
            _reply(
                _question(A="same text", B="same text", C="third", D="fourth")
            ),
            wanted=5,
        )

        assert report.count == 0
        assert any("duplicate options" in reason for reason in report.reasons)

    def test_a_repeated_question_is_dropped_but_the_first_is_kept(self) -> None:
        report = parse_questions(_reply(_question(), _question()), wanted=5)

        assert report.count == 1
        assert report.rejected == 1
        assert any("repeated" in reason for reason in report.reasons)

    def test_a_question_with_no_text_is_dropped(self) -> None:
        report = parse_questions(_reply(_question(text="   ")), wanted=5)

        assert report.count == 0
        assert report.rejected == 1

    def test_an_empty_option_is_dropped(self) -> None:
        report = parse_questions(
            _reply(_question(A="one", B="", C="three", D="four")), wanted=5
        )

        assert report.count == 0
        assert report.rejected == 1

    def test_one_bad_question_costs_only_itself(self) -> None:
        """The property that makes a twenty-question run worth attempting at all."""
        report = parse_questions(
            _reply(
                _question(text="A good question?"),
                _question(text="A broken one?", answer="Z"),
                _question(text="Another good question?"),
            ),
            wanted=5,
        )

        assert report.count == 2
        assert report.rejected == 1

    def test_nothing_is_repaired(self) -> None:
        """A malformed question is dropped, never fixed up.

        Repairing it — picking an answer, inventing a fourth option — is how a plausible wrong
        answer reaches somebody's certificate.
        """
        report = parse_questions(_reply(_question(answer="")), wanted=5)

        assert report.count == 0
        assert report.rejected == 1


def test_the_request_ceiling_is_a_guard_not_a_product_rule() -> None:
    # Documented as a guard against a mistyped count, so it should be generous but finite.
    assert 20 <= MAX_QUESTIONS_PER_REQUEST <= 100
