"""CoursesProvider adapter for the company Courses Agent.

Copied from ``_template.py`` and filled in. This file is the ONLY place the Courses Agent's
payload shape is known: no upstream field name, nesting or error string escapes past a return
statement or into an exception message.

TODO 3/4 (transport) is wired to read recorded staging responses when
``COMPANY_COURSES_BASE_URL`` uses a ``file://`` prefix, so the mapping can be exercised before
the endpoint is reachable. A real base URL performs the HTTP call - that branch is the one line
an integrator replaces once they have an endpoint.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...domain.errors import (
    NotFound,
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from ...domain.models import (
    CourseLessonRef,
    CourseStructure,
    EnrolmentRecord,
    LessonConcept,
    LessonContent,
    LessonSection,
    QuizItem,
)

PORT = "courses"

#: Upstream status vocabulary. Nothing outside this file knows these strings.
_ACTIVE_ENROLMENT_STATES = {"ACTIVE", "IN_PROGRESS"}


def _recorded_transport(root: Path) -> Callable[[str], dict[str, Any]]:
    """Recorded staging responses, keyed by request path.

    Used for the integration rehearsal. Replace with the real call once an endpoint exists.
    """

    def fetch(path: str) -> dict[str, Any]:
        target = root / (path.strip("/").replace("/", "__") + ".json")
        if not target.exists():
            raise NotFound(PORT, "resource not found")
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProviderInvalidResponse(PORT, "response body was not valid JSON") from exc
        # The staging recordings carry the Courses Agent's own error envelope.
        status = str(payload.get("status", "")).upper()
        if status == "UNAVAILABLE":
            raise ProviderUnavailable(PORT, "upstream reported unavailable")
        if status == "TIMEOUT":
            raise ProviderTimeout(PORT, "upstream exceeded its deadline")
        return payload

    return fetch


def _default_transport(base_url: str) -> Callable[[str], dict[str, Any]]:
    if base_url.startswith("file://"):
        return _recorded_transport(Path(base_url[len("file://") :]))

    def not_wired(_path: str) -> dict[str, Any]:
        # TODO(3/4) TRANSPORT: perform the real call here and translate transport outcomes into
        # ProviderUnavailable / ProviderTimeout / NotFound / ProviderInvalidResponse.
        raise ProviderUnavailable(PORT, "courses transport is not configured")

    return not_wired


class CompanyCoursesAdapter:
    """Implements ``uc04.ports.CoursesProvider``."""

    name = "company_courses"

    def __init__(self, transport: Callable[[str], dict[str, Any]] | None = None) -> None:
        # TODO(1/4) ENDPOINT: from configuration, never hard-coded.
        self.base_url = os.environ.get("COMPANY_COURSES_BASE_URL", "")
        # TODO(2/4) AUTH: stays inside this adapter; never accepted from or echoed to a caller.
        self.api_key = os.environ.get("COMPANY_COURSES_API_KEY", "")
        self.timeout_ms = int(os.environ.get("COMPANY_COURSES_TIMEOUT_MS", "5000"))
        self._transport = transport or _default_transport(self.base_url)

    # ------------------------------------------------------------------------- lessons

    def get_lesson(self, course_id: str, lesson_id: str) -> LessonContent:
        payload = self._transport(f"/courses/{course_id}/lessons/{lesson_id}")

        # TODO(4/4) MAPPING. Guard every access: a KeyError escaping this method is a contract
        # violation, whereas ProviderInvalidResponse is a handled state that degrades cleanly.
        try:
            sections = tuple(
                LessonSection(
                    section_id=str(block["id"]),
                    title=str(block["title"]),
                    body=str(block.get("text", "")),
                    key_points=tuple(str(p) for p in block.get("key_points", [])),
                    concept_tags=tuple(str(t) for t in block.get("concepts", [])),
                    order=index,
                )
                for index, block in enumerate(payload["sections"])
            )
            concepts = tuple(
                LessonConcept(
                    concept_tag=str(topic["id"]),
                    name=str(topic["name"]),
                    section_id=str(topic["section_id"]),
                    summary=str(topic.get("definition", "")),
                    keywords=tuple(str(k) for k in topic.get("keywords", [])),
                )
                for topic in payload.get("concepts", [])
            )
            quiz_items = tuple(
                QuizItem(
                    quiz_item_id=str(item["id"]),
                    question_text=str(item["question"]),
                    option_ids=tuple(str(o) for o in item.get("options", [])),
                    correct_option_id=str(item["answer"]) if item.get("answer") else None,
                    concept_tag=str(item["concept"]) if item.get("concept") else None,
                )
                for item in payload.get("quiz_items", [])
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderInvalidResponse(PORT, "lesson payload could not be mapped") from exc

        section_ids = {section.section_id for section in sections}
        if any(concept.section_id not in section_ids for concept in concepts):
            raise ProviderInvalidResponse(PORT, "lesson concepts reference unknown sections")

        return LessonContent(
            course_id=course_id,
            lesson_id=lesson_id,
            title=str(payload.get("title") or lesson_id),
            sections=sections,
            concepts=concepts,
            quiz_items=quiz_items,
            revision=str(payload["revision"]) if payload.get("revision") else None,
        )

    # ----------------------------------------------------------------------- structure

    def get_course_structure(self, course_id: str) -> CourseStructure:
        payload = self._transport(f"/courses/{course_id}")
        try:
            lessons = tuple(
                CourseLessonRef(lesson_id=str(item["id"]), title=str(item["title"]), order=index)
                for index, item in enumerate(payload.get("lessons", []))
            )
        except (KeyError, TypeError) as exc:
            raise ProviderInvalidResponse(PORT, "course payload could not be mapped") from exc
        return CourseStructure(
            course_id=course_id,
            title=str(payload.get("title") or course_id),
            lessons=lessons,
        )

    # ----------------------------------------------------------------------- enrolment

    def verify_enrolment(self, user_id: str, course_id: str) -> EnrolmentRecord:
        try:
            payload = self._transport(f"/enrolments/{user_id}/{course_id}")
        except NotFound:
            # No enrolment record is a definite "not enrolled", not an error.
            return EnrolmentRecord(user_id=user_id, course_id=course_id, enrolled=False, reason="no_record")

        # Anything not positively recognised as active is NOT enrolled: fail closed.
        state = str(payload.get("status", "")).upper()
        enrolled = state in _ACTIVE_ENROLMENT_STATES
        return EnrolmentRecord(
            user_id=user_id,
            course_id=course_id,
            enrolled=enrolled,
            reason=None if enrolled else "not_active",
        )
