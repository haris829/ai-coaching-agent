"""Integration swap proof: the unmodified service against a foreign upstream.

The LexPortal adapter family talks to a fictional system that resembles the
mocks in nothing:

======================= ============================ ========================
Concept                 Mock                         LexPortal
======================= ============================ ========================
identifier              ``sess-complete-multi-topic`` ``LP-SESS-0001``
envelope                none                         ``payload`` / ``result``
timestamps              ISO datetimes                epoch milliseconds
study level             ``level_7``                  ``RQF-7``
session state           ``completed``                ``FINISHED``
course progress         integer 62                   ratio 0.62
tags                    ``unfair-dismissal``         ``UNFAIR_DISMISSAL``
resource kind           ``legislation``              ``STATUTE``
suggestion fields       ``label`` / ``rationale``    ``headline`` / ``because``
errors                  contract errors              ``LexPortalError``
======================= ============================ ========================

Nothing in the service, the domain, the rendering layer, the API or the
persistence layer knows any of that. Swapping to it is four settings values and
no code change, and these tests show the results are correct on both.

Replaceability is demonstrated here, not asserted.
"""

from __future__ import annotations

import pytest

from tests.support.documents import pdf_text_normalised
from tests.support.harness import build_harness
from uc09_summary.adapters.foreign import lexportal_client as lp
from uc09_summary.adapters.mock import scenarios as S
from uc09_summary.domain.enums import (
    GenerationMode,
    NaricLevel,
    NaricLevelSource,
    ResourceKind,
    SessionStatus,
    SourceStatus,
    SuggestionSource,
)
from uc09_summary.rendering.html_document import CPD_LABEL, PARTIAL_MARKER, PRODUCT_NAME

FOREIGN = {
    "session_provider": "foreign",
    "interaction_provider": "foreign",
    "citation_provider": "foreign",
    "gap_report_provider": "foreign",
}


@pytest.fixture
def foreign_harness():
    return build_harness(**FOREIGN)


class TestTheSwapIsConfigurationOnly:
    def test_the_container_wires_the_foreign_family_from_settings_alone(
        self, foreign_harness
    ) -> None:
        from uc09_summary.adapters.foreign.session import ForeignSessionProvider

        assert isinstance(
            foreign_harness.container.providers["session_provider"],
            ForeignSessionProvider,
        )

    def test_the_service_object_is_the_same_class_in_both_configurations(
        self, foreign_harness
    ) -> None:
        assert type(foreign_harness.service) is type(build_harness().service), (
            "The service is not specialised per provider. That is what makes "
            "the swap a configuration change."
        )


class TestTheServiceProducesCorrectResultsAgainstTheForeignUpstream:
    def test_a_complete_session_summarises_correctly(self, foreign_harness) -> None:
        record = foreign_harness.service.generate(lp.SESSION_OK, lp.LEARNER_OK)

        assert record.generation_mode is GenerationMode.GENERATED
        assert record.is_partial is False
        assert record.session_status is SessionStatus.SUMMARY_GENERATED
        assert record.session_id == lp.SESSION_OK
        assert record.user_id == lp.LEARNER_OK
        assert record.user_display_name == "Amara Osei"

    def test_values_arrive_normalised_to_the_platform_contract(
        self, foreign_harness
    ) -> None:
        record = foreign_harness.service.generate(lp.SESSION_OK, lp.LEARNER_OK)

        assert record.naric_level is NaricLevel.LEVEL_7  # from "RQF-7"
        assert record.naric_level_source is NaricLevelSource.RETRIEVED
        assert record.explanation_profile == "advanced"
        assert record.session_duration_seconds == 47 * 60  # from epoch milliseconds

    def test_tags_are_normalised_so_grounding_still_works(
        self, foreign_harness
    ) -> None:
        record = foreign_harness.service.generate(lp.SESSION_OK, lp.LEARNER_OK)

        assert [t.topic_id for t in record.topics_covered] == [
            "unfair-dismissal",
            "remedies",
            "whistleblowing",
        ]

    def test_resource_kinds_are_translated(self, foreign_harness) -> None:
        record = foreign_harness.service.generate(lp.SESSION_OK, lp.LEARNER_OK)
        kinds = {r.resource_id: r.kind for r in record.resources_referenced}

        assert kinds["UK.ERA1996.S98"] is ResourceKind.LEGISLATION  # STATUTE
        assert kinds["UK.ICELAND.1983"] is ResourceKind.CASE_LAW  # JUDGMENT

    def test_gap_suggestions_are_mapped_and_grounded(self, foreign_harness) -> None:
        record = foreign_harness.service.generate(lp.SESSION_OK, lp.LEARNER_OK)

        assert [s.suggestion_id for s in record.next_steps] == [
            "LP-REC-TUPE",
            "LP-REC-DISCRIM",
        ]
        assert all(s.source is SuggestionSource.GAP_REPORT for s in record.next_steps)


