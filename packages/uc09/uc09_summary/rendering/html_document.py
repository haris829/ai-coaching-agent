"""The canonical CPD evidence document, as HTML.

This module is the only place that decides what the exported document says.
The PDF is produced by handing the output of :func:`build_html` to a
:class:`DocumentRenderer`; the printable fallback is that same string. A
divergence between the two is therefore not unlikely, it is impossible.

Layout rules that carry meaning
-------------------------------

* The three retrospective sections are marked ``data-orientation="retrospective"``
  and the forward-looking section ``data-orientation="forward-looking"``, and
  Next Steps additionally carries a visible sentence saying so. A reader must
  not be able to mistake a suggestion for a record of what happened.
* A partial summary carries the partial marker in the banner, under the title
  and beside the duration - three places, because this string is the only thing
  standing between a mid-session snapshot and a document that misrepresents how
  long a solicitor studied for.
* A section that is legitimately empty says why it is empty. It never renders
  as an absent section, because an absent section reads as an oversight.

No CSS framework, no template engine, no frontend. Inline print styles only,
so the document prints correctly from a browser without a stylesheet fetch.
"""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape

from uc09_summary.domain.enums import GenerationMode, SourceStatus
from uc09_summary.domain.models import SummaryRecord

#: Branding required on the export.
PRODUCT_NAME = "Loophole Larry"

#: Required label. Exact string; asserted in both output forms.
CPD_LABEL = "CPD Learning Evidence"

#: Visible partial marker. Exact string; asserted in both output forms.
PARTIAL_MARKER = "PARTIAL SUMMARY - SESSION INCOMPLETE"

#: Shown only on a complete record, so the two can never be confused by eye.
COMPLETE_MARKER = "Complete session record"

#: Added to the HTML when PDF rendering was unavailable. Additive only: it is
#: appended to the canonical document and changes nothing already in it.
PDF_UNAVAILABLE_NOTICE = (
    "PDF generation was unavailable. This printable HTML document carries the "
    "same content as the PDF export."
)

#: Wording used when the automatic summary failed and the question log stands in.
FALLBACK_NOTICE = (
    "Automatic summary generation was unavailable for this session. This "
    "document is a structured log of the questions asked, not a full summary."
)

#: The four sections. Order and wording are fixed.
CANONICAL_SECTION_TITLES = (
    "Topics Covered",
    "Key Concepts",
    "Resources Referenced",
    "Recommended Next Steps",
)

_FORWARD_LOOKING_CAPTION = (
    "Forward-looking: suggested future study. This section is not a record of "
    "what happened in this session."
)

_RETROSPECTIVE_CAPTION = "Record of this session."

_EMPTY_TEXT = {
    "topics_covered": "No topic was recorded as discussed in this session.",
    "key_concepts": "No key concept could be drawn from the recorded interactions.",
    "resources_referenced": (
        "No legislation or case law was cited during this session."
    ),
    "next_steps": "No next step could be grounded in session data or a gap report.",
}

_STYLE = """
:root { color-scheme: light; }
body { font-family: Georgia, 'Times New Roman', serif; color: #1a1a1a;
       margin: 2.5rem auto; max-width: 46rem; line-height: 1.5; }
.brand { font-size: 1.1rem; letter-spacing: .08em; text-transform: uppercase; }
.cpd-label { font-weight: bold; }
.banner { border: 2px solid #1a1a1a; padding: .6rem .8rem; margin: 1rem 0; }
.banner.partial { border-color: #8a1c1c; color: #8a1c1c; font-weight: bold; }
.banner.fallback { border-style: dashed; }
.meta dt { font-weight: bold; }
section { margin-top: 1.6rem; page-break-inside: avoid; }
section h2 { border-bottom: 1px solid #999; padding-bottom: .2rem; }
section[data-orientation="forward-looking"] { border-left: 4px solid #1a1a1a;
       padding-left: .9rem; }
.caption { font-style: italic; color: #444; }
.empty { font-style: italic; }
footer { margin-top: 2rem; border-top: 1px solid #999; padding-top: .6rem;
         font-size: .9rem; }
@media print { body { margin: 0; } }
"""


