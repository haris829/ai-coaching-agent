"""Placeholder platform tables.

``qa_users`` and ``qa_enrolments`` exist so the capabilities run end-to-end locally with real roles,
real audit attribution and a real enrolment rule. **The company owns both in production** — its
identity provider and its course-enrolment system — and they are replaced together.

Prefixed ``qa_`` (quiz agent platform) so they cannot collide with the company's own ``users`` or
``enrolments`` tables when both schemas live in one database.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_at_column
from app.modules.identity.enums import EnrolmentStatus
from app.modules.identity.principal import Role

PLATFORM_PREFIX = "qa_"

ROLE_VALUES = ", ".join(f"'{role.value}'" for role in Role)
ENROLMENT_STATUS_VALUES = ", ".join(f"'{item.value}'" for item in EnrolmentStatus)


class User(Base):
    __tablename__ = f"{PLATFORM_PREFIX}users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Development credential. The company IdP replaces this with a real token/claim check.
    api_token: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (CheckConstraint(f"role IN ({ROLE_VALUES})", name="users_role"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.email} {self.role}>"


class Enrolment(Base):
    """A learner's enrolment on a course.

    Placeholder for the company's enrolment system. UC-03 asks one yes/no question of it before
    creating an attempt — see ``app.modules.attempt_delivery.integration.enrolment``.
    """

    __tablename__ = f"{PLATFORM_PREFIX}enrolments"

    learner_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    #: The UC-01 course this enrolment is for, as an opaque string so the column does not
    #: constrain how the company identifies courses.
    course_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=EnrolmentStatus.ACTIVE.value
    )
    enrolled_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        CheckConstraint(f"status IN ({ENROLMENT_STATUS_VALUES})", name="enrolment_status"),
        Index(f"ix_{PLATFORM_PREFIX}enrolments_course_id", "course_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Enrolment {self.learner_id}@{self.course_id} {self.status}>"
