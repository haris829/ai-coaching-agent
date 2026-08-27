"""Contract conformance for every registered ``SummaryGenerator``.

The contract a generator must satisfy is mostly a contract about restraint: it
returns the four sections, it stays inside the specified bounds, and every
element it returns can be traced to the session data it was given. This suite
drives each registered generator with real :class:`SessionData` fixtures and
puts its output through the same grounding check the service uses.

A real generator registered here is held to exactly this standard, which is the
point: the check does not become laxer because the implementation got cleverer.
"""

from __future__ import annotations

import pytest

from tests.conformance.kit import build_adapter, parametrized_over, profile_for
from tests.support.factories import (
    make_session_data,
    multi_topic_session_data,
    no_citation_session_data,
    single_topic_session_data,
)
from uc09_summary.domain.grounding import (
    MAX_KEY_CONCEPTS,
    MAX_NEXT_STEPS,
    check_grounding,
)
from uc09_summary.domain.models import SummaryContent
from uc09_summary.ports import SummaryGenerator

PORT = "summary_generator"

_CASES = {
    "multi_topic": multi_topic_session_data,
    "single_topic": single_topic_session_data,
    "no_citations": no_citation_session_data,
}


@parametrized_over(PORT)
class TestSummaryGeneratorContract:
    def _adapter(self, adapter_name: str):
        adapter = build_adapter(PORT, adapter_name)
        return adapter, profile_for(adapter, PORT, adapter_name)

    def test_satisfies_the_port_protocol(self, adapter_name: str) -> None:
        adapter, _ = self._adapter(adapter_name)
        assert isinstance(adapter, SummaryGenerator)

    @pytest.mark.parametrize("case", sorted(_CASES))
    def test_returns_summary_content(self, adapter_name: str, case: str) -> None:
        adapter, _ = self._adapter(adapter_name)
        content = adapter.generate(_CASES[case]())
        assert isinstance(content, SummaryContent)

    @pytest.mark.parametrize("case", sorted(_CASES))
    def test_output_is_grounded_in_the_session_data(
        self, adapter_name: str, case: str
    ) -> None:
        adapter, _ = self._adapter(adapter_name)
        data = _CASES[case]()
        # Raises GroundingViolation if any element cannot be traced.
        check_grounding(adapter.generate(data), data)

    @pytest.mark.parametrize("case", sorted(_CASES))
    def test_respects_the_specified_section_bounds(
        self, adapter_name: str, case: str
    ) -> None:
        adapter, _ = self._adapter(adapter_name)
        content = adapter.generate(_CASES[case]())
        assert len(content.key_concepts) <= MAX_KEY_CONCEPTS
        assert len(content.next_steps) <= MAX_NEXT_STEPS

    def test_does_not_pad_a_single_topic_session(self, adapter_name: str) -> None:
        adapter, _ = self._adapter(adapter_name)
        content = adapter.generate(single_topic_session_data())
        assert len(content.topics_covered) == 1, (
            "A single-topic session produces a single-topic summary. Depth goes "
            "into the key concepts; the topic list is never inflated."
        )

    def test_does_not_invent_an_authority_when_none_was_cited(
        self, adapter_name: str
    ) -> None:
        adapter, _ = self._adapter(adapter_name)
        content = adapter.generate(no_citation_session_data())
        assert content.resources_referenced == (), (
            "Nothing was cited in this session, so nothing may be listed. Not "
            "an authority relevant to the topic, not one the learner ought to "
            "read."
        )

    def test_empty_session_produces_empty_sections_not_invented_ones(
        self, adapter_name: str
    ) -> None:
        adapter, _ = self._adapter(adapter_name)
        data = make_session_data(interactions=(), citations=(), gap_suggestions=None)
        content = adapter.generate(data)
        assert content.topics_covered == ()
        assert content.key_concepts == ()
        assert content.resources_referenced == ()
        assert content.next_steps == ()

    def test_is_deterministic(self, adapter_name: str) -> None:
        adapter, _ = self._adapter(adapter_name)
        data = multi_topic_session_data()
        first = adapter.generate(data)
        second = adapter.generate(data)
        assert first == second, (
            "The same session must always produce the same summary. A document "
            "of record that changes between regenerations cannot be verified "
            "against the session it describes."
        )
