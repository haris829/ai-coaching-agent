"""Building the question plan for a retake (§5, §6, §8).

The service does the reading; ``domain.question_plan`` does the deciding. Three inputs are
gathered and handed to a pure function:

1. **the eligible pool** — from UC-02, filtered to the types and topics the configuration version
   permits, with retired questions excluded at the query and again in the domain;
2. **the paper being retaken** — the delivered question ids of the previous attempt, from UC-03;
3. **the learner's whole history at this quiz** — the delivered ids of every earlier attempt,
   which is the preferred exclusion set.

Reading history costs one call per attempt, so it is bounded: only the most recent
``HISTORY_LOOKBACK_ATTEMPTS`` attempts are read. A learner on their fortieth attempt is not a
scenario worth an unbounded fan-out, and the questions they saw thirty attempts ago are the least
valuable ones to exclude. The paper being retaken is always included regardless of the bound, so
the guarantee §7 actually rests on is never affected by it.

An unreadable question bank raises rather than returning an empty pool. An empty pool and an
unavailable one are indistinguishable numerically, and treating a failure as "no alternatives
exist" would silently deliver an identical paper.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.core.errors import ProviderUnavailableError
from app.core.logging import get_logger
from app.modules.retakes.domain.configuration import validate_configuration_for_retake
from app.modules.retakes.domain.errors import (
    InsufficientQuestionsError,
    QuestionBankUnavailableError,
)
from app.modules.retakes.domain.question_plan import RetakeQuestionPlan, plan_retake_questions
from app.modules.retakes.integration.uc01 import QuizConfigurationVersion
from app.modules.retakes.integration.uc02 import (
    QuestionBankProvider,
    QuestionDescriptor,
    QuestionPoolQuery,
)
from app.modules.retakes.integration.uc03 import AttemptContext, AttemptProvider

logger = get_logger(__name__)

#: How many of the learner's most recent attempts are read to build the exclusion set.
#: A bound on fan-out, not a business rule — see the module docstring.
HISTORY_LOOKBACK_ATTEMPTS = 10


@dataclass(frozen=True, slots=True)
class PlannedRetake:
    """The plan, together with the delivered-question reads it was built from.

    The reads are carried rather than repeated. The difference check after delivery needs exactly
    the same two sets the plan was computed from, and reading them again would both cost a second
    round of upstream calls and open a window in which the two could disagree.
    """

    plan: RetakeQuestionPlan
    #: The paper being retaken, in delivery order.
    previous_question_ids: tuple[str, ...]
    #: Everything the learner has been delivered at this quiz, within the lookback bound.
    historical_question_ids: tuple[str, ...]


class RetakeQuestionPlanService:
    def __init__(
        self,
        *,
        attempts: AttemptProvider,
        question_bank: QuestionBankProvider,
        history_lookback: int = HISTORY_LOOKBACK_ATTEMPTS,
    ) -> None:
        self._attempts = attempts
        self._question_bank = question_bank
        self._history_lookback = max(1, history_lookback)

    async def build(
        self,
        *,
        config: QuizConfigurationVersion,
        course_id: str,
        previous_attempt: AttemptContext,
        attempts: Sequence[AttemptContext],
    ) -> PlannedRetake:
        """The plan for one retake.

        Raises :class:`InsufficientQuestionsError` when the eligible pool cannot fill the paper at
        all. That is a different condition from "not enough *unused* questions", which is handled
        by falling back through the exclusion tiers and recording the reuse (§8) — a small bank
        must not stop a learner retaking a quiz.
        """
        validate_configuration_for_retake(config)

        pool = await self._load_pool(config=config, course_id=course_id)
        previous_ids = await self._delivered_ids(previous_attempt.attempt_id)
        history_ids = await self._history_ids(attempts, previous_attempt)

        plan = plan_retake_questions(
            config=config,
            pool=pool,
            previous_attempt_question_ids=previous_ids,
            historical_question_ids=history_ids,
        )

        if not plan.feasible:
            raise InsufficientQuestionsError(
                quiz_id=config.quiz_id,
                configuration_version_id=config.configuration_version_id,
                required_count=plan.required_count,
                eligible_pool_size=plan.eligible_pool_size,
                shortfalls=list(plan.shortfalls),
            )

        if plan.reuse_expected:
            # Logged as well as recorded on the retake: an administrator watching the log is the
            # person who can fix a bank that is too small.
            logger.info(
                "retake.question_reuse_expected",
                extra={
                    "quiz_id": config.quiz_id,
                    "configuration_version_id": config.configuration_version_id,
                    "exclusion_scope": plan.exclusion_scope.value,
                    "reuse_reason": plan.reuse_reason.value if plan.reuse_reason else None,
                    "required_count": plan.required_count,
                    "unused_pool_size": plan.unused_pool_size,
                },
            )

        return PlannedRetake(
            plan=plan,
            previous_question_ids=previous_ids,
            historical_question_ids=history_ids,
        )

    # ------------------------------------------------------------ reading

    async def _load_pool(
        self, *, config: QuizConfigurationVersion, course_id: str
    ) -> tuple[QuestionDescriptor, ...]:
        query = QuestionPoolQuery(
            quiz_id=config.quiz_id,
            course_id=course_id or config.course_id,
            types=_pool_types(config),
            topic_ids=config.topic_ids,
            # Never False. A retired question is not an alternative (§8).
            exclude_retired=True,
        )
        try:
            return await self._question_bank.find_eligible_questions(query)
        except ProviderUnavailableError:
            raise
        except Exception as exc:  # pragma: no cover - adapter defect, not a domain condition
            logger.error("retake.question_bank_failed", extra={"quiz_id": config.quiz_id})
            raise QuestionBankUnavailableError() from exc

    async def _delivered_ids(self, attempt_id: str) -> tuple[str, ...]:
        try:
            return await self._attempts.get_delivered_question_ids(attempt_id)
        except ProviderUnavailableError:
            raise
        except Exception as exc:  # pragma: no cover - adapter defect
            logger.error("retake.delivered_questions_failed", extra={"attempt_id": attempt_id})
            raise QuestionBankUnavailableError(
                "The questions delivered in the previous attempt could not be read."
            ) from exc

    async def _history_ids(
        self, attempts: Sequence[AttemptContext], previous_attempt: AttemptContext
    ) -> tuple[str, ...]:
        """Delivered ids across the learner's recent attempts at this quiz.

        A single unreadable historical attempt degrades the exclusion set rather than failing the
        retake: excluding fewer questions produces a valid — if less varied — paper, whereas
        refusing the retake would deny a learner an attempt they are entitled to because of a
        record they cannot see. The paper being retaken is the exception, and is read separately by
        :meth:`_delivered_ids` where a failure does stop the retake.
        """
        recent = sorted(attempts, key=lambda attempt: attempt.attempt_number, reverse=True)[
            : self._history_lookback
        ]
        collected: list[str] = []
        for attempt in recent:
            if attempt.attempt_id == previous_attempt.attempt_id:
                continue
            try:
                collected.extend(
                    await self._attempts.get_delivered_question_ids(attempt.attempt_id)
                )
            except Exception:
                logger.warning(
                    "retake.history_questions_unreadable",
                    extra={"attempt_id": attempt.attempt_id},
                )
        return tuple(collected)


def _pool_types(config: QuizConfigurationVersion) -> tuple[str, ...]:
    """The types worth asking the bank for.

    Quota types when quotas are configured, the allowed list otherwise, and everything when
    neither is set. Passing the filter to UC-02 keeps the pool small; the domain applies the same
    restriction again so a permissive adapter cannot widen it.
    """
    quotas = [quota for quota in config.question_type_quotas if quota.count > 0]
    if quotas:
        return tuple(quota.type for quota in quotas)
    return tuple(config.allowed_question_types)
