"""Contract conformance for every registered ``DocumentRenderer``.

A renderer is trusted with a document of record, so the contract is strict on
one point above all: **no text may be dropped**. A renderer that silently omits
a section produces a PDF that misrepresents the session, and the omission is
invisible to everybody downstream.

The suite renders the canonical HTML of a real summary and reads the text back
out of the produced bytes with an independent PDF library, so it is checking
the artefact rather than the renderer own account of itself.
"""

from __future__ import annotations

import io

import pytest
from pypdf import PdfReader

from tests.conformance.kit import build_adapter, parametrized_over
from tests.support.documents import pdf_text
from tests.support.factories import multi_topic_session_data
from uc09_summary.adapters.mock.generator import DeterministicSummaryGenerator
from uc09_summary.ports import DocumentRenderer
from uc09_summary.rendering.html_document import (
    CANONICAL_SECTION_TITLES,
    CPD_LABEL,
    PRODUCT_NAME,
    build_html,
)
from uc09_summary.rendering.text_extract import extract_text_blocks

PORT = "document_renderer"


def _canonical_html() -> str:
    from tests.support.documents import summary_from_content

    data = multi_topic_session_data()
    content = DeterministicSummaryGenerator().generate(data)
    return build_html(summary_from_content(data, content))


@parametrized_over(PORT)
class TestDocumentRendererContract:
    def _adapter(self, adapter_name: str):
        return build_adapter(PORT, adapter_name)

    def test_satisfies_the_port_protocol(self, adapter_name: str) -> None:
        assert isinstance(self._adapter(adapter_name), DocumentRenderer)

    def test_returns_pdf_bytes(self, adapter_name: str) -> None:
        pdf = self._adapter(adapter_name).html_to_pdf(_canonical_html())
        assert isinstance(pdf, bytes)
        assert pdf.startswith(b"%PDF-"), "Output must be a PDF file."
        assert pdf.rstrip().endswith(b"%%EOF")

    def test_output_is_readable_by_an_independent_pdf_library(
        self, adapter_name: str
    ) -> None:
        pdf = self._adapter(adapter_name).html_to_pdf(_canonical_html())
        reader = PdfReader(io.BytesIO(pdf))
        assert len(reader.pages) >= 1

    def test_carries_every_text_block_of_the_source_document(
        self, adapter_name: str
    ) -> None:
        html = _canonical_html()
        pdf = self._adapter(adapter_name).html_to_pdf(html)
        rendered = " ".join(pdf_text(pdf).split())

        missing = [
            block
            for block in extract_text_blocks(html)
            if " ".join(block.split()) not in rendered
        ]
        assert not missing, (
            f"The renderer dropped {len(missing)} text block(s) from the "
            f"canonical document, beginning with {missing[0][:80]!r}. A PDF "
            "used as CPD evidence must carry the whole document."
        )

    def test_carries_the_required_export_fields(self, adapter_name: str) -> None:
        pdf = self._adapter(adapter_name).html_to_pdf(_canonical_html())
        rendered = " ".join(pdf_text(pdf).split())
        for required in (PRODUCT_NAME, CPD_LABEL, *CANONICAL_SECTION_TITLES):
            assert required in rendered, f"{required!r} missing from the PDF."

    def test_adds_nothing_that_was_not_in_the_document(self, adapter_name: str) -> None:
        html = _canonical_html()
        pdf = self._adapter(adapter_name).html_to_pdf(html)
        source = " ".join(" ".join(extract_text_blocks(html)).split())

        for line in pdf_text(pdf).splitlines():
            normalised = " ".join(line.split())
            if not normalised:
                continue
            assert normalised in source, (
                f"The PDF contains text that is not in the canonical document: "
                f"{normalised[:80]!r}. A renderer composes nothing of its own."
            )

    def test_is_deterministic(self, adapter_name: str) -> None:
        adapter = self._adapter(adapter_name)
        html = _canonical_html()
        assert adapter.html_to_pdf(html) == adapter.html_to_pdf(html), (
            "The same document must render to the same bytes, so that a "
            "regenerated export of an unchanged summary is the same document."
        )

    def test_empty_document_does_not_crash(self, adapter_name: str) -> None:
        pdf = self._adapter(adapter_name).html_to_pdf("<html><body></body></html>")
        assert pdf.startswith(b"%PDF-")


@pytest.mark.parametrize(
    "renderer_path",
    [
        "uc09_summary.adapters.mock.renderer:FailingDocumentRenderer",
        "uc09_summary.adapters.mock.renderer:TimingOutDocumentRenderer",
    ],
)
def test_failure_modes_raise_typed_contract_errors(renderer_path: str) -> None:
    """Documented failure modes raise the correct typed contract exception."""
    import importlib

    from uc09_summary.domain.errors import ProviderError

    module_path, _, attribute = renderer_path.partition(":")
    renderer = getattr(importlib.import_module(module_path), attribute)()
    with pytest.raises(ProviderError) as caught:
        renderer.html_to_pdf("<html><body><p>x</p></body></html>")
    assert caught.value.port == PORT
