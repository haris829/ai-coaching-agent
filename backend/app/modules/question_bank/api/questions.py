"""Question endpoints (UC-02 §21).

    GET    /questions                       list + filter + paginate
    POST   /questions                       create                          201
    GET    /questions/{id}                  read (works for retired too)
    PATCH  /questions/{id}                  update
    DELETE /questions/{id}                  hard delete, only without history  409 otherwise
    POST   /questions/{id}/retire           retire                             409 if retired
    POST   /questions/{id}/reactivate       un-retire
    GET    /questions/{id}/versions         snapshot history
    GET    /questions/{id}/versions/{n}     one frozen version
    GET    /questions/{id}/usages           attempts that used this question
    POST   /questions/{id}/topics           assign topics
    DELETE /questions/{id}/topics/{topicId} remove one topic

``{id}`` accepts either the internal id or the human-readable reference (``Q-000042``).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Query, status

from app.core.deps import DbSession
from app.core.schemas import ErrorResponse
from app.modules.identity.security import Actor
from app.modules.question_bank.api import serializers
from app.modules.question_bank.schemas.delivery import UsageOut
from app.modules.question_bank.schemas.question import (
    DeleteResult,
    QuestionCreate,
    QuestionListItem,
    QuestionOut,
    QuestionUpdate,
    RetireRequest,
    SnapshotOut,
)
from app.modules.question_bank.schemas.topic import AssignTopicsRequest
from app.modules.question_bank.services import delivery_service, question_service

router = APIRouter(
    prefix="/questions",
    tags=["Question Bank — Questions"],
    responses={
        400: {"model": ErrorResponse, "description": "Malformed request"},
        404: {"model": ErrorResponse, "description": "Question not found"},
        409: {"model": ErrorResponse, "description": "Lifecycle conflict"},
        422: {"model": ErrorResponse, "description": "Question failed validation"},
    },
)


@router.get(
    "",
    summary="List questions",
    response_model=dict,
)
def list_questions(
    db: DbSession,
    search: Annotated[
        str | None, Query(description="Matches question text, scenario, reference or external ref")
    ] = None,
    type: Annotated[list[str] | None, Query(description="Repeatable question-type filter")] = None,
    status_filter: Annotated[
        list[str] | None, Query(alias="status", description="Repeatable status filter")
    ] = None,
    topic_id: Annotated[str | None, Query(alias="topicId")] = None,
    topic_slug: Annotated[str | None, Query(alias="topicSlug")] = None,
    difficulty: Annotated[str | None, Query()] = None,
    deliverable_only: Annotated[
        bool,
        Query(alias="deliverableOnly", description="Only questions eligible for future delivery"),
    ] = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=200)] = 25,
    sort_by: Annotated[str, Query(alias="sortBy")] = "createdAt",
    sort_dir: Annotated[str, Query(alias="sortDir", pattern="^(asc|desc)$")] = "desc",
) -> dict[str, Any]:
    questions, total = question_service.list_questions(
        db,
        search=search,
        types=type,
        statuses=status_filter,
        topic_id=topic_id,
        topic_slug=topic_slug,
        difficulty=difficulty,
        deliverable_only=deliverable_only,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    usage = serializers.question_usage_bulk(db, questions)
    items: list[QuestionListItem] = [
        serializers.question_list_item(question, usage.get(question.id, 0))
        for question in questions
    ]
    return {
        "items": [item.model_dump(by_alias=True) for item in items],
        "meta": serializers.page_meta(page, page_size, total),
    }


@router.post(
    "",
    summary="Create a question",
    status_code=status.HTTP_201_CREATED,
    response_model=QuestionOut,
)
def create_question(
    db: DbSession,
    actor: Actor,
    payload: Annotated[QuestionCreate, Body()],
) -> QuestionOut:
    draft = question_service.draft_from_payload(payload.model_dump(by_alias=True))
    question = question_service.create_question(db, draft, actor=actor)
    return serializers.question_out(question, usage=question_service.usage_counts(db, question.id))


@router.get(
    "/{question_id}",
    summary="Get a question (including retired ones)",
    response_model=QuestionOut,
)
def get_question(db: DbSession, question_id: str) -> QuestionOut:
    question = question_service.get_question(db, question_id)
    return serializers.question_out(question, usage=question_service.usage_counts(db, question.id))


@router.patch(
    "/{question_id}",
    summary="Update a question",
    response_model=QuestionOut,
)
def update_question(
    db: DbSession,
    actor: Actor,
    question_id: str,
    payload: Annotated[QuestionUpdate, Body()],
) -> QuestionOut:
    # exclude_unset keeps a partial update partial — untouched fields keep their values.
    patch = payload.model_dump(by_alias=True, exclude_unset=True)
    question = question_service.update_question(db, question_id, patch, actor=actor)
    return serializers.question_out(question, usage=question_service.usage_counts(db, question.id))


@router.post(
    "/{question_id}/retire",
    summary="Retire a question (excluded from future delivery, preserved for reporting)",
    response_model=QuestionOut,
)
def retire_question(
    db: DbSession,
    actor: Actor,
    question_id: str,
    payload: Annotated[RetireRequest | None, Body()] = None,
) -> QuestionOut:
    question = question_service.retire_question(
        db, question_id, reason=(payload.reason if payload else None), actor=actor
    )
    return serializers.question_out(question, usage=question_service.usage_counts(db, question.id))


@router.post(
    "/{question_id}/reactivate",
    summary="Return a retired question to ACTIVE",
    response_model=QuestionOut,
)
def reactivate_question(db: DbSession, actor: Actor, question_id: str) -> QuestionOut:
    question = question_service.reactivate_question(db, question_id, actor=actor)
    return serializers.question_out(question, usage=question_service.usage_counts(db, question.id))


@router.delete(
    "/{question_id}",
    summary="Hard-delete a question — refused (409) once it has any attempt history",
    response_model=DeleteResult,
)
def delete_question(db: DbSession, actor: Actor, question_id: str) -> DeleteResult:
    question = question_service.delete_question(db, question_id, actor=actor)
    return DeleteResult(
        id=question.id,
        reference=question.reference,
        deleted=True,
        message=f"{question.reference} had no attempt history and was permanently deleted.",
    )


@router.get(
    "/{question_id}/versions",
    summary="Snapshot history — every frozen version of the question",
    response_model=list[SnapshotOut],
)
def list_versions(db: DbSession, question_id: str) -> list[SnapshotOut]:
    return [
        serializers.snapshot_out(snapshot)
        for snapshot in question_service.list_snapshots(db, question_id)
    ]


@router.get(
    "/{question_id}/versions/{version}",
    summary="One frozen version of the question",
    response_model=SnapshotOut,
)
def get_version(db: DbSession, question_id: str, version: int) -> SnapshotOut:
    return serializers.snapshot_out(question_service.get_snapshot(db, question_id, version))


@router.get(
    "/{question_id}/usages",
    summary="Attempts that used this question",
    response_model=list[UsageOut],
)
def list_usages(db: DbSession, question_id: str) -> list[UsageOut]:
    return [
        serializers.usage_out(usage)
        for usage in delivery_service.list_question_usages(db, question_id)
    ]


@router.post(
    "/{question_id}/topics",
    summary="Assign topics to a question",
    response_model=QuestionOut,
)
def assign_topics(
    db: DbSession,
    actor: Actor,
    question_id: str,
    payload: Annotated[AssignTopicsRequest, Body()],
) -> QuestionOut:
    question = question_service.assign_topics(
        db,
        question_id,
        topic_ids=payload.topic_ids,
        topic_names=payload.topic_names,
        replace=payload.replace,
        actor=actor,
    )
    return serializers.question_out(question, usage=question_service.usage_counts(db, question.id))


@router.delete(
    "/{question_id}/topics/{topic_id}",
    summary="Remove one topic from a question",
    response_model=QuestionOut,
)
def remove_topic(db: DbSession, actor: Actor, question_id: str, topic_id: str) -> QuestionOut:
    question = question_service.remove_topic(db, question_id, topic_id, actor=actor)
    return serializers.question_out(question, usage=question_service.usage_counts(db, question.id))
