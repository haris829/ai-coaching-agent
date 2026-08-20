"""UC-01, behind UC-08's ``ConfigurationProvider``.

**This adapter does not read UC-01's tables.** It delegates to
``attempt_delivery.integration.uc01.Uc01ConfigurationAdapter`` — the adapter UC-03 already uses —
and translates its result into UC-08's narrower shape.

That indirection is deliberate. Reading ``qc_`` rows here would be a second implementation of
"what does this configuration version mean?", and the two would drift the first time UC-01 added
a field: a retake would then be planned against a configuration that differs from the one UC-03
locks onto the attempt moments later. One reader, one meaning.

Read-only in the strongest sense available: the delegate exposes no write method, so there is no
call in this file — or reachable from it — that could alter a course-wide ``max_attempts``. That
is what makes UC-08's promise about grants structural rather than a matter of discipline.
"""

from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.async_db import offload
from app.modules.attempt_delivery.integration.uc01.configuration_adapter import (
    Uc01ConfigurationAdapter,
)
from app.modules.attempt_delivery.integration.uc01.types import (
    QuizConfigurationVersion as DeliveryConfiguration,
)
from app.modules.retakes.domain.errors import ConfigurationUnavailableError
from app.modules.retakes.integration.uc01 import (
    QuestionTypeQuota,
    QuizAvailability,
    QuizConfigurationVersion,
)


def _translate(version: DeliveryConfiguration) -> QuizConfigurationVersion:
    """UC-03's resolved configuration, narrowed to what a retake decision needs.

    Pass mark, time limit, delivery mode and presentation are dropped rather than carried: UC-08
    decides eligibility and an exclusion set, and a field it cannot use is a field it could be
    accused of having used.
    """
    return QuizConfigurationVersion(
        configuration_version_id=version.configuration_version_id,
        quiz_id=version.quiz_id,
        course_id=version.course_id,
        version=version.version,
        question_count=version.question_count,
        maximum_attempts=version.max_attempts,
        question_type_quotas=tuple(
            QuestionTypeQuota(type=str(quota.type), count=quota.count)
            for quota in version.question_type_quotas
        ),
        allowed_question_types=tuple(str(item) for item in version.allowed_question_types),
        topic_ids=tuple(version.topic_ids),
        randomise_question_order=version.randomise_question_order,
        randomise_option_order=version.randomise_option_order,
    )


class RetakeConfigurationAdapter:
    """``ConfigurationProvider`` over UC-01, through UC-03's reader."""

    __slots__ = ("_delegate",)

    def __init__(self, session: Session) -> None:
        self._delegate = Uc01ConfigurationAdapter(session)

    async def get_quiz_availability(self, quiz_id: str) -> QuizAvailability | None:
        return await offload(self._get_quiz_availability, quiz_id)

    async def get_active_configuration(self, quiz_id: str) -> QuizConfigurationVersion | None:
        return await offload(self._get_active_configuration, quiz_id)

    async def get_locked_configuration(
        self, configuration_version_id: str
    ) -> QuizConfigurationVersion | None:
        return await offload(self._get_locked_configuration, configuration_version_id)

    # ---- synchronous bodies ------------------------------------------------

    def _get_quiz_availability(self, quiz_id: str) -> QuizAvailability | None:
        try:
            availability = self._delegate.get_quiz_availability(quiz_id)
        except SQLAlchemyError as exc:
            # "We could not read the quiz" must never degrade into "the quiz is available":
            # a retake planned on an unreadable configuration is worse than one refused.
            raise ConfigurationUnavailableError(quiz_id) from exc
        if availability is None:
            return None
        return QuizAvailability(
            quiz_id=availability.quiz_id,
            course_id=availability.course_id,
            available=availability.available,
            reason=availability.reason,
        )

    def _get_active_configuration(self, quiz_id: str) -> QuizConfigurationVersion | None:
        try:
            version = self._delegate.get_active_configuration(quiz_id)
        except SQLAlchemyError as exc:
            raise ConfigurationUnavailableError(quiz_id) from exc
        return _translate(version) if version else None

    def _get_locked_configuration(
        self, configuration_version_id: str
    ) -> QuizConfigurationVersion | None:
        """A historical version by id alone.

        UC-03's reader is quiz-scoped because it always knows the quiz; UC-08 resolving a
        version off a learner's history does not, so the lookup is by version id and the caller
        checks the quiz and course match. That check lives in the service rather than here —
        ``RetakeConfigurationResolver`` refuses a version belonging to another quiz — because it
        is a rule, and rules do not belong in an adapter.
        """
        try:
            version = self._delegate.get_configuration_version(configuration_version_id)
        except SQLAlchemyError as exc:
            raise ConfigurationUnavailableError("", configuration_version_id) from exc
        return _translate(version) if version else None
