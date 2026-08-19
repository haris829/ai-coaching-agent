"""Question Bank CRUD, lifecycle and snapshotting (UC-02 §6, §14, §15, §16).

Lifecycle rules enforced here
-----------------------------
=========  ===========================================================================
DRAFT      Fully editable. Never delivered.
ACTIVE     Editable. Delivered to future quizzes. A content edit creates a NEW snapshot
           version; attempts already recorded stay pinned to the version they were given.
RETIRED    Withheld from all future delivery, fully preserved for reporting. Content is
           read-only; only topic tagging and reactivation are permitted.
=========  ===========================================================================

Hard delete is permitted **only** when a question has no recorded usage at all. Anything with
history must be retired instead — enforced both here and by ``ON DELETE RESTRICT`` in the
database, so a bug in this layer still cannot destroy history.
"""

from __future__ import annotations

from collections.abc import Sequence as TypingSequence
from typing import Any, Literal

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import (
    ConflictError,
    DatabaseError,
    FieldIssue,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.time import utcnow
from app.modules.question_bank.domain.content_hash import compute_content_hash
from app.modules.question_bank.domain.drafts import (
    OptionDraft,
    QuestionDraft,
    ScoringDraft,
    ValidatedQuestion,
)
from app.modules.question_bank.domain.enums import (
    DELIVERABLE_STATUSES,
    AttemptStatus,
    QuestionStatus,
    QuestionType,
)
from app.modules.question_bank.domain.policy import question_policy
from app.modules.question_bank.domain.snapshots import build_snapshot_payload, dump_payload
from app.modules.question_bank.domain.validator import validate_question_draft
from app.modules.question_bank.models import (
    Question,
    QuestionOption,
    QuestionSnapshot,
    QuestionTopic,
    QuestionUsage,
    Sequence,
    Topic,
)
from app.modules.question_bank.services import topic_service

logger = get_logger(__name__)

QUESTION_SEQUENCE = "question_reference"
REFERENCE_PREFIX = "Q-"
REFERENCE_PAD = 6


# ---------------------------------------------------------------------------
# Reference allocation
# ---------------------------------------------------------------------------


def _next_sequence_value(db: Session, name: str = QUESTION_SEQUENCE) -> int:
    """Atomically allocate the next value from the portable counter table.

    Uses ``SELECT ... FOR UPDATE`` where the backend supports it; SQLite serialises writers so
    the plain read-modify-write below is safe there.
    """
    row = (
        db.get(Sequence, name, with_for_update=True)
        if _supports_for_update(db)
        else db.get(Sequence, name)
    )
    if row is None:
        row = Sequence(name=name, value=0)
        db.add(row)
        db.flush()
    row.value += 1
    db.flush()
    return row.value


def _supports_for_update(db: Session) -> bool:
    return db.bind is not None and db.bind.dialect.name not in {"sqlite"}


def format_reference(seq: int) -> str:
    return f"{REFERENCE_PREFIX}{seq:0{REFERENCE_PAD}d}"


# ---------------------------------------------------------------------------
# Draft construction
# ---------------------------------------------------------------------------


def draft_from_payload(payload: dict[str, Any]) -> QuestionDraft:
    """Build a domain draft from an already-parsed API payload (camelCase or snake_case)."""

    def pick(*keys: str, default: Any = None) -> Any:
        for key in keys:
            if key in payload and payload[key] is not None:
                return payload[key]
        return default

    scoring_raw = pick("scoring", default={}) or {}
    if not isinstance(scoring_raw, dict):
        scoring_raw = {}

    options_raw = pick("options", default=[]) or []
    options: list[OptionDraft] = []
    for item in options_raw if isinstance(options_raw, list) else []:
        if not isinstance(item, dict):
            continue
        options.append(
            OptionDraft(
                label=item.get("label"),
                text=item.get("text"),
                position=item.get("position"),
                is_correct=item.get("isCorrect", item.get("is_correct")),
                is_primary=item.get("isPrimary", item.get("is_primary")),
                correct_position=item.get("correctPosition", item.get("correct_position")),
                feedback=item.get("feedback"),
            )
        )

    return QuestionDraft(
        type=pick("type"),
        status=pick("status"),
        question_text=pick("questionText", "question_text"),
        scenario_text=pick("scenarioText", "scenario_text"),
        explanation=pick("explanation"),
        difficulty=pick("difficulty"),
        external_ref=pick("externalRef", "external_ref"),
        options=options,
        topics=list(pick("topics", default=[]) or []),
        topic_ids=list(pick("topicIds", "topic_ids", default=[]) or []),
        scoring=ScoringDraft(
            points=scoring_raw.get("points"),
            scoring_strategy=scoring_raw.get(
                "scoringStrategy", scoring_raw.get("scoring_strategy")
            ),
            penalty_per_incorrect=scoring_raw.get(
                "penaltyPerIncorrect", scoring_raw.get("penalty_per_incorrect")
            ),
        ),
    )


def draft_from_question(question: Question) -> QuestionDraft:
    """Snapshot the current state of a stored question as a mutable draft.

    Used by update: the patch is merged onto this, and the *whole* merged result is
    re-validated. That is what guarantees an edit can never leave a question invalid.
    """
    return QuestionDraft(
        type=question.type,
        status=question.status,
        question_text=question.question_text,
        scenario_text=question.scenario_text,
        explanation=question.explanation,
        difficulty=question.difficulty,
        external_ref=question.external_ref,
        options=[
            OptionDraft(
                label=option.label,
                text=option.text,
                position=option.position,
                is_correct=option.is_correct,
                is_primary=option.is_primary,
                correct_position=option.correct_position,
                feedback=option.feedback,
            )
            for option in sorted(question.options, key=lambda o: o.position)
        ],
        topics=[link.topic.name for link in question.topic_links],
        topic_ids=[],
        scoring=ScoringDraft(
            points=question.points,
            scoring_strategy=question.scoring_strategy,
            penalty_per_incorrect=question.penalty_per_incorrect,
        ),
    )


def _validate_or_raise(draft: QuestionDraft) -> ValidatedQuestion:
    outcome = validate_question_draft(draft)
    if not outcome.ok or outcome.value is None:
        raise ValidationError(
            f"The question is not valid ({len(outcome.issues)} problem(s) found).",
            outcome.issues,
        )
    return outcome.value


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------


def _assert_not_duplicate(
    db: Session, content_hash: str, *, exclude_question_id: str | None = None
) -> None:
    if not question_policy.reject_duplicate_content:
        return
    stmt = select(Question).where(
        Question.content_hash == content_hash,
        Question.status != QuestionStatus.RETIRED.value,
    )
    if exclude_question_id:
        stmt = stmt.where(Question.id != exclude_question_id)
    existing = db.execute(stmt.limit(1)).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            f"An equivalent question already exists in the bank ({existing.reference}).",
            code="DUPLICATE_QUESTION",
            details=[
                FieldIssue(
                    "questionText",
                    "DUPLICATE_QUESTION",
                    f"Duplicate of {existing.reference}: same type, text and answer key.",
                )
            ],
        )


