"""Rendering: one canonical HTML document, and the text it contains.

The document is built **once**, as HTML. The PDF is a rendering of that same
HTML. There is no second renderer that composes content independently, so the
printable HTML fallback cannot drift from the PDF - not because two code paths
are kept in step by discipline, but because there is only one code path that
decides what the document says.
"""

from uc09_summary.rendering.html_document import (
    CANONICAL_SECTION_TITLES,
    CPD_LABEL,
    PARTIAL_MARKER,
    PDF_UNAVAILABLE_NOTICE,
    PRODUCT_NAME,
    build_html,
    with_pdf_unavailable_notice,
)
from uc09_summary.rendering.text_extract import extract_text_blocks

__all__ = [
    "CANONICAL_SECTION_TITLES",
    "CPD_LABEL",
    "PARTIAL_MARKER",
    "PDF_UNAVAILABLE_NOTICE",
    "PRODUCT_NAME",
    "build_html",
    "extract_text_blocks",
    "with_pdf_unavailable_notice",
]
