"""The authenticated caller, as the rest of the system sees it.

A plain frozen dataclass rather than the ORM row, so services and domain rules never depend on
how identity happens to be stored today.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    LEARNER = "learner"
    #: A human who reviews and approves passing formal assessments (UC-09).
    #:
    #: A distinct role rather than a flavour of admin: an assessor signs off on an individual
    #: learner's result and is named on the review record and in the audit trail, while an
    #: administrator configures quizzes and grants attempts. Conflating them would make "who
    #: approved this certificate?" answerable only as "somebody with admin rights".
    ASSESSOR = "assessor"


@dataclass(frozen=True, slots=True)
class Principal:
    #: Stable identifier. Opaque to the business rules; the company IdP supplies its own.
    id: int
    display_name: str
    role: Role
    #: Audit label written to ``created_by`` / ``updated_by`` style columns.
    actor: str

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN

    @property
    def is_assessor(self) -> bool:
        """Whether this caller may act on the assessor review queue.

        Authentication, not authorisation: it says the caller *is* an assessor, not that they may
        review a particular course. UC-09 checks the second question separately, on every
        operation, against the assessor directory — a token proves identity and says nothing
        about scope.
        """
        return self.role is Role.ASSESSOR
