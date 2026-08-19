"""Building log-safe context dictionaries (§22).

Two things in UC-07 must never reach a log sink: the answer key, and the coaching conversation. The
formatter in ``app.core.logging`` enforces that with a deny-list, but a deny-list is a net, not a
plan. These helpers are the plan — every call site in the module builds its ``extra`` here, so what
gets logged is decided once, in a file whose whole purpose is to be read by someone checking.

The rule is simple enough to state in one line: **identifiers, counts, statuses and codes. Never
content.** A coaching session is a learner working through something they got wrong; the operational
record of it should say that it happened, not what was said.
"""

from __future__ import annotations

from typing import Any

from app.modules.coaching.domain.eligibility import Eligibility
from app.modules.coaching.domain.sanitizer import SanitizationReport
from app.modules.coaching.domain.session import CoachingSession


def session_context(session: CoachingSession) -> dict[str, Any]:
    """The operational facts about a session (§22).

    Note what is absent: the topic is a *label*, so it stays; the transcript, the learner's
    reasoning and the coach's questions are content, so they never appear here at all.
    """
    return {
        "session_id": session.session_id,
        "learner_id": session.learner_id,
        "attempt_id": session.attempt_id,
        "course_id": session.course_id,
        "question_id": session.question_id,
        "topic": session.topic,
        "mode": session.mode.value,
        "status": session.status.value,
        "exchange_count": session.exchange_count,
        "direct_explanation_available": session.direct_explanation_available,
        "revision": session.revision,
    }


def eligibility_context(
    *, learner_id: str, attempt_id: str, question_id: str | None, eligibility: Eligibility
) -> dict[str, Any]:
    """Why coaching was or was not offered. Codes only — never the refusal's message text."""
    return {
        "learner_id": learner_id,
        "attempt_id": attempt_id,
        "question_id": question_id,
        "reason": eligibility.code.value,
        "coaching_available": eligibility.coaching_available,
    }


def sanitization_context(report: SanitizationReport) -> dict[str, Any]:
    """What the sanitiser removed — names and counts, never values (§13, §22)."""
    return {
        "removed_field_count": len(report.removed_fields),
        "scrubbed_field_count": len(report.scrubbed_fields),
        "forbidden_value_count": report.forbidden_value_count,
        "contamination_detected": not report.clean,
        "contamination_findings": list(report.findings),
        "answer_key_excluded": True,
    }
