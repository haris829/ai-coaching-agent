"""Tolerant CSV parsing (UC-02 §20).

Responsibilities, in order:

1. decode the upload without crashing on a BOM or a non-UTF-8 file;
2. sniff the delimiter so a semicolon-separated export from a European Excel still works;
3. resolve the header row, reporting *missing required headers* as a whole-file failure;
4. yield one ``ParsedRow`` per data row with a spreadsheet row number attached.

Nothing here decides whether a row is a valid *question* — that is the row mapper plus the
authoritative domain validator. This module only turns bytes into addressable rows, and it
never raises for row-level problems: a single malformed row must not fail the file.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from app.core.errors import FieldIssue
from app.modules.question_bank.csv_import.template import (
    CSV_HEADERS,
    REQUIRED_HEADERS,
    normalise_header,
)

#: csv defaults to 128 KB per field, which is plenty for a scenario vignette but we raise it a
#: little for very long explanations while still bounding memory.
csv.field_size_limit(1_000_000)


@dataclass(slots=True)
class ParsedRow:
    #: 1-based spreadsheet row number — the header is row 1, so data starts at row 2.
    row_number: int
    #: Canonical column name -> raw cell value.
    values: dict[str, str]
    #: The row exactly as it appeared, for the rejection report.
    raw: dict[str, str]
    #: Structural problems found while reading (ragged row, unparseable cells).
    issues: list[FieldIssue] = field(default_factory=list)

    def get(self, column: str) -> str:
        return self.values.get(column, "")


@dataclass(slots=True)
class ParseResult:
    rows: list[ParsedRow]
    #: Canonical headers that were present.
    headers: list[str]
    #: Raw header cells that were not recognised (ignored, reported as a warning).
    unknown_headers: list[str]
    #: Whole-file failure reason. When set, ``rows`` is empty and nothing may be imported.
    fatal_error: str | None = None
    fatal_code: str | None = None


class CsvParseError(Exception):
    """Raised for a whole-file failure. Row-level problems never use this."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def decode_upload(data: bytes) -> str:
    """Decode CSV bytes, tolerating a UTF-8 BOM and falling back for legacy encodings."""
    if not data:
        raise CsvParseError("EMPTY_FILE", "The uploaded file is empty.")

    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    # latin-1 cannot fail, so this is unreachable in practice.
    raise CsvParseError(
        "UNREADABLE_FILE",
        "The uploaded file could not be decoded as text. Save it as UTF-8 CSV and retry.",
    )


def _sniff_dialect(sample: str) -> type[csv.Dialect] | csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        # A single-column file (or one Sniffer can't read) is fine — assume a comma.
        return csv.excel


def parse_csv(text: str, *, max_rows: int) -> ParseResult:
    """Parse CSV text into addressable rows.

    Raises :class:`CsvParseError` only for whole-file problems: no content, no header row, or
    missing required headers.
    """
    stripped = text.strip()
    if not stripped:
        raise CsvParseError("EMPTY_FILE", "The uploaded file contains no data.")

    dialect = _sniff_dialect(stripped[:8192])
    reader = csv.reader(io.StringIO(text, newline=""), dialect)

    try:
        raw_header = next(reader)
    except StopIteration:
        raise CsvParseError("MISSING_HEADER_ROW", "The file has no header row.") from None
    except csv.Error as exc:
        raise CsvParseError(
            "MALFORMED_CSV", f"The file could not be parsed as CSV: {exc}."
        ) from exc

    # Resolve the header row -> canonical column names.
    canonical: list[str | None] = []
    unknown_headers: list[str] = []
    for cell in raw_header:
        name = normalise_header(cell)
        canonical.append(name)
        if name is None and cell.strip():
            unknown_headers.append(cell.strip())

    present = [name for name in canonical if name]
    duplicates = sorted({name for name in present if present.count(name) > 1})
    if duplicates:
        raise CsvParseError(
            "DUPLICATE_HEADERS",
            "The header row contains duplicate columns: " + ", ".join(duplicates) + ".",
        )

    missing = [header for header in REQUIRED_HEADERS if header not in present]
    if missing:
        raise CsvParseError(
            "MISSING_HEADERS",
            "The file is missing required column(s): "
            + ", ".join(missing)
            + ". Expected header: "
            + ", ".join(CSV_HEADERS)
            + ".",
        )

    rows: list[ParsedRow] = []
    row_number = 1  # the header occupies spreadsheet row 1

    while True:
        try:
            cells = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            # A malformed row must not kill the file: record it and carry on.
            row_number += 1
            rows.append(
                ParsedRow(
                    row_number=row_number,
                    values={},
                    raw={},
                    issues=[
                        FieldIssue(
                            field="row",
                            code="MALFORMED_CSV_ROW",
                            message=f"The row could not be parsed as CSV: {exc}.",
                        )
                    ],
                )
            )
            continue

        row_number += 1

        # Skip completely blank lines rather than reporting them as errors.
        if not any(cell.strip() for cell in cells):
            continue

        if len(rows) >= max_rows:
            raise CsvParseError(
                "TOO_MANY_ROWS",
                f"The file exceeds the maximum of {max_rows} data rows. Split it and retry.",
            )

        issues: list[FieldIssue] = []
        if len(cells) > len(canonical):
            issues.append(
                FieldIssue(
                    field="row",
                    code="ROW_HAS_EXTRA_COLUMNS",
                    message=(
                        f"The row has {len(cells)} values but the header defines "
                        f"{len(canonical)} columns. Check for an unescaped comma or quote."
                    ),
                )
            )

        values: dict[str, str] = {}
        raw: dict[str, str] = {}
        for index, name in enumerate(canonical):
            cell = cells[index] if index < len(cells) else ""
            header_label = raw_header[index].strip() if index < len(raw_header) else f"col{index}"
            raw[header_label or f"col{index}"] = cell
            if name:
                values[name] = cell

        rows.append(ParsedRow(row_number=row_number, values=values, raw=raw, issues=issues))

    if not rows:
        raise CsvParseError(
            "NO_DATA_ROWS", "The file has a valid header but contains no data rows."
        )

    return ParseResult(rows=rows, headers=present, unknown_headers=unknown_headers)


# ---------------------------------------------------------------------------
# Multi-value field helpers
# ---------------------------------------------------------------------------

PIPE = "|"


def split_list(value: str) -> list[str]:
    """Split a pipe-separated cell, dropping empty segments."""
    if not value:
        return []
    return [part.strip() for part in value.split(PIPE) if part.strip()]


def split_label_text(value: str) -> tuple[str, str] | None:
    """Split ``LABEL:Text`` on the FIRST colon.

    Returns ``None`` when there is no colon at all, so the caller can report a precise error.
    Option text may itself contain colons.
    """
    if ":" not in value:
        return None
    label, _, text = value.partition(":")
    return label.strip(), text.strip()
