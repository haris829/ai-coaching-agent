"""A deliberately FOREIGN adapter family.

This exists to prove replaceability rather than assert it. Its fictional upstream shares
nothing with the mock's shape:

* different field names           -- ``moduleRef`` / ``unitRef`` / ``bodyText``, not lesson_id
* different nesting               -- content sits under ``payload.unit.blocks[]``
* different value representation  -- qualification arrives as ``{"band": "masters"}`` and
                                     progress as a 0.0-1.0 float, not the platform's enum and
                                     integer percentage
* different error signalling      -- an ``{"errorCode": ...}`` envelope, not exceptions

The service runs against it unmodified. Everything foreign is normalised here, at the boundary:
no upstream field name, nesting or error string escapes this file.
"""

from __future__ import annotations

from typing import Any

from ...domain.enums import NaricLevel, NaricLevelSource, SourceStatus
from ...domain.errors import NotFound, ProviderInvalidResponse, ProviderTimeout, ProviderUnavailable
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

PORT_COURSES = "courses"
PORT_CONTEXT = "learner_context"

FOREIGN_COURSE = "MOD-7781"
FOREIGN_LESSON = "UNIT-19"
FOREIGN_RELATED = "UNIT-20"
FOREIGN_USER = "staff-4410"
#: Further staff refs, so the conformance kit can exercise every context state on this family.
FOREIGN_USER_UNKNOWN = "staff-0001"      # upstream holds no record
FOREIGN_USER_BAD_BAND = "staff-0002"     # upstream band maps to no enum member
FOREIGN_USER_DOWN = "staff-0003"         # lookup fails outright

#: How the fictional upstream expresses attainment. Nothing outside this file knows these words.
_BAND_TO_LEVEL: dict[str, NaricLevel] = {
    "college": NaricLevel.LEVEL_3,
    "advanced-college": NaricLevel.LEVEL_4,
    "foundation": NaricLevel.LEVEL_5,
    "bachelors": NaricLevel.LEVEL_6,
    "masters": NaricLevel.LEVEL_7,
    "doctoral": NaricLevel.LEVEL_7_PLUS,
}


def _upstream_lesson(unit_ref: str) -> dict[str, Any]:
    """Stand-in for the fictional upstream's JSON."""
    if unit_ref == "UNIT-DOWN":
        return {"errorCode": "SERVICE_DOWN"}
    if unit_ref == "UNIT-SLOW":
        return {"errorCode": "DEADLINE_EXCEEDED"}
    if unit_ref == "UNIT-BAD":
        return {"payload": {"unit": {"blocks": "this should have been a list"}}}
    if unit_ref not in (FOREIGN_LESSON, FOREIGN_RELATED):
        return {"errorCode": "NO_SUCH_UNIT"}

    if unit_ref == FOREIGN_LESSON:
        return {
            "payload": {
                "unit": {
                    "unitRef": FOREIGN_LESSON,
                    "moduleRef": FOREIGN_COURSE,
                    "heading": "Fire Risk Assessment",
                    "blocks": [
                        {
                            "blockRef": "BLK-1",
                            "caption": "The Five Step Assessment",
                            "bodyText": "PROPRIETARY UPSTREAM PROSE about the five step method.",
                            "bullets": [
                                "Identify the fire hazards present",
                                "Identify the people at risk",
                                "Evaluate, remove or reduce the risk",
                                "Record findings and prepare an emergency plan",
                                "Review the assessment regularly",
                            ],
                            "topicRefs": ["TPC-RISK"],
                        }
                    ],
                    "topics": [
                        {
                            "topicRef": "TPC-RISK",
                            "label": "Fire risk assessment",
                            "ownerBlock": "BLK-1",
                            "gloss": "A fire risk assessment is a structured review of fire hazards and the people they endanger.",
                            "tags": ["fire risk", "assessment", "five step"],
                        }
                    ],
                    "assessmentItems": [
                        {
                            "itemRef": "ITEM-1",
                            "stem": "Which of the following is the first step of a fire risk assessment?",
                            "choices": ["a", "b", "c"],
                            "keyedChoice": "b",
                            "topicRef": "TPC-RISK",
                        }
                    ],
                }
            }
        }

    return {
        "payload": {
            "unit": {
                "unitRef": FOREIGN_RELATED,
                "moduleRef": FOREIGN_COURSE,
                "heading": "Evacuation Planning",
                "blocks": [
                    {
                        "blockRef": "BLK-9",
                        "caption": "Assembly Points",
                        "bodyText": "PROPRIETARY UPSTREAM PROSE about assembly points.",
                        "bullets": ["A roll call confirms everyone has left the building"],
                        "topicRefs": ["TPC-ASSEMBLY"],
                    }
                ],
                "topics": [
                    {
                        "topicRef": "TPC-ASSEMBLY",
                        "label": "Assembly point",
                        "ownerBlock": "BLK-9",
                        "gloss": "An assembly point is the designated place occupants gather after evacuating.",
                        "tags": ["assembly point", "roll call", "evacuation"],
                    }
                ],
                "assessmentItems": [],
            }
        }
    }


def _raise_for_error(envelope: dict[str, Any], port: str) -> None:
    """Translate the upstream's error envelope into the contract's typed exceptions.

    The upstream's own code strings stop here: they are used to choose the exception type and
    are never carried out in a message a client could see.
    """
    code = envelope.get("errorCode")
    if code is None:
        return
    if code == "SERVICE_DOWN":
        raise ProviderUnavailable(port, "upstream reported unavailable")
    if code == "DEADLINE_EXCEEDED":
        raise ProviderTimeout(port, "upstream exceeded its deadline")
    if code == "NO_SUCH_UNIT":
        raise NotFound(port, "unit not found")
    raise ProviderInvalidResponse(port, "unrecognised upstream error envelope")


