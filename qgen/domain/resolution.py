"""Turning what a caller typed into one course, or into nothing at all.

Pure. The SQL that fetches candidate rows lives in :mod:`qgen.catalogue`; the rules about which
candidate wins, and when none does, live here so they can be tested without a database.

WHY AMBIGUITY RESOLVES TO NOTHING
---------------------------------
Four courses in the catalogue contain the word "International". Picking one of them produces a
confident, plausible paper about the wrong syllabus, with nothing in the output to show it
happened. Returning nothing is worse output and a better answer: the caller is told no course
matched, and the questions are generated from the words they typed, which is what they actually
supplied.
"""

from __future__ import annotations

from dataclasses import dataclass

from .replies import collapse

#: Below this length, no partial match is attempted at all.
#:
#: "Law" appears in most of the catalogue and "MA" in a good part of it. Matching on a fragment
#: that short is a coin toss dressed up as a lookup.
MIN_PARTIAL_CHARS = 5


@dataclass(frozen=True, slots=True)
class CourseRow:
    """A row of ``qc_courses``, as the lookup needs it."""

    code: str
    title: str
    description: str | None = None
    rqf_level: int | None = None
    subject_area: str | None = None


def normalise_reference(course_ref: str | None) -> str:
    """The caller's reference with its whitespace tidied. Empty means "they gave us nothing"."""
    return collapse(course_ref)


def escape_like(reference: str) -> str:
    """``reference`` as a LIKE pattern that matches it literally.

    ``%`` and ``_`` are wildcards. Unescaped, a search for ``%`` matches every course in the
    catalogue and the lookup silently reports an ambiguity that does not exist - and one course is
    genuinely titled ``100% Practical_Skills``, so these are not hypothetical characters.

    Escaped rather than stripped: they are legitimate characters in a title, and a caller who
    types one means it. The backslash goes first, or it would escape the escapes added after it.
    """
    escaped = reference.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def wants_partial_match(reference: str) -> bool:
    """Whether a partial-title match should be attempted for this reference at all."""
    return len(reference) >= MIN_PARTIAL_CHARS


def choose(
    *,
    by_code: list[CourseRow],
    by_title: list[CourseRow],
    by_partial: list[CourseRow],
) -> CourseRow | None:
    """The one course these candidate sets name, or ``None``.

    Three attempts, narrowest first, and each one must be unambiguous:

    1. the catalogue code, exactly;
    2. the title, exactly;
    3. a title containing the reference - but only when exactly one does.

    Order matters as much as the rule. An exact title match wins over a partial one even when the
    partial set is larger, because a caller who typed a course's full name meant that course, not
    the four others whose titles happen to contain it.
    """
    if len(by_code) == 1:
        return by_code[0]
    if len(by_title) == 1:
        return by_title[0]
    if len(by_partial) == 1:
        return by_partial[0]
    return None
