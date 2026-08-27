"""COPY THIS FILE to start a real adapter.

    cp src/uc04/adapters/real/_template.py src/uc04/adapters/real/company_courses.py

Then fill in every ``TODO`` below, add ONE line to the matching registry in
``uc04/adapters/registry.py``, and set ONE environment variable. Nothing else changes -
see docs/INTEGRATION.md.

Four rules this file exists to enforce. They are not style preferences:

1. **This is the only place the upstream payload shape is known.** No upstream field name,
   nesting, or error string may escape past the return statement.
2. **Never invent data.** A missing value maps to the documented default with its source field
   marked accordingly - never to a plausible-looking guess.
3. **Authorisation stays server-side, inside the adapter.** Credentials are read from config
   here and never accepted from a caller.
4. **If the real payload cannot be mapped to the platform contract, that is a contract
   conversation, not an adapter workaround.** Raise it. Do not bend the domain model.
"""

from __future__ import annotations

import os
from typing import Any

from ...domain.enums import NaricLevel, NaricLevelSource, SourceStatus
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
    LearnerContext,
    LessonConcept,
    LessonContent,
    LessonSection,
    QuizItem,
)

#: Name used in typed errors. Keep it generic - it reaches server-side logs, never a client.
PORT = "courses"


class TemplateCoursesAdapter:
    """Implements ``uc04.ports.CoursesProvider``. Rename the class when you copy this file."""

    #: Must match the key you add to the registry.
    name = "TODO_provider_name"

    def __init__(self) -> None:
        # TODO(1/4) ENDPOINT: read the base URL from configuration. Never hard-code it.
        self.base_url = os.environ.get("COMPANY_COURSES_BASE_URL", "")
        # TODO(2/4) AUTH: read the credential from configuration. It stays inside this adapter
        #                 and is never accepted from, or echoed to, a caller.
        self.api_key = os.environ.get("COMPANY_COURSES_API_KEY", "")
        self.timeout_ms = int(os.environ.get("COMPANY_COURSES_TIMEOUT_MS", "5000"))
        if not self.base_url:
            # Fail loudly at construction rather than silently returning nothing at runtime.
            raise ProviderUnavailable(PORT, "COMPANY_COURSES_BASE_URL is not configured")

    # ------------------------------------------------------------------ transport

    def _get(self, path: str) -> dict[str, Any]:
        """TODO(3/4) TRANSPORT: perform the real call and return the decoded body.

        Translate transport-level outcomes into the contract's typed errors here:

            connection refused / 5xx  -> ProviderUnavailable(PORT, "...")
            deadline exceeded         -> ProviderTimeout(PORT, "...")
            404                       -> NotFound(PORT, "...")
            2xx with unusable body    -> ProviderInvalidResponse(PORT, "...")

        Pass a short, generic detail string. The upstream's own error text must not travel.
        """
        raise NotImplementedError("TODO: implement the HTTP call")

    # ------------------------------------------------------------------- mapping

    def get_lesson(self, course_id: str, lesson_id: str) -> LessonContent:
        payload = self._get(f"/courses/{course_id}/lessons/{lesson_id}")

        # TODO(4/4) MAPPING: map the upstream shape onto the domain model.
        # Guard every access: a KeyError escaping this method is a contract violation, whereas
        # ProviderInvalidResponse is a handled state that degrades cleanly upstream.
        try:
            sections = tuple(
                LessonSection(
                    section_id=str(block["id"]),
                    title=str(block["title"]),
                    # `body` is used for matching only; it is never emitted to a learner.
                    body=str(block.get("text", "")),
                    # Curated bullets. These ARE quotable, within the extraction budget.
                    key_points=tuple(str(p) for p in block.get("key_points", [])),
                    concept_tags=tuple(str(t) for t in block.get("concepts", [])),
                    order=index,
                )
                for index, block in enumerate(payload.get("sections", []))
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
            # Load quiz items if the upstream exposes them: they are the deterministic half of
            # quiz protection. If it does NOT expose them, leave this empty and record that in
            # docs/assumptions.md (A-08) - detection then rests on intent classification alone.
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

        return LessonContent(
            course_id=course_id,
            lesson_id=lesson_id,
            title=str(payload.get("title") or lesson_id),
            sections=sections,
            concepts=concepts,
            quiz_items=quiz_items,
            revision=str(payload["revision"]) if payload.get("revision") else None,
        )

    def get_course_structure(self, course_id: str) -> CourseStructure:
        payload = self._get(f"/courses/{course_id}")
        try:
            lessons = tuple(
                CourseLessonRef(lesson_id=str(item["id"]), title=str(item["title"]), order=index)
                for index, item in enumerate(payload.get("lessons", []))
            )
        except (KeyError, TypeError) as exc:
            raise ProviderInvalidResponse(PORT, "course payload could not be mapped") from exc
        # This list is the whitelist cross-lesson references are verified against. Returning a
        # partial list silently drops real references; returning ids that do not exist would let
        # unresolvable ones through. Return exactly what the course contains.
        return CourseStructure(course_id=course_id, title=str(payload.get("title") or course_id), lessons=lessons)

    def verify_enrolment(self, user_id: str, course_id: str) -> EnrolmentRecord:
        payload = self._get(f"/enrolments/{user_id}/{course_id}")
        # Map the upstream's own vocabulary onto a boolean here. Anything not positively
        # recognised as active is NOT enrolled: fail closed.
        state = str(payload.get("status", "")).upper()
        return EnrolmentRecord(
            user_id=user_id,
            course_id=course_id,
            enrolled=state == "ACTIVE",
            reason=None if state == "ACTIVE" else "not_active",
        )


class TemplateLearnerContextAdapter:
    """Implements ``uc04.ports.LearnerContextProvider``."""

    name = "TODO_provider_name"

    def get_context(self, session_id: str, user_id: str) -> LearnerContext:
        # TODO: fetch the real context.
        payload: dict[str, Any] = {}

        raw_level = payload.get("naric_level")
        level = _coerce_level(raw_level)
        if level is None:
            # Unmappable value: apply the default, mark the source, record status invalid.
            # This is NOT a guess and NOT an exception - it is the documented fallback.
            return LearnerContext(
                user_id=user_id,
                naric_level=NaricLevel.LEVEL_5,
                naric_level_source=NaricLevelSource.DEFAULT,
                practice_area=None,
                source_status=SourceStatus.INVALID if raw_level is not None else SourceStatus.EMPTY,
            )
        return LearnerContext(
            user_id=user_id,
            naric_level=level,
            naric_level_source=NaricLevelSource.RETRIEVED,
            practice_area=str(payload.get("practice_area") or "") or None,
            source_status=SourceStatus.AVAILABLE,
        )


def _coerce_level(raw: object) -> NaricLevel | None:
    if isinstance(raw, str):
        try:
            return NaricLevel(raw.strip())
        except ValueError:
            return None
    return None
