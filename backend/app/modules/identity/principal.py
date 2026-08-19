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
