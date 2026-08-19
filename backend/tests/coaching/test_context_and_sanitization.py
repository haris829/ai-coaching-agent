"""The coaching context and the answer-key sanitiser (§11, §12, §13, §25, §26, §33).

Two questions are being asked here, and they are different:

1. does the context contain what a coach needs to teach? (§11)
2. does it contain anything that reveals the answer? (§12)

The second is checked against material that genuinely contains answer keys, explanations and
correct-answer text — see ``tests/conftest.py``. A sanitiser tested only on clean input proves
nothing.
"""

from __future__ import annotations

import json

import pytest

from app.modules.coaching.domain.context import SafeCoachingContext
from app.modules.coaching.domain.errors import AnswerKeyContaminationError
from app.modules.coaching.domain.sanitizer import (
    FORBIDDEN_KEY_FRAGMENTS,
    CoachingContextSanitizer,
    RawCoachingMaterial,
    SanitizedCoachingContext,
    forbidden_values,
    scrub_text,
    verify,
)
from app.modules.coaching.integration.uc03 import LearnerAnswer, QuestionType
from tests.coaching.world import (
    ATTEMPT_1,
    COURSE_NAME,
    MULTI_CORRECT_ANSWER_TEXT,
    MULTI_EXPLANATION,
    MULTI_RATIONALE,
    Q_MULTI,
    Q_SCENARIO,
    Q_TRUE_FALSE,
    World,
)

pytestmark = pytest.mark.anyio


async def build(world: World, question_id: str = Q_MULTI) -> SanitizedCoachingContext:
    """Run the real context builder over the standard quiz."""
    attempt = world.attempts.attempts[ATTEMPT_1]
    score = world.scores.scores[ATTEMPT_1]
    feedback = world.feedback.records[ATTEMPT_1]
    result = score.result_for(question_id)
    assert result is not None
    return await world.container.services.context.build(
        attempt=attempt, result=result, feedback=feedback
    )


def payload_text(context: SafeCoachingContext) -> str:
    """Everything in the context, flattened, for containment assertions."""
    return json.dumps(context.as_dict(), sort_keys=True).lower()


# ---------------------------------------------------------------------------
# What the context must contain (§11)
# ---------------------------------------------------------------------------


async def test_context_carries_the_topic(world: World) -> None:
    world.given_standard_quiz()

    sanitized = await build(world)

    assert sanitized.context.topics == ("Reporting concerns",)
    assert sanitized.context.topic == "Reporting concerns"


async def test_context_carries_the_course_and_the_question(world: World) -> None:
    world.given_standard_quiz()

    context = (await build(world)).context

    assert context.course_name == COURSE_NAME
    assert context.question_id == Q_MULTI
    assert context.question_position == 2
    assert context.question_type is QuestionType.MULTI_SELECT
    assert context.question_prompt == "Which actions are appropriate when you have a concern?"


async def test_context_carries_the_learners_own_answer(world: World) -> None:
    world.given_standard_quiz()

    response = (await build(world)).context.learner_response

    assert response is not None
    assert response.answered is True
    assert response.selected_option_ids == ("A", "B")
    assert response.selected_option_labels == ("Record what you saw", "Investigate yourself")
    assert "Record what you saw" in (response.summary or "")


async def test_context_carries_every_delivered_option_in_the_delivered_order(
    world: World,
) -> None:
    """A complete option set discriminates nothing; a subset or a re-ordering would (§12)."""
    world.given_standard_quiz()

    options = (await build(world)).context.options

    assert [option.option_id for option in options] == ["A", "B", "C", "D"]
    assert [option.position for option in options] == [1, 2, 3, 4]


async def test_context_carries_the_misconception_note_and_lesson(world: World) -> None:
    world.given_standard_quiz()

    context = (await build(world)).context

    assert context.misconception_note is not None
    assert "investigating the concern themselves" in context.misconception_note
    assert context.lesson is not None
    assert context.lesson.lesson_id == "lesson-rc"


async def test_context_carries_the_scenario_text(world: World) -> None:
    world.given_standard_quiz()

    context = (await build(world)).context

    assert context.scenario_text is None  # the multi-select has none
    scenario = (await build(world, Q_SCENARIO)).context
    assert scenario.scenario_text is not None
    assert "frightened to go home" in scenario.scenario_text


async def test_context_says_the_question_was_answered_incorrectly(world: World) -> None:
    world.given_standard_quiz()

    assert (await build(world)).context.outcome == "INCORRECT"


# ---------------------------------------------------------------------------
# What the context must NOT contain (§12)
# ---------------------------------------------------------------------------


