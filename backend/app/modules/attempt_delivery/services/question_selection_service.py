"""Question selection for a new attempt.

Runs exactly once, inside the attempt-creation transaction, and its output is frozen
onto the attempt. Nothing here executes again for the life of the attempt, which is
what guarantees a refresh cannot change the paper.

Randomisation is driven by a seed persisted on the attempt, so a selection can be
re-derived for audit and asserted on in tests without flakiness.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from app.modules.attempt_delivery.domain import errors
from app.modules.attempt_delivery.domain.enums import QuestionType
from app.modules.attempt_delivery.domain.rng import make_rng, sample_without_replacement, shuffled
from app.modules.attempt_delivery.integration.uc01.types import QuizConfigurationVersion
from app.modules.attempt_delivery.integration.uc02.types import BankQuestion, ScenarioSubQuestion


@dataclass(frozen=True, slots=True)
class SelectionResult:
    questions: tuple[BankQuestion, ...]
    #: Per-type counts actually delivered; surfaced for diagnostics and tests.
    type_counts: dict[str, int]


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------


def validate_configuration_for_delivery(config: QuizConfigurationVersion) -> None:
    """Validate the parts of a UC-01 configuration UC-03 must rely on.

    This is not a reimplementation of UC-01's authoring rules — it is a guard at the
    boundary. An attempt must never be created from a configuration that cannot
    produce a coherent paper, and failing here keeps a partial attempt from ever
    being persisted.
    """
    version_id = config.configuration_version_id

    if not isinstance(config.question_count, int) or config.question_count < 1:
        raise errors.invalid_configuration(
            '"questionCount" must be a positive integer.',
            configurationVersionId=version_id,
            questionCount=config.question_count,
        )

    if config.time_limit_seconds is not None and (
        not isinstance(config.time_limit_seconds, int) or config.time_limit_seconds <= 0
    ):
        raise errors.invalid_configuration(
            '"timeLimitSeconds" must be a positive integer or null.',
            configurationVersionId=version_id,
            timeLimitSeconds=config.time_limit_seconds,
        )

    if not 0 <= config.pass_mark_percentage <= 100:
        raise errors.invalid_configuration(
            '"passMarkPercentage" must be between 0 and 100.',
            configurationVersionId=version_id,
            passMarkPercentage=config.pass_mark_percentage,
        )

    if config.max_attempts is not None and (
        not isinstance(config.max_attempts, int) or config.max_attempts < 1
    ):
        raise errors.invalid_configuration(
            '"maxAttempts" must be a positive integer or null.',
            configurationVersionId=version_id,
            maxAttempts=config.max_attempts,
        )

    if config.question_type_quotas:
        seen: set[QuestionType] = set()
        total = 0
        for quota in config.question_type_quotas:
            if quota.type in seen:
                raise errors.invalid_configuration(
                    f'Duplicate question type quota for "{quota.type}".',
                    configurationVersionId=version_id,
                )
            seen.add(quota.type)
            if not isinstance(quota.count, int) or quota.count < 0:
                raise errors.invalid_configuration(
                    f'Quota count for "{quota.type}" must be a non-negative integer.',
                    configurationVersionId=version_id,
                )
            total += quota.count
        if total != config.question_count:
            raise errors.invalid_configuration(
                'Question type quotas must sum to "questionCount".',
                configurationVersionId=version_id,
                questionCount=config.question_count,
                quotaTotal=total,
            )


# ---------------------------------------------------------------------------
# Structural checks
# ---------------------------------------------------------------------------


def _sub_question_deliverable(sub: ScenarioSubQuestion) -> bool:
    if sub.type in {QuestionType.SINGLE_CHOICE, QuestionType.MULTI_SELECT}:
        return len(sub.options) >= 2
    if sub.type is QuestionType.TRUE_FALSE:
        return True
    if sub.type is QuestionType.DRAG_TO_ORDER:
        return len(sub.order_items) >= 2
    return False


def is_deliverable(question: BankQuestion) -> bool:
    """Whether a bank question can actually be delivered and answered.

    A structurally broken question (a single-choice with no options, an empty
    scenario) is skipped rather than delivered, because the learner could not answer
    it. Content *correctness* remains UC-02's responsibility.
    """
    if question.retired:
        return False
    if question.type in {QuestionType.SINGLE_CHOICE, QuestionType.MULTI_SELECT}:
        return len(question.options) >= 2
    if question.type is QuestionType.TRUE_FALSE:
        return True
    if question.type is QuestionType.DRAG_TO_ORDER:
        return len(question.order_items) >= 2
    if question.type is QuestionType.SCENARIO:
        return bool(question.sub_questions) and all(
            _sub_question_deliverable(sub) for sub in question.sub_questions
        )
    return False


# ---------------------------------------------------------------------------
# Option randomisation
# ---------------------------------------------------------------------------


def _randomise_options(question: BankQuestion, seed: str) -> BankQuestion:
    """Shuffle presented options/items, leaving grading metadata untouched.

    A per-question seed keeps each question's shuffle independent and stable, so
    adding a question to the bank does not reshuffle unrelated ones.
    """
    updates: dict[str, object] = {}

    if len(question.options) > 1:
        updates["options"] = tuple(
            shuffled(question.options, make_rng(f"{seed}:options:{question.question_id}"))
        )
    if len(question.order_items) > 1:
        # The *presented* order is shuffled; the correct sequence lives in each
        # item's correct_position and is not modified.
        updates["order_items"] = tuple(
            shuffled(question.order_items, make_rng(f"{seed}:items:{question.question_id}"))
        )
    if question.sub_questions:
        new_subs: list[ScenarioSubQuestion] = []
        for sub in question.sub_questions:
            sub_updates: dict[str, object] = {}
            if len(sub.options) > 1:
                sub_updates["options"] = tuple(
                    shuffled(
                        sub.options,
                        make_rng(f"{seed}:sub-options:{question.question_id}:{sub.sub_question_id}"),
                    )
                )
            if len(sub.order_items) > 1:
                sub_updates["order_items"] = tuple(
                    shuffled(
                        sub.order_items,
                        make_rng(f"{seed}:sub-items:{question.question_id}:{sub.sub_question_id}"),
                    )
                )
            new_subs.append(replace(sub, **sub_updates) if sub_updates else sub)
        updates["sub_questions"] = tuple(new_subs)

    return replace(question, **updates) if updates else question


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


class QuestionSelectionService:
    """Chooses and orders the questions for an attempt."""

    __slots__ = ()

    def select(
        self,
        config: QuizConfigurationVersion,
        pool: Sequence[BankQuestion],
        seed: str,
    ) -> SelectionResult:
        """Choose the questions for an attempt from ``pool`` according to ``config``.

        ``pool`` is expected to already exclude retired questions (the bank does
        this), but the filter is applied again here so a permissive adapter cannot
        leak a retired question into a new attempt.
        """
        validate_configuration_for_delivery(config)

        deliverable = [question for question in pool if is_deliverable(question)]
        quotas = [quota for quota in config.question_type_quotas if quota.count > 0]

        if quotas:
            selected = self._select_by_quota(config, deliverable, quotas, seed)
        else:
            selected = self._select_by_count(config, deliverable, seed)

        # Presentation order. With randomisation off the order is the pool's
        # deterministic order (quota groups in configured order), so the same
        # configuration always produces the same paper.
        ordered = (
            shuffled(selected, make_rng(f"{seed}:order"))
            if config.randomise_question_order
            else selected
        )

        questions = tuple(
            _randomise_options(question, seed) if config.randomise_option_order else question
            for question in ordered
        )

        type_counts: dict[str, int] = {}
        for question in questions:
            key = str(question.type)
            type_counts[key] = type_counts.get(key, 0) + 1

        return SelectionResult(questions=questions, type_counts=type_counts)

    def _select_by_quota(
        self,
        config: QuizConfigurationVersion,
        pool: Sequence[BankQuestion],
        quotas: Sequence[object],
        seed: str,
    ) -> list[BankQuestion]:
        shortfalls: list[dict[str, object]] = []
        selected: list[BankQuestion] = []

        for quota in quotas:
            quota_type = quota.type  # type: ignore[attr-defined]
            quota_count = quota.count  # type: ignore[attr-defined]
            candidates = [question for question in pool if question.type is quota_type]
            if len(candidates) < quota_count:
                shortfalls.append(
                    {
                        "type": str(quota_type),
                        "required": quota_count,
                        "available": len(candidates),
                    }
                )
                continue
            if config.randomise_question_order:
                selected.extend(
                    sample_without_replacement(
                        candidates, quota_count, make_rng(f"{seed}:quota:{quota_type}")
                    )
                )
            else:
                selected.extend(candidates[:quota_count])

        if shortfalls:
            raise errors.insufficient_questions(
                quizId=config.quiz_id,
                configurationVersionId=config.configuration_version_id,
                requestedQuestionCount=config.question_count,
                shortfalls=shortfalls,
            )

        return selected

    def _select_by_count(
        self,
        config: QuizConfigurationVersion,
        pool: Sequence[BankQuestion],
        seed: str,
    ) -> list[BankQuestion]:
        allowed = config.allowed_question_types
        candidates = (
            list(pool)
            if not allowed
            else [question for question in pool if question.type in allowed]
        )

        if len(candidates) < config.question_count:
            raise errors.insufficient_questions(
                quizId=config.quiz_id,
                configurationVersionId=config.configuration_version_id,
                requestedQuestionCount=config.question_count,
                availableQuestionCount=len(candidates),
                allowedQuestionTypes=[str(item) for item in allowed] or None,
            )

        if config.randomise_question_order:
            return sample_without_replacement(
                candidates, config.question_count, make_rng(f"{seed}:pool")
            )
        return candidates[: config.question_count]
