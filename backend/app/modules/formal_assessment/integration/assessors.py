"""The assessor directory — who may review what (§10, §19).

A bearer token proves *who* is calling. It says nothing about whether that person is a qualified
assessor for the course whose assessment they are about to approve. So every assessor action
performs two checks:

    require_assessor (app/core/security.py)   ->  authenticated: this is assessor A
    AssessorDirectory.get_assessor            ->  authorised:    A may review course C

The second check is this port. At integration it binds to the company's assessor register, role
system or permission service. Until then the composition root binds a directory that authorises
**nobody**, because the alternative — a default that authorises everybody — would mean an unwired
deployment could approve certificates.

SCOPE IS COURSE-SHAPED
----------------------
``authorised_course_ids`` empty plus ``all_courses`` False means "this assessor reviews nothing".
``all_courses`` True is for a platform-wide assessor and is a deliberate, explicit flag rather than
something inferred from an empty list — an empty list is the safest possible value and must not read
as "everything".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Assessor:
    """An assessor and the scope they may review within."""

    assessor_id: str
    active: bool = True
    #: Courses this assessor may review. Empty means none, unless ``all_courses`` is set.
    authorised_course_ids: frozenset[str] = field(default_factory=frozenset)
    all_courses: bool = False
    display_name: str | None = None
    #: Free-form, for the review record: "Lead Assessor", a qualification reference, whatever the
    #: company records. Never used in a decision.
    role: str | None = None

    def may_review(self, course_id: str | None) -> bool:
        """Whether this assessor may review an assessment on ``course_id``.

        A missing course id is refused. An assessment whose course cannot be determined is not an
        assessment anyone is authorised for, and defaulting to "allow" here would turn a data
        problem into an approval.
        """
        if not self.active:
            return False
        if not course_id:
            return False
        return self.all_courses or course_id in self.authorised_course_ids

    def as_dict(self) -> dict[str, Any]:
        return {
            "assessor_id": self.assessor_id,
            "active": self.active,
            "all_courses": self.all_courses,
            "authorised_course_ids": sorted(self.authorised_course_ids),
            "display_name": self.display_name,
            "role": self.role,
        }


@runtime_checkable
class AssessorDirectory(Protocol):
    """Read-only. UC-09 does not manage assessors; it asks about them."""

    async def get_assessor(self, assessor_id: str) -> Assessor | None:
        """The assessor, or ``None`` when the identifier is not a known assessor.

        ``None`` and "not authorised for this course" produce the same refusal in the service, so
        this port never has to decide how much to reveal.
        """
        ...

    async def list_authorised_course_ids(self, assessor_id: str) -> tuple[str, ...]:
        """Courses this assessor may review, for scoping the pending-review list.

        An assessor's queue shows the assessments they can act on. A directory that authorises all
        courses should return an empty tuple here and rely on ``get_assessor().all_courses`` — the
        service reads both, and an empty tuple with ``all_courses`` unset scopes the queue to
        nothing rather than to everything.
        """
        ...
