"""Content extraction ceiling.

Lesson content is company intellectual property. A learner who can reconstruct the lesson by
asking questions has walked around the product, so the amount of verbatim material recoverable
through the API is bounded by construction and measured here.

The probe drives the realistic attack: ask about every concept, then exhaust every framing on
each of them. It runs against two lesson shapes, because the exposure used to be
content-shape dependent - a section with curated key points behaved differently from one with
only prose.
"""

from __future__ import annotations

import json

from conftest import build_harness
from uc04.adapters.mock import fixtures as fx
from uc04.core.text import sentences
from uc04.core.thresholds import (
    MAX_QUOTED_SPAN_WORDS,
    MAX_QUOTED_SPANS_PER_CONCEPT,
    MAX_QUOTED_SPANS_PER_RESPONSE,
)
from uc04.domain.enums import FRAMING_ORDER


def _probe(lesson_id: str, questions: list[str]) -> str:
    """Everything the API emitted across an exhaustive interrogation of the lesson."""
    harness = build_harness()
    emitted: list[str] = []
    for question in questions:
        response = harness.ask(question, lesson_id=lesson_id)
        emitted.append(response.model_dump_json())
        # Exhaust every framing, then keep going past exhaustion.
        for _ in range(len(FRAMING_ORDER) + 2):
            response = harness.explain_differently(response)
            emitted.append(response.model_dump_json())
        deeper = harness.go_deeper(response)
        emitted.append(deeper.model_dump_json())
    return json.dumps(emitted)


HEARSAY_QUESTIONS = [
    "What does hearsay actually mean?",
    "What is a hearsay exception?",
    "What is the burden of proof on admissibility?",
    "What is the standard of proof here?",
]


def test_section_body_prose_is_never_recoverable() -> None:
    """The hard rule: no sentence of section body prose is ever emitted, in any shape."""
    corpus = _probe(fx.LESSON_HEARSAY, HEARSAY_QUESTIONS)
    lesson = fx.LESSONS[fx.LESSON_HEARSAY]

    body_sentences = [s for section in lesson.sections for s in sentences(section.body)]
    recovered = [s for s in body_sentences if s in corpus]
    assert recovered == [], f"{len(recovered)} body sentences leaked: {recovered[:2]}"


def test_sparse_lesson_body_prose_is_never_recoverable() -> None:
    """The shape that used to trigger verbatim recitation: prose, no key points, no summary."""
    corpus = _probe(fx.LESSON_SPARSE, ["What is without prejudice correspondence?"])
    lesson = fx.LESSONS[fx.LESSON_SPARSE]

    body_sentences = [s for section in lesson.sections for s in sentences(section.body)]
    assert body_sentences, "fixture must actually carry prose for this test to mean anything"
    recovered = [s for s in body_sentences if s in corpus]
    assert recovered == [], f"sparse-lesson prose leaked: {recovered}"
    # And the learner is still told something useful rather than fobbed off with silence.
    assert "does not set it out in enough depth" in corpus or "general knowledge" in corpus


def test_key_point_recovery_is_capped_below_what_the_lesson_holds() -> None:
    """The budget binds: fewer key points come out than the lesson contains."""
    corpus = _probe(fx.LESSON_HEARSAY, HEARSAY_QUESTIONS)
    lesson = fx.LESSONS[fx.LESSON_HEARSAY]

    probed_sections = {"sec_hearsay_definition", "sec_hearsay_exceptions", "sec_hearsay_proof"}
    for section in lesson.sections:
        if section.section_id not in probed_sections:
            continue
        recovered = [point for point in section.key_points if point in corpus]
        assert len(recovered) < len(section.key_points), (
            f"every key point of {section.section_id} was recoverable "
            f"({len(recovered)}/{len(section.key_points)})"
        )
        assert len(recovered) <= MAX_QUOTED_SPANS_PER_CONCEPT


