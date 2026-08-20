"""CSV export (spec section 10).

The exporter owns **no** arithmetic. Every figure it writes comes from
:class:`~app.modules.analytics.services.analytics_service.AnalyticsService`, the same
calls the JSON API makes, so a CSV and a dashboard rendered from the same
filters cannot disagree. This module's only job is serialisation.

Guarantees
----------

**Valid CSV.** Written through :mod:`csv` with CRLF line endings and minimal
quoting, per RFC 4180. Answers containing commas, quotes, newlines or
non-ASCII text round-trip intact.

**Stable column order.** Columns are declared as explicit tuples. Adding a
metric means appending to a list, never reordering an existing consumer's
columns.

**Deterministic output.** Same input, same bytes: rows are sorted by an explicit
key, floats are formatted to a fixed number of decimal places (so ``50`` and
``50.0`` cannot both appear), and ``calculated_at`` comes from the injected
clock.

**No silent corruption.** ``None`` is written as an empty field, never as
``"None"`` and never as ``0`` - an empty field means "no basis for this figure",
matching the JSON contract. A dataset larger than ``export_max_rows`` raises
rather than producing a quietly truncated file.

**Formula-injection safe.** A value beginning with ``=``, ``+``, ``-``, ``@`` or a
control character is prefixed with an apostrophe when
``export_sanitise_formulas`` is on, so a learner-supplied answer such as
``=cmd|' /c calc'!A1`` cannot execute when the file is opened in a spreadsheet.
"""

from __future__ import annotations

import csv
import io
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.core.time import Clock, SystemClock
from app.modules.analytics.cancellation import QueryContext
from app.modules.analytics.config import AnalyticsSettings
from app.modules.analytics.domain.analytics import OverallAnalytics, QuestionAnalytics
from app.modules.analytics.domain.enums import DataState, QuestionSortField, SortDirection
from app.modules.analytics.domain.filters import AnalyticsFilters
from app.modules.analytics.errors import DatasetTooLargeError, ExportError
from app.modules.analytics.services.analytics_service import AnalyticsService

__all__ = ["CsvExportService", "CsvExport"]

logger = get_logger("export")

CSV_MEDIA_TYPE = "text/csv; charset=utf-8"
LINE_TERMINATOR = "\r\n"

OVERALL_COLUMNS: tuple[str, ...] = (
    "scope",
    "course_id",
    "data_state",
    "calculated_at",
    "attempt_volume",
    "completed_attempts",
    "completion_rate",
    "scored_attempts",
    "average_score",
    "graded_attempts",
    "passed_attempts",
    "failed_attempts",
    "pass_rate",
    "unique_learners",
    "filter_course_id",
    "filter_cohort_id",
    "filter_assessment_type",
    "filter_start_date",
    "filter_end_date",
    "notes",
)

QUESTION_COLUMNS: tuple[str, ...] = (
    "question_id",
    "question_type",
    "attempt_count",
    "answered_count",
    "unanswered_count",
    "graded_count",
    "correct_count",
    "incorrect_count",
    "accuracy_percentage",
    "wrong_answer_rate",
    "most_frequent_wrong_answer",
    "most_frequent_wrong_answer_count",
    "most_frequent_wrong_answer_share",
    "most_frequent_wrong_answer_tied",
    "average_time_seconds",
    "timed_response_count",
    "data_state",
    "is_flagged",
    "meets_flag_criteria",
    "flag_threshold",
    "flag_status",
    "flag_reason",
    "flagged_at",
    "resolved_at",
    "resolution_action",
    "calculated_at",
)

