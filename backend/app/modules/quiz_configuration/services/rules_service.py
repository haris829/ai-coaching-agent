"""The learner rules summary.

UC-01's own requirement: show a learner what the quiz will be like, and how many attempts they have
left, **without starting anything**. Strictly read-only — a test asserts that viewing it repeatedly
creates nothing.

Every value comes from the quiz's *active* configuration version, so the summary and the attempt a
learner is about to start cannot disagree. The attempt counts come from UC-03 through
:class:`~app.modules.quiz_configuration.ports.AttemptStatisticsPort`; UC-01 does not own attempts.

Starting the quiz is UC-03's ``POST /api/v1/attempts``. This module deliberately has no equivalent:
one owner of attempts, one place that can consume an allowance.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.modules.identity.principal import Principal
from app.modules.quiz_configuration.api import serializers
from app.modules.quiz_configuration.context import QuizConfigurationContext
from app.modules.quiz_configuration.services.configuration_service import (
    evaluate_bank_capacity,
    require_active_version,
    require_quiz,
    to_domain,
)

logger = get_logger(__name__)


def get_rules(
    ctx: QuizConfigurationContext, quiz_id: int, learner: Principal
) -> dict[str, Any]:
    """Learner-facing rules summary. Performs only reads."""
    quiz = require_quiz(ctx, quiz_id)
    active = require_active_version(ctx, quiz)
    config = to_domain(active)

    learner_ref = str(learner.id)
    attempts_used = ctx.attempt_stats.count_for_learner(quiz.id, learner_ref)
    remaining = max(0, active.max_attempts - attempts_used)
    open_attempt = ctx.attempt_stats.find_open_for_learner(quiz.id, learner_ref)
    capacity = evaluate_bank_capacity(ctx, config)

    # Reported in priority order: an open attempt is the most actionable thing to tell a learner,
    # and an exhausted allowance matters more than a bank problem they cannot influence.
    blocked_reason: str | None = None
    if open_attempt is not None:
        blocked_reason = "attempt_in_progress"
    elif remaining <= 0:
        blocked_reason = "attempt_limit_reached"
    elif not capacity.satisfiable:
        blocked_reason = "question_bank_insufficient"

    return {
        "quiz": serializers.quiz_summary(quiz),
        **serializers.rules_summary(active),
        "attemptsUsed": attempts_used,
        "remainingAttempts": remaining,
        "canStart": blocked_reason is None,
        "blockedReason": blocked_reason,
        "attemptInProgress": (
            None
            if open_attempt is None
            else {
                "id": open_attempt.id,
                "attemptNumber": open_attempt.attempt_number,
                "status": open_attempt.status,
                "configurationVersionId": open_attempt.configuration_version_id,
            }
        ),
    }
