"""Identity data access, behind an interface.

``UserRepository`` is the contract; ``SqlAlchemyUserRepository`` is today's local implementation.
The company's identity adapter implements the same protocol and nothing above it changes.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.identity.models import User


class UserRepository(Protocol):
    def get_by_token(self, token: str) -> User | None: ...

    def get(self, user_id: int) -> User | None: ...

    def list_all(self) -> list[User]: ...


class SqlAlchemyUserRepository:
    """Local development / test implementation over the ``qa_users`` table."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_token(self, token: str) -> User | None:
        return self._db.scalar(select(User).where(User.api_token == token))

    def get(self, user_id: int) -> User | None:
        return self._db.get(User, user_id)

    def list_all(self) -> list[User]:
        return list(self._db.scalars(select(User).order_by(User.role, User.id)))
