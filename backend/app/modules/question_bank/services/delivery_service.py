"""Delivery eligibility, usage recording and historical reporting.

This is the INTEGRATION SEAM between UC-02 (the question bank) and the quiz-delivery /
attempt module that is being built separately. UC-02 owns three guarantees here:

1. **Rule 3 — retired questions are never delivered.** ``select_deliverable_questions`` filters
   on ``DELIVERABLE_STATUSES`` at the query level, so exclusion is structural rather than
   something a caller has to remember.
2. **Rule 7 — historical references never break.** Recording a delivery pins the question's
   *current snapshot*. Reports are rendered from that snapshot, so editing or retiring the
   question afterwards cannot alter what a completed attempt shows.
3. **Presentation order stays separate from answer order.** The order the learner actually saw
   is stored on the usage row; the correct order lives in the snapshot.

``attempt_ref`` is an opaque string owned by the delivery module — intentionally not a foreign
key, so this module does not depend on another team's unfinished tables.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ConflictError, FieldIssue, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.time import utcnow
from app.modules.question_bank.domain.enums import (
    DELIVERABLE_STATUSES,
    AttemptStatus,
    QuestionStatus,
)
from app.modules.question_bank.domain.grading import grade, validate_response
from app.modules.question_bank.domain.snapshots import load_payload, parse_snapshot_view
from app.modules.question_bank.models import (
    Question,
    QuestionTopic,
    QuestionUsage,
    Topic,
)
from app.modules.question_bank.services import question_service

logger = get_logger(__name__)

_DELIVERABLE = [status.value for status in DELIVERABLE_STATUSES]


# ---------------------------------------------------------------------------
# Delivery pool
# ---------------------------------------------------------------------------


def deliverable_conditions(
    *,
    topic_ids: list[str] | None = None,
    topic_slugs: list[str] | None = None,
    types: list[str] | None = None,
    difficulty: str | None = None,
) -> list[Any]:
    """Build the eligibility predicate for FUTURE delivery.

    The single enforcement point for UC-02 Rule 3: only ACTIVE questions can appear. DRAFT is not
    publishable and RETIRED is withheld. Every caller that needs a deliverable pool — the pool
    endpoint, the per-type capacity count, the per-type draw — composes this, so exclusion is
    structural rather than something each caller has to remember.
    """
    conditions: list[Any] = [Question.status.in_(_DELIVERABLE)]

    if types:
        conditions.append(Question.type.in_([t.strip().upper() for t in types if t.strip()]))
    if difficulty:
        conditions.append(Question.difficulty == difficulty.strip().upper())
    if topic_ids:
        conditions.append(
            Question.id.in_(
                select(QuestionTopic.question_id).where(QuestionTopic.topic_id.in_(topic_ids))
            )
        )
    if topic_slugs:
        conditions.append(
            Question.id.in_(
                select(QuestionTopic.question_id)
                .join(Topic, Topic.id == QuestionTopic.topic_id)
                .where(Topic.slug.in_([slug.strip().lower() for slug in topic_slugs]))
            )
        )

    return conditions


def select_deliverable_questions(
    db: Session,
    *,
    topic_ids: list[str] | None = None,
    topic_slugs: list[str] | None = None,
    types: list[str] | None = None,
    difficulty: str | None = None,
    limit: int = 10,
) -> tuple[list[Question], int]:
    """Return questions eligible for a FUTURE quiz, plus how many exist in total."""
    conditions = deliverable_conditions(
        topic_ids=topic_ids, topic_slugs=topic_slugs, types=types, difficulty=difficulty
    )

    count_stmt = select(func.count(Question.id))
    data_stmt = select(Question).options(
        selectinload(Question.options),
        selectinload(Question.topic_links).joinedload(QuestionTopic.topic),
    )
    for condition in conditions:
        count_stmt = count_stmt.where(condition)
        data_stmt = data_stmt.where(condition)

    total_available = int(db.execute(count_stmt).scalar_one())
    rows = (
        db.execute(data_stmt.order_by(Question.seq).limit(max(1, min(200, limit))))
        .scalars()
        .all()
    )
    return list(rows), total_available


def count_deliverable_by_type(
    db: Session,
    *,
    types: list[str] | None = None,
    topic_ids: list[str] | None = None,
) -> dict[str, int]:
    """Eligible question counts grouped by type — the input to UC-01's capacity rule.

    One aggregate query regardless of how many types are asked about, so validating a
    five-type configuration costs the same as validating a one-type configuration.
    """
    stmt = select(Question.type, func.count(Question.id))
    for condition in deliverable_conditions(types=types, topic_ids=topic_ids):
        stmt = stmt.where(condition)
    rows = db.execute(stmt.group_by(Question.type)).all()
    return {str(question_type): int(total) for question_type, total in rows}


def draw_deliverable(
    db: Session,
    *,
    question_type: str,
    limit: int,
    randomise: bool = False,
    topic_ids: list[str] | None = None,
) -> list[Question]:
    """Draw up to ``limit`` eligible questions of one type.

    Ordering and limiting happen in the database (``RANDOM()`` is available on SQLite and
    PostgreSQL alike), so a large bank is never loaded into memory to pick a few questions.
    """
    if limit <= 0:
        return []

    stmt = select(Question).options(selectinload(Question.options))
    for condition in deliverable_conditions(types=[question_type], topic_ids=topic_ids):
        stmt = stmt.where(condition)

    ordering = func.random() if randomise else Question.seq.asc()
    return list(db.execute(stmt.order_by(ordering).limit(limit)).scalars().all())


# ---------------------------------------------------------------------------
# Usage recording
# ---------------------------------------------------------------------------


def record_usage(
    db: Session,
    *,
    attempt_ref: str,
    question_id: str,
    learner_ref: str | None = None,
    presentation_order: list[str] | None = None,
) -> QuestionUsage:
    """Record that a question was delivered to an attempt, pinning its current snapshot."""
    question = question_service.get_question(db, question_id)

    if question.status not in _DELIVERABLE:
        raise ConflictError(
            f"{question.reference} has status {question.status} and cannot be delivered to a "
            "new attempt. Only ACTIVE questions are deliverable.",
            code="QUESTION_NOT_DELIVERABLE",
        )

    existing = db.execute(
        select(QuestionUsage).where(
            QuestionUsage.attempt_ref == attempt_ref, QuestionUsage.question_id == question.id
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            f"{question.reference} has already been delivered to attempt '{attempt_ref}'.",
            code="USAGE_ALREADY_RECORDED",
        )

    snapshot = question_service.latest_snapshot(db, question.id)

    if presentation_order:
        known = {option.label.upper() for option in question.options}
        unknown = [label for label in presentation_order if label.upper() not in known]
        if unknown:
            raise ValidationError(
                "The presentation order references unknown options.",
                [
                    FieldIssue(
                        "presentationOrder",
                        "UNKNOWN_OPTION_LABEL",
                        "Unknown option label(s): " + ", ".join(unknown) + ".",
                    )
                ],
            )

    usage = QuestionUsage(
        attempt_ref=attempt_ref,
        learner_ref=learner_ref,
        question_id=question.id,
        snapshot_id=snapshot.id,
        snapshot_version=snapshot.version,
        attempt_status=AttemptStatus.IN_PROGRESS.value,
        presentation_order=json.dumps(presentation_order) if presentation_order else None,
        max_points=question.points,
    )
    db.add(usage)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            f"{question.reference} has already been delivered to attempt '{attempt_ref}'.",
            code="USAGE_ALREADY_RECORDED",
        ) from exc

    db.refresh(usage)
    logger.info(
        "delivery.usage_recorded",
        extra={
            "attempt_ref": attempt_ref,
            "question_id": question.id,
            "snapshot_version": snapshot.version,
        },
    )
    return usage


def record_usages(
    db: Session,
    *,
    attempt_ref: str,
    questions: Sequence[Question],
    learner_ref: str | None = None,
    commit: bool = False,
) -> list[QuestionUsage]:
    """Pin a whole attempt's worth of questions in one unit of work.

    Unlike :func:`record_usage` this does **not** commit by default: creating an attempt and
    pinning its questions is a single transaction owned by the caller, so a failure half-way
    through must leave neither behind. It also resolves every snapshot in one query rather than
    one per question.

    ``questions`` must already be known deliverable — the caller drew them from
    :func:`draw_deliverable`, which filters on status. Eligibility is re-asserted here anyway,
    because "an attempt never receives a retired question" is too important to assume.
    """
    if not questions:
        return []

    not_deliverable = [q.reference for q in questions if q.status not in _DELIVERABLE]
    if not_deliverable:
        raise ConflictError(
            "These questions are no longer deliverable and cannot be added to an attempt: "
            + ", ".join(sorted(not_deliverable))
            + ".",
            code="QUESTION_NOT_DELIVERABLE",
        )

    snapshots = question_service.latest_snapshots_bulk(db, [q.id for q in questions])
    missing = [q.reference for q in questions if q.id not in snapshots]
    if missing:
        # Unreachable in practice: every question is snapshotted at creation.
        logger.error("delivery.snapshot_missing", extra={"references": missing})
        raise ConflictError(
            "Some questions have no stored snapshot and cannot be delivered.",
            code="SNAPSHOT_MISSING",
        )

    usages: list[QuestionUsage] = []
    for position, question in enumerate(questions, start=1):
        snapshot = snapshots[question.id]
        usage = QuestionUsage(
            attempt_ref=attempt_ref,
            learner_ref=learner_ref,
            question_id=question.id,
            snapshot_id=snapshot.id,
            snapshot_version=snapshot.version,
            delivery_position=position,
            attempt_status=AttemptStatus.IN_PROGRESS.value,
            presentation_order=json.dumps([option.label for option in question.options])
            if question.options
            else None,
            max_points=question.points,
        )
        db.add(usage)
        usages.append(usage)

    db.flush()
    if commit:
        db.commit()

    logger.info(
        "delivery.usages_recorded",
        extra={"attempt_ref": attempt_ref, "question_count": len(usages)},
    )
    return usages


def usages_for_attempt(db: Session, attempt_ref: str) -> list[QuestionUsage]:
    """The questions an attempt was given, in their locked order.

    ``QuestionUsage.snapshot`` is eagerly joined by the mapping, so this is one query.
    """
    return list(
        db.execute(
            select(QuestionUsage)
            .where(QuestionUsage.attempt_ref == attempt_ref)
            # Positions are assigned together; delivered_at is the tie-break for callers that
            # never set one.
            .order_by(QuestionUsage.delivery_position, QuestionUsage.delivered_at)
        )
        .scalars()
        .all()
    )


def record_response(
    db: Session,
    usage_id: str,
    *,
    selected_labels: list[str] | None = None,
    ordered_labels: list[str] | None = None,
    attempt_status: AttemptStatus = AttemptStatus.COMPLETED,
) -> QuestionUsage:
    """Record the learner's response and score it against the pinned snapshot.

    Grading reads the snapshot, never the live question, so a score stays reproducible after
    the question has been edited or retired.
    """
    usage = db.get(QuestionUsage, usage_id)
    if usage is None:
        raise NotFoundError("Usage record", usage_id)

    if usage.attempt_status == AttemptStatus.COMPLETED.value:
        raise ConflictError(
            "This attempt's answer has already been recorded and completed responses are "
            "immutable.",
            code="USAGE_ALREADY_COMPLETED",
        )

    view = parse_snapshot_view(load_payload(usage.snapshot.payload))
    if view is None:
        # A corrupt snapshot must not crash the delivery module.
        logger.error(
            "delivery.snapshot_unreadable",
            extra={"usage_id": usage.id, "snapshot_id": usage.snapshot_id},
        )
        raise ConflictError(
            "The stored question snapshot for this attempt could not be read, so the response "
            "cannot be scored.",
            code="SNAPSHOT_UNREADABLE",
        )

    issues = validate_response(view, selected_labels, ordered_labels)
    if issues:
        raise ValidationError("The learner response is not valid for this question.", issues)

    result = grade(view, selected_labels=selected_labels, ordered_labels=ordered_labels)

    usage.learner_response = json.dumps(
        {
            "selectedLabels": selected_labels or [],
            "orderedLabels": ordered_labels or [],
        }
    )
    usage.is_correct = result.is_correct
    usage.awarded_points = result.awarded_points
    usage.max_points = result.max_points
    usage.attempt_status = attempt_status.value
    usage.responded_at = utcnow()
    if attempt_status is AttemptStatus.COMPLETED:
        usage.completed_at = utcnow()

    db.commit()
    db.refresh(usage)
    logger.info(
        "delivery.response_recorded",
        extra={
            "usage_id": usage.id,
            "attempt_ref": usage.attempt_ref,
            "is_correct": usage.is_correct,
            "awarded": usage.awarded_points,
        },
    )
    return usage


def list_question_usages(db: Session, question_id: str) -> list[QuestionUsage]:
    question = question_service.get_question(db, question_id)
    return list(
        db.execute(
            select(QuestionUsage)
            .where(QuestionUsage.question_id == question.id)
            .order_by(QuestionUsage.delivered_at)
        )
        .scalars()
        .all()
    )


# ---------------------------------------------------------------------------
# Historical reporting (UC-02 §16)
# ---------------------------------------------------------------------------


def build_attempt_report(db: Session, attempt_ref: str) -> dict[str, object]:
    """Render a completed attempt entirely from frozen snapshots.

    This is the test of UC-02 §16: everything below comes from ``qb_question_snapshots`` and
    ``qb_question_usages``, so retiring or editing a question afterwards cannot change it. The
    live question's current status is reported alongside as context, not as content.
    """
    usages = (
        db.execute(
            select(QuestionUsage)
            .where(QuestionUsage.attempt_ref == attempt_ref)
            .order_by(QuestionUsage.delivered_at)
        )
        .scalars()
        .all()
    )
    if not usages:
        raise NotFoundError("Attempt", attempt_ref)

    question_ids = [usage.question_id for usage in usages]
    live_statuses = dict(
        db.execute(
            select(Question.id, Question.status).where(Question.id.in_(question_ids))
        ).all()
    )

    items: list[dict[str, object]] = []
    total_awarded = 0.0
    total_max = 0.0

    for usage in usages:
        payload = load_payload(usage.snapshot.payload)
        snapshot = usage.snapshot

        learner_response = None
        if usage.learner_response:
            try:
                learner_response = json.loads(usage.learner_response)
            except ValueError:
                learner_response = None

        presentation_order = None
        if usage.presentation_order:
            try:
                parsed = json.loads(usage.presentation_order)
                presentation_order = parsed if isinstance(parsed, list) else None
            except ValueError:
                presentation_order = None

        total_awarded += float(usage.awarded_points or 0)
        total_max += float(usage.max_points or snapshot.points or 0)

        items.append(
            {
                "questionId": usage.question_id,
                # Original question identity, preserved through retirement.
                "questionReference": snapshot.reference,
                "snapshotVersion": usage.snapshot_version,
                "currentQuestionStatus": live_statuses.get(
                    usage.question_id, QuestionStatus.RETIRED.value
                ),
                "type": snapshot.type,
                "questionText": snapshot.question_text,
                "scenarioText": snapshot.scenario_text,
                "explanation": snapshot.explanation,
                "options": payload.get("options", []),
                "correctLabels": payload.get("correctLabels", []),
                "correctOrder": payload.get("correctOrder", []),
                "topics": payload.get("topics", []),
                "learnerResponse": learner_response,
                "presentationOrder": presentation_order,
                "isCorrect": usage.is_correct,
                "awardedPoints": usage.awarded_points,
                "maxPoints": usage.max_points,
                "deliveredAt": usage.delivered_at,
                "completedAt": usage.completed_at,
            }
        )

    statuses = {usage.attempt_status for usage in usages}
    if statuses == {AttemptStatus.COMPLETED.value}:
        attempt_status = AttemptStatus.COMPLETED.value
    elif AttemptStatus.IN_PROGRESS.value in statuses:
        attempt_status = AttemptStatus.IN_PROGRESS.value
    else:
        attempt_status = sorted(statuses)[0]

    return {
        "attemptRef": attempt_ref,
        "learnerRef": next((u.learner_ref for u in usages if u.learner_ref), None),
        "attemptStatus": attempt_status,
        "questionCount": len(items),
        "totalAwardedPoints": round(total_awarded, 4),
        "totalMaxPoints": round(total_max, 4),
        "items": items,
    }
