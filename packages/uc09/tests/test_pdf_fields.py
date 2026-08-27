"""Every required field is asserted in the rendered PDF, not at the renderer call.

The distinction matters. A test that checks the summary record carried a
session id proves the service intended to export it. Only reading the field
back out of the PDF bytes proves a solicitor holding that document can check it
against the platform record.

Text is extracted with ``pypdf`` - an independent library, not this repository
own extractor - so the assertions are about the artefact.
"""

from __future__ import annotations

import io

import pytest
from pypdf import PdfReader

from tests.support.documents import pdf_text_normalised
from tests.support.harness import build_harness
from uc09_summary.adapters.mock import scenarios as S
from uc09_summary.rendering.html_document import (
    CANONICAL_SECTION_TITLES,
    CPD_LABEL,
    PRODUCT_NAME,
)


@pytest.fixture
def exported():
    harness = build_harness()
    record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
    result = harness.service.export(record.summary_id, S.OWNER_USER_ID)
    assert result.pdf is not None
    return record, result, pdf_text_normalised(result.pdf)


class TestRequiredPdfFields:
    def test_it_is_a_real_pdf(self, exported) -> None:
        _, result, _ = exported
        assert result.pdf.startswith(b"%PDF-")
        assert PdfReader(io.BytesIO(result.pdf)).pages

    def test_branding(self, exported) -> None:
        _, _, text = exported
        assert PRODUCT_NAME in text

    def test_user_name(self, exported) -> None:
        record, _, text = exported
        assert record.user_display_name in text
        assert "Amara Osei" in text

    def test_session_date(self, exported) -> None:
        record, _, text = exported
        assert record.session_started_at.strftime("%Y-%m-%d") in text

    def test_session_duration(self, exported) -> None:
        record, _, text = exported
        total = record.session_duration_seconds
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        assert f"{hours}h {minutes:02d}m {seconds:02d}s" in text
        assert total == 47 * 60

    @pytest.mark.parametrize("title", CANONICAL_SECTION_TITLES)
    def test_all_four_section_headings(self, exported, title: str) -> None:
        _, _, text = exported
        assert title in text

    def test_cpd_learning_evidence_label(self, exported) -> None:
        _, _, text = exported
        assert CPD_LABEL in text
        assert "CPD Learning Evidence" in text

    def test_session_id_for_verification(self, exported) -> None:
        """The session id is what makes the document checkable. Required, not decorative."""
        record, _, text = exported
        assert record.session_id in text
        assert "Session ID for verification" in text

    def test_the_summary_id_is_present_too(self, exported) -> None:
        record, _, text = exported
        assert record.summary_id in text


class TestSectionContentReachesThePdf:
    def test_every_topic_label_is_in_the_pdf(self, exported) -> None:
        record, _, text = exported
        assert record.topics_covered
        for topic in record.topics_covered:
            assert topic.label in text

    def test_every_concept_label_and_explanation_is_in_the_pdf(self, exported) -> None:
        record, _, text = exported
        assert record.key_concepts
        for concept in record.key_concepts:
            assert concept.label in text
            assert " ".join(concept.explanation.split()) in text

    def test_every_cited_authority_is_in_the_pdf(self, exported) -> None:
        record, _, text = exported
        assert record.resources_referenced
        for resource in record.resources_referenced:
            assert resource.title in text
            assert resource.citation in text

    def test_every_next_step_is_in_the_pdf(self, exported) -> None:
        record, _, text = exported
        assert record.next_steps
        for suggestion in record.next_steps:
            assert suggestion.label in text


class TestPdfAcrossScenarios:
    @pytest.mark.parametrize(
        "session_id",
        [
            S.SESSION_COMPLETE,
            S.SESSION_IN_PROGRESS,
            S.SESSION_SINGLE_TOPIC,
            S.SESSION_NO_CITATIONS,
            S.SESSION_ONE_INTERACTION,
            S.SESSION_NO_INTERACTIONS,
            S.SESSION_INVALID_NARIC,
        ],
    )
    def test_the_required_fields_are_present_in_every_scenario(
        self, session_id: str
    ) -> None:
        owner = S.owner_of(session_id)
        harness = build_harness()
        record = harness.service.generate(session_id, owner)
        result = harness.service.export(record.summary_id, owner)
        text = pdf_text_normalised(result.pdf or b"")

        for required in (
            PRODUCT_NAME,
            CPD_LABEL,
            record.user_display_name,
            record.session_id,
            record.session_started_at.strftime("%Y-%m-%d"),
            *CANONICAL_SECTION_TITLES,
        ):
            assert required in text, f"{required!r} missing for {session_id}"


class TestPdfDelivery:
    def test_the_endpoint_returns_a_pdf_with_a_filename(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        response = harness.client.get(
            f"/api/v1/summaries/{record.summary_id}/pdf",
            headers=harness.as_user(S.OWNER_USER_ID),
        )

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert record.summary_id in response.headers["content-disposition"]
        assert response.content.startswith(b"%PDF-")

    def test_the_delivered_bytes_carry_the_required_fields(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        response = harness.client.get(
            f"/api/v1/summaries/{record.summary_id}/pdf",
            headers=harness.as_user(S.OWNER_USER_ID),
        )

        text = pdf_text_normalised(response.content)
        assert CPD_LABEL in text
        assert record.session_id in text

    def test_preview_is_available_before_download(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        preview = harness.client.get(
            f"/api/v1/summaries/{record.summary_id}/preview",
            headers=harness.as_user(S.OWNER_USER_ID),
        )

        assert preview.status_code == 200
        assert "text/html" in preview.headers["content-type"]
        assert CPD_LABEL in preview.text
        assert harness.downloads.for_summary(record.summary_id) == (), (
            "A preview is not a download and must not be logged as one."
        )

    def test_the_preview_and_the_pdf_describe_the_same_document(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        preview = harness.client.get(
            f"/api/v1/summaries/{record.summary_id}/preview",
            headers=harness.as_user(S.OWNER_USER_ID),
        ).text
        pdf = harness.client.get(
            f"/api/v1/summaries/{record.summary_id}/pdf",
            headers=harness.as_user(S.OWNER_USER_ID),
        ).content

        from uc09_summary.rendering.text_extract import extract_text_blocks

        rendered = pdf_text_normalised(pdf)
        for block in extract_text_blocks(preview):
            assert " ".join(block.split()) in rendered
