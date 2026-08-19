"""CSV import request/response contracts (UC-02 §17–§20)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.schemas import CamelModel
from app.modules.question_bank.domain.enums import ImportStatus


class ImportRowError(CamelModel):
    #: 1-based spreadsheet row number (the header is row 1), so it matches what the admin sees.
    row_number: int
    field: str | None
    code: str
    message: str


class ImportedRowSummary(CamelModel):
    row_number: int
    question_id: str
    reference: str
    question_text: str


class RejectedRowSummary(CamelModel):
    row_number: int
    errors: list[ImportRowError]
    raw_row: dict[str, Any] | None = None


class ImportResult(CamelModel):
    """The structured result required by UC-02 §19.

    Valid rows are persisted even when other rows fail — the import is intentionally
    non-atomic at the row level, and every rejection is reported with its reason.
    """

    id: str
    filename: str
    status: ImportStatus
    total_rows: int
    imported_rows: int
    rejected_rows: int
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None
    imported: list[ImportedRowSummary]
    rejected: list[RejectedRowSummary]


class ImportListItem(CamelModel):
    id: str
    filename: str
    status: ImportStatus
    total_rows: int
    imported_rows: int
    rejected_rows: int
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None