class ForeignCoursesAdapter:
    name = "foreign_demo"

    def get_lesson(self, course_id: str, lesson_id: str) -> LessonContent:
        envelope = _upstream_lesson(lesson_id)
        _raise_for_error(envelope, PORT_COURSES)

        unit = envelope.get("payload", {}).get("unit")
        if not isinstance(unit, dict):
            raise ProviderInvalidResponse(PORT_COURSES, "payload.unit missing")
        blocks = unit.get("blocks")
        if not isinstance(blocks, list):
            raise ProviderInvalidResponse(PORT_COURSES, "payload.unit.blocks is not a list")

        sections = tuple(
            LessonSection(
                section_id=str(block["blockRef"]),
                title=str(block["caption"]),
                body=str(block.get("bodyText", "")),
                key_points=tuple(str(b) for b in block.get("bullets", [])),
                concept_tags=tuple(str(t) for t in block.get("topicRefs", [])),
                order=index,
            )
            for index, block in enumerate(blocks)
        )
        concepts = tuple(
            LessonConcept(
                concept_tag=str(topic["topicRef"]),
                name=str(topic["label"]),
                section_id=str(topic["ownerBlock"]),
                summary=str(topic.get("gloss", "")),
                keywords=tuple(str(t) for t in topic.get("tags", [])),
            )
            for topic in unit.get("topics", [])
        )
        quiz_items = tuple(
            QuizItem(
                quiz_item_id=str(item["itemRef"]),
                question_text=str(item["stem"]),
                option_ids=tuple(str(c) for c in item.get("choices", [])),
                correct_option_id=str(item["keyedChoice"]) if item.get("keyedChoice") else None,
                concept_tag=str(item["topicRef"]) if item.get("topicRef") else None,
            )
            for item in unit.get("assessmentItems", [])
        )
        return LessonContent(
            course_id=str(unit["moduleRef"]),
            lesson_id=str(unit["unitRef"]),
            title=str(unit["heading"]),
            sections=sections,
            concepts=concepts,
            quiz_items=quiz_items,
        )

    def get_course_structure(self, course_id: str) -> CourseStructure:
        if course_id != FOREIGN_COURSE:
            raise NotFound(PORT_COURSES, "module not found")
        return CourseStructure(
            course_id=FOREIGN_COURSE,
            title="Workplace Fire Safety",
            lessons=(
                CourseLessonRef(lesson_id=FOREIGN_LESSON, title="Fire Risk Assessment", order=0),
                CourseLessonRef(lesson_id=FOREIGN_RELATED, title="Evacuation Planning", order=1),
            ),
        )

    def verify_enrolment(self, user_id: str, course_id: str) -> EnrolmentRecord:
        # The fictional upstream returns a grant list with a status string, not a boolean.
        grants = [{"staffRef": FOREIGN_USER, "moduleRef": FOREIGN_COURSE, "state": "ACTIVE"}]
        match = next(
            (g for g in grants if g["staffRef"] == user_id and g["moduleRef"] == course_id), None
        )
        return EnrolmentRecord(
            user_id=user_id,
            course_id=course_id,
            enrolled=bool(match and match["state"] == "ACTIVE"),
            reason=None if match else "no_grant",
        )


class ForeignLearnerContextAdapter:
    name = "foreign_demo"

    def get_context(self, session_id: str, user_id: str) -> LearnerContext:
        if user_id == FOREIGN_USER_DOWN:
            raise ProviderUnavailable(PORT_CONTEXT, "upstream reported unavailable")

        if user_id == FOREIGN_USER_UNKNOWN:
            # The upstream answered; it simply holds nothing. Empty, not unavailable.
            return LearnerContext(
                user_id=user_id,
                naric_level=NaricLevel.LEVEL_5,
                naric_level_source=NaricLevelSource.DEFAULT,
                practice_area=None,
                source_status=SourceStatus.EMPTY,
            )

        band_value = "postgraduate-ish" if user_id == FOREIGN_USER_BAD_BAND else "masters"
        upstream: dict[str, Any] = {
            "staffRef": user_id,
            "qual": {"band": band_value},
            #: A 0.0-1.0 float. The platform contract wants an integer 0-100; the adapter is
            #: where that conversion happens, and it is not used further here.
            "moduleProgress": 0.42,
            "specialism": "regulatory",
        }
        band = str(upstream.get("qual", {}).get("band", "")).lower()
        level = _BAND_TO_LEVEL.get(band)
        if level is None:
            # Unmappable: apply the documented default and mark it, never guess a level.
            return LearnerContext(
                user_id=user_id,
                naric_level=NaricLevel.LEVEL_5,
                naric_level_source=NaricLevelSource.DEFAULT,
                practice_area=None,
                source_status=SourceStatus.INVALID,
            )
        return LearnerContext(
            user_id=user_id,
            naric_level=level,
            naric_level_source=NaricLevelSource.RETRIEVED,
            practice_area=str(upstream.get("specialism") or "") or None,
            source_status=SourceStatus.AVAILABLE,
        )


def foreign_progress_percent(raw: float) -> int:
    """Normalise the upstream's 0.0-1.0 float to the contract's integer 0-100."""
    return max(0, min(100, round(raw * 100)))
