"""Topic endpoints (UC-02 §8, §21).

GET    /topics            list with question counts
POST   /topics            create                                     201
GET    /topics/{id}       read
PATCH  /topics/{id}       rename / describe / deactivate
DELETE /topics/{id}       delete; requires ?force=true when in use   409 otherwise
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Query, status

from app.core.deps import DbSession
from app.core.schemas import ErrorResponse, MessageResponse
from app.modules.identity.security import Actor
from app.modules.question_bank.api import serializers
from app.modules.question_bank.schemas.topic import TopicCreate, TopicOut, TopicUpdate
from app.modules.question_bank.services import topic_service

router = APIRouter(
    prefix="/topics",
    tags=["Question Bank — Topics"],
    responses={
        404: {"model": ErrorResponse, "description": "Topic not found"},
        409: {"model": ErrorResponse, "description": "Topic name taken, or topic still in use"},
        422: {"model": ErrorResponse, "description": "Topic failed validation"},
    },
)


@router.get("", summary="List topics with question counts", response_model=list[TopicOut])
def list_topics(
    db: DbSession,
    include_inactive: Annotated[bool, Query(alias="includeInactive")] = True,
    search: Annotated[str | None, Query()] = None,
) -> list[TopicOut]:
    return [
        serializers.topic_out(topic, count)
        for topic, count in topic_service.list_topics(
            db, include_inactive=include_inactive, search=search
        )
    ]


@router.post(
    "", summary="Create a topic", status_code=status.HTTP_201_CREATED, response_model=TopicOut
)
def create_topic(db: DbSession, actor: Actor, payload: Annotated[TopicCreate, Body()]) -> TopicOut:
    topic = topic_service.create_topic(
        db, name=payload.name, description=payload.description, is_active=payload.is_active
    )
    return serializers.topic_out(topic, 0)


@router.get("/{topic_id}", summary="Get a topic", response_model=TopicOut)
def get_topic(db: DbSession, topic_id: str) -> TopicOut:
    topic = topic_service.get_topic(db, topic_id)
    return serializers.topic_out(topic, topic_service.count_topic_questions(db, topic_id))


@router.patch("/{topic_id}", summary="Update a topic", response_model=TopicOut)
def update_topic(
    db: DbSession, actor: Actor, topic_id: str, payload: Annotated[TopicUpdate, Body()]
) -> TopicOut:
    topic = topic_service.update_topic(
        db,
        topic_id,
        name=payload.name,
        description=payload.description,
        is_active=payload.is_active,
    )
    return serializers.topic_out(topic, topic_service.count_topic_questions(db, topic_id))


@router.delete(
    "/{topic_id}",
    summary="Delete a topic (force=true also detaches it from questions)",
    response_model=MessageResponse,
)
def delete_topic(
    db: DbSession,
    actor: Actor,
    topic_id: str,
    force: Annotated[
        bool, Query(description="Detach the topic from questions before deleting")
    ] = False,
) -> MessageResponse:
    detached = topic_service.delete_topic(db, topic_id, force=force)
    suffix = f" It was removed from {detached} question(s)." if detached else ""
    return MessageResponse(message=f"Topic deleted.{suffix}")
