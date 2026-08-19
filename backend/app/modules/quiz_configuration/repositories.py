"""Data access for UC-01, behind interfaces.

Each ``…Repository`` protocol states what the business rules need; each ``SqlAlchemy…Repository``
is today's local implementation. The services depend only on the protocols, so replacing the
temporary local store with the company's adapter tomorrow means writing new classes that satisfy
the same protocols — no service or domain change.

Repositories never commit. Transaction boundaries belong to the service, because "create the
version, its question types, its topic scope and repoint the quiz" is one unit of work.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.modules.quiz_configuration.domain.rules import QuizConfiguration
from app.modules.quiz_configuration.models import (
    ConfigurationVersion,
    ConfigurationVersionQuestionType,
    ConfigurationVersionTopic,
    Course,
    Quiz,
)
from app.modules.quiz_configuration.ports import TopicRef

# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class QuizRepository(Protocol):
    def get(self, quiz_id: int) -> Quiz | None: ...

    def list_all(self) -> list[Quiz]: ...

    def set_active_configuration_version(self, quiz: Quiz, version_id: int) -> None: ...


class ConfigurationVersionRepository(Protocol):
    def next_version_number(self, quiz_id: int) -> int: ...

    def insert(
        self,
        *,
        quiz_id: int,
        version_number: int,
        config: QuizConfiguration,
        fingerprint: str,
        created_by_user_id: int | None,
        created_by: str | None,
    ) -> ConfigurationVersion: ...

    def insert_question_types(
        self, version: ConfigurationVersion, config: QuizConfiguration
    ) -> None: ...

    def insert_topics(
        self, version: ConfigurationVersion, topics: Sequence[TopicRef]
    ) -> None: ...

    def get(self, version_id: int) -> ConfigurationVersion | None: ...

    def get_active(self, quiz: Quiz) -> ConfigurationVersion | None: ...

    def list_for_quiz(self, quiz_id: int) -> list[ConfigurationVersion]: ...




# ---------------------------------------------------------------------------
# SQLAlchemy implementations
# ---------------------------------------------------------------------------


class SqlAlchemyQuizRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, quiz_id: int) -> Quiz | None:
        return self._db.scalar(
            select(Quiz).options(selectinload(Quiz.course)).where(Quiz.id == quiz_id)
        )

    def list_all(self) -> list[Quiz]:
        return list(
            self._db.scalars(
                select(Quiz)
                .options(selectinload(Quiz.course))
                .join(Course)
                .order_by(Course.title, Quiz.title)
            )
        )

    def set_active_configuration_version(self, quiz: Quiz, version_id: int) -> None:
        quiz.active_configuration_version_id = version_id
        self._db.flush()


class SqlAlchemyConfigurationVersionRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def next_version_number(self, quiz_id: int) -> int:
        current = self._db.scalar(
            select(func.coalesce(func.max(ConfigurationVersion.version_number), 0)).where(
                ConfigurationVersion.quiz_id == quiz_id
            )
        )
        return int(current or 0) + 1

    def insert(
        self,
        *,
        quiz_id: int,
        version_number: int,
        config: QuizConfiguration,
        fingerprint: str,
        created_by_user_id: int | None,
        created_by: str | None,
    ) -> ConfigurationVersion:
        version = ConfigurationVersion(
            quiz_id=quiz_id,
            version_number=version_number,
            question_count=config.question_count,
            time_limit_minutes=config.time_limit_minutes,
            pass_mark=config.pass_mark,
            randomise_questions=config.randomise_questions,
            max_attempts=config.max_attempts,
            delivery_mode=config.delivery_mode.value,
            question_presentation=config.question_presentation.value,
            randomise_option_order=config.randomise_option_order,
            allow_incomplete_submission=config.allow_incomplete_submission,
            settings_fingerprint=fingerprint,
            created_by_user_id=created_by_user_id,
            created_by=created_by,
        )
        self._db.add(version)
        self._db.flush()
        return version

    def insert_question_types(
        self, version: ConfigurationVersion, config: QuizConfiguration
    ) -> None:
        for position, selection in enumerate(config.question_types, start=1):
            self._db.add(
                ConfigurationVersionQuestionType(
                    configuration_version_id=version.id,
                    question_type=selection.type.value,
                    question_quota=selection.quota,
                    position=position,
                )
            )
        self._db.flush()

    def insert_topics(self, version: ConfigurationVersion, topics: Sequence[TopicRef]) -> None:
        for position, topic in enumerate(topics, start=1):
            self._db.add(
                ConfigurationVersionTopic(
                    configuration_version_id=version.id,
                    topic_id=topic.id,
                    topic_slug=topic.slug,
                    topic_name=topic.name,
                    position=position,
                )
            )
        self._db.flush()

    def get(self, version_id: int) -> ConfigurationVersion | None:
        return self._db.scalar(
            select(ConfigurationVersion).where(ConfigurationVersion.id == version_id)
        )

    def get_active(self, quiz: Quiz) -> ConfigurationVersion | None:
        if quiz.active_configuration_version_id is None:
            return None
        return self.get(quiz.active_configuration_version_id)

    def list_for_quiz(self, quiz_id: int) -> list[ConfigurationVersion]:
        return list(
            self._db.scalars(
                select(ConfigurationVersion)
                .where(ConfigurationVersion.quiz_id == quiz_id)
                .order_by(ConfigurationVersion.version_number.desc())
            )
        )
