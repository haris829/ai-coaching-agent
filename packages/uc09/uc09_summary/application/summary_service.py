"""The summary service: assemble, ground, store, export.

Order of operations, and why it is this order:

1. Read the session and **check ownership before anything else**. A summary is
   a personal record; nothing is assembled for a caller who does not own the
   session.
2. Decide the cover window. A session that is not complete produces a partial
   summary stamped at the generation moment, and only interactions at or before
   that instant can ground anything.
3. Read interactions, citations and the gap report, recording a status for each.
   A source that fails degrades its section and is reported; it never silently
   becomes an empty section, because ``empty`` and ``unavailable`` are
   different facts about the session.
4. Generate, then **check grounding before storing**. Content that fails is
   discarded whole and the question-log fallback is produced instead. No part
   of a rejected response reaches a stored record, an HTML preview or a PDF.
5. Store, with the ``summary_generated`` status on the record this component
   owns.

Export goes through one canonical HTML document. The PDF is that document
rendered; the printable fallback is that document itself.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from uc09_summary.domain.enums import (
    GenerationMode,
    SessionStatus,
    SourceStatus,
    SuggestionSource,
)
from uc09_summary.domain.errors import (
    AccessDenied,
    ProviderError,
    ProviderInvalidResponse,
    SummaryNotFound,
)
from uc09_summary.domain.grounding import (
    MAX_NEXT_STEPS,
    MIN_KEY_CONCEPTS,
    SessionData,
    check_grounding,
)
from uc09_summary.domain.models import (
    DownloadEvent,
    InteractionRecord,
    QuestionLogEntry,
    Resource,
    SessionRecord,
    Suggestion,
    SummaryContent,
    SummaryRecord,
    Topic,
)
from uc09_summary.domain.naric import explanation_profile_for, profile_is_assumed
from uc09_summary.logging_setup import get_logger
from uc09_summary.ports import (
    CitationProvider,
    Clock,
    DocumentRenderer,
    DownloadLogRepository,
    GapReportProvider,
    InteractionProvider,
    SessionProvider,
    SummaryGenerator,
    SummaryRepository,
)
from uc09_summary.rendering.html_document import build_html, with_pdf_unavailable_notice

_log = get_logger(__name__)

#: Note recorded when a generator response was rejected for fabrication.
GROUNDING_REJECTED_NOTE = (
    "The automatic summary for this session was rejected because it contained "
    "material that could not be traced to the session record. A log of the "
    "questions asked is shown instead. Nothing unverified has been included."
)

#: Note recorded when the generator itself failed.
GENERATOR_FAILED_NOTE = (
    "Automatic summary generation was unavailable. A log of the questions "
    "asked is shown instead."
)


@dataclass(frozen=True)
class ExportResult:
    """Outcome of an export request.

    Attributes:
        summary: the record exported.
        html: the document served. Canonical HTML, plus the PDF-unavailable
            notice when ``pdf_available`` is false.
        pdf: the rendered bytes, or ``None`` when rendering failed.
        pdf_available: whether the PDF was produced.
        canonical_html: the canonical document with nothing appended. The PDF,
            when there is one, is a rendering of exactly this string.
    """

    summary: SummaryRecord
    html: str
    pdf: bytes | None
    pdf_available: bool
    canonical_html: str


class SummaryService:
    """Generates, retrieves and exports session summaries."""

    def __init__(
        self,
        *,
        sessions: SessionProvider,
        interactions: InteractionProvider,
        citations: CitationProvider,
        gap_reports: GapReportProvider,
        generator: SummaryGenerator,
        renderer: DocumentRenderer,
        summaries: SummaryRepository,
        downloads: DownloadLogRepository,
        clock: Clock,
    ) -> None:
        self._sessions = sessions
        self._interactions = interactions
        self._citations = citations
        self._gap_reports = gap_reports
        self._generator = generator
        self._renderer = renderer
        self._summaries = summaries
        self._downloads = downloads
        self._clock = clock

    # -- generation ---------------------------------------------------------

    def generate(self, session_id: str, user_id: str) -> SummaryRecord:
        """Generate (or regenerate) the summary of a session for its owner.

        Args:
            session_id: opaque platform session identifier, received not minted.
            user_id: the resolved caller.

        Returns:
            The stored :class:`SummaryRecord`.

        Raises:
            SessionNotFound: no such session.
            AccessDenied: the session belongs to somebody else.
            ProviderError: the session itself could not be read. Every other
                source degrades rather than failing the request.
        """
        session = self._sessions.get_session(session_id)
        if session.user_id != user_id:
            _log.warning(
                "summary_generate_denied",
                session_id=session_id,
                reason="requester_is_not_session_owner",
            )
            raise AccessDenied(session_id)

        now = _utc(self._clock.now())
        is_partial, covers_through = _cover_window(session, now)

        status: dict[str, SourceStatus] = {"session": SourceStatus.AVAILABLE}
        status["naric_level"] = session.naric_level_status

        interactions, status["interactions"] = self._read_interactions(
            session_id, covers_through
        )
        citations, status["citations"] = self._read_citations(session_id)
        gap_suggestions, status["gap_report"] = self._read_gap_report(session.user_id)

        data = SessionData(
            session=session,
            interactions=interactions,
            citations=citations,
            gap_suggestions=gap_suggestions,
            covers_interactions_through=covers_through,
        )

        content, mode, generation_note = self._generate_content(data)

        record = self._assemble(
            session=session,
            data=data,
            content=content,
            mode=mode,
            generation_note=generation_note,
            source_status=status,
            generated_at=now,
            is_partial=is_partial,
            covers_through=covers_through,
        )
        self._summaries.save(record)

        _log.info(
            "summary_generated",
            summary_id=record.summary_id,
            session_id=record.session_id,
            generation_mode=record.generation_mode.value,
            is_partial=record.is_partial,
            session_status=record.session_status.value,
            topic_count=len(record.topics_covered),
            concept_count=len(record.key_concepts),
            resource_count=len(record.resources_referenced),
            next_step_count=len(record.next_steps),
            interaction_count=len(interactions),
            duration_seconds=record.session_duration_seconds,
        )
        return record

    # -- reads --------------------------------------------------------------

    def get(self, summary_id: str, user_id: str) -> SummaryRecord:
        """Return a summary owned by ``user_id``.

        Raises:
            SummaryNotFound: no such summary, **or** it belongs to another
                learner. The two are indistinguishable to a caller by design:
                a probe must not be able to confirm that a summary exists.
        """
        record = self._summaries.get(summary_id)
        if record is None:
            raise SummaryNotFound(summary_id)
        if record.user_id != user_id:
            _log.warning(
                "summary_access_denied",
                summary_id=summary_id,
                session_id=record.session_id,
                reason="requester_is_not_summary_owner",
            )
            raise SummaryNotFound(summary_id)
        return record

    def preview_html(self, summary_id: str, user_id: str) -> tuple[SummaryRecord, str]:
        """Return the canonical HTML preview. Does not log a download."""
        record = self.get(summary_id, user_id)
        html = build_html(record)
        _log.info(
            "summary_previewed",
            summary_id=record.summary_id,
            session_id=record.session_id,
            is_partial=record.is_partial,
            html_bytes=len(html.encode("utf-8")),
        )
        return record, html

    def export(self, summary_id: str, user_id: str) -> ExportResult:
        """Export a summary and record the download.

        The canonical HTML is built once. The renderer turns that one document
        into a PDF; if it cannot, the same document is served as printable HTML
        with a notice. The two cannot carry different content because only one
        of them was ever composed.
        """
        record = self.get(summary_id, user_id)
        canonical = build_html(record)

        pdf: bytes | None
        try:
            pdf = self._renderer.html_to_pdf(canonical)
            pdf_available = True
            html = canonical
        except ProviderError as exc:
            pdf = None
            pdf_available = False
            html = with_pdf_unavailable_notice(canonical)
            _log.error(
                "pdf_render_failed",
                summary_id=record.summary_id,
                session_id=record.session_id,
                error_code=exc.code,
                port=exc.port,
                served="printable_html",
            )

        event = DownloadEvent(
            download_id=f"dl_{uuid.uuid4().hex}",
            summary_id=record.summary_id,
            session_id=record.session_id,
            user_id=record.user_id,
            downloaded_at=_utc(self._clock.now()),
            format="pdf" if pdf_available else "html",
            pdf_available=pdf_available,
            byte_count=len(pdf) if pdf is not None else len(html.encode("utf-8")),
        )
        self._downloads.record(event)
        _log.info(
            "summary_downloaded",
            summary_id=record.summary_id,
            session_id=record.session_id,
            download_id=event.download_id,
            format=event.format,
            pdf_available=event.pdf_available,
            byte_count=event.byte_count,
        )

        return ExportResult(
            summary=record,
            html=html,
            pdf=pdf,
            pdf_available=pdf_available,
            canonical_html=canonical,
        )

    # -- source reads with per-source status -------------------------------

    def _read_interactions(
        self, session_id: str, covers_through: datetime
    ) -> tuple[tuple[InteractionRecord, ...], SourceStatus]:
        try:
            records = tuple(self._interactions.for_session(session_id))
        except ProviderError as exc:
            _log.error(
                "interaction_source_failed",
                session_id=session_id,
                error_code=exc.code,
                port=exc.port,
            )
            return (), SourceStatus.UNAVAILABLE

        within = tuple(r for r in records if _utc(r.occurred_at) <= covers_through)
        if not records:
            return (), SourceStatus.EMPTY
        if not within:
            # The session has interactions, but none inside the cover window.
            return (), SourceStatus.PARTIAL
        if len(within) < len(records):
            return within, SourceStatus.PARTIAL
        return within, SourceStatus.AVAILABLE

    def _read_citations(
        self, session_id: str
    ) -> tuple[tuple[Resource, ...], SourceStatus]:
        try:
            records = tuple(self._citations.for_session(session_id))
        except ProviderError as exc:
            _log.error(
                "citation_source_failed",
                session_id=session_id,
                error_code=exc.code,
                port=exc.port,
            )
            return (), SourceStatus.UNAVAILABLE
        return records, SourceStatus.AVAILABLE if records else SourceStatus.EMPTY

    def _read_gap_report(
        self, user_id: str
    ) -> tuple[tuple[Suggestion, ...] | None, SourceStatus]:
        try:
            result = self._gap_reports.suggestions(user_id)
        except ProviderError as exc:
            _log.error(
                "gap_report_source_failed", error_code=exc.code, port=exc.port
            )
            return None, SourceStatus.UNAVAILABLE
        if result is None:
            return None, SourceStatus.UNAVAILABLE
        records = tuple(result)
        return records, SourceStatus.AVAILABLE if records else SourceStatus.EMPTY

    # -- generation and grounding ------------------------------------------

    def _generate_content(
        self, data: SessionData
    ) -> tuple[SummaryContent, GenerationMode, str]:
        """Generate and ground, or fall back to the question log.

        A grounding failure and a generator outage both end here, but they are
        recorded differently: one says the generator could not be reached, the
        other says its answer was refused. Collapsing them would hide the more
        serious of the two.
        """
        try:
            content = self._generator.generate(data)
            if not isinstance(content, SummaryContent):
                raise ProviderInvalidResponse(
                    "summary_generator",
                    f"generator returned {type(content).__name__}, expected SummaryContent",
                )
            check_grounding(content, data)
            return content, GenerationMode.GENERATED, ""
        except ProviderError as exc:
            violations = getattr(exc, "violations", None)
            if violations is not None:
                # Fabrication. Loud, and no part of the response survives.
                _log.error(
                    "generated_summary_rejected_ungrounded",
                    session_id=data.session.session_id,
                    error_code=exc.code,
                    violation_count=len(violations),
                    # Reasons only. The identifiers are deliberately not logged.
                    violation_reasons=sorted(set(getattr(exc, "reasons", ()))),
                    action="rejected_whole_response",
                )
                note = GROUNDING_REJECTED_NOTE
            else:
                _log.error(
                    "summary_generation_failed",
                    session_id=data.session.session_id,
                    error_code=exc.code,
                    port=exc.port,
                )
                note = GENERATOR_FAILED_NOTE
            return (
                _question_log_content(data),
                GenerationMode.QUESTION_LOG_FALLBACK,
                note,
            )

    # -- assembly -----------------------------------------------------------

    def _assemble(
        self,
        *,
        session: SessionRecord,
        data: SessionData,
        content: SummaryContent,
        mode: GenerationMode,
        generation_note: str,
        source_status: dict[str, SourceStatus],
        generated_at: datetime,
        is_partial: bool,
        covers_through: datetime,
    ) -> SummaryRecord:
        status = dict(source_status)
        status.update(_section_status(content, data, mode))
        if generation_note == GROUNDING_REJECTED_NOTE:
            # The generator answered; its answer was refused. That is a
            # different fact from the generator being unreachable, and the
            # vocabulary has a word for each.
            status["summary_generator"] = SourceStatus.INVALID

        notes = dict(content.section_notes)
        if generation_note:
            notes["generation"] = generation_note
        if profile_is_assumed(session.naric_level):
            notes["explanation_profile"] = (
                f"The explanation depth for {session.naric_level.value} is an "
                "assumption recorded in the assumptions register, not a rule "
                "confirmed by the awarding body."
            )
        if session.naric_level_status is SourceStatus.INVALID:
            notes["study_level"] = (
                "The study level supplied for this learner did not match a "
                "known level, so the default was applied."
            )

        duration = max(0, int((covers_through - _utc(session.started_at)).total_seconds()))
        if not is_partial and session.ended_at is not None:
            duration = max(
                0, int((_utc(session.ended_at) - _utc(session.started_at)).total_seconds())
            )

        return SummaryRecord(
            summary_id=f"sum_{uuid.uuid4().hex}",
            session_id=session.session_id,
            user_id=session.user_id,
            generated_at=generated_at,
            is_partial=is_partial,
            covers_interactions_through=covers_through,
            topics_covered=content.topics_covered,
            key_concepts=content.key_concepts,
            resources_referenced=content.resources_referenced,
            next_steps=content.next_steps,
            source_status=status,
            generation_mode=mode,
            session_status=SessionStatus.SUMMARY_GENERATED,
            user_display_name=session.user_display_name,
            session_started_at=session.started_at,
            session_ended_at=session.ended_at,
            session_duration_seconds=duration,
            naric_level=session.naric_level,
            naric_level_source=session.naric_level_source,
            explanation_profile=explanation_profile_for(session.naric_level).value,
            section_notes=notes,
            question_log=(
                _question_log_entries(data)
                if mode is GenerationMode.QUESTION_LOG_FALLBACK
                else ()
            ),
        )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _cover_window(session: SessionRecord, now: datetime) -> tuple[bool, datetime]:
    """Decide partiality and the instant the summary covers interactions through."""
    if session.is_complete and session.ended_at is not None:
        return False, _utc(session.ended_at)
    # Not complete: this is a mid-session snapshot, stamped at the generation
    # moment. A summary stamped any other way would misstate the session length
    # on a document a regulator may read.
    return True, now


def _question_log_content(data: SessionData) -> SummaryContent:
    """The fallback body: what was asked, plus only what needs no generation.

    Topics and resources are direct reads of the tag and citation records, so
    they remain grounded without a generator. Key concepts require judgement and
    are therefore absent rather than guessed at, which is what stops the
    fallback reading as a full summary.
    """
    topics = tuple(
        Topic(
            topic_id=topic_id,
            label=topic_id.replace("_", "-").replace("-", " ").capitalize(),
            interaction_count=len(data.interactions_for_topic(topic_id)),
            first_discussed_at=min(
                i.occurred_at for i in data.interactions_for_topic(topic_id)
            ),
            last_discussed_at=max(
                i.occurred_at for i in data.interactions_for_topic(topic_id)
            ),
        )
        for topic_id in data.topic_ids
    )
    steps: tuple[Suggestion, ...] = ()
    if data.gap_suggestions:
        steps = tuple(
            s
            for s in data.gap_suggestions[:MAX_NEXT_STEPS]
            if s.source is SuggestionSource.GAP_REPORT
        )

    notes = {
        "key_concepts": (
            "Key concepts are not available for this session because automatic "
            "summary generation did not complete. They have not been inferred."
        )
    }
    if not steps:
        notes["next_steps"] = (
            "No next step could be taken from a gap report, and none has been "
            "inferred from the question log."
        )
    if not data.citations:
        notes["resources_referenced"] = (
            "No legislation or case law was cited during this session."
        )

    return SummaryContent(
        topics_covered=topics,
        key_concepts=(),
        resources_referenced=tuple(data.citations),
        next_steps=steps,
        section_notes=notes,
    )


def _question_log_entries(data: SessionData) -> tuple[QuestionLogEntry, ...]:
    return tuple(
        QuestionLogEntry(
            interaction_id=i.interaction_id,
            asked_at=i.occurred_at,
            question_text=i.question_text,
            topic_tags=i.topic_tags,
        )
        for i in data.interactions
    )


def _section_status(
    content: SummaryContent, data: SessionData, mode: GenerationMode
) -> dict[str, SourceStatus]:
    """Derive a status per section from what actually got into it."""
    status: dict[str, SourceStatus] = {}

    status["topics_covered"] = (
        SourceStatus.AVAILABLE if content.topics_covered else SourceStatus.EMPTY
    )

    if mode is GenerationMode.QUESTION_LOG_FALLBACK:
        status["key_concepts"] = SourceStatus.UNAVAILABLE
    elif not content.key_concepts:
        status["key_concepts"] = SourceStatus.EMPTY
    elif len(content.key_concepts) < MIN_KEY_CONCEPTS:
        # Fewer than the target, because fewer could be grounded. Reported as
        # partial rather than padded up to the number.
        status["key_concepts"] = SourceStatus.PARTIAL
    else:
        status["key_concepts"] = SourceStatus.AVAILABLE

    status["resources_referenced"] = (
        SourceStatus.AVAILABLE if content.resources_referenced else SourceStatus.EMPTY
    )
    status["next_steps"] = (
        SourceStatus.AVAILABLE if content.next_steps else SourceStatus.EMPTY
    )
    status["summary_generator"] = (
        SourceStatus.AVAILABLE
        if mode is GenerationMode.GENERATED
        else SourceStatus.UNAVAILABLE
    )
    return status


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = [
    "GENERATOR_FAILED_NOTE",
    "GROUNDING_REJECTED_NOTE",
    "ExportResult",
    "SummaryService",
]
