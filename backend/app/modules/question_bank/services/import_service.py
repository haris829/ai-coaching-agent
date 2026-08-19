"""CSV bulk import (UC-02 §17–§20).

Import semantics
----------------
Row-level, NOT atomic — this is the behaviour UC-02 §17 explicitly requires: *persist valid
records while reporting imported and rejected counts*. Each row is committed on its own, so one
bad row can never roll back the good ones, and one bad row can never abort the run.

A whole-file problem (no header, missing required columns, undecodable bytes, too many rows) is
different: nothing is imported and the run is recorded as FAILED with the reason.

Every rejection is stored in ``qb_question_import_errors`` with its row number, field, code and
message, so the report is reproducible long after the upload.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import (
    AppError,
    BadRequestError,
    FieldIssue,
    NotFoundError,
    PayloadTooLargeError,
)
from app.core.logging import get_logger
from app.core.time import utcnow
from app.modules.question_bank.csv_import.parser import (
    CsvParseError,
    decode_upload,
    parse_csv,
)
from app.modules.question_bank.csv_import.row_mapper import map_row
from app.modules.question_bank.domain.content_hash import compute_content_hash
from app.modules.question_bank.domain.enums import ImportStatus
from app.modules.question_bank.domain.validator import validate_question_draft
from app.modules.question_bank.models import (
    Question,
    QuestionImport,
    QuestionImportError,
)
from app.modules.question_bank.services import question_service

logger = get_logger(__name__)


@dataclass(slots=True)
class ImportedRow:
    row_number: int
    question_id: str
    reference: str
    question_text: str


@dataclass(slots=True)
class RejectedRow:
    row_number: int
    issues: list[FieldIssue]
    raw: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ImportOutcome:
    import_run: QuestionImport
    imported: list[ImportedRow]
    rejected: list[RejectedRow]


def import_csv(
    db: Session,
    *,
    data: bytes,
    filename: str,
    actor: str | None = None,
) -> ImportOutcome:
    """Parse, validate and import a CSV file, returning a full row-level report."""
    if len(data) > settings.csv_max_bytes:
        raise PayloadTooLargeError(
            f"The uploaded file is {len(data)} bytes, which exceeds the "
            f"{settings.csv_max_bytes} byte limit."
        )

    import_run = QuestionImport(
        filename=(filename or "upload.csv")[:255],
        status=ImportStatus.PROCESSING.value,
        created_by=actor,
    )
    db.add(import_run)
    db.commit()
    db.refresh(import_run)

    logger.info(
        "csv_import.started",
        extra={"import_id": import_run.id, "filename": import_run.filename, "bytes": len(data)},
    )

    # ---- Whole-file parsing -------------------------------------------------
    try:
        text = decode_upload(data)
        parsed = parse_csv(text, max_rows=settings.csv_max_rows)
    except CsvParseError as exc:
        _fail_import(db, import_run, code=exc.code, message=exc.message)
        logger.warning(
            "csv_import.file_rejected",
            extra={"import_id": import_run.id, "code": exc.code, "reason": exc.message},
        )
        raise BadRequestError(
            exc.message,
            [FieldIssue(field="file", code=exc.code, message=exc.message)],
        ) from exc

    if parsed.unknown_headers:
        logger.info(
            "csv_import.unknown_headers",
            extra={"import_id": import_run.id, "headers": parsed.unknown_headers},
        )

    # ---- Row-by-row ---------------------------------------------------------
    imported: list[ImportedRow] = []
    rejected: list[RejectedRow] = []
    # Duplicate detection *within the file*, so two identical rows in one upload do not both
    # import. Cross-file duplicates are caught by the service layer's content-hash check.
    seen_hashes: dict[str, int] = {}
    seen_external_refs: dict[str, int] = {}

    for row in parsed.rows:
        mapped = map_row(row)
        issues = list(mapped.issues)

        validated = None
        if mapped.draft is not None:
            outcome = validate_question_draft(mapped.draft)
            if outcome.ok and outcome.value is not None:
                validated = outcome.value
            else:
                issues.extend(outcome.issues)

        # ---- in-file duplicate checks ----
        if validated is not None and not issues:
            content_hash = compute_content_hash(validated)
            first_seen = seen_hashes.get(content_hash)
            if first_seen is not None:
                issues.append(
                    FieldIssue(
                        field="question_text",
                        code="DUPLICATE_ROW_IN_FILE",
                        message=(
                            f"This question duplicates row {first_seen} in the same file "
                            "(same type, text and answer key)."
                        ),
                    )
                )
            if validated.external_ref:
                ref_row = seen_external_refs.get(validated.external_ref.lower())
                if ref_row is not None:
                    issues.append(
                        FieldIssue(
                            field="external_ref",
                            code="DUPLICATE_EXTERNAL_REF_IN_FILE",
                            message=(
                                f'external_ref "{validated.external_ref}" is already used by '
                                f"row {ref_row} in the same file."
                            ),
                        )
                    )

        if issues or validated is None:
            rejected.append(RejectedRow(row_number=row.row_number, issues=issues, raw=row.raw))
            continue

        # ---- persist the row ----
        try:
            question = question_service.create_question(
                db,
                mapped.draft,  # type: ignore[arg-type]
                actor=actor,
                import_id=import_run.id,
                import_row_number=row.row_number,
                commit=True,
            )
        except AppError as exc:
            # A rejected row is normal operation, not a server fault: record why and continue.
            db.rollback()
            row_issues = exc.details or [
                FieldIssue(field="row", code=exc.code, message=exc.message)
            ]
            rejected.append(RejectedRow(row_number=row.row_number, issues=row_issues, raw=row.raw))
            continue
        except SQLAlchemyError as exc:
            db.rollback()
            logger.error(
                "csv_import.row_database_error",
                extra={"import_id": import_run.id, "row": row.row_number, "err": str(exc)},
            )
            rejected.append(
                RejectedRow(
                    row_number=row.row_number,
                    issues=[
                        FieldIssue(
                            field="row",
                            code="ROW_PERSISTENCE_FAILED",
                            message="The row could not be saved due to a database error.",
                        )
                    ],
                    raw=row.raw,
                )
            )
            continue

        seen_hashes[question.content_hash] = row.row_number
        if question.external_ref:
            seen_external_refs[question.external_ref.lower()] = row.row_number

        imported.append(
            ImportedRow(
                row_number=row.row_number,
                question_id=question.id,
                reference=question.reference,
                question_text=question.question_text,
            )
        )

    # ---- Persist the report -------------------------------------------------
    for rejection in rejected:
        raw_json = json.dumps(rejection.raw, ensure_ascii=False) if rejection.raw else None
        for issue in rejection.issues:
            db.add(
                QuestionImportError(
                    import_id=import_run.id,
                    row_number=rejection.row_number,
                    field=issue.field[:128],
                    code=issue.code[:64],
                    message=issue.message,
                    raw_row=raw_json,
                )
            )

    import_run.total_rows = len(parsed.rows)
    import_run.imported_rows = len(imported)
    import_run.rejected_rows = len(rejected)
    import_run.status = ImportStatus.COMPLETED.value
    import_run.completed_at = utcnow()
    db.commit()
    db.refresh(import_run)

    logger.info(
        "csv_import.completed",
        extra={
            "import_id": import_run.id,
            "total": import_run.total_rows,
            "imported": import_run.imported_rows,
            "rejected": import_run.rejected_rows,
        },
    )

    return ImportOutcome(import_run=import_run, imported=imported, rejected=rejected)


def _fail_import(db: Session, import_run: QuestionImport, *, code: str, message: str) -> None:
    """Record a whole-file failure. No rows are imported."""
    import_run.status = ImportStatus.FAILED.value
    import_run.error_message = message
    import_run.total_rows = 0
    import_run.imported_rows = 0
    import_run.rejected_rows = 0
    import_run.completed_at = utcnow()
    db.add(
        QuestionImportError(
            import_id=import_run.id,
            row_number=0,  # 0 == whole file, not a specific row
            field="file",
            code=code[:64],
            message=message,
        )
    )
    db.commit()


# ---------------------------------------------------------------------------
# Reading past imports
# ---------------------------------------------------------------------------


def get_import(db: Session, import_id: str) -> QuestionImport:
    import_run = db.get(QuestionImport, import_id)
    if import_run is None:
        raise NotFoundError("Import", import_id)
    return import_run


def list_imports(
    db: Session, *, limit: int = 25, offset: int = 0
) -> tuple[list[QuestionImport], int]:
    from sqlalchemy import func

    total = int(db.execute(select(func.count(QuestionImport.id))).scalar_one())
    rows = (
        db.execute(
            select(QuestionImport)
            .order_by(QuestionImport.started_at.desc())
            .offset(max(0, offset))
            .limit(max(1, min(100, limit)))
        )
        .scalars()
        .all()
    )
    return list(rows), total


def rebuild_outcome(db: Session, import_run: QuestionImport) -> ImportOutcome:
    """Reconstruct the row-level report for a past import from persisted data."""
    questions = (
        db.execute(
            select(Question)
            .where(Question.import_id == import_run.id)
            .order_by(Question.import_row_number)
        )
        .scalars()
        .all()
    )
    imported = [
        ImportedRow(
            row_number=question.import_row_number or 0,
            question_id=question.id,
            reference=question.reference,
            question_text=question.question_text,
        )
        for question in questions
    ]

    grouped: dict[int, RejectedRow] = {}
    for error in sorted(import_run.errors, key=lambda e: (e.row_number, e.created_at)):
        bucket = grouped.get(error.row_number)
        if bucket is None:
            raw: dict[str, str] = {}
            if error.raw_row:
                try:
                    parsed = json.loads(error.raw_row)
                    if isinstance(parsed, dict):
                        raw = {str(k): str(v) for k, v in parsed.items()}
                except ValueError:
                    raw = {}
            bucket = RejectedRow(row_number=error.row_number, issues=[], raw=raw)
            grouped[error.row_number] = bucket
        bucket.issues.append(
            FieldIssue(field=error.field or "row", code=error.code, message=error.message)
        )

    return ImportOutcome(
        import_run=import_run,
        imported=imported,
        rejected=[grouped[key] for key in sorted(grouped)],
    )