def _assert_external_ref_free(
    db: Session, external_ref: str | None, *, exclude_question_id: str | None = None
) -> None:
    if not external_ref:
        return
    stmt = select(Question).where(Question.external_ref == external_ref)
    if exclude_question_id:
        stmt = stmt.where(Question.id != exclude_question_id)
    existing = db.execute(stmt.limit(1)).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            f'External reference "{external_ref}" is already used by {existing.reference}.',
            code="EXTERNAL_REF_ALREADY_USED",
            details=[
                FieldIssue(
                    "externalRef",
                    "EXTERNAL_REF_ALREADY_USED",
                    f"Already used by {existing.reference}.",
                )
            ],
        )


# ---------------------------------------------------------------------------
# Snapshotting
# ---------------------------------------------------------------------------


def _write_snapshot(
    db: Session,
    question: Question,
    validated: ValidatedQuestion,
    topics: list[Topic],
    *,
    actor: str | None,
) -> QuestionSnapshot:
    """Freeze the current version of a question. Snapshots are append-only."""
    payload = build_snapshot_payload(
        validated,
        reference=question.reference,
        topics=[topic.name for topic in topics],
    )
    snapshot = QuestionSnapshot(
        question_id=question.id,
        version=question.version,
        reference=question.reference,
        type=question.type,
        status=question.status,
        question_text=question.question_text,
        scenario_text=question.scenario_text,
        explanation=question.explanation,
        points=question.points,
        scoring_strategy=question.scoring_strategy,
        penalty_per_incorrect=question.penalty_per_incorrect,
        content_hash=question.content_hash,
        payload=dump_payload(payload),
        created_by=actor,
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def _apply_options(db: Session, question: Question, validated: ValidatedQuestion) -> None:
    """Replace the question's option rows with the validated set.

    Options are wholly owned by their question, so replace-in-place is correct: any attempt
    that referenced the previous option set reads it from its snapshot, not from these rows.
    """
    for existing in list(question.options):
        db.delete(existing)
    # Flush the deletes before inserting, so the (question_id, position) and
    # (question_id, label) unique constraints cannot trip on rows that are about to disappear.
    db.flush()

    for option in validated.options:
        db.add(
            QuestionOption(
                question_id=question.id,
                label=option.label,
                text=option.text,
                position=option.position,
                is_correct=option.is_correct,
                is_primary=option.is_primary,
                correct_position=option.correct_position,
                feedback=option.feedback,
            )
        )
    db.flush()


def _apply_topics(
    db: Session, question: Question, topics: list[Topic], *, actor: str | None
) -> None:
    wanted = {topic.id for topic in topics}
    current = {link.topic_id for link in question.topic_links}

    for link in list(question.topic_links):
        if link.topic_id not in wanted:
            db.delete(link)
    for topic in topics:
        if topic.id not in current:
            db.add(QuestionTopic(question_id=question.id, topic_id=topic.id, assigned_by=actor))
    db.flush()


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def create_question(
    db: Session,
    draft: QuestionDraft,
    *,
    actor: str | None = None,
    import_id: str | None = None,
    import_row_number: int | None = None,
    commit: bool = True,
) -> Question:
    """Validate, then persist a new question together with its first snapshot.

    Validation happens strictly before any write (UC-02 §14). Options, topic links and the
    version-1 snapshot are written in the same transaction as the question row.
    """
    validated = _validate_or_raise(draft)

    if validated.status is QuestionStatus.RETIRED:
        raise ValidationError(
            "The question is not valid.",
            [
                FieldIssue(
                    "status",
                    "CANNOT_CREATE_RETIRED",
                    "A new question may be created as DRAFT or ACTIVE, not RETIRED.",
                )
            ],
        )

    content_hash = compute_content_hash(validated)
    _assert_not_duplicate(db, content_hash)
    _assert_external_ref_free(db, validated.external_ref)

    topics = topic_service.resolve_topics(
        db,
        names=validated.topic_names,
        ids=validated.topic_ids,
        auto_create=question_policy.auto_create_topics,
    )

    try:
        seq = _next_sequence_value(db)
        question = Question(
            seq=seq,
            reference=format_reference(seq),
            external_ref=validated.external_ref,
            type=validated.type.value,
            status=validated.status.value,
            question_text=validated.question_text,
            scenario_text=validated.scenario_text,
            explanation=validated.explanation,
            difficulty=validated.difficulty.value if validated.difficulty else None,
            points=validated.points,
            scoring_strategy=validated.scoring_strategy.value,
            penalty_per_incorrect=validated.penalty_per_incorrect,
            version=1,
            content_hash=content_hash,
            created_by=actor,
            updated_by=actor,
            import_id=import_id,
            import_row_number=import_row_number,
        )
        db.add(question)
        db.flush()

        _apply_options(db, question, validated)
        _apply_topics(db, question, topics, actor=actor)
        _write_snapshot(db, question, validated, topics, actor=actor)

        if commit:
            db.commit()
        else:
            db.flush()
    except IntegrityError as exc:
        db.rollback()
        logger.error("question.create.integrity_error", extra={"err": str(exc)})
        raise ConflictError(
            "The question could not be saved because it conflicts with an existing record.",
            code="INTEGRITY_CONFLICT",
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("question.create.database_error", extra={"err": str(exc)})
        raise DatabaseError("The question could not be saved.") from exc

    if commit:
        db.refresh(question)
    logger.info(
        "question.created",
        extra={"question_id": question.id, "reference": question.reference, "type": question.type},
    )
    return question


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def _with_relations(stmt: Select[Any]) -> Select[Any]:
    return stmt.options(
        selectinload(Question.options),
        selectinload(Question.topic_links).joinedload(QuestionTopic.topic),
    )


def get_question(db: Session, question_id: str) -> Question:
    """Fetch a question by id or by human-readable reference.

    Retired questions are returned exactly like active ones — retirement withholds a question
    from *delivery*, never from *reading* (UC-02 §15).
    """
    stmt = _with_relations(
        select(Question).where(
            or_(Question.id == question_id, Question.reference == question_id.upper())
        )
    )
    question = db.execute(stmt).scalar_one_or_none()
    if question is None:
        raise NotFoundError("Question", question_id)
    return question


SortField = Literal["createdAt", "updatedAt", "reference", "type", "status"]

_SORT_COLUMNS = {
    "createdAt": Question.created_at,
    "updatedAt": Question.updated_at,
    "reference": Question.seq,
    "type": Question.type,
    "status": Question.status,
}


def list_questions(
    db: Session,
    *,
    search: str | None = None,
    types: list[str] | None = None,
    statuses: list[str] | None = None,
    topic_id: str | None = None,
    topic_slug: str | None = None,
    difficulty: str | None = None,
    deliverable_only: bool = False,
    page: int = 1,
    page_size: int = 25,
    sort_by: str = "createdAt",
    sort_dir: str = "desc",
) -> tuple[list[Question], int]:
    """Filtered, paginated question list for the admin screen."""
    filters = []

    if search:
        needle = f"%{search.strip().lower()}%"
        filters.append(
            or_(
                func.lower(Question.question_text).like(needle),
                func.lower(Question.scenario_text).like(needle),
                func.lower(Question.reference).like(needle),
                func.lower(Question.external_ref).like(needle),
            )
        )
    if types:
        filters.append(Question.type.in_([t.strip().upper() for t in types if t.strip()]))
    if statuses:
        filters.append(Question.status.in_([s.strip().upper() for s in statuses if s.strip()]))
    if deliverable_only:
        filters.append(Question.status.in_([s.value for s in DELIVERABLE_STATUSES]))
    if difficulty:
        filters.append(Question.difficulty == difficulty.strip().upper())
    if topic_id:
        filters.append(
            Question.id.in_(
                select(QuestionTopic.question_id).where(QuestionTopic.topic_id == topic_id)
            )
        )
    if topic_slug:
        filters.append(
            Question.id.in_(
                select(QuestionTopic.question_id)
                .join(Topic, Topic.id == QuestionTopic.topic_id)
                .where(Topic.slug == topic_slug.strip().lower())
            )
        )

    count_stmt = select(func.count(Question.id))
    data_stmt = _with_relations(select(Question))
    for condition in filters:
        count_stmt = count_stmt.where(condition)
        data_stmt = data_stmt.where(condition)

    total = int(db.execute(count_stmt).scalar_one())

    column = _SORT_COLUMNS.get(sort_by, Question.created_at)
    ordering = column.desc() if sort_dir.lower() == "desc" else column.asc()
    # Secondary key keeps pagination stable when the primary key ties.
    data_stmt = data_stmt.order_by(ordering, Question.seq.desc())

    page = max(1, page)
    page_size = max(1, min(200, page_size))
    rows = db.execute(data_stmt.offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return list(rows), total


def usage_counts(db: Session, question_id: str) -> dict[str, int]:
    rows = db.execute(
        select(QuestionUsage.attempt_status, func.count(QuestionUsage.id))
        .where(QuestionUsage.question_id == question_id)
        .group_by(QuestionUsage.attempt_status)
    ).all()
    counts = {status: int(count) for status, count in rows}
    return {
        "total": sum(counts.values()),
        "completed": counts.get(AttemptStatus.COMPLETED.value, 0),
        "in_progress": counts.get(AttemptStatus.IN_PROGRESS.value, 0),
        "abandoned": counts.get(AttemptStatus.ABANDONED.value, 0),
    }


def usage_counts_bulk(db: Session, question_ids: list[str]) -> dict[str, int]:
    if not question_ids:
        return {}
    rows = db.execute(
        select(QuestionUsage.question_id, func.count(QuestionUsage.id))
        .where(QuestionUsage.question_id.in_(question_ids))
        .group_by(QuestionUsage.question_id)
    ).all()
    return {question_id: int(count) for question_id, count in rows}


def list_snapshots(db: Session, question_id: str) -> list[QuestionSnapshot]:
    question = get_question(db, question_id)
    return list(
        db.execute(
            select(QuestionSnapshot)
            .where(QuestionSnapshot.question_id == question.id)
            .order_by(QuestionSnapshot.version)
        )
        .scalars()
        .all()
    )


def get_snapshot(db: Session, question_id: str, version: int) -> QuestionSnapshot:
    question = get_question(db, question_id)
    snapshot = db.execute(
        select(QuestionSnapshot).where(
            QuestionSnapshot.question_id == question.id, QuestionSnapshot.version == version
        )
    ).scalar_one_or_none()
    if snapshot is None:
        raise NotFoundError(f"Version {version} of question {question.reference}")
    return snapshot


def latest_snapshot(db: Session, question_id: str) -> QuestionSnapshot:
    snapshot = db.execute(
        select(QuestionSnapshot)
        .where(QuestionSnapshot.question_id == question_id)
        .order_by(QuestionSnapshot.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    if snapshot is None:
        # Should be unreachable: every question is snapshotted at creation.
        raise NotFoundError("Question snapshot", question_id)
    return snapshot


def latest_snapshots_bulk(
    db: Session, question_ids: TypingSequence[str]
) -> dict[str, QuestionSnapshot]:
    """Newest snapshot per question, in one query.

    Pinning a whole quiz's worth of questions to an attempt would otherwise issue one query per
    question; this keeps starting a 100-question quiz to a single round trip.
    """
    if not question_ids:
        return {}

    newest = (
        select(
            QuestionSnapshot.question_id.label("question_id"),
            func.max(QuestionSnapshot.version).label("version"),
        )
        .where(QuestionSnapshot.question_id.in_(question_ids))
        .group_by(QuestionSnapshot.question_id)
        .subquery()
    )
    rows = db.execute(
        select(QuestionSnapshot).join(
            newest,
            and_(
                QuestionSnapshot.question_id == newest.c.question_id,
                QuestionSnapshot.version == newest.c.version,
            ),
        )
    ).scalars()
    return {snapshot.question_id: snapshot for snapshot in rows}


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

#: Fields whose change does NOT alter the question's semantic content, so they do not need a
#: new snapshot version. Everything else does.
_NON_CONTENT_FIELDS = {"explanation", "difficulty", "external_ref", "topics", "status"}


def update_question(
    db: Session,
    question_id: str,
    patch: dict[str, Any],
    *,
    actor: str | None = None,
) -> Question:
    """Apply a partial update, re-validating the merged result in full.

    A content-changing edit bumps ``version`` and writes a new snapshot. Attempts already
    recorded keep pointing at the snapshot they were delivered, so completed-attempt reporting
    is unaffected by the edit (UC-02 §6, §16).
    """
    question = get_question(db, question_id)

    provided = {key for key, value in patch.items() if value is not None}
    content_change_requested = bool(provided - _NON_CONTENT_FIELDS - {"scoring"}) or (
        "scoring" in provided
    )

    if question.status == QuestionStatus.RETIRED.value and content_change_requested:
        raise ConflictError(
            f"{question.reference} is retired and its content is read-only. "
            "Reactivate it first, or edit a replacement question.",
            code="QUESTION_RETIRED",
        )

    # Merge the patch onto the question's current state, then validate the whole thing.
    draft = draft_from_question(question)
    _merge_patch(draft, patch)

    requested_status = patch.get("status")
    if requested_status is not None:
        target = str(requested_status).strip().upper()
        if target == QuestionStatus.RETIRED.value:
            raise ConflictError(
                "Use POST /questions/{id}/retire to retire a question so the reason and "
                "timestamp are recorded.",
                code="USE_RETIRE_ENDPOINT",
            )
        if (
            question.status == QuestionStatus.RETIRED.value
            and target != QuestionStatus.RETIRED.value
        ):
            raise ConflictError(
                "Use POST /questions/{id}/reactivate to bring a retired question back.",
                code="USE_REACTIVATE_ENDPOINT",
            )
        draft.status = target
    else:
        draft.status = question.status

    validated = _validate_or_raise(draft)

    new_hash = compute_content_hash(validated)
    content_changed = new_hash != question.content_hash or _scoring_changed(question, validated)

    if new_hash != question.content_hash:
        _assert_not_duplicate(db, new_hash, exclude_question_id=question.id)
    if validated.external_ref != question.external_ref:
        _assert_external_ref_free(db, validated.external_ref, exclude_question_id=question.id)

    topics = topic_service.resolve_topics(
        db,
        names=validated.topic_names,
        ids=validated.topic_ids,
        auto_create=question_policy.auto_create_topics,
    )

    try:
        question.type = validated.type.value
        question.status = validated.status.value
        question.question_text = validated.question_text
        question.scenario_text = validated.scenario_text
        question.explanation = validated.explanation
        question.difficulty = validated.difficulty.value if validated.difficulty else None
        question.external_ref = validated.external_ref
        question.points = validated.points
        question.scoring_strategy = validated.scoring_strategy.value
        question.penalty_per_incorrect = validated.penalty_per_incorrect
        question.content_hash = new_hash
        question.updated_by = actor

        _apply_options(db, question, validated)
        _apply_topics(db, question, topics, actor=actor)

        if content_changed:
            question.version += 1
            db.flush()
            _write_snapshot(db, question, validated, topics, actor=actor)

        db.commit()
    except IntegrityError as exc:
        db.rollback()
        logger.error("question.update.integrity_error", extra={"err": str(exc)})
        raise ConflictError(
            "The question could not be saved because it conflicts with an existing record.",
            code="INTEGRITY_CONFLICT",
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("question.update.database_error", extra={"err": str(exc)})
        raise DatabaseError("The question could not be updated.") from exc

    db.refresh(question)
    logger.info(
        "question.updated",
        extra={
            "question_id": question.id,
            "reference": question.reference,
            "version": question.version,
            "content_changed": content_changed,
        },
    )
    return question


def _scoring_changed(question: Question, validated: ValidatedQuestion) -> bool:
    """Scoring is not part of the content hash but still warrants a new snapshot version."""
    return (
        float(question.points) != float(validated.points)
        or question.scoring_strategy != validated.scoring_strategy.value
        or float(question.penalty_per_incorrect) != float(validated.penalty_per_incorrect)
    )


def _merge_patch(draft: QuestionDraft, patch: dict[str, Any]) -> None:
    """Overlay only the keys the caller actually supplied."""

    def has(*keys: str) -> str | None:
        for key in keys:
            if key in patch and patch[key] is not None:
                return key
        return None

    if (key := has("type")) is not None:
        draft.type = patch[key]
    if (key := has("questionText", "question_text")) is not None:
        draft.question_text = patch[key]
    if (key := has("explanation")) is not None:
        draft.explanation = patch[key]
    if (key := has("difficulty")) is not None:
        draft.difficulty = patch[key]
    if (key := has("externalRef", "external_ref")) is not None:
        draft.external_ref = patch[key]

    # scenarioText is explicitly nullable: an empty string clears it (needed when changing a
    # SCENARIO question into another type).
    for key in ("scenarioText", "scenario_text"):
        if key in patch:
            draft.scenario_text = patch[key]
            break

    if (key := has("options")) is not None:
        raw = patch[key]
        draft.options = [
            OptionDraft(
                label=item.get("label"),
                text=item.get("text"),
                position=item.get("position"),
                is_correct=item.get("isCorrect", item.get("is_correct")),
                is_primary=item.get("isPrimary", item.get("is_primary")),
                correct_position=item.get("correctPosition", item.get("correct_position")),
                feedback=item.get("feedback"),
            )
            for item in (raw if isinstance(raw, list) else [])
            if isinstance(item, dict)
        ]

    if (key := has("topics")) is not None:
        draft.topics = list(patch[key])
        draft.topic_ids = []
    if (key := has("topicIds", "topic_ids")) is not None:
        draft.topic_ids = list(patch[key])
        if has("topics") is None:
            draft.topics = []

    if (key := has("scoring")) is not None:
        scoring = patch[key]
        if isinstance(scoring, dict):
            if scoring.get("points") is not None:
                draft.scoring.points = scoring["points"]

            strategy = scoring.get("scoringStrategy", scoring.get("scoring_strategy"))
            penalty = scoring.get("penaltyPerIncorrect", scoring.get("penalty_per_incorrect"))

            if strategy is not None:
                draft.scoring.scoring_strategy = strategy
                # The penalty is only meaningful for PARTIAL_CREDIT_WITH_PENALTY, so switching
                # strategy without naming a new penalty resets it rather than carrying over a
                # value that is invalid under the new strategy.
                if penalty is None:
                    draft.scoring.penalty_per_incorrect = None

            if penalty is not None:
                draft.scoring.penalty_per_incorrect = penalty


# ---------------------------------------------------------------------------
# Retirement / reactivation
# ---------------------------------------------------------------------------


def retire_question(
    db: Session, question_id: str, *, reason: str | None = None, actor: str | None = None
) -> Question:
    """Retire a question: excluded from future delivery, fully preserved for history.

    The row, its id, its reference, its options, its topics and every snapshot remain intact.
    """
    question = get_question(db, question_id)

    if question.status == QuestionStatus.RETIRED.value:
        raise ConflictError(
            f"{question.reference} is already retired.", code="QUESTION_ALREADY_RETIRED"
        )

    question.status = QuestionStatus.RETIRED.value
    question.retired_at = utcnow()
    question.retired_reason = (reason or "").strip() or None
    question.retired_by = actor
    question.updated_by = actor

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("question.retire.database_error", extra={"err": str(exc)})
        raise DatabaseError("The question could not be retired.") from exc

    db.refresh(question)
    logger.info(
        "question.retired",
        extra={
            "question_id": question.id,
            "reference": question.reference,
            "usage": usage_counts(db, question.id),
        },
    )
    return question


def reactivate_question(db: Session, question_id: str, *, actor: str | None = None) -> Question:
    """Return a retired question to ACTIVE so it can be delivered again."""
    question = get_question(db, question_id)

    if question.status != QuestionStatus.RETIRED.value:
        raise ConflictError(
            f"{question.reference} is not retired (status is {question.status}).",
            code="QUESTION_NOT_RETIRED",
        )

    # Reactivating must not resurrect a duplicate of a question that is currently live.
    _assert_not_duplicate(db, question.content_hash, exclude_question_id=question.id)

    question.status = QuestionStatus.ACTIVE.value
    question.retired_at = None
    question.retired_reason = None
    question.retired_by = None
    question.updated_by = actor

    try:
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("question.reactivate.database_error", extra={"err": str(exc)})
        raise DatabaseError("The question could not be reactivated.") from exc

    db.refresh(question)
    logger.info("question.reactivated", extra={"question_id": question.id})
    return question


# ---------------------------------------------------------------------------
# Delete (safe semantics)
# ---------------------------------------------------------------------------


def delete_question(db: Session, question_id: str, *, actor: str | None = None) -> Question:
    """Hard-delete a question — permitted ONLY when it has no recorded usage.

    A question with any usage history is refused with 409 and must be retired instead
    (UC-02 §6, Rule 2). ``ON DELETE RESTRICT`` on ``qb_question_usages`` is the backstop.
    """
    question = get_question(db, question_id)
    counts = usage_counts(db, question.id)

    if counts["total"] > 0:
        raise ConflictError(
            f"{question.reference} has been used by {counts['total']} quiz attempt(s) "
            f"({counts['completed']} completed) and cannot be deleted. "
            "Retire it instead to withdraw it from future quizzes while preserving history.",
            code="QUESTION_HAS_HISTORY",
        )

    try:
        # Options, topic links and snapshots cascade; usages would RESTRICT, but there are none.
        db.delete(question)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        logger.error("question.delete.restricted", extra={"err": str(exc)})
        raise ConflictError(
            f"{question.reference} is referenced by historical data and cannot be deleted. "
            "Retire it instead.",
            code="QUESTION_HAS_HISTORY",
        ) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("question.delete.database_error", extra={"err": str(exc)})
        raise DatabaseError("The question could not be deleted.") from exc

    logger.info(
        "question.deleted",
        extra={"question_id": question.id, "reference": question.reference, "actor": actor},
    )
    return question


# ---------------------------------------------------------------------------
# Topic tagging on a question
# ---------------------------------------------------------------------------


def assign_topics(
    db: Session,
    question_id: str,
    *,
    topic_ids: list[str] | None = None,
    topic_names: list[str] | None = None,
    replace: bool = False,
    actor: str | None = None,
) -> Question:
    """Add (or replace) the topics on a question.

    Permitted on retired questions: tagging is metadata and does not alter the question's
    content, so it cannot affect historical reporting (snapshots carry frozen topic names).
    """
    question = get_question(db, question_id)
    topics = topic_service.resolve_topics(
        db,
        names=topic_names or [],
        ids=topic_ids or [],
        auto_create=question_policy.auto_create_topics,
    )

    if not topics and not replace:
        raise ValidationError(
            "No topics were supplied.",
            [FieldIssue("topicIds", "TOPICS_REQUIRED", "Provide topicIds and/or topicNames.")],
        )

    existing = {link.topic_id: link.topic for link in question.topic_links}
    if replace:
        target = topics
    else:
        merged = dict(existing)
        for topic in topics:
            merged[topic.id] = topic
        target = list(merged.values())

    if question_policy.require_at_least_one_topic and not target:
        raise ValidationError(
            "The question is not valid.",
            [
                FieldIssue(
                    "topics",
                    "TOPICS_REQUIRED",
                    "At least one topic must be assigned to the question.",
                )
            ],
        )
    if len(target) > question_policy.max_topics_per_question:
        raise ValidationError(
            "The question is not valid.",
            [
                FieldIssue(
                    "topics",
                    "TOO_MANY_TOPICS",
                    f"A question may not have more than "
                    f"{question_policy.max_topics_per_question} topics (received {len(target)}).",
                )
            ],
        )

    _apply_topics(db, question, target, actor=actor)
    question.updated_by = actor
    db.commit()
    db.refresh(question)
    logger.info(
        "question.topics_assigned",
        extra={"question_id": question.id, "topics": [t.slug for t in target]},
    )
    return question


def remove_topic(
    db: Session, question_id: str, topic_id: str, *, actor: str | None = None
) -> Question:
    question = get_question(db, question_id)
    link = next((item for item in question.topic_links if item.topic_id == topic_id), None)
    if link is None:
        raise NotFoundError(f"Topic assignment on {question.reference}", topic_id)

    if question_policy.require_at_least_one_topic and len(question.topic_links) == 1:
        raise ConflictError(
            f"{question.reference} must keep at least one topic. "
            "Assign a replacement topic before removing the last one.",
            code="LAST_TOPIC_CANNOT_BE_REMOVED",
        )

    db.delete(link)
    question.updated_by = actor
    db.commit()
    db.refresh(question)
    logger.info("question.topic_removed", extra={"question_id": question.id, "topic_id": topic_id})
    return question


# ---------------------------------------------------------------------------
# Helpers used by serialisers
# ---------------------------------------------------------------------------


def is_deliverable(question: Question) -> bool:
    return question.status in {status.value for status in DELIVERABLE_STATUSES}


def correct_labels(question: Question) -> list[str]:
    if question.type == QuestionType.DRAG_TO_ORDER.value:
        return []
    return [o.label for o in sorted(question.options, key=lambda o: o.position) if o.is_correct]


def correct_order(question: Question) -> list[str]:
    if question.type != QuestionType.DRAG_TO_ORDER.value:
        return []
    ordered = sorted(
        (o for o in question.options if o.correct_position is not None),
        key=lambda o: o.correct_position or 0,
    )
    return [o.label for o in ordered]


def primary_label(question: Question) -> str | None:
    for option in sorted(question.options, key=lambda o: o.position):
        if option.is_primary:
            return option.label
    return None
