"""Cross-lesson reference verification.

A referenced lesson must exist in the loaded course structure. A fabricated lesson title is the
same failure class as a fabricated citation: it sends the learner looking for something that is
not there. Anything that does not resolve is stripped, and the strip is reported so it can be
counted.

Same course only. The course structure is scoped to one course, so a reference to a lesson in
another course cannot resolve and is stripped by the same rule.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..domain.models import CourseStructure, CrossLessonRef


@dataclass(frozen=True)
class VerificationOutcome:
    verified: tuple[CrossLessonRef, ...]
    #: Lesson ids that did not resolve against the course structure.
    stripped: tuple[str, ...]


def verify_references(
    candidates: Iterable[CrossLessonRef],
    structure: CourseStructure | None,
    current_lesson_id: str,
) -> VerificationOutcome:
    verified: list[CrossLessonRef] = []
    stripped: list[str] = []
    seen: set[str] = set()

    for ref in candidates:
        if ref.lesson_id == current_lesson_id or ref.lesson_id in seen:
            stripped.append(ref.lesson_id)
            continue
        # No structure loaded means nothing can be verified, so nothing may be referenced.
        entry = structure.find(ref.lesson_id) if structure is not None else None
        if entry is None:
            stripped.append(ref.lesson_id)
            continue
        seen.add(ref.lesson_id)
        # The title comes from the course structure, never from the generator, so a plausible
        # but wrong title cannot survive verification either.
        verified.append(CrossLessonRef(lesson_id=entry.lesson_id, title=entry.title, reason=ref.reason))

    return VerificationOutcome(verified=tuple(verified), stripped=tuple(stripped))
