"""The mandatory depth-difference test (scope section 16).

One fixed question, rendered through the Level 3 and Level 7 profiles, must
produce materially different output -- not merely a different enum value. No LLM
is involved: the renderer is a pure function.
"""

from __future__ import annotations

from uc02.application.explanation_renderer import (
    TECHNICAL_TERMS,
    count_technical_terms,
    render_explanation,
)
from uc02.domain.explanation_mapping import profile_for_level

QUESTION = "When is a promise legally binding?"


def _render(level: int):
    return render_explanation(QUESTION, profile_for_level(level))


def test_level_3_and_level_7_outputs_are_not_the_same_text():
    assert _render(3).text != _render(7).text


def test_level_3_output_is_measurably_shorter():
    basic = _render(3).metrics
    advanced = _render(7).metrics
    assert basic.word_count < advanced.word_count
    # Not a marginal difference: the advanced rendering is substantially longer.
    assert advanced.word_count >= basic.word_count * 1.5
    assert basic.sentence_count < advanced.sentence_count


def test_level_3_output_uses_fewer_technical_terms_from_the_defined_list():
    basic_text = _render(3).text
    advanced_text = _render(7).text
    basic_occurrences, basic_distinct = count_technical_terms(basic_text)
    advanced_occurrences, advanced_distinct = count_technical_terms(advanced_text)

    assert basic_distinct < advanced_distinct
    assert basic_occurrences < advanced_occurrences
    # The A-level-equivalent rendering avoids the term list entirely.
    assert basic_distinct == 0
    assert advanced_distinct >= 5


def test_intermediate_sits_between_the_two():
    basic, intermediate, advanced = _render(3), _render(5), _render(7)
    assert (
        basic.metrics.distinct_technical_terms
        <= intermediate.metrics.distinct_technical_terms
        <= advanced.metrics.distinct_technical_terms
    )
    assert (
        basic.metrics.word_count
        <= intermediate.metrics.word_count
        <= advanced.metrics.word_count
    )


def test_level_4_renders_as_level_3_and_level_6_as_level_5():
    """Grouping assumption A-03, observable in rendered output."""
    assert _render(4).text == _render(3).text
    assert _render(6).text == _render(5).text
    assert _render(6).text != _render(7).text


def test_renderer_is_deterministic():
    assert _render(7).text == _render(7).text


def test_the_question_appears_in_every_rendering():
    for level in (3, 5, 7):
        assert QUESTION in _render(level).text


def test_term_list_is_defined_and_non_trivial():
    """The metric is only meaningful against an explicit term list."""
    assert len(TECHNICAL_TERMS) >= 15
    assert "ratio decidendi" in TECHNICAL_TERMS
