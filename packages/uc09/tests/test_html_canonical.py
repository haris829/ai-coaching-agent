"""HTML is canonical; the PDF is a rendering of it.

The specification requires a printable HTML fallback when PDF generation
fails. Two independent renderers would drift, and the fallback is the path
nobody exercises until it matters. So the document is built once as HTML and
the PDF is rendered from that string.

These tests assert that structurally, not by inspection:

* the renderer receives exactly the canonical HTML;
* every text block of the canonical HTML is present in the PDF;
* every line of text in the PDF comes from the canonical HTML;
* the fallback carries the same content the PDF would have carried, with the
  notice as the only addition.

The last one is content equivalence, asserted against the real rendered bytes -
not merely that something was returned.
"""

from __future__ import annotations

import pytest

from tests.support.documents import pdf_text, pdf_text_normalised
from tests.support.harness import build_harness
from uc09_summary.adapters.mock import scenarios as S
from uc09_summary.adapters.mock.renderer import (
    FailingDocumentRenderer,
    TimingOutDocumentRenderer,
)
from uc09_summary.rendering.html_document import (
    PDF_UNAVAILABLE_NOTICE,
    build_html,
    with_pdf_unavailable_notice,
)
from uc09_summary.rendering.text_extract import extract_text_blocks

SCENARIOS = [
    S.SESSION_COMPLETE,
    S.SESSION_IN_PROGRESS,
    S.SESSION_SINGLE_TOPIC,
    S.SESSION_NO_CITATIONS,
    S.SESSION_ONE_INTERACTION,
    S.SESSION_NO_INTERACTIONS,
]


class TestThePdfIsRenderedFromTheCanonicalHtml:
    def test_the_renderer_is_handed_exactly_the_canonical_document(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        result = harness.service.export(record.summary_id, S.OWNER_USER_ID)

        assert harness.renderer.calls == [result.canonical_html]
        assert result.canonical_html == build_html(record), (
            "There is one document builder. The export must not compose a "
            "second version of the document for the PDF."
        )

    @pytest.mark.parametrize("session_id", SCENARIOS)
    def test_every_canonical_block_appears_in_the_pdf(self, session_id: str) -> None:
        harness = build_harness()
        record = harness.service.generate(session_id, S.owner_of(session_id))
        result = harness.service.export(record.summary_id, S.owner_of(session_id))

        rendered = pdf_text_normalised(result.pdf or b"")
        missing = [
            block
            for block in extract_text_blocks(result.canonical_html)
            if " ".join(block.split()) not in rendered
        ]
        assert not missing, f"Missing from the PDF: {missing[:3]}"

    @pytest.mark.parametrize("session_id", SCENARIOS)
    def test_the_pdf_contains_nothing_the_html_does_not(self, session_id: str) -> None:
        harness = build_harness()
        record = harness.service.generate(session_id, S.owner_of(session_id))
        result = harness.service.export(record.summary_id, S.owner_of(session_id))

        source = " ".join(" ".join(extract_text_blocks(result.canonical_html)).split())
        for line in pdf_text(result.pdf or b"").splitlines():
            normalised = " ".join(line.split())
            if normalised:
                assert normalised in source, (
                    f"The PDF says something the canonical document does not: "
                    f"{normalised[:80]!r}"
                )


class TestTheFallbackCarriesTheSameContent:
    """Content equivalence between the fallback HTML and the PDF that failed."""

    @pytest.mark.parametrize(
        "renderer", [FailingDocumentRenderer, TimingOutDocumentRenderer]
    )
    @pytest.mark.parametrize("session_id", SCENARIOS)
    def test_fallback_html_equals_the_canonical_document_plus_one_notice(
        self, renderer: type, session_id: str
    ) -> None:
        harness = build_harness(overrides={"document_renderer": renderer()})
        record = harness.service.generate(session_id, S.owner_of(session_id))
        result = harness.service.export(record.summary_id, S.owner_of(session_id))

        assert result.pdf is None
        assert result.pdf_available is False

        canonical_blocks = extract_text_blocks(result.canonical_html)
        fallback_blocks = extract_text_blocks(result.html)

        assert fallback_blocks == canonical_blocks + [PDF_UNAVAILABLE_NOTICE], (
            "The fallback must be the canonical document plus the notice, and "
            "nothing else - no reordering, no omission, no substitution."
        )

    @pytest.mark.parametrize("session_id", SCENARIOS)
    def test_fallback_content_matches_what_the_pdf_would_have_carried(
        self, session_id: str
    ) -> None:
        """The core equivalence assertion, against real rendered bytes.

        Render the same summary twice - once with a working renderer, once with
        a failing one - and compare the text of the PDF with the text of the
        fallback HTML. Everything in the PDF must be in the fallback.
        """
        owner = S.owner_of(session_id)

        working = build_harness()
        record = working.service.generate(session_id, owner)
        pdf_result = working.service.export(record.summary_id, owner)
        assert pdf_result.pdf is not None

        failing = build_harness(
            overrides={"document_renderer": FailingDocumentRenderer()}
        )
        same_record = failing.service.generate(session_id, owner)
        html_result = failing.service.export(same_record.summary_id, owner)
        assert html_result.pdf is None

        # Both documents describe the same session; only the ids differ.
        fallback_text = " ".join(extract_text_blocks(html_result.html))
        fallback_text = fallback_text.replace(
            same_record.summary_id, record.summary_id
        )
        fallback_text = " ".join(fallback_text.split())

        for line in pdf_text(pdf_result.pdf).splitlines():
            normalised = " ".join(line.split())
            if normalised:
                assert normalised in fallback_text, (
                    "The PDF carried content the printable fallback does not: "
                    f"{normalised[:80]!r}. The fallback must be the same "
                    "document, not a reduced one."
                )

    def test_the_notice_is_additive_and_says_what_happened(self) -> None:
        harness = build_harness(
            overrides={"document_renderer": FailingDocumentRenderer()}
        )
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        result = harness.service.export(record.summary_id, S.OWNER_USER_ID)

        assert PDF_UNAVAILABLE_NOTICE in result.html
        assert PDF_UNAVAILABLE_NOTICE not in result.canonical_html
        assert result.canonical_html.replace("</body>", "") in result.html.replace(
            "</body>", ""
        ).replace(
            f'<p class="banner" data-role="pdf-unavailable">{PDF_UNAVAILABLE_NOTICE}</p>\n',
            "",
        )

    def test_the_learner_is_never_blocked_from_their_record(self) -> None:
        harness = build_harness(
            overrides={"document_renderer": FailingDocumentRenderer()}
        )
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        response = harness.client.get(
            f"/api/v1/summaries/{record.summary_id}/pdf",
            headers=harness.as_user(S.OWNER_USER_ID),
        )

        assert response.status_code == 200
        assert response.headers["x-pdf-available"] == "false"
        assert "text/html" in response.headers["content-type"]
        assert PDF_UNAVAILABLE_NOTICE in response.text


class TestNoticeHelper:
    def test_the_notice_is_inserted_before_the_body_close(self) -> None:
        html = "<html><body><p>content</p></body></html>"
        assert with_pdf_unavailable_notice(html).index(
            PDF_UNAVAILABLE_NOTICE
        ) < with_pdf_unavailable_notice(html).index("</body>")

    def test_a_document_without_a_body_close_still_gets_the_notice(self) -> None:
        assert PDF_UNAVAILABLE_NOTICE in with_pdf_unavailable_notice("<p>x</p>")
