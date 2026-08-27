"""NARIC levels, explanation profiles, and the lowercase enum rule."""

from __future__ import annotations

import pytest

from tests.support.factories import make_session
from tests.support.harness import build_harness
from uc09_summary.adapters.mock import scenarios as S
from uc09_summary.domain.enums import (
    ExplanationProfile,
    GenerationMode,
    NaricLevel,
    NaricLevelSource,
    ResourceKind,
    SessionStatus,
    SourceStatus,
    SuggestionSource,
)
from uc09_summary.domain.naric import (
    ASSUMED_PROFILE_LEVELS,
    DEFAULT_NARIC_LEVEL,
    explanation_profile_for,
    profile_is_assumed,
    resolve_naric_level,
)

ALL_ENUMS = [
    NaricLevel,
    NaricLevelSource,
    ExplanationProfile,
    SourceStatus,
    GenerationMode,
    SessionStatus,
    ResourceKind,
    SuggestionSource,
]


class TestTheNaricEnumIsClosed:
    def test_it_holds_exactly_the_six_specified_levels(self) -> None:
        assert [level.value for level in NaricLevel] == [
            "level_3",
            "level_4",
            "level_5",
            "level_6",
            "level_7",
            "level_7_plus",
        ]

    @pytest.mark.parametrize("enum", ALL_ENUMS)
    def test_every_emitted_value_is_lowercase(self, enum: type) -> None:
        for member in enum:
            assert member.value == member.value.lower()

    @pytest.mark.parametrize("enum", ALL_ENUMS)
    def test_member_names_may_be_uppercase(self, enum: type) -> None:
        for member in enum:
            assert member.name == member.name.upper()

    def test_the_source_vocabulary_is_exactly_the_five_specified_values(self) -> None:
        assert [status.value for status in SourceStatus] == [
            "available",
            "empty",
            "partial",
            "unavailable",
            "invalid",
        ]


class TestNaricResolution:
    @pytest.mark.parametrize(
        "raw",
        ["level_7", "LEVEL_7", " Level_7 ", NaricLevel.LEVEL_7],
    )
    def test_a_recognised_value_is_retrieved(self, raw: object) -> None:
        result = resolve_naric_level(raw)

        assert result.level is NaricLevel.LEVEL_7
        assert result.source is NaricLevelSource.RETRIEVED
        assert result.status is SourceStatus.AVAILABLE

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_an_absent_value_is_empty_not_invalid(self, raw: object) -> None:
        result = resolve_naric_level(raw)

        assert result.level is DEFAULT_NARIC_LEVEL
        assert result.source is NaricLevelSource.DEFAULT
        assert result.status is SourceStatus.EMPTY, (
            "The source answered and carried nothing. That is empty, and it is "
            "a different fact from an unmappable value."
        )

    @pytest.mark.parametrize("raw", [7, 3.5, "masters", "RQF-7", "high", True, ["level_7"]])
    def test_an_unmappable_value_is_invalid(self, raw: object) -> None:
        """Never an integer scale, never a pedagogic scale of our own invention."""
        result = resolve_naric_level(raw)

        assert result.level is NaricLevel.LEVEL_5
        assert result.source is NaricLevelSource.DEFAULT
        assert result.status is SourceStatus.INVALID

    def test_an_integer_is_never_read_as_a_level(self) -> None:
        assert resolve_naric_level(7).level is not NaricLevel.LEVEL_7

    def test_the_default_is_level_5(self) -> None:
        assert DEFAULT_NARIC_LEVEL is NaricLevel.LEVEL_5


class TestExplanationProfiles:
    @pytest.mark.parametrize(
        ("level", "profile"),
        [
            (NaricLevel.LEVEL_3, ExplanationProfile.BASIC),
            (NaricLevel.LEVEL_4, ExplanationProfile.BASIC),
            (NaricLevel.LEVEL_5, ExplanationProfile.INTERMEDIATE),
            (NaricLevel.LEVEL_6, ExplanationProfile.INTERMEDIATE),
            (NaricLevel.LEVEL_7, ExplanationProfile.ADVANCED),
            (NaricLevel.LEVEL_7_PLUS, ExplanationProfile.ADVANCED),
        ],
    )
    def test_the_mapping(self, level: NaricLevel, profile: ExplanationProfile) -> None:
        assert explanation_profile_for(level) is profile

    def test_the_mapping_is_total_over_the_enum(self) -> None:
        for level in NaricLevel:
            assert explanation_profile_for(level) in ExplanationProfile

    def test_levels_4_and_6_are_recorded_as_an_assumption(self) -> None:
        assert set(ASSUMED_PROFILE_LEVELS) == {NaricLevel.LEVEL_4, NaricLevel.LEVEL_6}
        assert profile_is_assumed(NaricLevel.LEVEL_4)
        assert profile_is_assumed(NaricLevel.LEVEL_6)
        assert not profile_is_assumed(NaricLevel.LEVEL_7)