def build_html(summary: SummaryRecord) -> str:
    """Build the canonical document for ``summary``.

    Args:
        summary: the stored summary record.

    Returns:
        A complete standalone HTML document. This is the canonical artefact:
        the PDF is a rendering of this string, and the printable fallback is
        this string.
    """
    e = escape
    partial = summary.is_partial
    parts: list[str] = []

    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en-GB"><head><meta charset="utf-8">')
    parts.append(f"<title>{e(CPD_LABEL)} - {e(summary.summary_id)}</title>")
    parts.append(f"<style>{_STYLE}</style>")
    parts.append("</head><body>")

    # ---- Header: branding, CPD label, and the partial/complete banner -----
    parts.append("<header>")
    parts.append(f'<p class="brand">{e(PRODUCT_NAME)}</p>')
    parts.append(f'<h1 class="cpd-label">{e(CPD_LABEL)}</h1>')
    if partial:
        parts.append(f'<p class="banner partial">{e(PARTIAL_MARKER)}</p>')
        parts.append(
            "<p>This document covers the session only up to "
            f"{e(_stamp(summary.covers_interactions_through))} and is not a "
            "complete record of the session.</p>"
        )
    else:
        parts.append(f'<p class="banner">{e(COMPLETE_MARKER)}</p>')
    if summary.generation_mode is GenerationMode.QUESTION_LOG_FALLBACK:
        parts.append(f'<p class="banner fallback">{e(FALLBACK_NOTICE)}</p>')
    parts.append("</header>")

    # ---- Verification block ------------------------------------------------
    parts.append('<dl class="meta">')
    parts.extend(_definition("Learner", summary.user_display_name))
    parts.extend(_definition("Session date", _date_only(summary.session_started_at)))
    parts.extend(_definition("Session duration", _duration(summary, partial)))
    parts.extend(_definition("Session ID for verification", summary.session_id))
    parts.extend(_definition("Summary ID", summary.summary_id))
    parts.extend(_definition("Generated at", _stamp(summary.generated_at)))
    parts.extend(
        _definition(
            "Covers interactions through",
            _stamp(summary.covers_interactions_through),
        )
    )
    parts.extend(_definition("Study level", _level_text(summary)))
    parts.extend(_definition("Record status", PARTIAL_MARKER if partial else COMPLETE_MARKER))
    parts.append("</dl>")

    # ---- The four sections -------------------------------------------------
    parts.append(_topics_section(summary))
    parts.append(_concepts_section(summary))
    parts.append(_resources_section(summary))
    parts.append(_next_steps_section(summary))

    # ---- Question log, only in fallback mode -------------------------------
    if summary.generation_mode is GenerationMode.QUESTION_LOG_FALLBACK:
        parts.append(_question_log_section(summary))

    # ---- Footer ------------------------------------------------------------
    parts.append("<footer>")
    parts.append(
        f"<p>{e(PRODUCT_NAME)} - {e(CPD_LABEL)}. Verify this record against "
        f"session {e(summary.session_id)}.</p>"
    )
    if partial:
        parts.append(f'<p class="banner partial">{e(PARTIAL_MARKER)}</p>')
    parts.append("</footer>")

    parts.append("</body></html>")
    return "\n".join(parts)


def with_pdf_unavailable_notice(html: str) -> str:
    """Append the PDF-unavailable notice to the canonical document.

    Purely additive: the canonical content is untouched, so the fallback still
    carries exactly what the PDF would have carried, plus one explanatory line.

    Args:
        html: the canonical document from :func:`build_html`.

    Returns:
        The same document with the notice inserted before ``</body>``.
    """
    notice = (
        '<p class="banner" data-role="pdf-unavailable">'
        f"{escape(PDF_UNAVAILABLE_NOTICE)}</p>"
    )
    if "</body>" in html:
        return html.replace("</body>", f"{notice}\n</body>", 1)
    return f"{html}\n{notice}"


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------


def _topics_section(summary: SummaryRecord) -> str:
    e = escape
    rows = []
    for topic in summary.topics_covered:
        rows.append(
            f"<li><strong>{e(topic.label)}</strong> "
            f"({topic.interaction_count} interaction"
            f"{'' if topic.interaction_count == 1 else 's'})</li>"
        )
    return _section(
        "Topics Covered",
        "topics_covered",
        rows,
        summary,
        retrospective=True,
    )