async def test_the_correct_answer_text_is_excluded(world: World) -> None:
    world.given_standard_quiz()

    text = payload_text((await build(world)).context)

    assert MULTI_CORRECT_ANSWER_TEXT.lower() not in text


async def test_the_uc06_explanation_is_excluded(world: World) -> None:
    """It is written to state and justify the right answer, so it cannot travel (§12)."""
    world.given_standard_quiz()

    text = payload_text((await build(world)).context)

    assert MULTI_EXPLANATION.lower() not in text


async def test_the_answer_key_is_excluded(world: World) -> None:
    world.given_standard_quiz()

    text = payload_text((await build(world)).context)

    assert "correct_option_ids" not in text
    assert MULTI_RATIONALE.lower() not in text


async def test_the_upstream_metadata_blobs_are_excluded(world: World) -> None:
    world.given_standard_quiz()

    text = payload_text((await build(world)).context)

    assert "answer_key_hash" not in text
    assert "marking_notes" not in text
    assert "sha256:deadbeef" not in text


async def test_no_field_name_anywhere_suggests_an_answer_key(world: World) -> None:
    """The structural half of the guarantee: no key in the payload can hold one (§12, §26)."""
    world.given_standard_quiz()

    payload = json.dumps((await build(world)).context.as_dict()).lower()

    for fragment in FORBIDDEN_KEY_FRAGMENTS:
        assert f'"{fragment}' not in payload, fragment


async def test_no_option_carries_a_correctness_flag(world: World) -> None:
    world.given_standard_quiz()

    for option in (await build(world)).context.options:
        assert not hasattr(option, "is_correct")
        assert set(option.as_dict()) == {"option_id", "text", "position"}


async def test_the_learners_answer_is_not_annotated_with_correctness(world: World) -> None:
    """Telling the coach which of four selections were right hands over half the key (§12)."""
    world.given_standard_quiz()

    response = (await build(world)).context.learner_response
    assert response is not None

    keys = set(response.as_dict())
    assert not any("correct" in key for key in keys)
    assert not any("missed" in key for key in keys)


async def test_a_true_false_context_does_not_reveal_the_expected_value(world: World) -> None:
    world.given_standard_quiz()

    context = (await build(world, Q_TRUE_FALSE)).context

    assert context.learner_response is not None
    assert context.learner_response.boolean_value is True  # what the learner said
    assert "correct_value" not in payload_text(context)


async def test_the_drag_to_order_solution_sequence_is_not_revealed(world: World) -> None:
    """Delivered (shuffled) positions only — never the solution order (§12)."""
    world.given_standard_quiz()
    score = world.scores.scores[ATTEMPT_1]
    result = score.result_for("q-order")
    assert result is not None
    assert result.answer_key is not None  # the key exists upstream…

    attempt = world.attempts.attempts[ATTEMPT_1]
    sanitized = await world.container.services.context.build(
        attempt=attempt,
        result=result,
        feedback=world.feedback.records[ATTEMPT_1],
    )

    # …and nothing named after it survives.
    assert "correct_sequence" not in payload_text(sanitized.context)
    assert [item.position for item in sanitized.context.order_items] == [1, 2, 3]


# ---------------------------------------------------------------------------
# The sanitisation report (§13, §22)
# ---------------------------------------------------------------------------


async def test_the_report_names_what_was_removed(world: World) -> None:
    world.given_standard_quiz()

    report = (await build(world)).report

    assert "uc04.question_result.answer_key" in report.removed_fields
    assert "uc06.question_feedback.explanation" in report.removed_fields
    assert "uc06.question_feedback.correct_answer_text" in report.removed_fields
    assert "uc06.question_feedback.correct_option_ids" in report.removed_fields
    assert "uc06.question_feedback.metadata" in report.removed_fields
    assert "uc03.delivered_question.metadata" in report.removed_fields


async def test_the_report_contains_names_and_counts_but_no_values(world: World) -> None:
    world.given_standard_quiz()

    report = (await build(world)).report
    serialised = json.dumps(report.as_dict()).lower()

    assert report.forbidden_value_count > 0
    assert MULTI_CORRECT_ANSWER_TEXT.lower() not in serialised
    assert MULTI_RATIONALE.lower() not in serialised


async def test_a_clean_build_reports_no_contamination(world: World) -> None:
    world.given_standard_quiz()

    report = (await build(world)).report

    assert report.clean is True
    assert report.findings == ()


# ---------------------------------------------------------------------------
# Stage 2 — scrubbing narrative text
# ---------------------------------------------------------------------------


