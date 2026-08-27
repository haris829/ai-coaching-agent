"""Document helpers for tests: PDF text extraction and record construction.

:func:`pdf_text` uses ``pypdf`` deliberately - an independent library, not this
repository own extractor. Asserting a field is in the PDF with the same code
that put it there would prove nothing.
"""

from __future__ import annotations

import io
from datetime import timedelta

from pypdf import PdfReader

from uc09_summary.domain.enums import GenerationMode, SessionStatus, SourceStatus
from uc09_summary.domain.grounding import SessionData
from uc09_summary.domain.models import SummaryContent, SummaryRecord
from uc09_summary.domain.naric import explanation_profile_for


def pdf_text(pdf: bytes) -> str:
    """Extract the text of a PDF using an independent library."""
    reader = PdfReader(io.BytesIO(pdf))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def pdf_text_normalised(pdf: bytes) -> str:
    """Whitespace-normalised PDF text, for containment assertions."""
    return " ".join(pdf_text(pdf).split())


def summary_from_content(
    data: SessionData,
    content: SummaryContent,
    *,
    summary_id: str = "sum_test0000000000000000000000000",
    is_partial: bool = False,
    mode: GenerationMode = GenerationMode.GENERATED,
) -> SummaryRecord:
    """Build a stored-shape record from generated content, for rendering tests."""
    session = data.session
    ended = session.ended_at or data.covers_interactions_through
    duration = max(0, int((ended - session.started_at).total_seconds()))
    return SummaryRecord(
        summary_id=summary_id,
        session_id=session.session_id,
        user_id=session.user_id,
        generated_at=session.started_at + timedelta(hours=1),
        is_partial=is_partial,
        covers_interactions_through=data.covers_interactions_through,
        topics_covered=content.topics_covered,
        key_concepts=content.key_concepts,
        resources_referenced=content.resources_referenced,
        next_steps=content.next_steps,
        source_status={
            "session": SourceStatus.AVAILABLE,
            "interactions": SourceStatus.AVAILABLE,
            "citations": SourceStatus.AVAILABLE,
            "gap_report": SourceStatus.AVAILABLE,
        },
        generation_mode=mode,
        session_status=SessionStatus.SUMMARY_GENERATED,
        user_display_name=session.user_display_name,
        session_started_at=session.started_at,
        session_ended_at=session.ended_at,
        session_duration_seconds=duration,
        naric_level=session.naric_level,
        naric_level_source=session.naric_level_source,
        explanation_profile=explanation_profile_for(session.naric_level).value,
        section_notes=dict(content.section_notes),
        question_log=(),
    )


def section_html(html: str, key: str) -> str:
    """Return the markup of one section of the canonical document.

    Needed because a claim about one section must be tested against that
    section. The forward-looking section legitimately names subjects the
    session did not cover; the retrospective ones must not.
    """
    import re

    match = re.search(
        rf'<section data-section="{re.escape(key)}".*?</section>', html, re.DOTALL
    )
    assert match is not None, f"No section {key!r} in the document."
    return match.group(0)