def _concepts_section(summary: SummaryRecord) -> str:
    e = escape
    rows = []
    for concept in summary.key_concepts:
        rows.append(
            f"<li><strong>{e(concept.label)}</strong>: {e(concept.explanation)}</li>"
        )
    return _section("Key Concepts", "key_concepts", rows, summary, retrospective=True)


def _resources_section(summary: SummaryRecord) -> str:
    e = escape
    rows = []
    for resource in summary.resources_referenced:
        kind = resource.kind.value.replace("_", " ")
        rows.append(
            f"<li><strong>{e(resource.title)}</strong> - {e(resource.citation)} "
            f"[{e(kind)}]</li>"
        )
    return _section(
        "Resources Referenced",
        "resources_referenced",
        rows,
        summary,
        retrospective=True,
        extra_caption=(
            "Authorities cited during this session. Nothing is listed here that "
            "was not cited."
        ),
    )


def _next_steps_section(summary: SummaryRecord) -> str:
    e = escape
    rows = []
    for suggestion in summary.next_steps:
        provenance = (
            "suggested by gap analysis"
            if suggestion.source.value == "gap_report"
            else "drawn from this session"
        )
        rationale = f" - {e(suggestion.rationale)}" if suggestion.rationale else ""
        rows.append(
            f"<li><strong>{e(suggestion.label)}</strong>{rationale} "
            f"({e(provenance)})</li>"
        )
    return _section(
        "Recommended Next Steps",
        "next_steps",
        rows,
        summary,
        retrospective=False,
    )


def _question_log_section(summary: SummaryRecord) -> str:
    e = escape
    rows = [
        f"<li>{e(_stamp(entry.asked_at))} - {e(entry.question_text)}</li>"
        for entry in summary.question_log
    ]
    body = (
        "<ol>" + "".join(rows) + "</ol>"
        if rows
        else '<p class="empty">No question was recorded for this session.</p>'
    )
    return (
        '<section data-section="question_log" data-orientation="retrospective">'
        "<h2>Questions Asked</h2>"
        f'<p class="caption">{e(FALLBACK_NOTICE)}</p>'
        f"{body}</section>"
    )


def _section(
    title: str,
    key: str,
    rows: list[str],
    summary: SummaryRecord,
    *,
    retrospective: bool,
    extra_caption: str = "",
) -> str:
    e = escape
    orientation = "retrospective" if retrospective else "forward-looking"
    caption = _RETROSPECTIVE_CAPTION if retrospective else _FORWARD_LOOKING_CAPTION
    chunks = [
        f'<section data-section="{key}" data-orientation="{orientation}">',
        f"<h2>{e(title)}</h2>",
        f'<p class="caption">{e(caption)}</p>',
    ]
    if extra_caption:
        chunks.append(f'<p class="caption">{e(extra_caption)}</p>')

    if rows:
        chunks.append("<ul>" + "".join(rows) + "</ul>")
    else:
        chunks.append(f'<p class="empty">{e(_EMPTY_TEXT[key])}</p>')

    note = summary.section_notes.get(key)
    if note:
        chunks.append(f'<p class="caption">{e(note)}</p>')

    status = summary.source_status.get(key)
    if status is not None and status is not SourceStatus.AVAILABLE:
        chunks.append(f'<p class="caption">Section status: {e(status.value)}.</p>')

    chunks.append("</section>")
    return "".join(chunks)


# --------------------------------------------------------------------------
# Field formatting
# --------------------------------------------------------------------------


def _definition(term: str, value: str) -> list[str]:
    return [f"<dt>{escape(term)}</dt>", f"<dd>{escape(value)}</dd>"]


def _stamp(value: datetime) -> str:
    return _as_utc(value).strftime("%Y-%m-%d %H:%M:%S UTC")


def _date_only(value: datetime) -> str:
    return _as_utc(value).strftime("%Y-%m-%d")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _duration(summary: SummaryRecord, partial: bool) -> str:
    total = summary.session_duration_seconds
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    text = f"{hours}h {minutes:02d}m {seconds:02d}s"
    if partial:
        # An elapsed time on a partial record is not a session length. Saying
        # so here is what stops the export misrepresenting how long the
        # learner studied.
        return f"{text} elapsed at the time of this partial summary"
    return text


def _level_text(summary: SummaryRecord) -> str:
    return (
        f"{summary.naric_level.value} "
        f"(source: {summary.naric_level_source.value}; "
        f"explanation profile: {summary.explanation_profile})"
    )