class TestEveryBehaviourHoldsOnBothFamilies:
    def test_grounding_still_rejects_a_fabrication(self, foreign_harness) -> None:
        from uc09_summary.adapters.mock.generator import UngroundedAuthorityGenerator

        harness = build_harness(
            overrides={"summary_generator": UngroundedAuthorityGenerator()}, **FOREIGN
        )
        record = harness.service.generate(lp.SESSION_OK, lp.LEARNER_OK)

        assert record.generation_mode is GenerationMode.QUESTION_LOG_FALLBACK
        assert record.source_status["summary_generator"] is SourceStatus.INVALID
        assert all("Polkey" not in r.title for r in record.resources_referenced)

    def test_a_single_topic_foreign_session_is_not_padded(self, foreign_harness) -> None:
        record = foreign_harness.service.generate(
            lp.SESSION_SINGLE_TOPIC, lp.LEARNER_OK
        )

        assert len(record.topics_covered) == 1
        assert record.topics_covered[0].topic_id == "restrictive-covenants"

    def test_a_foreign_session_that_cited_nothing_lists_nothing(
        self, foreign_harness
    ) -> None:
        record = foreign_harness.service.generate(
            lp.SESSION_NO_AUTHORITIES, lp.LEARNER_NO_RECOMMENDATIONS
        )

        assert record.resources_referenced == ()
        assert record.source_status["citations"] is SourceStatus.EMPTY

    def test_a_live_foreign_session_produces_a_marked_partial_summary(
        self, foreign_harness
    ) -> None:
        record = foreign_harness.service.generate(lp.SESSION_LIVE, lp.LEARNER_OK)
        result = foreign_harness.service.export(record.summary_id, lp.LEARNER_OK)

        assert record.is_partial is True
        assert PARTIAL_MARKER in pdf_text_normalised(result.pdf or b"")

    def test_an_unmappable_foreign_level_becomes_the_default_marked_invalid(
        self, foreign_harness
    ) -> None:
        record = foreign_harness.service.generate(lp.SESSION_BAD_TIER, lp.LEARNER_OK)

        assert record.naric_level is NaricLevel.LEVEL_5
        assert record.naric_level_source is NaricLevelSource.DEFAULT
        assert record.source_status["naric_level"] is SourceStatus.INVALID

    def test_ownership_is_enforced_against_foreign_identifiers(
        self, foreign_harness
    ) -> None:
        from uc09_summary.domain.errors import AccessDenied

        with pytest.raises(AccessDenied):
            foreign_harness.service.generate(lp.SESSION_OK, "LP-USER-somebody-else")

    def test_an_unavailable_foreign_upstream_surfaces_as_a_contract_error(
        self, foreign_harness
    ) -> None:
        from uc09_summary.domain.errors import ProviderUnavailable

        with pytest.raises(ProviderUnavailable):
            foreign_harness.service.generate(lp.SESSION_DOWN, lp.LEARNER_OK)

    def test_a_missing_foreign_session_surfaces_as_not_found(
        self, foreign_harness
    ) -> None:
        from uc09_summary.domain.errors import SessionNotFound

        with pytest.raises(SessionNotFound):
            foreign_harness.service.generate(lp.SESSION_ABSENT, lp.LEARNER_OK)


