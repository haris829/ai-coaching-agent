"""Quiz configuration — validation, capacity, immutable versioning and atomic saves."""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError

from app.core.errors import (
    AppError,
    ConflictError,
    FieldIssue,
    NotFoundError,
    PersistenceFailedError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.question_types import QuestionPresentation
from app.modules.quiz_configuration.api import serializers
from app.modules.quiz_configuration.context import QuizConfigurationContext
from app.modules.quiz_configuration.domain.enums import (
    DeliveryMode,
    QuestionType,
)
from app.modules.quiz_configuration.domain.rules import (
    CapacityReport,
    QuestionTypeSelection,
    QuizConfiguration,
    evaluate_capacity,
    fingerprint_configuration,
    validate_configuration,
)
from app.modules.quiz_configuration.models import (
    ConfigurationVersion,
    Quiz,
    is_immutability_violation,
)
from app.modules.quiz_configuration.ports import BankScope, TopicRef

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Lookups shared with the attempt service
# ---------------------------------------------------------------------------


def require_quiz(ctx: QuizConfigurationContext, quiz_id: int) -> Quiz:
    quiz = ctx.quizzes.get(quiz_id)
    if quiz is None:
        raise NotFoundError("Quiz", str(quiz_id))
    return quiz


def require_active_version(ctx: QuizConfigurationContext, quiz: Quiz) -> ConfigurationVersion:
    """The active version is required to *start* a quiz; without one there is nothing to lock."""
    active = ctx.versions.get_active(quiz)
    if active is None:
        raise ConflictError(
            f'"{quiz.title}" has not been configured yet, so it cannot be started.',
            code="CONFIGURATION_UNAVAILABLE",
        )
    return active


def to_domain(version: ConfigurationVersion) -> QuizConfiguration:
    """Rehydrate a stored version into the domain object the rules operate on.

    Every rule — capacity, drawing questions, the learner rules summary — runs against this, so a
    historical version behaves exactly as it did the day it was written.
    """
    return QuizConfiguration(
        question_count=version.question_count,
        time_limit_minutes=version.time_limit_minutes,
        pass_mark=version.pass_mark,
        question_types=tuple(
            QuestionTypeSelection(QuestionType(entry.question_type), entry.question_quota)
            for entry in version.question_types
        ),
        randomise_questions=bool(version.randomise_questions),
        max_attempts=version.max_attempts,
        delivery_mode=DeliveryMode(version.delivery_mode),
        topic_ids=tuple(entry.topic_id for entry in version.topics),
        question_presentation=QuestionPresentation(version.question_presentation),
        randomise_option_order=bool(version.randomise_option_order),
        allow_incomplete_submission=bool(version.allow_incomplete_submission),
        is_formal_assessment=bool(version.is_formal_assessment),
        requires_human_review=bool(version.requires_human_review),
        requires_assessor_approval=bool(version.requires_assessor_approval),
    )


def scope_for(config: QuizConfiguration) -> BankScope:
    """The slice of the question bank a configuration considers eligible."""
    return BankScope(types=config.selected_types, topic_ids=config.topic_ids)


def evaluate_bank_capacity(
    ctx: QuizConfigurationContext, config: QuizConfiguration
) -> CapacityReport:
    """Can the question bank satisfy this configuration right now?

    Availability comes from the bank through the port, so retired and draft questions are already
    excluded and the answer cannot drift from what an attempt would actually be able to draw.
    """
    return evaluate_capacity(config, ctx.bank.available_by_type(scope_for(config)))


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _version_json(
    ctx: QuizConfigurationContext, quiz: Quiz, version: ConfigurationVersion
) -> dict[str, Any]:
    counts = ctx.attempt_stats.count_by_configuration_version([version.id])
    return serializers.configuration_version(
        version,
        is_active=quiz.active_configuration_version_id == version.id,
        attempt_count=counts.get(version.id, 0),
    )


def get_configuration(ctx: QuizConfigurationContext, quiz_id: int) -> dict[str, Any]:
    """Active configuration plus a live capacity report for the admin screen."""
    quiz = require_quiz(ctx, quiz_id)
    active = ctx.versions.get_active(quiz)
    if active is None:
        return {"quiz": serializers.quiz_summary(quiz), "configuration": None, "capacity": None}

    config = to_domain(active)
    return {
        "quiz": serializers.quiz_summary(quiz),
        "configuration": _version_json(ctx, quiz, active),
        "capacity": serializers.capacity(evaluate_bank_capacity(ctx, config)),
    }


def list_versions(ctx: QuizConfigurationContext, quiz_id: int) -> dict[str, Any]:
    """Immutable version history, newest first."""
    quiz = require_quiz(ctx, quiz_id)
    versions = ctx.versions.list_for_quiz(quiz_id)
    counts = ctx.attempt_stats.count_by_configuration_version([version.id for version in versions])
    return {
        "quiz": serializers.quiz_summary(quiz),
        "versions": [
            serializers.configuration_version(
                version,
                is_active=quiz.active_configuration_version_id == version.id,
                attempt_count=counts.get(version.id, 0),
            )
            for version in versions
        ],
    }


def get_question_bank_availability(
    ctx: QuizConfigurationContext, quiz_id: int, topic_ids: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Live eligible-question counts per type, for the admin screen's capacity hint.

    Retired questions are absent by construction — the count comes from the bank's deliverable
    query, the same one that feeds capacity validation and question drawing.
    """
    quiz = require_quiz(ctx, quiz_id)
    scope = BankScope(types=tuple(QuestionType), topic_ids=topic_ids)
    return {
        "quiz": serializers.quiz_summary(quiz),
        "topicIds": list(topic_ids),
        "availableByType": serializers.availability(ctx.bank.available_by_type(scope)),
    }


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------


def save_configuration(
    ctx: QuizConfigurationContext,
    quiz_id: int,
    payload: Any,
    *,
    actor_user_id: int | None,
    actor: str | None,
) -> tuple[dict[str, Any], bool]:
    """Save a configuration by creating a **new immutable version**.

    Nothing is written until every gate has passed:

    1. authoritative field validation, run independently of whatever the UI did;
    2. topic-scope resolution against the question bank;
    3. question-bank capacity validation;
    4. no-op detection against the active version's fingerprint;
    5. one transaction that inserts the version, its question types, its topic scope and
       repoints the quiz — any failure rolls all four back.

    Returns ``(response_body, created)``.
    """
    quiz = require_quiz(ctx, quiz_id)

    # 1. Field-level rules. The UI mirrors these, but this is the gate that counts.
    result = validate_configuration(payload)
    if not result.valid or result.value is None:
        raise ValidationError("The quiz configuration is not valid.", result.errors)
    config = result.value

    # 2. Resolve the topic scope. An unknown topic id is a configuration error, not something to
    #    silently drop: a scope that means less than the administrator asked for would quietly
    #    change which questions learners see.
    topics = _resolve_scope(ctx, config)

    # 3. Can the question bank actually deliver this quiz?
    capacity = evaluate_bank_capacity(ctx, config)
    if not capacity.satisfiable:
        raise _insufficient_bank(capacity)

    # 4. Only a *meaningful* change creates a version.
    fingerprint = fingerprint_configuration(config)
    active = ctx.versions.get_active(quiz)
    if active is not None and active.settings_fingerprint == fingerprint:
        return (
            {
                "configuration": _version_json(ctx, quiz, active),
                "capacity": serializers.capacity(capacity),
                "created": False,
                "unchanged": True,
            },
            False,
        )

    # 5. Atomic write.
    version = _write_version(
        ctx,
        quiz,
        config,
        topics,
        fingerprint=fingerprint,
        actor_user_id=actor_user_id,
        actor=actor,
    )

    logger.info(
        "configuration.version_created",
        extra={
            "quiz_id": quiz.id,
            "version_id": version.id,
            "version_number": version.version_number,
            "actor": actor,
            "question_count": version.question_count,
            "delivery_mode": version.delivery_mode,
        },
    )

    return (
        {
            "configuration": _version_json(ctx, quiz, version),
            "capacity": serializers.capacity(capacity),
            "created": True,
        },
        True,
    )


def _resolve_scope(
    ctx: QuizConfigurationContext, config: QuizConfiguration
) -> list[TopicRef]:
    if not config.topic_ids:
        return []
    topics = ctx.bank.resolve_topics(config.topic_ids)
    known = {topic.id for topic in topics}
    unknown = [topic_id for topic_id in config.topic_ids if topic_id not in known]
    if unknown:
        raise ValidationError(
            "The quiz configuration is not valid.",
            [
                FieldIssue(
                    "topicIds",
                    "UNKNOWN_TOPIC",
                    "These topic ids do not exist in the question bank: "
                    + ", ".join(unknown)
                    + ".",
                )
            ],
        )
    return topics


def _write_version(
    ctx: QuizConfigurationContext,
    quiz: Quiz,
    config: QuizConfiguration,
    topics: list[TopicRef],
    *,
    fingerprint: str,
    actor_user_id: int | None,
    actor: str | None,
) -> ConfigurationVersion:
    """Insert a new version and repoint the quiz, in one transaction.

    On any failure the rollback leaves no version row, no question-type rows, no topic rows, and
    the quiz still pointing at whatever was active before. A failed save also does not consume a
    version number, because the number is allocated inside the same transaction.
    """
    try:
        version = ctx.versions.insert(
            quiz_id=quiz.id,
            version_number=ctx.versions.next_version_number(quiz.id),
            config=config,
            fingerprint=fingerprint,
            created_by_user_id=actor_user_id,
            created_by=actor,
        )
        ctx.versions.insert_question_types(version, config)
        ctx.versions.insert_topics(version, topics)
        ctx.quizzes.set_active_configuration_version(quiz, version.id)
        ctx.commit()
    except Exception as error:  # noqa: BLE001 — re-raised below as a client-safe error
        ctx.rollback()
        if isinstance(error, AppError):
            raise
        logger.error(
            "configuration.save_failed",
            extra={"quiz_id": quiz.id, "actor": actor},
            exc_info=error,
        )
        if is_immutability_violation(error):
            raise ConflictError(
                "Existing configuration versions cannot be modified. A new version must be "
                "created instead.",
                code="IMMUTABLE_CONFIGURATION_VERSION",
            ) from error
        if isinstance(error, IntegrityError):
            # Two administrators saved concurrently and both claimed the same version number.
            # Nothing was written, so the caller can simply retry and get the next number.
            raise ConflictError(
                "Another administrator saved a configuration for this quiz at the same moment. "
                "Nothing was saved — reload the configuration and try again.",
                code="CONCURRENT_CONFIGURATION_UPDATE",
                extra={"retryable": True},
            ) from error
        raise PersistenceFailedError("save_quiz_configuration", error) from error

    ctx.refresh(quiz)
    stored = ctx.versions.get(version.id)
    if stored is None:
        logger.error("configuration.version_missing_after_commit", extra={"quiz_id": quiz.id})
        raise PersistenceFailedError(
            "read_back_quiz_configuration", RuntimeError("version missing after commit")
        )
    return stored


def _insufficient_bank(capacity: CapacityReport) -> ValidationError:
    return ValidationError(
        "The question bank cannot satisfy this configuration.",
        code="QUESTION_BANK_INSUFFICIENT",
        extra={"capacity": serializers.capacity(capacity)},
    )
