"""CSV import endpoints (UC-02 §17–§21).

    GET  /imports/template        download the documented CSV template
    GET  /imports/template/guide  the same format described as JSON, for the UI's help panel
    POST /imports                 upload + import; returns the full row-level report   201
    GET  /imports                 past import runs
    GET  /imports/{id}            re-read one import's result

The upload accepts either ``multipart/form-data`` (a real file picker, which is what the admin
UI uses) or a raw ``text/csv`` body, which is convenient for scripts and tests.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, Query, Request, UploadFile, status
from fastapi.responses import PlainTextResponse

from app.core.config import settings
from app.core.deps import DbSession
from app.core.errors import BadRequestError, FieldIssue, PayloadTooLargeError
from app.core.schemas import ErrorResponse
from app.modules.identity.security import Actor
from app.modules.question_bank.api import serializers
from app.modules.question_bank.csv_import.template import (
    CSV_HEADERS,
    FIELD_GUIDE,
    REQUIRED_HEADERS,
    render_template_csv,
)
from app.modules.question_bank.schemas.import_run import ImportListItem, ImportResult
from app.modules.question_bank.services import import_service

router = APIRouter(
    prefix="/imports",
    tags=["Question Bank — CSV Import"],
    responses={
        400: {"model": ErrorResponse, "description": "The file could not be processed at all"},
        404: {"model": ErrorResponse, "description": "Import not found"},
        413: {"model": ErrorResponse, "description": "File too large"},
    },
)


TEMPLATE_FILENAME = "question-bank-import-template.csv"


@router.get(
    "/template",
    summary="Download the CSV template (one worked example per question type)",
    response_class=PlainTextResponse,
)
def download_template() -> PlainTextResponse:
    return PlainTextResponse(
        content=render_template_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{TEMPLATE_FILENAME}"'},
    )


@router.get("/template/guide", summary="The CSV format described as JSON", response_model=dict)
def template_guide() -> dict[str, Any]:
    return {
        "headers": list(CSV_HEADERS),
        "requiredHeaders": list(REQUIRED_HEADERS),
        "listDelimiter": "|",
        "optionSyntax": "LABEL:Text — split on the first colon, e.g. A:Paris|B:London",
        "maxBytes": settings.csv_max_bytes,
        "maxRows": settings.csv_max_rows,
        "fields": FIELD_GUIDE,
        "templateUrl": "/api/question-bank/imports/template",
    }


async def _read_upload(request: Request, file: UploadFile | None) -> tuple[bytes, str]:
    """Accept either a multipart file or a raw text/csv body."""
    if file is not None:
        data = await file.read()
        if len(data) > settings.csv_max_bytes:
            raise PayloadTooLargeError(
                f"The uploaded file exceeds the {settings.csv_max_bytes} byte limit."
            )
        return data, file.filename or "upload.csv"

    body = await request.body()
    if not body:
        raise BadRequestError(
            "No CSV content was received. Upload a file in the 'file' field, or send the CSV "
            "as a text/csv request body.",
            [
                FieldIssue(
                    field="file",
                    code="FILE_REQUIRED",
                    message="A CSV file is required.",
                )
            ],
        )
    if len(body) > settings.csv_max_bytes:
        raise PayloadTooLargeError(
            f"The uploaded content exceeds the {settings.csv_max_bytes} byte limit."
        )
    name = request.headers.get("x-filename", "upload.csv")
    return body, name


@router.post(
    "",
    summary="Import questions from CSV (valid rows are imported; invalid rows are reported)",
    status_code=status.HTTP_201_CREATED,
    response_model=ImportResult,
)
async def import_questions(
    request: Request,
    db: DbSession,
    actor: Actor,
    file: Annotated[UploadFile | None, File(description="CSV file to import")] = None,
) -> ImportResult:
    data, filename = await _read_upload(request, file)
    outcome = import_service.import_csv(db, data=data, filename=filename, actor=actor)
    return serializers.import_result(outcome)


@router.get("", summary="List past import runs", response_model=dict)
def list_imports(
    db: DbSession,
    actor: Actor,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    runs, total = import_service.list_imports(db, limit=limit, offset=offset)
    items: list[ImportListItem] = [serializers.import_list_item(run) for run in runs]
    return {
        "items": [item.model_dump(by_alias=True) for item in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/{import_id}",
    summary="Re-read an import result, including every row-level error",
    response_model=ImportResult,
)
def get_import(db: DbSession, actor: Actor, import_id: str) -> ImportResult:
    run = import_service.get_import(db, import_id)
    return serializers.import_result(import_service.rebuild_outcome(db, run))
