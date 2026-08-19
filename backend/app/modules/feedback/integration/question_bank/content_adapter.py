"""UC-02's authored content, seen through UC-06's port.

Reads ``qb_question_snapshots`` -- the immutable, one-row-per-version copy -- for the exact versions
the attempt was delivered, and returns the explanation, the per-option feedback and a lesson
reference.

The lesson reference is derived from the topic names frozen in that snapshot, prefixed so a reader
knows what they are looking at. This is the seam the company's real lesson mapping replaces: UC-06
asks for ``lesson_reference`` and never learns that today it comes from a topic.

Nothing is invented here. A snapshot with no explanation yields ``None`` and the caller substitutes
the defined fallback; a question with no topics yields no lesson reference at all.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.modules.feedback.integration.question_bank.port import (
    QuestionContent,
    QuestionVersionRef,
)
from app.modules.question_bank.domain.snapshots import load_payload
from app.modules.question_bank.models import QuestionSnapshot

#: How a topic name is presented when it stands in for a lesson reference. Labelled rather than
#: passed off as a lesson, because it is a topic and the report should not imply otherwise.
LESSON_PREFIX = "Topic"


def lesson_reference_from_topics(topics: Sequence[str]) -> str | None:
    """A lesson reference built from the question's frozen topic names."""
    named = [str(topic).strip() for topic in topics if str(topic).strip()]
    if not named:
        return None
    return f"{LESSON_PREFIX}: {', '.join(named)}"


class QuestionContentAdapter:
    """:class:`~...question_bank.port.QuestionContentPort` over the in-process question bank."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def find_content(
        self, refs: Sequence[QuestionVersionRef]
    ) -> dict[QuestionVersionRef, QuestionContent]:
        wanted = {QuestionVersionRef(str(ref.question_id), int(ref.version)) for ref in refs}
        if not wanted:
            return {}

        rows = self._session.scalars(
            select(QuestionSnapshot).where(
                tuple_(QuestionSnapshot.question_id, QuestionSnapshot.version).in_(
                    [(ref.question_id, ref.version) for ref in wanted]
                )
            )
        ).all()

        content: dict[QuestionVersionRef, QuestionContent] = {}
        for row in rows:
            payload = load_payload(row.payload)
            topics = tuple(str(name) for name in (payload.get("topics") or []) if name)
            option_feedback = tuple(
                (str(option.get("label")), str(option.get("feedback")))
                for option in (payload.get("options") or [])
                if isinstance(option, dict) and option.get("feedback")
            )
            ref = QuestionVersionRef(row.question_id, row.version)
            content[ref] = QuestionContent(
                question_id=row.question_id,
                version=row.version,
                explanation=row.explanation or None,
                lesson_reference=lesson_reference_from_topics(topics),
                question_reference=row.reference,
                topics=topics,
                option_feedback=option_feedback,
            )
        return content
