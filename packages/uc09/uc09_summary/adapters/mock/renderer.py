"""Deterministic ``DocumentRenderer`` fakes: success, failure, timeout.

:class:`FakeDocumentRenderer` produces a real, parseable PDF - it delegates to
the shipped pure-Python renderer - so a test running against the fake still
asserts against genuine PDF bytes rather than a sentinel string. A fake that
returned ``b"pdf"`` would make every PDF assertion in the suite meaningless.
"""

from __future__ import annotations

from uc09_summary.adapters.real.pdf_renderer import SimplePdfRenderer
from uc09_summary.domain.errors import ProviderTimeout, ProviderUnavailable

PORT = "document_renderer"


class FakeDocumentRenderer:
    """Deterministic renderer for tests. Counts calls; output is a real PDF."""

    @classmethod
    def from_settings(cls, settings: object) -> FakeDocumentRenderer:
        return cls()

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._delegate = SimplePdfRenderer()

    def html_to_pdf(self, html: str) -> bytes:
        self.calls.append(html)
        return self._delegate.html_to_pdf(html)

    @classmethod
    def conformance_profile(cls) -> dict[str, object]:
        return {"upstream_tokens": ("FakeDocumentRenderer",)}


class FailingDocumentRenderer:
    """Always fails. Drives the printable-HTML fallback path."""

    @classmethod
    def from_settings(cls, settings: object) -> FailingDocumentRenderer:
        return cls()

    def __init__(self) -> None:
        self.calls: list[str] = []

    def html_to_pdf(self, html: str) -> bytes:
        self.calls.append(html)
        raise ProviderUnavailable(PORT, "scenario_renderer_unavailable")


class TimingOutDocumentRenderer:
    """Always times out."""

    @classmethod
    def from_settings(cls, settings: object) -> TimingOutDocumentRenderer:
        return cls()

    def __init__(self) -> None:
        self.calls: list[str] = []

    def html_to_pdf(self, html: str) -> bytes:
        self.calls.append(html)
        raise ProviderTimeout(PORT, "scenario_renderer_timeout")
