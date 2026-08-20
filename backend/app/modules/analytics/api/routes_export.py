"""CSV export routes (spec sections 10, 19).

Each export streams through :class:`~fastapi.responses.StreamingResponse` and
carries the data state in response headers, since a CSV body has no envelope in
which to say "this file is empty because nothing matched" as opposed to "these
are real zeros".
"""

from __future__ import annotations

from datetime import UTC
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.modules.analytics.api.deps import AdminDep, ContextDep, FiltersDep, get_export_service
from app.modules.analytics.domain.enums import QuestionSortField, SortDirection
from app.modules.analytics.services.export_service import CsvExport, CsvExportService

router = APIRouter(prefix="/analytics/exports", tags=["exports"])

ExportServiceDep = Annotated[CsvExportService, Depends(get_export_service)]

DATA_STATE_HEADER = "X-Analytics-Data-State"
CALCULATED_AT_HEADER = "X-Analytics-Calculated-At"
ROW_COUNT_HEADER = "X-Analytics-Row-Count"


def _stream(export: CsvExport) -> StreamingResponse:
    return StreamingResponse(
        export.iter_chunks(),
        media_type=export.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{export.filename}"',
            DATA_STATE_HEADER: export.data_state.value,
            CALCULATED_AT_HEADER: export.calculated_at.astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            ROW_COUNT_HEADER: str(export.row_count),
        },
    )


@router.get(
    "/overall.csv",
    summary="Export overall analytics as CSV",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/csv": {}},
            "description": "Single-row CSV of dashboard metrics.",
        }
    },
    description=(
        "Dashboard metrics as CSV, computed by the same service call the JSON "
        "endpoint uses, so the two can never disagree."
    ),
)
async def export_overall(
    service: ExportServiceDep,
    filters: FiltersDep,
    context: ContextDep,
    _admin: AdminDep,
) -> StreamingResponse:
    return _stream(await service.export_overall(filters, context))


@router.get(
    "/questions.csv",
    summary="Export question analytics as CSV",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/csv": {}}, "description": "One row per question."}},
)
async def export_questions(
    service: ExportServiceDep,
    filters: FiltersDep,
    context: ContextDep,
    _admin: AdminDep,
    sort_by: Annotated[QuestionSortField, Query()] = QuestionSortField.QUESTION_ID,
    direction: Annotated[SortDirection, Query()] = SortDirection.ASC,
) -> StreamingResponse:
    export = await service.export_questions(
        filters, context, sort_by=sort_by, direction=direction
    )
    return _stream(export)


@router.get(
    "/flagged-questions.csv",
    summary="Export the content-review queue as CSV",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/csv": {}}, "description": "One row per flagged question."}},
    description=(
        "Flagged questions in the same column layout as the question export, so a "
        "reviewer can diff the two files directly."
    ),
)
async def export_flagged_questions(
    service: ExportServiceDep,
    filters: FiltersDep,
    context: ContextDep,
    _admin: AdminDep,
    include_candidates: Annotated[bool, Query()] = False,
) -> StreamingResponse:
    export = await service.export_flagged_questions(
        filters, context, include_candidates=include_candidates
    )
    return _stream(export)
