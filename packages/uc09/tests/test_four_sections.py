"""Every generated summary contains exactly the four sections.

Present in every summary, in every mode, for every scenario - including the
scenarios where a section is legitimately empty. An empty section says why it
is empty; it never simply disappears, because an absent section reads as an
oversight rather than as a fact about the session.
"""

from __future__ import annotations

import pytest

from tests.support.harness import build_harness
from uc09_summary.adapters.mock import scenarios as S
from uc09_summary.adapters.mock.generator import UnavailableGenerator
from uc09_summary.api.sections import SECTION_DESCRIPTORS
from uc09_summary.domain.enums import SourceStatus
from uc09_summary.rendering.html_document import CANONICAL_SECTION_TITLES, build_html

SECTION_KEYS = ("topics_covered", "key_concepts", "resources_referenced", "next_steps")

SCENARIOS = [
    S.SESSION_COMPLETE,
    S.SESSION_IN_PROGRESS,
    S.SESSION_SINGLE_TOPIC,
    S.SESSION_ONE_INTERACTION,
    S.SESSION_NO_INTERACTIONS,
    S.SESSION_NO_CITATIONS,
    S.SESSION_CITATIONS_UNAVAILABLE,
    S.SESSION_NO_GAP_SUGGESTIONS,
    S.SESSION_GAP_UNAVAILABLE,
    S.SESSION_INTERACTIONS_UNAVAILABLE,
    S.SESSION_INVALID_NARIC,
]


@pytest.mark.parametrize("session_id", SCENARIOS)
class TestFourSectionsAlwaysPresent:
    def test_record_carries_all_four_section_fields(self, session_id: str) -> None:
        harness = build_harness()
        record = harness.service.generate(session_id, S.owner_of(session_id))

        for key in SECTION_KEYS:
            assert hasattr(record, key)
            assert isinstance(getattr(record, key), tuple)

    def test_record_carries_a_status_for_every_section(self, session_id: str) -> None:
        harness = build_harness()
        record = harness.service.generate(session_id, S.owner_of(session_id))

        for key in SECTION_KEYS:
            assert key in record.source_status
            assert isinstance(record.source_status[key], SourceStatus)

    def test_html_renders_all_four_headings(self, session_id: str) -> None:
        harness = build_harness()
        record = harness.service.generate(session_id, S.owner_of(session_id))
        html = build_html(record)

        for title in CANONICAL_SECTION_TITLES:
            assert f"<h2>{title}</h2>" in html

    def test_api_describes_all_four_sections_in_order(self, session_id: str) -> None:
        harness = build_harness()
        record = harness.service.generate(session_id, S.owner_of(session_id))

        response = harness.client.get(
            f"/api/v1/summaries/{record.summary_id}",
            headers=harness.as_user(S.owner_of(session_id)),
        )
        assert response.status_code == 200
        sections = response.json()["sections"]
        assert [s["key"] for s in sections] == [d[0] for d in SECTION_DESCRIPTORS]


class TestSectionContentIsTraceable:
    def test_each_section_reports_its_own_item_count(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)

        response = harness.client.get(
            f"/api/v1/summaries/{record.summary_id}",
            headers=harness.as_user(S.OWNER_USER_ID),
        )
        by_key = {s["key"]: s for s in response.json()["sections"]}

        assert by_key["topics_covered"]["item_count"] == len(record.topics_covered)
        assert by_key["key_concepts"]["item_count"] == len(record.key_concepts)
        assert by_key["resources_referenced"]["item_count"] == len(
            record.resources_referenced
        )
        assert by_key["next_steps"]["item_count"] == len(record.next_steps)

    def test_key_concepts_are_between_three_and_five_when_material_allows(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        assert 3 <= len(record.key_concepts) <= 5

    def test_next_steps_are_two_to_three_when_material_allows(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        assert 2 <= len(record.next_steps) <= 3

    def test_every_concept_names_the_interactions_behind_it(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        known = {i.interaction_id for i in S.INTERACTIONS[S.SESSION_COMPLETE]}

        for concept in record.key_concepts:
            assert concept.evidence_interaction_ids
            assert set(concept.evidence_interaction_ids) <= known


class TestNextStepsIsVisiblyForwardLooking:
    """The one forward-looking section must be distinguishable from the other three."""

    def test_html_marks_orientation_on_every_section(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        html = build_html(record)

        assert html.count('data-orientation="retrospective"') >= 3
        assert 'data-orientation="forward-looking"' in html

    def test_html_says_in_words_that_next_steps_is_not_a_record(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        html = build_html(record)

        assert (
            "Forward-looking: suggested future study. This section is not a "
            "record of what happened in this session." in html
        )

    def test_the_pdf_carries_the_same_distinction(self) -> None:
        from tests.support.documents import pdf_text_normalised

        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        result = harness.service.export(record.summary_id, S.OWNER_USER_ID)

        rendered = pdf_text_normalised(result.pdf or b"")
        assert "not a record of what happened in this session" in rendered
        assert "Record of this session." in rendered

    def test_api_exposes_orientation_as_data(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        response = harness.client.get(
            f"/api/v1/summaries/{record.summary_id}",
            headers=harness.as_user(S.OWNER_USER_ID),
        )
        by_key = {s["key"]: s["orientation"] for s in response.json()["sections"]}

        assert by_key["topics_covered"] == "retrospective"
        assert by_key["key_concepts"] == "retrospective"
        assert by_key["resources_referenced"] == "retrospective"
        assert by_key["next_steps"] == "forward_looking"


class TestFourSectionsSurviveTheFallback:
    def test_the_fallback_still_carries_all_four_headings(self) -> None:
        harness = build_harness(overrides={"summary_generator": UnavailableGenerator()})
        record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
        html = build_html(record)

        for title in CANONICAL_SECTION_TITLES:
            assert f"<h2>{title}</h2>" in html
        for key in SECTION_KEYS:
            assert key in record.source_status
