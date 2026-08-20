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
    #: Ids that were deprioritised but delivered anyway because the eligible pool could not
    #: fill the paper without them (UC-08 §17). Empty for every non-retake attempt, and empty
    #: for a retake the bank was large enough to satisfy.
    reused_question_ids: tuple[str, ...] = ()


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


def _split_by_preference(
    candidates: Sequence[BankQuestion], deprioritised: frozenset[str]
) -> tuple[list[BankQuestion], list[BankQuestion]]:
    """Partition ``candidates`` into (not seen before, seen before), order preserved."""
    unseen = [question for question in candidates if question.question_id not in deprioritised]
    seen = [question for question in candidates if question.question_id in deprioritised]
    return unseen, seen


class QuestionSelectionService:
    """Chooses and orders the questions for an attempt."""

    __slots__ = ()

    def select(
        self,
        config: QuizConfigurationVersion,
        pool: Sequence[BankQuestion],
        seed: str,
        *,
        deprioritised_question_ids: Sequence[str] = (),
    ) -> SelectionResult:
        """Choose the questions for an attempt from ``pool`` according to ``config``.

        ``pool`` is expected to already exclude retired questions (the bank does
        this), but the filter is applied again here so a permissive adapter cannot
        leak a retired question into a new attempt.

        ``deprioritised_question_ids`` is UC-08's retake preference: questions this learner
        has already been shown. It is a **preference, not a filter** — unseen questions are
        drawn first and the remainder comes from the seen ones, so a bank too small to avoid
        reuse still produces a full paper rather than a short one. Quotas are honoured within
        each type, and a retired question is never reached for to avoid reuse, because the
        pool is still the eligible pool.

        Passing nothing (or a set that no candidate matches) takes the original code path
        untouched, so every non-retake attempt selects exactly the paper it did before.
        """
        validate_configuration_for_delivery(config)

        deliverable = [question for question in pool if is_deliverable(question)]
        quotas = [quota for quota in config.question_type_quotas if quota.count > 0]

        # Narrowed to what is actually deliverable: an id that cannot be drawn anyway is not a
        # preference, and letting it through would take the retake code path for a selection
        # that is identical to the ordinary one.
        deliverable_ids = {question.question_id for question in deliverable}
        deprioritised = frozenset(
            question_id
            for question_id in deprioritised_question_ids
            if question_id in deliverable_ids
        )

        if quotas:
            selected = self._select_by_quota(config, deliverable, quotas, seed, deprioritised)
        else:
            selected = self._select_by_count(config, deliverable, seed, deprioritised)

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

        # Recorded from what was actually delivered, not from what the planner hoped for, so
        # "reuse was unavoidable" is an observation rather than a prediction.
        reused = tuple(
            question.question_id for question in questions if question.question_id in deprioritised
        )

        return SelectionResult(
            questions=questions, type_counts=type_counts, reused_question_ids=reused
        )

    def _select_by_quota(
        self,
        config: QuizConfigurationVersion,
        pool: Sequence[BankQuestion],
        quotas: Sequence[object],
        seed: str,
        deprioritised: frozenset[str] = frozenset(),
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
            selected.extend(
                self._draw(
                    candidates,
                    quota_count,
                    f"{seed}:quota:{quota_type}",
                    randomise=config.randomise_question_order,
                    deprioritised=deprioritised,
                )
            )

        if shortfalls:
            raise errors.insufficient_questions(
                quizId=config.quiz_id,
                configurationVersionId=config.configuration_version_id,
                requestedQuestionCount=config.question_count,
                shortfalls=shortfalls,
            )

        return selected

    @staticmethod
    def _draw(
        candidates: Sequence[BankQuestion],
        count: int,
        seed: str,
        *,
        randomise: bool,
        deprioritised: frozenset[str],
    ) -> list[BankQuestion]:
        """Take ``count`` questions, preferring ones the learner has not seen.

        With no preference in play this is exactly the original two-line draw, seed string
        included — which is what makes a non-retake attempt byte-for-byte unchanged. The
        preference branch draws the unseen pool first and only then tops up from the seen
        one, each with its own derived seed so the two draws cannot correlate.
        """
        if not deprioritised:
            if randomise:
                return list(sample_without_replacement(candidates, count, make_rng(seed)))
            return list(candidates[:count])

        unseen, seen = _split_by_preference(candidates, deprioritised)
        from_unseen = min(count, len(unseen))
        shortfall = count - from_unseen

        if randomise:
            picked = list(
                sample_without_replacement(unseen, from_unseen, make_rng(f"{seed}:unseen"))
            )
            if shortfall:
                picked.extend(
                    sample_without_replacement(seen, shortfall, make_rng(f"{seed}:seen"))
                )
            return picked

        return list(unseen[:from_unseen]) + list(seen[:shortfall])

    def _select_by_count(
        self,
        config: QuizConfigurationVersion,
        pool: Sequence[BankQuestion],
        seed: str,
        deprioritised: frozenset[str] = frozenset(),
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

        return self._draw(
            candidates,
            config.question_count,
            f"{seed}:pool",
            randomise=config.randomise_question_order,
            deprioritised=deprioritised,
        )
