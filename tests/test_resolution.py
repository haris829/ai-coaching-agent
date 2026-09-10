"""The lookup rules, without a database.

The rule that earns most of this file is that ambiguity resolves to nothing. Getting it wrong
does not produce an error anybody sees - it produces a confident, well-formed paper about the
wrong syllabus.
"""

from __future__ import annotations

import pytest

from qgen.domain.resolution import (
    MIN_PARTIAL_CHARS,
    CourseRow,
    choose,
    escape_like,
    normalise_reference,
    wants_partial_match,
)

CRIMINOLOGY = CourseRow(code="LL-34590", title="Criminology (Postgraduate)")
TRADE = CourseRow(code="LL-52728", title="International Trade Law (Postgraduate)")
MARITIME = CourseRow(code="LL-36653", title="PGDip International Trade and Maritime Law")


def test_an_exact_code_wins():
    assert choose(by_code=[CRIMINOLOGY], by_title=[], by_partial=[]) is CRIMINOLOGY


def test_an_exact_title_wins_when_no_code_matched():
    assert choose(by_code=[], by_title=[CRIMINOLOGY], by_partial=[]) is CRIMINOLOGY


def test_an_exact_title_beats_an_ambiguous_partial():
    """A caller who typed the whole name meant that course, not the others containing it."""
    assert choose(by_code=[], by_title=[TRADE], by_partial=[TRADE, MARITIME]) is TRADE


def test_a_partial_match_wins_only_when_exactly_one_course_matches():
    assert choose(by_code=[], by_title=[], by_partial=[TRADE]) is TRADE


def test_two_partial_matches_resolve_to_nothing_rather_than_the_first():
    assert choose(by_code=[], by_title=[], by_partial=[TRADE, MARITIME]) is None


def test_no_match_at_all_resolves_to_nothing():
    assert choose(by_code=[], by_title=[], by_partial=[]) is None


@pytest.mark.parametrize("reference", ["Law", "MA", "LLM", "", "   "])
def test_a_short_reference_never_attempts_a_partial_match(reference):
    """"Law" is in most of the catalogue; matching on it is a coin toss dressed as a lookup."""
    assert wants_partial_match(normalise_reference(reference)) is False


def test_a_reference_of_the_minimum_length_does_attempt_one():
    assert wants_partial_match("x" * MIN_PARTIAL_CHARS) is True


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("  Medical   Law  ", "Medical Law"),
        ("Medical\tLaw\n", "Medical Law"),
        (None, ""),
        ("", ""),
    ],
)
def test_a_reference_has_its_whitespace_tidied(given, expected):
    assert normalise_reference(given) == expected


# ---------------------------------------------------------------------------
# LIKE escaping
# ---------------------------------------------------------------------------


def test_a_percent_sign_is_escaped_so_it_cannot_match_the_whole_catalogue():
    assert escape_like("%") == "%\\%%"


def test_an_underscore_is_escaped_so_it_matches_only_itself():
    # One course is genuinely titled "100% Practical_Skills", so these are not hypothetical.
    assert escape_like("Practical_Skills") == "%Practical\\_Skills%"


def test_a_backslash_is_escaped_first_so_it_does_not_consume_the_others():
    assert escape_like("a\\%b") == "%a\\\\\\%b%"


def test_an_ordinary_reference_is_wrapped_and_otherwise_untouched():
    assert escape_like("criminology") == "%criminology%"
