"""Topic management (UC-02 §8).

Topics are first-class rows joined to questions through ``qb_question_topics`` — never a
comma-separated string. Resolving a topic by name is idempotent and case-insensitive so
repeated CSV imports converge on one row per topic rather than creating near-duplicates.
"""

from __future__ import annotations

import re
import unicodedata

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, FieldIssue, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.modules.question_bank.models import QuestionTopic, Topic

logger = get_logger(__name__)

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Stable, comparable key for a topic name."""
    normalised = unicodedata.normalize("NFKD", name)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_STRIP.sub("-", ascii_only.lower()).strip("-")
    return slug or re.sub(r"\s+", "-", name.strip().lower())[:96]


def list_topics(
    db: Session, *, include_inactive: bool = True, search: str | None = None
) -> list[tuple[Topic, int]]:
    """Return topics with the number of questions tagged with each."""
    stmt = (
        select(Topic, func.count(QuestionTopic.question_id))
        .outerjoin(QuestionTopic, QuestionTopic.topic_id == Topic.id)
        .group_by(Topic.id)
        .order_by(Topic.name)
    )
    if not include_inactive:
        stmt = stmt.where(Topic.is_active.is_(True))
    if search:
        needle = f"%{search.strip().lower()}%"
        stmt = stmt.where(func.lower(Topic.name).like(needle))
    return [(topic, count) for topic, count in db.execute(stmt).all()]


def get_topic(db: Session, topic_id: str) -> Topic:
    topic = db.get(Topic, topic_id)
    if topic is None:
        raise NotFoundError("Topic", topic_id)
    return topic


def count_topic_questions(db: Session, topic_id: str) -> int:
    return int(
        db.execute(
            select(func.count(QuestionTopic.question_id)).where(QuestionTopic.topic_id == topic_id)
        ).scalar_one()
    )


def create_topic(
    db: Session, *, name: str, description: str | None = None, is_active: bool = True
) -> Topic:
    clean = name.strip()
    if not clean:
        raise ValidationError(
            "The topic is not valid.",
            [FieldIssue("name", "TOPIC_NAME_REQUIRED", "A topic name is required.")],
        )

    slug = slugify(clean)
    existing = db.execute(select(Topic).where(Topic.slug == slug)).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            f'A topic named "{existing.name}" already exists.',
            code="TOPIC_ALREADY_EXISTS",
        )

    topic = Topic(name=clean, slug=slug, description=description, is_active=is_active)
    db.add(topic)
    try:
        db.commit()
    except IntegrityError as exc:  # concurrent create of the same topic
        db.rollback()
        logger.warning("topic.create.conflict", extra={"slug": slug, "err": str(exc)})
        raise ConflictError(
            f'A topic named "{clean}" already exists.', code="TOPIC_ALREADY_EXISTS"
        ) from exc
    db.refresh(topic)
    logger.info("topic.created", extra={"topic_id": topic.id, "slug": topic.slug})
    return topic


def update_topic(
    db: Session,
    topic_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    is_active: bool | None = None,
) -> Topic:
    topic = get_topic(db, topic_id)

    if name is not None:
        clean = name.strip()
        if not clean:
            raise ValidationError(
                "The topic is not valid.",
                [FieldIssue("name", "TOPIC_NAME_REQUIRED", "A topic name is required.")],
            )
        new_slug = slugify(clean)
        if new_slug != topic.slug:
            clash = db.execute(select(Topic).where(Topic.slug == new_slug)).scalar_one_or_none()
            if clash is not None:
                raise ConflictError(
                    f'A topic named "{clash.name}" already exists.', code="TOPIC_ALREADY_EXISTS"
                )
        topic.name = clean
        topic.slug = new_slug

    if description is not None:
        topic.description = description or None
    if is_active is not None:
        topic.is_active = is_active

    db.commit()
    db.refresh(topic)
    logger.info("topic.updated", extra={"topic_id": topic.id})
    return topic


def delete_topic(db: Session, topic_id: str, *, force: bool = False) -> int:
    """Delete a topic, detaching it from questions.

    Safe with respect to history: historical reports read topic *names* frozen inside question
    snapshots, so removing a live topic never alters a completed attempt's report. Deleting a
    topic that is still in use requires ``force`` so it cannot happen by accident.
    """
    topic = get_topic(db, topic_id)
    in_use = count_topic_questions(db, topic_id)

    if in_use and not force:
        raise ConflictError(
            f'Topic "{topic.name}" is assigned to {in_use} question(s). '
            "Re-send with force=true to remove it from those questions.",
            code="TOPIC_IN_USE",
        )

    db.execute(delete(QuestionTopic).where(QuestionTopic.topic_id == topic_id))
    db.delete(topic)
    db.commit()
    logger.info("topic.deleted", extra={"topic_id": topic_id, "detached_questions": in_use})
    return in_use


# ---------------------------------------------------------------------------
# Resolution used by question persistence and CSV import
# ---------------------------------------------------------------------------


def resolve_topics(
    db: Session,
    *,
    names: list[str] | None = None,
    ids: list[str] | None = None,
    auto_create: bool = True,
) -> list[Topic]:
    """Resolve topic names/ids to `Topic` rows, creating unknown names when allowed.

    Raises ``ValidationError`` for topic ids that do not exist — an unknown id is a client bug,
    whereas an unknown name is a legitimate "create as you tag" flow.
    """
    resolved: dict[str, Topic] = {}

    for topic_id in ids or []:
        topic = db.get(Topic, topic_id)
        if topic is None:
            raise ValidationError(
                "The question references a topic that does not exist.",
                [
                    FieldIssue(
                        "topicIds",
                        "TOPIC_NOT_FOUND",
                        f"Topic id '{topic_id}' does not exist.",
                    )
                ],
            )
        resolved[topic.id] = topic

    for name in names or []:
        clean = name.strip()
        if not clean:
            continue
        slug = slugify(clean)
        topic = db.execute(select(Topic).where(Topic.slug == slug)).scalar_one_or_none()
        if topic is None:
            if not auto_create:
                raise ValidationError(
                    "The question references a topic that does not exist.",
                    [
                        FieldIssue(
                            "topics",
                            "TOPIC_NOT_FOUND",
                            f'Topic "{clean}" does not exist and auto-creation is disabled.',
                        )
                    ],
                )
            topic = Topic(name=clean, slug=slug)
            db.add(topic)
            # Flush (not commit) so the id exists for the join row inside the caller's
            # transaction; a later rollback removes the topic too.
            db.flush()
            logger.info("topic.auto_created", extra={"slug": slug})
        resolved[topic.id] = topic

    return list(resolved.values())