class TestTheProfileShapesTheDocument:
    @pytest.mark.parametrize(
        ("level", "marker"),
        [
            (NaricLevel.LEVEL_3, "You looked at"),
            (NaricLevel.LEVEL_5, "You explored"),
            (NaricLevel.LEVEL_7, "You examined"),
        ],
    )
    def test_explanations_change_with_the_profile(
        self, level: NaricLevel, marker: str
    ) -> None:
        from tests.support.factories import make_session, make_session_data
        from uc09_summary.adapters.mock.generator import DeterministicSummaryGenerator

        data = make_session_data(
            session=make_session(S.SESSION_COMPLETE, naric=level),
            interactions=S.INTERACTIONS[S.SESSION_COMPLETE],
            citations=S.CITATIONS[S.SESSION_COMPLETE],
            gap_suggestions=(),
        )
        content = DeterministicSummaryGenerator().generate(data)

        assert all(marker in c.explanation for c in content.key_concepts)

    def test_an_assumed_profile_is_disclosed_on_the_record(self) -> None:
        from tests.support.factories import make_session

        harness = build_harness()
        session = make_session(S.SESSION_COMPLETE, naric=NaricLevel.LEVEL_6)
        S.SESSIONS[S.SESSION_COMPLETE] = session
        try:
            record = harness.service.generate(S.SESSION_COMPLETE, S.OWNER_USER_ID)
            assert "assumption recorded in the assumptions register" in (
                record.section_notes["explanation_profile"]
            )
        finally:
            S.SESSIONS[S.SESSION_COMPLETE] = make_session(
                S.SESSION_COMPLETE, naric=NaricLevel.LEVEL_7
            )

    def test_an_invalid_level_is_disclosed_on_the_record(self) -> None:
        harness = build_harness()
        record = harness.service.generate(S.SESSION_INVALID_NARIC, S.OWNER_USER_ID)

        assert record.naric_level is NaricLevel.LEVEL_5
        assert record.naric_level_source is NaricLevelSource.DEFAULT
        assert record.source_status["naric_level"] is SourceStatus.INVALID
        assert "did not match a known level" in record.section_notes["study_level"]

    def test_the_document_prints_the_level_and_its_source(self) -> None:
        from tests.support.documents import pdf_text_normalised

        harness = build_harness()
        record = harness.service.generate(S.SESSION_INVALID_NARIC, S.OWNER_USER_ID)
        result = harness.service.export(record.summary_id, S.OWNER_USER_ID)
        text = pdf_text_normalised(result.pdf or b"")

        assert "level_5" in text
        assert "source: default" in text


class TestCourseCompletionIsAnInteger:
    """Completion is an integer 0-100. A 0..1 ratio is an adapter concern."""

    @staticmethod
    def _with(percent: object):
        from uc09_summary.domain.models import SessionRecord

        base = make_session().model_dump()
        return SessionRecord.model_validate({**base, "course_completion_percent": percent})

    @pytest.mark.parametrize("ratio", [0.62, 0.5, 1.5])
    def test_a_ratio_is_rejected(self, ratio: float) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            self._with(ratio)

    @pytest.mark.parametrize("bad", [-1, 101, 1000])
    def test_out_of_range_values_are_rejected(self, bad: int) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            self._with(bad)

    @pytest.mark.parametrize("good", [0, 50, 100])
    def test_a_valid_percentage_is_accepted(self, good: int) -> None:
        assert self._with(good).course_completion_percent == good

    def test_the_foreign_adapter_converts_its_ratio(self) -> None:
        from uc09_summary.adapters.foreign import lexportal_client as lp
        from uc09_summary.adapters.foreign.session import ForeignSessionProvider

        record = ForeignSessionProvider(lp.LexPortalClient()).get_session(lp.SESSION_OK)

        assert record.course_completion_percent == 62
        assert isinstance(record.course_completion_percent, int)
