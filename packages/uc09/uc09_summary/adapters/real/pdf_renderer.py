"""Working ``DocumentRenderer``: canonical HTML to a real PDF, no native dependencies.

This is a shipped implementation, not a stub. It produces a standards-conformant
PDF 1.4 file with uncompressed Type1 text runs, which means the exported bytes
can be opened by any reader **and** read back by any PDF library - so the tests
assert the required fields are present *in the rendered document*, not merely
that they were handed to a renderer.

It renders by laying out the ordered text blocks of the canonical HTML
(:func:`extract_text_blocks`). It composes nothing of its own. Every word in
the PDF came from the HTML, and no word in the HTML is dropped.

Deterministic: the same HTML always produces byte-identical output. There is no
creation timestamp and no random identifier, so a regenerated export of an
unchanged summary is bit-for-bit the same document.

Choosing a different renderer (a browser engine, a print service) is a registry
line - see ``registry.py`` and ``adapters/real/_template.py``.
"""

from __future__ import annotations

from uc09_summary.rendering.text_extract import extract_text_blocks

#: A4 in PostScript points.
PAGE_WIDTH = 595
PAGE_HEIGHT = 842
MARGIN_X = 56
MARGIN_TOP = 56
MARGIN_BOTTOM = 56

BODY_SIZE = 10
HEADING_SIZE = 13
LINE_GAP = 4

#: Helvetica advance widths for printable ASCII, in 1/1000 em. Used so that
#: wrapping matches what a reader will actually see.
_HELVETICA_WIDTHS: dict[str, int] = {
    " ": 278, "!": 278, '"': 355, "#": 556, "$": 556, "%": 889, "&": 667,
    "'": 191, "(": 333, ")": 333, "*": 389, "+": 584, ",": 278, "-": 333,
    ".": 278, "/": 278, ":": 278, ";": 278, "<": 584, "=": 584, ">": 584,
    "?": 556, "@": 1015, "[": 278, "\\": 278, "]": 278, "^": 469, "_": 556,
    "`": 333, "{": 334, "|": 260, "}": 334, "~": 584,
    "A": 667, "B": 667, "C": 722, "D": 722, "E": 667, "F": 611, "G": 778,
    "H": 722, "I": 278, "J": 500, "K": 667, "L": 556, "M": 833, "N": 722,
    "O": 778, "P": 667, "Q": 778, "R": 722, "S": 667, "T": 611, "U": 722,
    "V": 667, "W": 944, "X": 667, "Y": 667, "Z": 611,
    "a": 556, "b": 556, "c": 500, "d": 556, "e": 556, "f": 278, "g": 556,
    "h": 556, "i": 222, "j": 222, "k": 500, "l": 222, "m": 833, "n": 556,
    "o": 556, "p": 556, "q": 556, "r": 333, "s": 500, "t": 278, "u": 556,
    "v": 500, "w": 722, "x": 500, "y": 500, "z": 500,
}
_DIGIT_WIDTH = 556
_FALLBACK_WIDTH = 556


