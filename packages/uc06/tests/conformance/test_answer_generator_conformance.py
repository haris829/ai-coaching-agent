"""AnswerGenerator conformance. Adapter-agnostic.

The contract a generator adapter must honour is narrow and strict, because
everything downstream treats its output as untrusted:

  * return a GenerationResult with string content and identifier-only citations
  * honour the deadline it is handed
  * never invent a fact identifier it was not given
  * never carry upstream vocabulary or provider names into its output
  * fail only with the three contract exceptions
"""

from __future__ import annotations

import inspect

import pytest

from uc06.application.fact_references import MARKER
from uc06.application.prompt_registry import PromptRegistry
from uc06.domain.enums import ExplanationProfile
from uc06.domain.errors import ProviderError
from uc06.domain.models import GenerationRequest, GenerationResult
from uc06.ports.generator import AnswerGenerator

LEAK_MARKERS = ("envelope", "sourceRef", "instructionBlock", "mattersphere", "TODO_", "<<ref:")

FACTS = (
    ("F-1", "The gate was opened from the inside at 23:41."),
    ("F-2", "No contact was made with the police beforehand."),
)


def _request(**overrides) -> GenerationRequest:
    prompt = PromptRegistry.for_case_linked(ExplanationProfile.INTERMEDIATE)
    kwargs = dict(
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.version,
        system_instructions=prompt.system_instructions,
        question_text="How does the defence of duress apply here?",
        profile=ExplanationProfile.INTERMEDIATE.value,
        practice_area="criminal",
        case_file_id="CONFORMANCE-1",
        available_fact_ids=tuple(fact_id for fact_id, _ in FACTS),
        fact_digest=FACTS,
        charges=("Robbery",),
        legislation=("Theft Act 1968, s.8",),
        timeout_ms=10_000,
    )
    kwargs.update(overrides)
    return GenerationRequest(**kwargs)


class TestShape:
    def test_it_satisfies_the_port(self, generator_adapter):
        assert isinstance(generator_adapter, AnswerGenerator)

    def test_the_signature_matches_the_port(self, generator_adapter):
        assert list(inspect.signature(generator_adapter.generate).parameters) == ["request"]


class TestReturnContract:
    def test_it_returns_the_platform_type(self, generator_adapter):
        result = generator_adapter.generate(_request())
        assert isinstance(result, GenerationResult)
        assert isinstance(result.content, str)
        assert result.content.strip()

    def test_citations_are_identifiers_only(self, generator_adapter):
        result = generator_adapter.generate(_request())
        assert isinstance(result.fact_ids_referenced, tuple)
        assert all(isinstance(fact_id, str) for fact_id in result.fact_ids_referenced)

    def test_it_never_cites_a_fact_it_was_not_given(self, generator_adapter):
        """An adapter must not invent an identifier. The service verifies this
        too, but an adapter that does it is defective at source."""
        request = _request()
        result = generator_adapter.generate(request)
        available = set(request.available_fact_ids)

        assert set(result.fact_ids_referenced) <= available
        in_text = {match.group("fact_id") for match in MARKER.finditer(result.content)}
        assert in_text <= available

    def test_it_uses_the_platform_marker_syntax(self, generator_adapter):
        """Whatever the upstream's own syntax, the adapter translates it."""
        result = generator_adapter.generate(_request())
        for marker in LEAK_MARKERS:
            assert marker not in result.content

    def test_it_is_deterministic_for_the_same_request(self, generator_adapter):
        first = generator_adapter.generate(_request())
        second = generator_adapter.generate(_request())
        assert first.content == second.content

    def test_it_works_with_no_facts_available(self, generator_adapter):
        """The general-topic path supplies no facts. No marker may appear."""
        result = generator_adapter.generate(_request(available_fact_ids=(), fact_digest=(), case_file_id=None))
        assert isinstance(result, GenerationResult)
        assert MARKER.search(result.content) is None


class TestBoundaryHygiene:
    def test_no_provider_name_appears_in_the_content(self, generator_adapter):
        result = generator_adapter.generate(_request())
        assert type(generator_adapter).__name__ not in result.content

    def test_failures_use_only_the_contract_exceptions(self, generator_adapter):
        """A generator handed an impossible request must fail by contract."""
        try:
            generator_adapter.generate(_request(timeout_ms=0, question_text=""))
        except ProviderError as exc:
            assert exc.port == "answer_generator"
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"uncontracted exception escaped: {type(exc).__name__}")

    def test_a_supplied_disclaimer_is_reported_not_smuggled(self, generator_adapter):
        """If an adapter surfaces model-written disclaimer text at all, it goes
        in supplied_disclaimer, where the service discards it."""
        result = generator_adapter.generate(_request())
        assert result.supplied_disclaimer is None or isinstance(result.supplied_disclaimer, str)


class TestPromptHandling:
    def test_the_request_carries_everything_the_adapter_needs(self, generator_adapter):
        """An adapter must read prompts only from the request: prompts are
        server-side and versioned, never adapter-local."""
        request = _request()
        assert request.system_instructions
        assert request.prompt_id and request.prompt_version
        generator_adapter.generate(request)

    def test_the_adapter_defines_no_prompt_of_its_own(self, generator_adapter):
        source = inspect.getsource(type(generator_adapter))
        for phrase in ("You are an educational", "system prompt", "You are a helpful"):
            assert phrase not in source
