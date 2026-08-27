"""The three disclaimer enforcement layers, each tested on its own terms.

Layer 1  type level          - the response type cannot be constructed without it
Layer 2  serialisation       - an independent boundary check on the raw payload
Layer 3  output scan         - the exact string in emitted response bodies

Each layer is tested without relying on the others, because the point of having
three is that they fail independently.
"""

from __future__ import annotations

import dataclasses
import inspect
import re
from pathlib import Path

import pytest

from uc06.application import boundary
from uc06.domain.disclaimer import CANONICAL_DISCLAIMER, KNOWN_VARIANT_UC06_STEP5, is_canonical
from uc06.domain.enums import GuardClass, NaricLevel, NaricLevelSource, SourceStatus
from uc06.domain.errors import DisclaimerBoundaryFailure
from uc06.domain.responses import (
    CaseLinkedResponse,
    DisclaimedResponse,
    GeneralTopicResponse,
    SafeErrorResponse,
)

RESPONSE_TYPES = (CaseLinkedResponse, GeneralTopicResponse, SafeErrorResponse)


def build_case_linked(**overrides) -> CaseLinkedResponse:
    kwargs = dict(
        response_id="r1",
        session_id="s1",
        case_file_id="CASE-FULL-001",
        explanation_profile="advanced",
        naric_level=NaricLevel.LEVEL_7,
        naric_level_source=NaricLevelSource.RETRIEVED,
        content="Explanation body.",
        case_facts_referenced=("F-001",),
        guard_triggered=None,
        case_file_status=SourceStatus.AVAILABLE,
        learner_context_status=SourceStatus.AVAILABLE,
        topic_tag="duress",
    )
    kwargs.update(overrides)
    return CaseLinkedResponse(**kwargs)


# ---------------------------------------------------------------- LAYER 1
class TestLayerOneTypeLevel:
    def test_canonical_text_is_the_expected_string(self):
        assert CANONICAL_DISCLAIMER == (
            "This response is provided for educational and training purposes only. "
            "It does not constitute legal advice. Always consult a qualified legal "
            "professional before acting on any legal matter."
        )

    @pytest.mark.parametrize("response_type", RESPONSE_TYPES)
    def test_constructor_accepts_no_disclaimer_parameter(self, response_type):
        """There is no argument to pass, so there is no code path that omits it."""
        assert "disclaimer" not in inspect.signature(response_type).parameters

    @pytest.mark.parametrize("response_type", RESPONSE_TYPES)
    def test_disclaimer_field_is_init_false(self, response_type):
        field = {f.name: f for f in dataclasses.fields(response_type)}["disclaimer"]
        assert field.init is False

    def test_constructed_response_carries_the_canonical_text(self):
        assert is_canonical(build_case_linked().disclaimer)

    def test_passing_a_disclaimer_is_a_type_error(self):
        with pytest.raises(TypeError):
            build_case_linked(disclaimer="something else")

    @pytest.mark.parametrize("response_type", RESPONSE_TYPES)
    def test_response_types_are_frozen_so_there_is_no_setter(self, response_type):
        assert response_type.__dataclass_params__.frozen is True

    def test_disclaimer_cannot_be_reassigned(self):
        response = build_case_linked()
        with pytest.raises(dataclasses.FrozenInstanceError):
            response.disclaimer = ""

    def test_disclaimer_cannot_be_deleted(self):
        response = build_case_linked()
        with pytest.raises(dataclasses.FrozenInstanceError):
            del response.disclaimer

    def test_subclass_body_cannot_displace_it(self):
        """to_payload writes the disclaimer after the subclass body, so a body
        that tries to define its own is overwritten by the constant."""

        @dataclasses.dataclass(frozen=True)
        class Hostile(DisclaimedResponse):
            def _body(self):
                return {"disclaimer": "", "content": "x"}

        assert is_canonical(Hostile().to_payload()["disclaimer"])

    def test_the_constant_is_defined_exactly_once_in_source(self):
        """No duplicated literal: a second copy is a second thing that can drift."""
        needle = "It does not constitute legal advice. Always consult"
        hits = [
            path
            for path in Path("uc06").rglob("*.py")
            if needle in path.read_text(encoding="utf-8")
        ]
        assert hits == [Path("uc06/domain/disclaimer.py")]