def test_an_answer_assertion_is_scrubbed_from_narrative_text() -> None:
    cleaned, changed = scrub_text("Think again. The correct answer is B. What led you there?", [])

    assert changed is True
    assert "B." not in (cleaned or "")
    assert "What led you there?" in (cleaned or "")


def test_asking_about_the_correct_answer_is_not_scrubbed() -> None:
    """A question that merely uses the phrase reveals nothing and must survive intact."""
    prompt = "Which of these do you think is the correct answer, and why?"

    cleaned, changed = scrub_text(prompt, [])

    assert changed is False
    assert cleaned == prompt


def test_an_exact_answer_value_is_scrubbed_from_narrative_text() -> None:
    cleaned, changed = scrub_text(
        f"The learner missed that {MULTI_CORRECT_ANSWER_TEXT}.", [MULTI_CORRECT_ANSWER_TEXT]
    )

    assert changed is True
    assert MULTI_CORRECT_ANSWER_TEXT.lower() not in (cleaned or "").lower()


async def test_a_misconception_note_that_leaks_the_answer_is_scrubbed(world: World) -> None:
    """UC-06 is not trusted to have written a safe note (§13)."""
    world.given_standard_quiz()
    feedback = world.feedback.records[ATTEMPT_1]
    contaminated = tuple(
        (
            item
            if item.question_id != Q_MULTI
            else type(item)(
                question_id=item.question_id,
                topics=item.topics,
                lesson_reference=item.lesson_reference,
                misconception_note=(
                    f"The learner did not realise that {MULTI_CORRECT_ANSWER_TEXT}."
                ),
                learner_answer_summary=item.learner_answer_summary,
                explanation=item.explanation,
                correct_answer_text=item.correct_answer_text,
                correct_option_ids=item.correct_option_ids,
                metadata=item.metadata,
            )
        )
        for item in feedback.question_feedback
    )
    world.feedback.set(
        type(feedback)(
            attempt_id=feedback.attempt_id,
            status=feedback.status,
            learner_id=feedback.learner_id,
            course_id=feedback.course_id,
            generated_at=feedback.generated_at,
            question_feedback=contaminated,
        )
    )

    sanitized = await build(world)

    assert "misconception_note" in sanitized.report.scrubbed_fields
    assert MULTI_CORRECT_ANSWER_TEXT.lower() not in payload_text(sanitized.context)


# ---------------------------------------------------------------------------
# Stage 3 — verification fails closed (§25, §26)
# ---------------------------------------------------------------------------


def test_verify_catches_an_answer_bearing_field_name() -> None:
    findings = verify({"question_prompt": "Fine", "correct_option_id": "C"}, [])

    assert findings == ("key:correct_option_id",)


def test_verify_catches_a_nested_answer_bearing_field_name() -> None:
    findings = verify({"lesson": {"answer_key_hash": "x"}}, [])

    assert findings == ("key:lesson.answer_key_hash",)


def test_verify_catches_a_forbidden_value_in_narrative_text() -> None:
    findings = verify(
        {"misconception_note": f"Really it is {MULTI_CORRECT_ANSWER_TEXT}."},
        [MULTI_CORRECT_ANSWER_TEXT],
    )

    assert findings == ("value:misconception_note",)


def test_verify_reports_paths_and_never_values() -> None:
    findings = verify({"question_prompt": MULTI_RATIONALE}, [MULTI_RATIONALE])

    assert findings == ("value:question_prompt",)
    assert MULTI_RATIONALE not in "".join(findings)


def test_verify_allows_the_correct_option_text_among_the_full_option_set() -> None:
    """Presence is not discrimination — see the sanitiser's module docstring."""
    payload = {
        "options": [
            {"option_id": "A", "text": "Record what you saw", "position": 1},
            {"option_id": "B", "text": "Investigate yourself", "position": 2},
        ]
    }

    assert verify(payload, ["Record what you saw"]) == ()


def test_verify_passes_a_clean_payload() -> None:
    payload = {
        "question_prompt": "Which actions are appropriate?",
        "topics": ["Reporting concerns"],
        "outcome": "INCORRECT",
    }

    assert verify(payload, [MULTI_CORRECT_ANSWER_TEXT]) == ()


