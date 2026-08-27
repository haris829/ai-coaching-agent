"""Unexplored-speciality analysis, including every "could not perform" state."""

from __future__ import annotations

from tests.conftest import build_harness
from uc07.application.unexplored import ProfileLoad, analyse_unexplored
from uc07.domain.enums import GapType, SourceStatus, UnexploredAnalysisState
from uc07.domain.models import LearnerProfile


def profile(areas, status=SourceStatus.AVAILABLE) -> LearnerProfile:
    return LearnerProfile(
        user_id="u", speciality_areas=tuple(areas), speciality_status=status
    )


def test_speciality_area_with_zero_interactions_is_unexplored():
    outcome = analyse_unexplored(
        ProfileLoad.loaded(profile(("alpha", "beta"))), ("alpha",)
    )
    assert outcome.unexplored_areas == ("beta",)
    assert outcome.analysis.state is UnexploredAnalysisState.PERFORMED
    assert outcome.analysis.speciality_areas_considered == 2
    assert outcome.analysis.unexplored_areas_found == 1
    assert outcome.analysis.may_be_incomplete is False


def test_fully_covered_speciality_produces_no_unexplored_gap():
    outcome = analyse_unexplored(
        ProfileLoad.loaded(profile(("alpha", "beta"))), ("alpha", "beta", "gamma")
    )
    assert outcome.unexplored_areas == ()
    assert outcome.analysis.state is UnexploredAnalysisState.PERFORMED
    assert outcome.analysis.unexplored_areas_found == 0


def test_no_speciality_is_stated_explicitly_and_never_inferred():
    outcome = analyse_unexplored(
        ProfileLoad.loaded(profile((), SourceStatus.EMPTY)), ("alpha", "beta")
    )
    assert outcome.unexplored_areas == ()
    assert outcome.analysis.state is (
        UnexploredAnalysisState.NOT_PERFORMED_NO_SPECIALITY
    )
    assert "could not be performed" in outcome.analysis.explanation
    assert "no speciality areas set" in outcome.analysis.explanation.lower()
    assert "no speciality was inferred" in outcome.analysis.explanation.lower()


def test_partial_speciality_keeps_partial_status_and_flags_incompleteness():
    outcome = analyse_unexplored(
        ProfileLoad.loaded(profile(("alpha", "beta"), SourceStatus.PARTIAL)), ("alpha",)
    )
    assert outcome.analysis.state is UnexploredAnalysisState.PERFORMED_PARTIAL
    assert outcome.analysis.speciality_status is SourceStatus.PARTIAL
    assert outcome.analysis.may_be_incomplete is True
    assert outcome.unexplored_areas == ("beta",)


def test_unavailable_profile_does_not_invent_speciality_areas():
    outcome = analyse_unexplored(
        ProfileLoad.failed(SourceStatus.UNAVAILABLE), ("alpha", "beta")
    )
    assert outcome.unexplored_areas == ()
    assert outcome.analysis.state is (
        UnexploredAnalysisState.NOT_PERFORMED_PROFILE_UNAVAILABLE
    )
    assert outcome.analysis.speciality_areas_considered == 0
    assert "unavailable" in outcome.analysis.explanation.lower()


def test_invalid_profile_is_distinct_from_unavailable():
    outcome = analyse_unexplored(ProfileLoad.failed(SourceStatus.INVALID), ("alpha",))
    assert outcome.analysis.state is (
        UnexploredAnalysisState.NOT_PERFORMED_PROFILE_INVALID
    )
    assert outcome.analysis.speciality_status is SourceStatus.INVALID


def test_speciality_comparison_is_exact_and_case_sensitive():
    outcome = analyse_unexplored(ProfileLoad.loaded(profile(("Alpha",))), ("alpha",))
    assert outcome.unexplored_areas == ("Alpha",)


# ---------------------------------------------------------------------------
# End to end through the service
# ---------------------------------------------------------------------------


def test_report_contains_unexplored_gaps_for_uncovered_speciality_areas():
    report = build_harness("struggle_mixed").service.current_report("learner-001").report
    assert report is not None
    unexplored = [gap for gap in report.gaps if gap.gap_type is GapType.UNEXPLORED]
    assert [gap.topic_tag for gap in unexplored] == [
        "commercial_drafting",
        "data_protection",
    ]
    assert all(gap.evidence_interaction_ids == () for gap in unexplored)
    assert report.unexplored_analysis.state.value == "performed"


def test_fully_covered_speciality_yields_no_unexplored_gaps_in_the_report():
    report = (
        build_harness("profile_fully_covered").service.current_report("learner-001").report
    )
    assert report is not None
    assert [gap for gap in report.gaps if gap.gap_type is GapType.UNEXPLORED] == []
    assert report.unexplored_analysis.unexplored_areas_found == 0


def test_no_speciality_reports_that_analysis_could_not_be_performed():
    report = (
        build_harness("profile_no_speciality").service.current_report("learner-001").report
    )
    assert report is not None
    assert report.unexplored_analysis.state.value == "not_performed_no_speciality"
    assert "speciality_analysis_not_possible_no_speciality" in {
        notice.code.value for notice in report.notices
    }
    assert [gap for gap in report.gaps if gap.gap_type is GapType.UNEXPLORED] == []


def test_partial_speciality_is_preserved_and_documented_in_the_report():
    report = build_harness("profile_partial").service.current_report("learner-001").report
    assert report is not None
    assert report.source_statuses.profile is SourceStatus.PARTIAL
    assert report.unexplored_analysis.state.value == "performed_partial"
    assert report.unexplored_analysis.may_be_incomplete is True
    assert "speciality_analysis_partial" in {
        notice.code.value for notice in report.notices
    }
    # data_protection has no interactions -> still surfaced from partial data.
    assert "data_protection" in {
        gap.topic_tag for gap in report.gaps if gap.gap_type is GapType.UNEXPLORED
    }


def test_unavailable_profile_still_yields_evidence_based_struggle_analysis():
    report = (
        build_harness("profile_unavailable").service.current_report("learner-001").report
    )
    assert report is not None
    assert report.source_statuses.profile is SourceStatus.UNAVAILABLE
    assert report.unexplored_analysis.state.value == (
        "not_performed_profile_unavailable"
    )
    assert "speciality_analysis_unavailable" in {
        notice.code.value for notice in report.notices
    }
    struggle_topics = {
        gap.topic_tag for gap in report.gaps if gap.gap_type is GapType.STRUGGLE
    }
    assert struggle_topics == {"contract_formation", "land_registration", "negligence"}