# ---------------------------------------------------------------- LAYER 2
class TestLayerTwoBoundaryCheck:
    """Operates on a raw mapping, so it cannot be satisfied by the type layer."""

    def test_accepts_a_correct_payload(self):
        boundary.check_payload({"content": "x", "disclaimer": CANONICAL_DISCLAIMER})

    def test_absent_disclaimer_fails(self):
        with pytest.raises(DisclaimerBoundaryFailure) as exc:
            boundary.check_payload({"content": "x"})
        assert exc.value.reason == boundary.REASON_ABSENT
        assert exc.value.observed_present is False

    def test_empty_disclaimer_fails(self):
        with pytest.raises(DisclaimerBoundaryFailure) as exc:
            boundary.check_payload({"disclaimer": ""})
        assert exc.value.reason == boundary.REASON_EMPTY

    def test_non_string_disclaimer_fails(self):
        with pytest.raises(DisclaimerBoundaryFailure) as exc:
            boundary.check_payload({"disclaimer": True})
        assert exc.value.reason == boundary.REASON_WRONG_TYPE

    def test_the_uc06_step5_shortened_variant_is_refused_and_named(self):
        """The wording discrepancy in the scope document is recognised, and still
        refused: only the canonical text ships."""
        with pytest.raises(DisclaimerBoundaryFailure) as exc:
            boundary.check_payload({"disclaimer": KNOWN_VARIANT_UC06_STEP5})
        assert exc.value.reason == boundary.REASON_SHORTENED_VARIANT

    @pytest.mark.parametrize(
        "altered",
        [
            CANONICAL_DISCLAIMER.upper(),
            CANONICAL_DISCLAIMER.replace("legal advice", "advice"),
            CANONICAL_DISCLAIMER + " (optional)",
            "Note: " + CANONICAL_DISCLAIMER,
            CANONICAL_DISCLAIMER.replace(".", ""),
            CANONICAL_DISCLAIMER[:-1],
        ],
    )
    def test_altered_wording_fails(self, altered):
        with pytest.raises(DisclaimerBoundaryFailure) as exc:
            boundary.check_payload({"disclaimer": altered})
        assert exc.value.reason in {boundary.REASON_ALTERED, boundary.REASON_SHORTENED_VARIANT}

    def test_leading_or_trailing_whitespace_fails(self):
        with pytest.raises(DisclaimerBoundaryFailure):
            boundary.check_payload({"disclaimer": " " + CANONICAL_DISCLAIMER})

    @pytest.mark.parametrize(
        "key",
        ["suppress_disclaimer", "skip_disclaimer", "no_disclaimer", "disclaimer_enabled", "hide_disclaimer"],
    )
    def test_a_suppression_key_anywhere_in_the_payload_fails(self, key):
        with pytest.raises(DisclaimerBoundaryFailure) as exc:
            boundary.check_payload({"disclaimer": CANONICAL_DISCLAIMER, key: False})
        assert exc.value.reason == boundary.REASON_SUPPRESSION_KEY

    def test_legitimate_response_fields_are_not_mistaken_for_suppression(self):
        boundary.check_payload(
            {
                "disclaimer": CANONICAL_DISCLAIMER,
                "guard_triggered": "outcome_prediction",
                "explanation_profile": "basic",
                "naric_level": "LEVEL_5",
            }
        )

    def test_it_does_not_consult_the_response_object(self):
        """A well-formed response object cannot rescue a corrupted payload."""
        payload = build_case_linked().to_payload()
        del payload["disclaimer"]
        with pytest.raises(DisclaimerBoundaryFailure):
            boundary.check_payload(payload)


# ---------------------------------------------------------------- LAYER 3
class TestLayerThreeOutputScan:
    """Scans emitted HTTP response bodies. See test_output_scan.py for the
    exhaustive sweep across every path; these are the primitives."""

    def test_scan_finds_the_exact_string(self):
        assert boundary.scan_text_for_disclaimer(f'{{"disclaimer": "{CANONICAL_DISCLAIMER}"}}')

    def test_scan_rejects_a_near_miss(self):
        assert not boundary.scan_text_for_disclaimer(KNOWN_VARIANT_UC06_STEP5)

    def test_scan_is_not_fooled_by_whitespace_normalisation(self):
        squashed = re.sub(r"\s+", " ", CANONICAL_DISCLAIMER.replace(" ", "  "))
        assert boundary.scan_text_for_disclaimer(squashed) is (squashed == CANONICAL_DISCLAIMER)
