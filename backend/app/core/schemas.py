"""Response conventions shared by the whole API.

The API speaks camelCase (a TypeScript UI consumes it) while the Python code uses snake_case;
``CamelModel`` bridges the two and accepts either spelling on input, so a client written against
snake_case still works.

These live in ``app.core`` rather than inside a capability because the **error envelope and the
pagination shape are properties of the API as a whole**. When they lived in the question bank's
schema package, UC-01 had to import from UC-02 to declare a 404 — a dependency that said nothing
true about the two capabilities' relationship.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

T = TypeVar("T")


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
        str_strip_whitespace=False,
    )


class PageMeta(CamelModel):
    page: int
    page_size: int
    total: int
    total_pages: int
    has_next: bool
    has_previous: bool


class Page(CamelModel, Generic[T]):
    items: list[T]
    meta: PageMeta


class FieldIssueOut(CamelModel):
    """One field-level problem. Mirrors :class:`app.core.errors.FieldIssue` on the wire."""

    field: str
    code: str
    message: str


class ErrorBody(CamelModel):
    code: str
    message: str
    details: list[FieldIssueOut] | None = None


class ErrorResponse(CamelModel):
    """The single error envelope used by every failing endpoint, in both capabilities."""

    error: ErrorBody


class MessageResponse(CamelModel):
    message: str