class TestTheExportIsIdenticalInKind:
    def test_the_foreign_pdf_carries_every_required_field(
        self, foreign_harness
    ) -> None:
        record = foreign_harness.service.generate(lp.SESSION_OK, lp.LEARNER_OK)
        result = foreign_harness.service.export(record.summary_id, lp.LEARNER_OK)
        text = pdf_text_normalised(result.pdf or b"")

        for required in (
            PRODUCT_NAME,
            CPD_LABEL,
            "Amara Osei",
            lp.SESSION_OK,
            "Topics Covered",
            "Key Concepts",
            "Resources Referenced",
            "Recommended Next Steps",
        ):
            assert required in text

    def test_the_html_fallback_equivalence_holds_on_the_foreign_family(self) -> None:
        from uc09_summary.adapters.mock.renderer import FailingDocumentRenderer
        from uc09_summary.rendering.html_document import PDF_UNAVAILABLE_NOTICE
        from uc09_summary.rendering.text_extract import extract_text_blocks

        harness = build_harness(
            overrides={"document_renderer": FailingDocumentRenderer()}, **FOREIGN
        )
        record = harness.service.generate(lp.SESSION_OK, lp.LEARNER_OK)
        result = harness.service.export(record.summary_id, lp.LEARNER_OK)

        assert extract_text_blocks(result.html) == extract_text_blocks(
            result.canonical_html
        ) + [PDF_UNAVAILABLE_NOTICE]

    def test_no_upstream_detail_reaches_the_exported_document(
        self, foreign_harness
    ) -> None:
        record = foreign_harness.service.generate(lp.SESSION_OK, lp.LEARNER_OK)
        result = foreign_harness.service.export(record.summary_id, lp.LEARNER_OK)
        text = pdf_text_normalised(result.pdf or b"")

        for token in (
            "lexportal",
            "LexPortal",
            "academicTier",
            "RQF-7",
            "FINISHED",
            "openedAtEpochMs",
            "STATUTE",
            "JUDGMENT",
            "headline",
            "progressRatio",
        ):
            assert token not in text, (
                f"{token!r} escaped the adapter and reached the CPD document."
            )


class TestTheApiIsUnchanged:
    def test_the_same_endpoints_work_against_the_foreign_family(self) -> None:
        harness = build_harness(**FOREIGN)

        created = harness.client.post(
            f"/api/v1/sessions/{lp.SESSION_OK}/summary",
            headers=harness.as_user(lp.LEARNER_OK),
            json={},
        )
        assert created.status_code == 201
        summary_id = created.json()["summary_id"]

        assert (
            harness.client.get(
                f"/api/v1/summaries/{summary_id}",
                headers=harness.as_user(lp.LEARNER_OK),
            ).status_code
            == 200
        )
        assert (
            harness.client.get(
                f"/api/v1/summaries/{summary_id}/preview",
                headers=harness.as_user(lp.LEARNER_OK),
            ).status_code
            == 200
        )
        pdf = harness.client.get(
            f"/api/v1/summaries/{summary_id}/pdf",
            headers=harness.as_user(lp.LEARNER_OK),
        )
        assert pdf.status_code == 200
        assert pdf.content.startswith(b"%PDF-")

    def test_the_response_shape_is_identical_across_families(self) -> None:
        mock_harness = build_harness()
        foreign = build_harness(**FOREIGN)

        mock_record = mock_harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        foreign_record = foreign.service.generate(lp.SESSION_OK, lp.LEARNER_OK)

        mock_body = mock_harness.client.get(
            f"/api/v1/summaries/{mock_record.summary_id}",
            headers=mock_harness.as_user(S.OWNER_USER_ID),
        ).json()
        foreign_body = foreign.client.get(
            f"/api/v1/summaries/{foreign_record.summary_id}",
            headers=foreign.as_user(lp.LEARNER_OK),
        ).json()

        assert set(mock_body) == set(foreign_body), (
            "A consumer of this API cannot tell which upstream is configured, "
            "which is the whole point of the boundary."
        )