class SimplePdfRenderer:
    """Renders the canonical HTML document to PDF bytes.

    Args:
        page_width: page width in points.
        page_height: page height in points.
    """

    #: Registry construction contract. Every adapter exposes this.
    @classmethod
    def from_settings(cls, settings: object) -> SimplePdfRenderer:
        """Build from application settings. Takes nothing from configuration."""
        return cls()

    def __init__(self, page_width: int = PAGE_WIDTH, page_height: int = PAGE_HEIGHT) -> None:
        self.page_width = page_width
        self.page_height = page_height

    def html_to_pdf(self, html: str) -> bytes:
        """Render the canonical HTML document to a PDF.

        Args:
            html: the canonical document.

        Returns:
            PDF bytes. Every text block of the input appears in the output.
        """
        blocks = extract_text_blocks(html)
        lines = self._layout(blocks)
        pages = self._paginate(lines)
        return self._write_pdf(pages)

    # -- layout ------------------------------------------------------------

    def _layout(self, blocks: list[str]) -> list[tuple[str, int, bool]]:
        """Wrap blocks into ``(text, size, bold)`` lines, plus spacing markers."""
        usable = self.page_width - 2 * MARGIN_X
        out: list[tuple[str, int, bool]] = []
        for block in blocks:
            bold = _looks_like_heading(block)
            size = HEADING_SIZE if bold else BODY_SIZE
            for line in _wrap(block, usable, size):
                out.append((line, size, bold))
            out.append(("", BODY_SIZE, False))  # blank line between blocks
        return out

    def _paginate(self, lines: list[tuple[str, int, bool]]) -> list[list[tuple[str, int, bool]]]:
        pages: list[list[tuple[str, int, bool]]] = []
        current: list[tuple[str, int, bool]] = []
        y = self.page_height - MARGIN_TOP
        for text, size, bold in lines:
            step = size + LINE_GAP
            if y - step < MARGIN_BOTTOM and current:
                pages.append(current)
                current = []
                y = self.page_height - MARGIN_TOP
            current.append((text, size, bold))
            y -= step
        if current or not pages:
            pages.append(current)
        return pages

    # -- PDF assembly ------------------------------------------------------

    def _write_pdf(self, pages: list[list[tuple[str, int, bool]]]) -> bytes:
        objects: list[bytes] = []

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)  # 1-based object number

        catalog_num = add(b"")  # 1: placeholder, filled once Pages is known
        pages_num = add(b"")  # 2: placeholder
        font_regular = add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
            b"/Encoding /WinAnsiEncoding >>"
        )
        font_bold = add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
            b"/Encoding /WinAnsiEncoding >>"
        )

        page_nums: list[int] = []
        for page_lines in pages:
            stream = self._content_stream(page_lines)
            content_num = add(
                b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
                + stream
                + b"\nendstream"
            )
            page_num = add(
                b"<< /Type /Page /Parent "
                + str(pages_num).encode("ascii")
                + b" 0 R /MediaBox [0 0 "
                + f"{self.page_width} {self.page_height}".encode("ascii")
                + b"] /Resources << /Font << /F1 "
                + str(font_regular).encode("ascii")
                + b" 0 R /F2 "
                + str(font_bold).encode("ascii")
                + b" 0 R >> >> /Contents "
                + str(content_num).encode("ascii")
                + b" 0 R >>"
            )
            page_nums.append(page_num)

        kids = b" ".join(f"{n} 0 R".encode("ascii") for n in page_nums)
        objects[pages_num - 1] = (
            b"<< /Type /Pages /Kids [" + kids + b"] /Count "
            + str(len(page_nums)).encode("ascii")
            + b" >>"
        )
        objects[catalog_num - 1] = (
            b"<< /Type /Catalog /Pages " + str(pages_num).encode("ascii") + b" 0 R >>"
        )

        return _serialise(objects)

    def _content_stream(self, lines: list[tuple[str, int, bool]]) -> bytes:
        chunks: list[bytes] = [b"BT"]
        y = self.page_height - MARGIN_TOP
        for text, size, bold in lines:
            y -= size + LINE_GAP
            if not text:
                continue
            font = b"/F2" if bold else b"/F1"
            chunks.append(font + b" " + str(size).encode("ascii") + b" Tf")
            chunks.append(
                b"1 0 0 1 " + f"{MARGIN_X} {y}".encode("ascii") + b" Tm"
            )
            chunks.append(b"(" + _escape_pdf_text(text) + b") Tj")
        chunks.append(b"ET")
        return b"\n".join(chunks)


def _serialise(objects: list[bytes]) -> bytes:
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(index).encode("ascii") + b" 0 obj\n" + body + b"\nendobj\n"

    xref_offset = len(out)
    count = len(objects) + 1
    out += b"xref\n0 " + str(count).encode("ascii") + b"\n"
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        b"trailer\n<< /Size " + str(count).encode("ascii")
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(xref_offset).encode("ascii")
        + b"\n%%EOF\n"
    )
    return bytes(out)


def _escape_pdf_text(text: str) -> bytes:
    """Escape a string for a PDF literal, encoded WinAnsi (cp1252)."""
    encoded = text.encode("cp1252", errors="replace")
    out = bytearray()
    for byte in encoded:
        if byte in (0x28, 0x29, 0x5C):  # ( ) backslash
            out += b"\\" + bytes([byte])
        elif byte < 32 or byte > 126:
            out += f"\\{byte:03o}".encode("ascii")
        else:
            out.append(byte)
    return bytes(out)


def _char_width(char: str, size: int) -> float:
    if char.isdigit():
        width = _DIGIT_WIDTH
    else:
        width = _HELVETICA_WIDTHS.get(char, _FALLBACK_WIDTH)
    return width * size / 1000.0


def _text_width(text: str, size: int) -> float:
    return sum(_char_width(char, size) for char in text)


def _wrap(text: str, usable_width: int, size: int) -> list[str]:
    """Greedy word wrap using real Helvetica metrics.

    Every produced line is a contiguous run of the source block, so each PDF
    line remains a substring of the canonical text. The equivalence test relies
    on that property.
    """
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if _text_width(candidate, size) <= usable_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _looks_like_heading(block: str) -> bool:
    """Headings are rendered bold and larger. Presentation only; content is unchanged."""
    from uc09_summary.rendering.html_document import (
        CANONICAL_SECTION_TITLES,
        CPD_LABEL,
        PRODUCT_NAME,
    )

    return block in CANONICAL_SECTION_TITLES or block in (
        CPD_LABEL,
        PRODUCT_NAME,
        "Questions Asked",
    )
