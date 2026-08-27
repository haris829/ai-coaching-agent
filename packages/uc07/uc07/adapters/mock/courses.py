"""Deterministic mock CoursesProvider. Read-only.

The mock deliberately offers unresolvable candidates (unknown course id, unknown
lesson id) so that recommendation validation is exercised end to end: UC-07 must
remove them rather than guess a replacement.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from uc07.domain.enums import SourceStatus
from uc07.domain.errors import (
    PortName,
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from uc07.domain.models import CourseSummary, Enrolment, LessonSummary, Recommendation
from uc07.ports.read_only import CoursesProvider

_PORT = PortName.COURSES


@dataclass(frozen=True, slots=True)
class MockCoursesPayload:
    catalogue: tuple[dict[str, Any], ...] = ()
    candidates: tuple[dict[str, Any], ...] = ()
    enrolments: tuple[dict[str, Any], ...] = ()
    status: str = SourceStatus.AVAILABLE.value
    failure: str | None = None  # "unavailable" | "timeout" | "invalid"


class MockCoursesProvider(CoursesProvider):
    def __init__(self, payload: MockCoursesPayload) -> None:
        self._payload = payload

    def _guard(self) -> None:
        if self._payload.failure == "unavailable":
            raise ProviderUnavailable(_PORT)
        if self._payload.failure == "timeout":
            raise ProviderTimeout(_PORT)
        if self._payload.failure == "invalid":
            raise ProviderInvalidResponse(_PORT)

    def resolve_recommendations(
        self, topic_tags: Sequence[str]
    ) -> Sequence[Recommendation]:
        self._guard()
        wanted = set(topic_tags)
        try:
            return tuple(
                Recommendation(
                    topic_tag=raw["topic_tag"],
                    recommendation_type=raw["recommendation_type"],
                    course_id=raw["course_id"],
                    lesson_id=raw.get("lesson_id"),
                    title=raw.get("title"),
                )
                for raw in self._payload.candidates
                if raw.get("topic_tag") in wanted
            )
        except (KeyError, ValueError, TypeError, ValidationError) as exc:
            raise ProviderInvalidResponse(_PORT) from exc

    def enrolments_for(self, user_id: str) -> Sequence[Enrolment]:
        self._guard()
        try:
            return tuple(
                Enrolment(
                    user_id=raw["user_id"],
                    course_id=raw["course_id"],
                    enrolled_at=(
                        datetime.fromisoformat(raw["enrolled_at"])
                        if isinstance(raw.get("enrolled_at"), str)
                        else raw.get("enrolled_at")
                    ),
                    completion_percentage=raw.get("completion_percentage"),
                )
                for raw in self._payload.enrolments
                if raw.get("user_id") == user_id
            )
        except (KeyError, ValueError, TypeError, ValidationError) as exc:
            raise ProviderInvalidResponse(_PORT) from exc

    def catalogue(self) -> Sequence[CourseSummary]:
        self._guard()
        try:
            return tuple(
                CourseSummary(
                    course_id=raw["course_id"],
                    title=raw.get("title"),
                    topic_tags=tuple(raw.get("topic_tags", ())),
                    lessons=tuple(
                        LessonSummary(
                            lesson_id=lesson["lesson_id"],
                            title=lesson.get("title"),
                            topic_tags=tuple(lesson.get("topic_tags", ())),
                        )
                        for lesson in raw.get("lessons", ())
                    ),
                )
                for raw in self._payload.catalogue
            )
        except (KeyError, ValueError, TypeError, ValidationError) as exc:
            raise ProviderInvalidResponse(_PORT) from exc

    def status(self) -> SourceStatus:
        self._guard()
        return SourceStatus(self._payload.status)
