"""Configuration version locking.

When an attempt is created, the active UC-01 configuration version is read **once**,
normalised, and frozen onto the attempt row. Every later decision for that attempt —
time limit, question count, delivery mode, pass mark, whether an incomplete
submission is allowed — is read from that frozen snapshot.

The consequence is the behaviour UC-03 requires: an administrator publishing a new
version, or withdrawing the current one, cannot alter an attempt already in flight.
UC-03 never re-reads UC-01 during an active attempt.

Normalising at lock time matters as much as freezing: it means an attempt can never
be persisted with an ambiguous configuration, and every read of the snapshot sees
fully-specified values.
"""

from __future__ import annotations

from dataclasses import replace

from app.modules.attempt_delivery.domain import errors
from app.modules.attempt_delivery.domain.enums import QuestionPresentation
from app.modules.attempt_delivery.integration.uc01.types import QuizConfigurationVersion
from app.modules.attempt_delivery.services.question_selection_service import (
    validate_configuration_for_delivery,
)


def lock_configuration(config: QuizConfigurationVersion) -> QuizConfigurationVersion:
    """Validate and normalise a configuration version into the attempt snapshot.

    Raises :func:`app.domain.errors.invalid_configuration` rather than guessing
    whenever a rule UC-03 depends on is missing or incoherent, so an attempt is never
    created from a configuration that cannot be delivered.
    """
    if not config.configuration_version_id:
        raise errors.invalid_configuration(
            "The configuration version is missing an identifier.", quizId=config.quiz_id
        )
    version_id = config.configuration_version_id

    if not isinstance(config.version, int):
        raise errors.invalid_configuration(
            '"version" must be an integer.', configurationVersionId=version_id
        )

    if not isinstance(config.question_presentation, QuestionPresentation):
        # Defensive: the dataclass coerces on construction, but a hand-built value
        # must not slip an unknown mode into a persisted attempt.
        try:
            config = replace(
                config, question_presentation=QuestionPresentation(config.question_presentation)
            )
        except ValueError as exc:
            raise errors.invalid_configuration(
                '"questionPresentation" must be one of ONE_AT_A_TIME, ALL_AT_ONCE.',
                configurationVersionId=version_id,
                received=str(config.question_presentation),
            ) from exc

    if not config.quiz_id or not config.course_id:
        raise errors.invalid_configuration(
            "The configuration version must identify both a quiz and a course.",
            configurationVersionId=version_id,
        )

    # Reuses the same delivery rules the selector enforces, so an attempt can never be
    # created from a configuration whose question requirements are incoherent.
    validate_configuration_for_delivery(config)

    return config
