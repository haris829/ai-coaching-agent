"""A guard at the UC-01 boundary — not a second copy of UC-01's authoring rules.

UC-01 validates configuration when it is authored, and UC-03 validates again the parts it must
rely on before locking a version onto an attempt. UC-08 checks the same small set, for the same
reason UC-03 gives: **a retake must never be reserved against a configuration that cannot produce
a coherent paper.** Failing here means nothing is written; failing later would mean a reservation
held for an attempt that can never be created.

Only fields UC-08 actually uses are checked. There is no pass-mark check, no time-limit check and
no delivery-mode check, because a retake decision does not read them — checking them would be
this module forming opinions about rules it does not own.
"""

from __future__ import annotations

from app.modules.retakes.domain.errors import InvalidConfigurationError
from app.modules.retakes.integration.uc01 import QuizConfigurationVersion


def validate_configuration_for_retake(config: QuizConfigurationVersion) -> None:
    """Raise :class:`InvalidConfigurationError` when a retake could not be delivered."""
    version_id = config.configuration_version_id
    if not version_id:
        raise InvalidConfigurationError("The configuration version is missing an identifier.")

    if not config.quiz_id or not config.course_id:
        raise InvalidConfigurationError(
            "The configuration version must identify both a quiz and a course.",
            configuration_version_id=version_id,
        )

    if not _is_positive_int(config.question_count):
        raise InvalidConfigurationError(
            "The configured question count must be a positive integer.",
            configuration_version_id=version_id,
            question_count=config.question_count,
        )

    quotas = config.question_type_quotas
    if not quotas:
        return

    seen: set[str] = set()
    total = 0
    for quota in quotas:
        if quota.type in seen:
            raise InvalidConfigurationError(
                f'Duplicate question type quota for "{quota.type}".',
                configuration_version_id=version_id,
            )
        seen.add(quota.type)
        if not isinstance(quota.count, int) or isinstance(quota.count, bool) or quota.count < 0:
            raise InvalidConfigurationError(
                f'The quota count for "{quota.type}" must be a non-negative integer.',
                configuration_version_id=version_id,
            )
        total += quota.count

    if total != config.question_count:
        # UC-01's own rule. Checked because a mismatch makes "the configured number of questions"
        # ambiguous, and a retake would have to guess which number to honour.
        raise InvalidConfigurationError(
            "Question type quotas must sum to the configured question count.",
            configuration_version_id=version_id,
            question_count=config.question_count,
            quota_total=total,
        )


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
