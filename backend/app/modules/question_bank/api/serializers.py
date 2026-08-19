"""ORM -> response-schema mapping.

Kept in one place so every endpoint returns the same shape for the same entity, and so the
routers stay thin.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.modules.question_bank.domain.snapshots import load_payload
from app.modules.question_bank.models import (
    Question,
    QuestionImport,
    QuestionSnapshot,
    QuestionUsage,
    Topic,
)
from app.modules.question_bank.schemas.delivery import (
    DeliverableOption,
    DeliverableQuestion,
    UsageOut,
)
from app.modules.question_bank.schemas.import_run import (
    ImportedRowSummary,
    ImportListItem,
    ImportResult,
    ImportRowError,
    RejectedRowSummary,
)
from app.modules.question_bank.schemas.question import (
    OptionOut,
    QuestionListItem,
    QuestionOut,
    ScoringOut,
    SnapshotOut,
    UsageSummary,
)
from app.modules.question_bank.schemas.topic import TopicOut, TopicRef
from app.modules.question_bank.services import import_service, question_service


def topic_ref(topic: Topic) -> TopicRef:
    return TopicRef(id=topic.id, slug=topic.slug, name=topic.name)


def topic_out(topic: Topic, question_count: int | None = None) -> TopicOut:
    return TopicOut(
        id=topic.id,
        slug=topic.slug,
        name=topic.name,
        description=topic.description,
        is_active=topic.is_active,
        created_at=topic.created_at,
        updated_at=topic.updated_at,
        question_count=question_count,
    )


def option_out(option: Any) -> OptionOut:
    return OptionOut(
        id=option.id,
        label=option.label,
        text=option.text,
        position=option.position,
        is_correct=option.is_correct,
        is_primary=option.is_primary,
        correct_position=option.correct_position,
        feedback=option.feedback,
    )


def usage_summary(counts: dict[str, int]) -> UsageSummary:
    total = counts.get("total", 0)
    return UsageSummary(
        total=total,
        completed=counts.get("completed", 0),
        in_progress=counts.get("in_progress", 0),
        has_history=total > 0,
        # A question with any usage must be retired, never hard-deleted (UC-02 Rule 2).
        can_hard_delete=total == 0,
    )


def question_out(question: Question, *, usage: dict[str, int] | None = None) -> QuestionOut:
    return QuestionOut(
        id=question.id,
        reference=question.reference,
        seq=question.seq,
        external_ref=question.external_ref,
        type=question.type,  # type: ignore[arg-type]
        status=question.status,  # type: ignore[arg-type]
        question_text=question.question_text,
        scenario_text=question.scenario_text,
        explanation=question.explanation,
        difficulty=question.difficulty,  # type: ignore[arg-type]
        scoring=ScoringOut(
            points=question.points,
            scoring_strategy=question.scoring_strategy,  # type: ignore[arg-type]
            penalty_per_incorrect=question.penalty_per_incorrect,
        ),
        version=question.version,
        content_hash=question.content_hash,
        options=[
            option_out(option) for option in sorted(question.options, key=lambda o: o.position)
        ],
        topics=[
            topic_ref(link.topic)
            for link in sorted(question.topic_links, key=lambda item: item.topic.name)
        ],
        correct_labels=question_service.correct_labels(question),
        correct_order=question_service.correct_order(question),
        primary_label=question_service.primary_label(question),
        retired_at=question.retired_at,
        retired_reason=question.retired_reason,
        retired_by=question.retired_by,
        created_at=question.created_at,
        updated_at=question.updated_at,
        created_by=question.created_by,
        updated_by=question.updated_by,
        import_id=question.import_id,
        import_row_number=question.import_row_number,
        is_deliverable=question_service.is_deliverable(question),
        usage=usage_summary(usage) if usage is not None else None,
    )


def question_list_item(question: Question, usage_count: int) -> QuestionListItem:
    return QuestionListItem(
        id=question.id,
        reference=question.reference,
        type=question.type,  # type: ignore[arg-type]
        status=question.status,  # type: ignore[arg-type]
        question_text=question.question_text,
        topics=[
            topic_ref(link.topic)
            for link in sorted(question.topic_links, key=lambda item: item.topic.name)
        ],
        points=question.points,
        scoring_strategy=question.scoring_strategy,  # type: ignore[arg-type]
        difficulty=question.difficulty,  # type: ignore[arg-type]
        version=question.version,
        option_count=len(question.options),
        usage_count=usage_count,
        is_deliverable=question_service.is_deliverable(question),
        created_at=question.created_at,
        updated_at=question.updated_at,
    )


def snapshot_out(snapshot: QuestionSnapshot) -> SnapshotOut:
    return SnapshotOut(
        id=snapshot.id,
        question_id=snapshot.question_id,
        version=snapshot.version,
        reference=snapshot.reference,
        type=snapshot.type,  # type: ignore[arg-type]
        status=snapshot.status,
        question_text=snapshot.question_text,
        scenario_text=snapshot.scenario_text,
        explanation=snapshot.explanation,
        points=snapshot.points,
        scoring_strategy=snapshot.scoring_strategy,
        penalty_per_incorrect=snapshot.penalty_per_incorrect,
        content_hash=snapshot.content_hash,
        payload=load_payload(snapshot.payload),
        created_at=snapshot.created_at,
    )


def deliverable_question(question: Question) -> DeliverableQuestion:
    """Serialise a question for delivery WITHOUT the answer key."""
    return DeliverableQuestion(
        id=question.id,
        reference=question.reference,
        version=question.version,
        type=question.type,  # type: ignore[arg-type]
        question_text=question.question_text,
        scenario_text=question.scenario_text,
        difficulty=question.difficulty,
        points=question.points,
        scoring_strategy=question.scoring_strategy,
        options=[
            DeliverableOption(label=option.label, text=option.text, position=option.position)
            for option in sorted(question.options, key=lambda o: o.position)
        ],
        topics=[topic_ref(link.topic) for link in question.topic_links],
    )


def usage_out(usage: QuestionUsage) -> UsageOut:
    import json

    learner_response = None
    if usage.learner_response:
        try:
            parsed = json.loads(usage.learner_response)
            learner_response = parsed if isinstance(parsed, dict) else None
        except ValueError:
            learner_response = None

    presentation_order = None
    if usage.presentation_order:
        try:
            parsed_order = json.loads(usage.presentation_order)
            presentation_order = parsed_order if isinstance(parsed_order, list) else None
        except ValueError:
            presentation_order = None

    return UsageOut(
        id=usage.id,
        attempt_ref=usage.attempt_ref,
        learner_ref=usage.learner_ref,
        question_id=usage.question_id,
        question_reference=usage.snapshot.reference,
        snapshot_id=usage.snapshot_id,
        snapshot_version=usage.snapshot_version,
        delivery_position=usage.delivery_position,
        attempt_status=usage.attempt_status,  # type: ignore[arg-type]
        learner_response=learner_response,
        presentation_order=presentation_order,
        is_correct=usage.is_correct,
        awarded_points=usage.awarded_points,
        max_points=usage.max_points,
        delivered_at=usage.delivered_at,
        responded_at=usage.responded_at,
        completed_at=usage.completed_at,
    )


def import_result(outcome: import_service.ImportOutcome) -> ImportResult:
    run = outcome.import_run
    return ImportResult(
        id=run.id,
        filename=run.filename,
        status=run.status,  # type: ignore[arg-type]
        total_rows=run.total_rows,
        imported_rows=run.imported_rows,
        rejected_rows=run.rejected_rows,
        error_message=run.error_message,
        started_at=run.started_at,
        completed_at=run.completed_at,
        imported=[
            ImportedRowSummary(
                row_number=row.row_number,
                question_id=row.question_id,
                reference=row.reference,
                question_text=row.question_text,
            )
            for row in outcome.imported
        ],
        rejected=[
            RejectedRowSummary(
                row_number=row.row_number,
                errors=[
                    ImportRowError(
                        row_number=row.row_number,
                        field=issue.field,
                        code=issue.code,
                        message=issue.message,
                    )
                    for issue in row.issues
                ],
                raw_row=row.raw or None,
            )
            for row in outcome.rejected
        ],
    )


def import_list_item(run: QuestionImport) -> ImportListItem:
    return ImportListItem(
        id=run.id,
        filename=run.filename,
        status=run.status,  # type: ignore[arg-type]
        total_rows=run.total_rows,
        imported_rows=run.imported_rows,
        rejected_rows=run.rejected_rows,
        error_message=run.error_message,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


def page_meta(page: int, page_size: int, total: int) -> dict[str, Any]:
    total_pages = (total + page_size - 1) // page_size if page_size else 0
    return {
        "page": page,
        "pageSize": page_size,
        "total": total,
        "totalPages": total_pages,
        "hasNext": page < total_pages,
        "hasPrevious": page > 1,
    }


def question_usage_bulk(db: Session, questions: list[Question]) -> dict[str, int]:
    return question_service.usage_counts_bulk(db, [question.id for question in questions])
