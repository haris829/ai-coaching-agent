"""Report assembly: minimum three topics without padding, descriptions, notices."""

from __future__ import annotations

import pytest

from tests.conftest import DEFAULT_THRESHOLDS, build_harness, registry
from uc07.application.config import AnalysisThresholds
from uc07.application.topic_descriptions import TopicDescriptionRegistry
from uc07.domain.enums import DescriptionSource, GapType, SourceStatus
from uc07.domain.errors import ConfigurationError


def test_report_surfaces_at_least_three_topic_areas_when_history_supports_it():
    report = build_harness("struggle_mixed").service.current_report("learner-001").report
    assert report is not None
    assert report.topic_coverage.topic_areas_in_history == 5
    assert report.topic_coverage.sufficient_topic_diversity is True
    assert report.topic_coverage.identifiable_topic_areas == 5
    assert len({gap.topic_tag for gap in report.gaps}) >= 3
    assert "insufficient_topic_diversity" not in {n.code.value for n in report.notices}


def test_narrow_history_is_not_padded_and_says_so():
    report = build_harness("narrow_topics").service.current_report("learner-001").report
    assert report is not None
    assert report.topic_coverage.topic_areas_in_history == 1
    assert report.topic_coverage.sufficient_topic_diversity is False
    # One struggle topic is all the history supports; the only other gap comes
    # from a genuine speciality area with zero interactions. Nothing is padded.
    struggles = [g for g in report.gaps if g.gap_type is GapType.STRUGGLE]
    unexplored = [g for g in report.gaps if g.gap_type is GapType.UNEXPLORED]
    assert [g.topic_tag for g in struggles] == ["contract_formation"]
    assert [g.topic_tag for g in unexplored] == ["negligence"]
    notice = next(
        n for n in report.notices if n.code.value == "insufficient_topic_diversity"
    )
    assert "1 topic area(s)" in notice.message
    assert "broader coaching activity" in notice.message
    assert "No additional gaps were invented" in notice.message


def test_no_gap_is_emitted_for_a_topic_below_every_threshold():
    report = build_harness("struggle_mixed").service.current_report("learner-001").report
    assert report is not None
    assert "professional_conduct" not in {
        gap.topic_tag for gap in report.gaps if gap.gap_type is GapType.STRUGGLE
    }


def test_gap_descriptions_come_from_the_configured_registry():
    report = build_harness("struggle_mixed").service.current_report("learner-001").report
    assert report is not None
    configured = registry()
    for gap in report.gaps:
        expected, source = configured.describe(gap.topic_tag)
        assert gap.description == expected
        assert gap.description_source is source is DescriptionSource.REGISTRY


def test_unknown_topic_tags_fall_back_to_the_configured_default_template():
    configured = TopicDescriptionRegistry(
        descriptions={"known": "Known description."},
        default_template="No description configured for '{topic_tag}'.",
    )
    text, source = configured.describe("mystery_topic")
    assert text == "No description configured for 'mystery_topic'."
    assert source is DescriptionSource.REGISTRY_DEFAULT


def test_registry_rejects_a_default_template_without_the_topic_placeholder():
    with pytest.raises(ConfigurationError):
        TopicDescriptionRegistry(descriptions={}, default_template="no placeholder")


def test_registry_missing_file_fails_loudly():
    from pathlib import Path

    with pytest.raises(ConfigurationError):
        TopicDescriptionRegistry.from_path(Path("does/not/exist.json"))


def test_report_carries_versions_threshold_and_source_statuses():
    report = build_harness("struggle_mixed").service.current_report("learner-001").report
    assert report is not None
    assert report.report_version == "1.0.0"
    assert report.analysis_version == "1.0.0"
    assert report.threshold == 10
    assert report.source_statuses.interactions is SourceStatus.AVAILABLE
    assert report.source_statuses.feedback is SourceStatus.AVAILABLE
    assert report.source_statuses.profile is SourceStatus.AVAILABLE
    assert report.source_statuses.courses is SourceStatus.AVAILABLE
    assert report.report_id.startswith("gr_")
    assert len(report.content_fingerprint) == 64


def test_minimum_topic_areas_is_configuration_driven():
    thresholds = AnalysisThresholds(
        gap_report_threshold=DEFAULT_THRESHOLDS.gap_report_threshold,
        min_topic_areas=6,
        explain_differently_struggle_threshold=2,
        low_rating_struggle_threshold=1,
        follow_up_struggle_threshold=2,
    )
    report = (
        build_harness("struggle_mixed", thresholds=thresholds)
        .service.current_report("learner-001")
        .report
    )
    assert report is not None
    assert report.topic_coverage.minimum_expected_topic_areas == 6
    assert report.topic_coverage.sufficient_topic_diversity is False
    assert "insufficient_topic_diversity" in {n.code.value for n in report.notices}


def test_gap_ordering_is_struggle_first_then_unexplored_each_sorted_by_topic():
    report = build_harness("struggle_mixed").service.current_report("learner-001").report
    assert report is not None
    assert [(gap.gap_type.value, gap.topic_tag) for gap in report.gaps] == [
        ("struggle", "contract_formation"),
        ("struggle", "land_registration"),
        ("struggle", "negligence"),
        ("unexplored", "commercial_drafting"),
        ("unexplored", "data_protection"),
    ]