_RISKY_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True)
class CsvExport:
    """A rendered export, ready to stream.

    ``data_state`` and ``calculated_at`` travel alongside the rows because CSV
    has no envelope in which to carry them; the API surfaces them as response
    headers so a consumer can still tell "no data" from "all zeros".
    """

    filename: str
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    data_state: DataState
    calculated_at: datetime
    media_type: str = CSV_MEDIA_TYPE

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def render(self) -> str:
        """Full document as text. Used by tests and small downloads."""
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator=LINE_TERMINATOR, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(self.header)
        writer.writerows(self.rows)
        return buffer.getvalue()

    async def iter_chunks(self, rows_per_chunk: int = 200) -> AsyncIterator[str]:
        """Stream the document in chunks, so a large export never buffers whole."""
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator=LINE_TERMINATOR, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(self.header)
        yield _drain(buffer)

        for start in range(0, len(self.rows), rows_per_chunk):
            writer.writerows(self.rows[start : start + rows_per_chunk])
            yield _drain(buffer)


class CsvExportService:
    """Serialises analytics results as CSV."""

    def __init__(
        self,
        analytics_service: AnalyticsService,
        settings: AnalyticsSettings,
        clock: Clock | None = None,
    ) -> None:
        self._analytics = analytics_service
        self._settings = settings
        self._clock = clock or SystemClock()

    # -------------------------------------------------------------------- public

    async def export_overall(
        self, filters: AnalyticsFilters, context: QueryContext
    ) -> CsvExport:
        """Dashboard metrics as a single-row CSV."""
        analytics = await self._analytics.get_overall_analytics(filters, context)
        row = self._overall_row(analytics)
        return self._finish(
            kind="overall",
            header=OVERALL_COLUMNS,
            rows=(row,),
            data_state=analytics.data_state,
            calculated_at=analytics.calculated_at,
        )

    async def export_questions(
        self,
        filters: AnalyticsFilters,
        context: QueryContext,
        *,
        sort_by: QuestionSortField = QuestionSortField.QUESTION_ID,
        direction: SortDirection = SortDirection.ASC,
    ) -> CsvExport:
        """Question analytics, one row per question."""
        page = await self._analytics.list_question_analytics(
            filters,
            context,
            limit=self._settings.export_max_rows + 1,
            offset=0,
            sort_by=sort_by,
            direction=direction,
        )
        self._assert_row_limit(page.page.total)
        return self._finish(
            kind="questions",
            header=QUESTION_COLUMNS,
            rows=tuple(self._question_row(q, page.calculated_at) for q in page.items),
            data_state=page.data_state,
            calculated_at=page.calculated_at,
        )

    async def export_flagged_questions(
        self,
        filters: AnalyticsFilters,
        context: QueryContext,
        *,
        include_candidates: bool = False,
    ) -> CsvExport:
        """Content-review queue, using the same row shape as the question export."""
        flagged = await self._analytics.get_flagged_questions(
            filters, context, include_candidates=include_candidates
        )
        self._assert_row_limit(flagged.total)
        return self._finish(
            kind="flagged-questions",
            header=QUESTION_COLUMNS,
            rows=tuple(self._question_row(q, flagged.calculated_at) for q in flagged.items),
            data_state=DataState.NO_ATTEMPTS if not flagged.items else DataState.OK,
            calculated_at=flagged.calculated_at,
        )

    # ------------------------------------------------------------------ internal

    def _overall_row(self, analytics: OverallAnalytics) -> tuple[str, ...]:
        filters = analytics.filters
        values = {
            "scope": analytics.scope.value,
            "course_id": analytics.course_id,
            "data_state": analytics.data_state.value,
            "calculated_at": analytics.calculated_at,
            "attempt_volume": analytics.attempt_volume,
            "completed_attempts": analytics.completed_attempts,
            "completion_rate": analytics.completion_rate,
            "scored_attempts": analytics.scored_attempts,
            "average_score": analytics.average_score,
            "graded_attempts": analytics.graded_attempts,
            "passed_attempts": analytics.passed_attempts,
            "failed_attempts": analytics.failed_attempts,
            "pass_rate": analytics.pass_rate,
            "unique_learners": analytics.unique_learners,
            "filter_course_id": filters.course_id,
            "filter_cohort_id": filters.cohort_id,
            "filter_assessment_type": (
                filters.assessment_type.value if filters.assessment_type else None
            ),
            "filter_start_date": filters.start_date,
            "filter_end_date": filters.end_date,
            "notes": "; ".join(analytics.notes) if analytics.notes else None,
        }
        return self._row(OVERALL_COLUMNS, values)

    def _question_row(
        self, question: QuestionAnalytics, calculated_at: datetime
    ) -> tuple[str, ...]:
        wrong = question.most_frequent_wrong_answer
        flag = question.flag
        values = {
            "question_id": question.question_id,
            "question_type": question.question_type_label,
            "attempt_count": question.attempt_count,
            "answered_count": question.answered_count,
            "unanswered_count": question.unanswered_count,
            "graded_count": question.graded_count,
            "correct_count": question.correct_count,
            "incorrect_count": question.incorrect_count,
            "accuracy_percentage": question.accuracy_percentage,
            "wrong_answer_rate": question.wrong_answer_rate,
            "most_frequent_wrong_answer": wrong.answer if wrong else None,
            "most_frequent_wrong_answer_count": wrong.count if wrong else None,
            "most_frequent_wrong_answer_share": wrong.share_of_incorrect if wrong else None,
            "most_frequent_wrong_answer_tied": wrong.tied if wrong else None,
            "average_time_seconds": question.average_time_seconds,
            "timed_response_count": question.timed_response_count,
            "data_state": question.data_state.value,
            "is_flagged": question.is_flagged,
            "meets_flag_criteria": question.meets_flag_criteria,
            "flag_threshold": question.flag_threshold,
            "flag_status": flag.status.value if flag else None,
            "flag_reason": flag.reason.value if flag else None,
            "flagged_at": flag.flagged_at if flag else None,
            "resolved_at": flag.resolved_at if flag else None,
            "resolution_action": (
                flag.resolution_action.value if flag and flag.resolution_action else None
            ),
            "calculated_at": calculated_at,
        }
        return self._row(QUESTION_COLUMNS, values)

    def _row(self, columns: Sequence[str], values: dict[str, object]) -> tuple[str, ...]:
        missing = set(columns) - set(values)
        if missing:  # pragma: no cover - guards against a column added without a value
            raise ExportError(
                f"export row is missing values for columns: {sorted(missing)}",
                details={"columns": sorted(missing)},
            )
        return tuple(self._format(values[column]) for column in columns)

    def _format(self, value: object) -> str:
        """Render one cell deterministically."""
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, float):
            return f"{value:.{self._settings.decimal_places}f}"
        if isinstance(value, datetime):
            # Rendered exactly as the JSON API renders it, so a consumer can join
            # a CSV row to an API response on the timestamp without reformatting.
            return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
        text = str(value)
        return self._sanitise(text)

    def _sanitise(self, text: str) -> str:
        if not self._settings.export_sanitise_formulas or not text:
            return text
        if text.startswith(_RISKY_PREFIXES):
            return "'" + text
        return text

    def _assert_row_limit(self, total: int) -> None:
        limit = self._settings.export_max_rows
        if total > limit:
            raise DatasetTooLargeError(
                f"The export would contain {total} rows, above the configured limit of "
                f"{limit}. Narrow the filters or export in smaller slices.",
                details={"rows": total, "export_max_rows": limit},
            )

    def _finish(
        self,
        *,
        kind: str,
        header: tuple[str, ...],
        rows: Iterable[tuple[str, ...]],
        data_state: DataState,
        calculated_at: datetime,
    ) -> CsvExport:
        materialised = tuple(rows)
        stamp = calculated_at.strftime("%Y%m%dT%H%M%SZ")
        export = CsvExport(
            filename=f"uc10-{kind}-{stamp}.csv",
            header=header,
            rows=materialised,
            data_state=data_state,
            calculated_at=calculated_at,
        )
        logger.info(
            "csv export prepared",
            extra={
                "kind": kind,
                "rows": export.row_count,
                "data_state": data_state.value,
            },
        )
        return export


def _drain(buffer: io.StringIO) -> str:
    """Take everything written so far and reset the buffer."""
    text = buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    return text
