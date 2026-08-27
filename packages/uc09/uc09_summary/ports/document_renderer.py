"""DocumentRenderer port.

HTML is the canonical document. A renderer turns that one HTML document into
PDF bytes. It is never given the summary record, and it never composes content
of its own - which is what makes the HTML fallback identical to the PDF by
construction rather than by discipline.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DocumentRenderer(Protocol):
    """Renders the canonical HTML document to PDF bytes."""

    def html_to_pdf(self, html: str) -> bytes:
        """Render ``html`` to a PDF.

        Args:
            html: the canonical document. The complete and only input.

        Returns:
            PDF bytes beginning with the ``%PDF-`` header. Every text run in
            the source document must be present in the rendered output: the
            exported PDF is evidence, and a renderer that silently drops
            content produces a false record.

        Raises:
            ProviderUnavailable: the renderer could not run.
            ProviderTimeout: the renderer exceeded the configured deadline.

        A failure is never fatal to the learner. The service catches it and
        serves the canonical HTML, marked as PDF-unavailable.
        """
        ...