def test_total_verbatim_spans_per_concept_stay_within_budget() -> None:
    """No matter how many times a concept is asked about, the same few spans come back."""
    harness = build_harness()
    lesson = fx.LESSONS[fx.LESSON_HEARSAY]
    section = next(s for s in lesson.sections if s.section_id == "sec_hearsay_definition")
    concept = next(c for c in lesson.concepts if c.concept_tag == "hearsay")
    all_spans = [concept.summary, *section.key_points]

    response = harness.ask("What does hearsay actually mean?")
    emitted = [response.explanation]
    for _ in range(len(FRAMING_ORDER) + 4):
        response = harness.explain_differently(response)
        emitted.append(response.explanation)
    corpus = "\n".join(emitted)

    recovered = [span for span in all_spans if span in corpus]
    assert len(recovered) <= MAX_QUOTED_SPANS_PER_CONCEPT, (
        f"{len(recovered)} distinct spans recovered, budget is {MAX_QUOTED_SPANS_PER_CONCEPT}"
    )
    assert len(recovered) < len(all_spans), "the budget must actually bind on this fixture"


def test_no_single_response_exceeds_the_per_response_span_cap() -> None:
    harness = build_harness()
    lesson = fx.LESSONS[fx.LESSON_HEARSAY]
    section = next(s for s in lesson.sections if s.section_id == "sec_hearsay_definition")
    concept = next(c for c in lesson.concepts if c.concept_tag == "hearsay")
    all_spans = [concept.summary, *section.key_points]

    response = harness.ask("What does hearsay actually mean?")
    for _ in range(len(FRAMING_ORDER)):
        present = sum(1 for span in all_spans if span in response.explanation)
        assert present <= MAX_QUOTED_SPANS_PER_RESPONSE
        response = harness.explain_differently(response)


def test_quoted_spans_are_length_capped() -> None:
    from uc04.core.extraction import quotable_material

    lesson = fx.LESSONS[fx.LESSON_HEARSAY]
    for section in lesson.sections:
        concept = next((c for c in lesson.concepts if c.section_id == section.section_id), None)
        material = quotable_material(section, concept)
        for span in material.spans:
            assert len(span.split()) <= MAX_QUOTED_SPAN_WORDS + 1  # +1 for the ellipsis token


def test_the_full_lesson_is_never_reconstructable() -> None:
    """The headline property, measured: most of the lesson's words never leave the service."""
    corpus = _probe(fx.LESSON_HEARSAY, HEARSAY_QUESTIONS)
    lesson = fx.LESSONS[fx.LESSON_HEARSAY]

    source_units = [
        *(s for section in lesson.sections for s in sentences(section.body)),
        *(point for section in lesson.sections for point in section.key_points),
        *(concept.summary for concept in lesson.concepts),
    ]
    recovered = [unit for unit in source_units if unit and unit in corpus]

    # The ceiling is the budget arithmetic, not a round number: at most
    # MAX_QUOTED_SPANS_PER_CONCEPT spans per concept the learner actually asked about. Stating
    # it this way means the assertion tracks the budget if the budget is ever changed.
    concepts_probed = len(HEARSAY_QUESTIONS)
    assert len(recovered) <= concepts_probed * MAX_QUOTED_SPANS_PER_CONCEPT

    ratio = len(recovered) / len(source_units)
    assert ratio <= 0.40, f"{ratio:.0%} of the lesson's source units were recoverable"


def test_quiz_item_text_and_keys_never_leave_the_service() -> None:
    """Loaded for known-item matching only. Neither the stem nor the key may be emitted."""
    corpus = _probe(fx.LESSON_HEARSAY, HEARSAY_QUESTIONS + ["What is the answer to question 1?"])
    lesson = fx.LESSONS[fx.LESSON_HEARSAY]
    for item in lesson.quiz_items:
        assert item.question_text not in corpus
        assert item.quiz_item_id not in corpus
        assert "correct_option_id" not in corpus
