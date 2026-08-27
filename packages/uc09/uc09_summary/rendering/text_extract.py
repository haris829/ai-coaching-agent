"""Extract the readable text of the canonical HTML document, in order.

This is the bridge that makes HTML canonical in practice and not just in
principle. The PDF renderer lays out exactly these blocks, so the PDF cannot
contain a word the HTML does not, nor omit one the HTML has. The equivalence
test asserts that in both directions against the real rendered bytes.

Deliberately a small ordered-block extractor over ``html.parser`` rather than a
general HTML-to-text library: the input is one document produced by one module
in this repository, and a dependency-free extractor keeps the guarantee
inspectable.
"""

from __future__ import annotations

from html.parser import HTMLParser

#: Tags whose text content is never part of the readable document.
_SKIPPED = frozenset({"style", "script", "title", "head"})

#: Tags that end the current text block.
_BLOCK_TAGS = frozenset(
    {
        "p",
        "h1",
        "h2",
        "h3",
        "li",
        "dt",
        "dd",
        "section",
        "header",
        "footer",
        "div",
        "ul",
        "ol",
        "dl",
        "br",
        "tr",
    }
)


class _BlockExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._buffer: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in _SKIPPED:
            self._skip_depth += 1
            return
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if data.strip():
            self._buffer.append(data)

    def _flush(self) -> None:
        if not self._buffer:
            return
        text = " ".join("".join(self._buffer).split())
        self._buffer = []
        if text:
            self.blocks.append(text)

    def close(self) -> None:
        super().close()
        self._flush()


def extract_text_blocks(html: str) -> list[str]:
    """Return the readable text of ``html`` as ordered, whitespace-normalised blocks.

    Args:
        html: an HTML document.

    Returns:
        One string per readable block, in document order. Style, script and
        title content is excluded; a document rendered from these blocks says
        exactly what the HTML says.
    """
    parser = _BlockExtractor()
    parser.feed(html)
    parser.close()
    return parser.blocks


def extract_text(html: str) -> str:
    """Return the readable text of ``html`` as one whitespace-normalised string."""
    return " ".join(extract_text_blocks(html))