async def test_the_sanitizer_refuses_a_contaminated_context(world: World) -> None:
    """If stage 1 and 2 were ever bypassed, stage 3 stops coaching rather than leaking (§25)."""
    world.given_standard_quiz()

    class LeakySanitizer(CoachingContextSanitizer):
        def _build(self, material, values):  # type: ignore[override]
            context, scrubbed = super()._build(material, values)
            from dataclasses import replace

            leaked = replace(
                context,
                misconception_note=f"Between us, {MULTI_CORRECT_ANSWER_TEXT}.",
            )
            return leaked, scrubbed

    attempt = world.attempts.attempts[ATTEMPT_1]
    result = world.scores.scores[ATTEMPT_1].result_for(Q_MULTI)
    assert result is not None
    material = RawCoachingMaterial(
        attempt=attempt,
        question=next(
            item
            for item in world.attempts.delivered[ATTEMPT_1]
            if item.question_id == Q_MULTI
        ),
        result=result,
        feedback=world.feedback.records[ATTEMPT_1].feedback_for(Q_MULTI),
    )

    with pytest.raises(AnswerKeyContaminationError) as error:
        LeakySanitizer().sanitize(material)

    assert error.value.findings == ("value:misconception_note",)
    assert error.value.retryable is False
    # The error itself does not carry the leaked value (§22).
    assert MULTI_CORRECT_ANSWER_TEXT not in error.value.message


# ---------------------------------------------------------------------------
# Building the forbidden-value set
# ---------------------------------------------------------------------------


async def test_structural_answer_key_fields_are_not_treated_as_secrets(world: World) -> None:
    """"SINGLE_CHOICE" is vocabulary, not an answer — treating it as one would fail every build."""
    world.given_standard_quiz()
    attempt = world.attempts.attempts[ATTEMPT_1]
    result = world.scores.scores[ATTEMPT_1].result_for(Q_MULTI)
    assert result is not None
    material = RawCoachingMaterial(
        attempt=attempt,
        question=next(
            item
            for item in world.attempts.delivered[ATTEMPT_1]
            if item.question_id == Q_MULTI
        ),
        result=result,
        feedback=world.feedback.records[ATTEMPT_1].feedback_for(Q_MULTI),
    )

    values = forbidden_values(material)

    assert "MULTI_SELECT" not in values
    assert MULTI_RATIONALE in values
    assert MULTI_CORRECT_ANSWER_TEXT in values


def test_a_short_value_is_never_treated_as_a_secret() -> None:
    """"A" or "true" appears in ordinary prose constantly; guarding against it would fire on
    innocent text while identifying nothing in a context that already lists options A–D.
    """
    cleaned, changed = scrub_text("Option A felt right to me.", ["A"])

    assert changed is False
    assert cleaned == "Option A felt right to me."
    assert verify({"misconception_note": "Option A felt right."}, ["A"]) == ()


async def test_a_learners_free_text_is_scrubbed_of_answer_bearing_material(
    world: World,
) -> None:
    """The learner's own words are not exempt: whatever the route, it must not reach the model."""
    world.given_standard_quiz()
    attempt = world.attempts.attempts[ATTEMPT_1]
    result = world.scores.scores[ATTEMPT_1].result_for(Q_MULTI)
    assert result is not None
    question = next(
        item for item in world.attempts.delivered[ATTEMPT_1] if item.question_id == Q_MULTI
    )
    material = RawCoachingMaterial(
        attempt=attempt,
        question=question,
        result=result,
        answer=LearnerAnswer(
            question_id=Q_MULTI,
            answered=True,
            response={"type": "MULTI_SELECT", "text": f"I read somewhere that {MULTI_RATIONALE}"},
        ),
        feedback=None,
    )

    sanitized = CoachingContextSanitizer().sanitize(material)

    assert "learner_free_text" in sanitized.report.scrubbed_fields
    assert MULTI_RATIONALE.lower() not in payload_text(sanitized.context)


async def test_delivered_option_text_is_not_treated_as_a_secret(world: World) -> None:
    """Otherwise the learner's own answer summary would be scrubbed of the choice they made."""
    world.given_standard_quiz()
    attempt = world.attempts.attempts[ATTEMPT_1]
    result = world.scores.scores[ATTEMPT_1].result_for(Q_MULTI)
    assert result is not None
    question = next(
        item for item in world.attempts.delivered[ATTEMPT_1] if item.question_id == Q_MULTI
    )
    feedback = world.feedback.records[ATTEMPT_1].feedback_for(Q_MULTI)
    assert feedback is not None
    material = RawCoachingMaterial(
        attempt=attempt,
        question=question,
        result=result,
        feedback=type(feedback)(
            question_id=feedback.question_id,
            topics=feedback.topics,
            correct_answer_text="Record what you saw",
        ),
    )

    values = forbidden_values(material)

    assert "Record what you saw" not in values
    # The genuinely hidden material from UC-04 and UC-03 is still guarded against.
    assert MULTI_RATIONALE in values
