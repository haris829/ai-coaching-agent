"""UC-01 quiz configuration, seen through UC-03's port.

This replaces the provisional ``LocalQuizConfigurationAdapter`` that read opaque JSON out of
``ext_*`` tables. It is the anti-corruption layer the port existed for: UC-01's model is translated
here, once, and no UC-03 service learns anything about it.

Two translations are worth reading carefully, because the two capabilities were built in separate
workspaces and disagree on shape:

**Identifiers.** UC-01 keys quizzes, courses and versions by ``int``; UC-03 treats every
cross-boundary reference as an opaque ``str``. Conversion happens here and nowhere else, so a
malformed id becomes "not found" rather than a 500.

**"Delivery mode".** Both capabilities independently coined that name for different things — UC-01
means grading policy (``practice``/``assessment``/``exam``), UC-03 means pagination
(``ONE_AT_A_TIME``/``ALL_AT_ONCE``). They are *not* mapped onto each other. UC-01 now carries a
separate ``question_presentation`` setting, which is what UC-03 reads; UC-01's own delivery mode is
passed through in ``extra`` so the attempt keeps a faithful record of the version it ran under.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.question_types import QuestionPresentation
from app.core.time import to_iso
from app.modules.attempt_delivery.domain.enums import QuestionType
from app.modules.attempt_delivery.integration.uc01.types import (
    QuestionTypeQuota,
    QuizAvailability,
    QuizConfigurationVersion,
)
from app.modules.quiz_configuration.models import ConfigurationVersion, Quiz
from app.modules.quiz_configuration.repositories import (
    SqlAlchemyConfigurationVersionRepository,
    SqlAlchemyQuizRepository,
)


def _as_int(value: str) -> int | None:
    """UC-03 hands over opaque strings; UC-01 keys on integers."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


class Uc01ConfigurationAdapter:
    """:class:`~...uc01.port.QuizConfigurationPort` over the in-process UC-01 module."""

    __slots__ = ("_quizzes", "_versions")

    def __init__(self, session: Session) -> None:
        self._quizzes = SqlAlchemyQuizRepository(session)
        self._versions = SqlAlchemyConfigurationVersionRepository(session)

    # ---- QuizConfigurationPort -------------------------------------------

    def get_quiz_availability(self, quiz_id: str) -> QuizAvailability | None:
        numeric = _as_int(quiz_id)
        quiz = None if numeric is None else self._quizzes.get(numeric)
        if quiz is None:
            return None

        # A quiz with no active configuration version exists but cannot be attempted. Reporting
        # that as an availability reason gives the learner an accurate message instead of the
        # "insufficient questions" confusion that a missing configuration would otherwise cause.
        active = self._versions.get_active(quiz)
        if active is None:
            return QuizAvailability(
                quiz_id=str(quiz.id),
                course_id=str(quiz.course_id),
                available=False,
                reason="QUIZ_NOT_CONFIGURED",
            )
        return QuizAvailability(
            quiz_id=str(quiz.id), course_id=str(quiz.course_id), available=True
        )

    def get_active_configuration(self, quiz_id: str) -> QuizConfigurationVersion | None:
        numeric = _as_int(quiz_id)
        quiz = None if numeric is None else self._quizzes.get(numeric)
        if quiz is None:
            return None
        active = self._versions.get_active(quiz)
        return None if active is None else self._to_port(quiz, active)

    def get_configuration_version(
        self, configuration_version_id: str
    ) -> QuizConfigurationVersion | None:
        numeric = _as_int(configuration_version_id)
        version = None if numeric is None else self._versions.get(numeric)
        if version is None:
            return None
        quiz = self._quizzes.get(version.quiz_id)
        return None if quiz is None else self._to_port(quiz, version)

    # ---- translation ------------------------------------------------------

    def _to_port(self, quiz: Quiz, version: ConfigurationVersion) -> QuizConfigurationVersion:
        quotas: list[QuestionTypeQuota] = []
        allowed: list[QuestionType] = []
        for entry in version.question_types:
            question_type = QuestionType(entry.question_type)
            if entry.question_quota is None:
                # No quota: the type is permitted, and the count is drawn freely across types.
                allowed.append(question_type)
            else:
                quotas.append(QuestionTypeQuota(type=question_type, count=entry.question_quota))

        # UC-01's rules make quotas all-or-nothing, so exactly one of these lists is populated.
        # Sending both would let UC-03's selection double-count a type.
        if quotas:
            allowed = []

        return QuizConfigurationVersion(
            configuration_version_id=str(version.id),
            quiz_id=str(quiz.id),
            course_id=str(quiz.course_id),
            version=version.version_number,
            question_count=version.question_count,
            pass_mark_percentage=float(version.pass_mark),
            activated_at=to_iso(version.created_at),
            # UC-01 configures minutes; UC-03 reasons in seconds. NULL means untimed in both.
            time_limit_seconds=(
                None
                if version.time_limit_minutes is None
                else version.time_limit_minutes * 60
            ),
            max_attempts=version.max_attempts,
            question_type_quotas=tuple(quotas),
            allowed_question_types=tuple(allowed),
            topic_ids=tuple(entry.topic_id for entry in version.topics),
            randomise_question_order=bool(version.randomise_questions),
            randomise_option_order=bool(version.randomise_option_order),
            question_presentation=QuestionPresentation(version.question_presentation),
            allow_incomplete_submission=bool(version.allow_incomplete_submission),
            extra={
                # UC-01's grading/feedback policy. UC-03 does not act on it, but the attempt keeps
                # it so a future grading capability can read the version it actually ran under.
                "uc01DeliveryMode": version.delivery_mode,
                "quizTitle": quiz.title,
                "courseTitle": quiz.course.title if quiz.course is not None else None,
                "settingsFingerprint": version.settings_fingerprint,
            },
        )
