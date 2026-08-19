from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.core.schemas import CamelModel
from app.modules.question_bank.domain.policy import question_policy


class TopicCreate(CamelModel):
    name: str = Field(min_length=1, max_length=question_policy.max_topic_name_length)
    description: str | None = Field(default=None, max_length=2_000)
    is_active: bool = True


class TopicUpdate(CamelModel):
    name: str | None = Field(
        default=None, min_length=1, max_length=question_policy.max_topic_name_length
    )
    description: str | None = Field(default=None, max_length=2_000)
    is_active: bool | None = None


class TopicOut(CamelModel):
    id: str
    slug: str
    name: str
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    #: Number of questions currently tagged with this topic (all statuses).
    question_count: int | None = None


class TopicRef(CamelModel):
    """Compact topic reference embedded in a question payload."""

    id: str
    slug: str
    name: str


class AssignTopicsRequest(CamelModel):
    """Assign topics to a question by id and/or by name.

    Names that do not yet exist are created, so an admin can tag freely without a separate
    round-trip to the topic endpoints.
    """

    topic_ids: list[str] = Field(default_factory=list)
    topic_names: list[str] = Field(default_factory=list)
    #: When true, replace the question's topics instead of adding to them.
    replace: bool = False
