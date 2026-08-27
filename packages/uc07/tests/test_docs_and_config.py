"""The deliverable documents and configuration surface are part of the contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from uc07.application.config import Settings

ASSUMPTIONS = Path("docs/assumptions.md")
SHARED_CONTRACT = Path("docs/SHARED_CONTRACT.md")
INTEGRATION = Path("docs/INTEGRATION.md")
ENV_EXAMPLE = Path(".env.example")


@pytest.mark.parametrize("path", [ASSUMPTIONS, SHARED_CONTRACT, INTEGRATION, ENV_EXAMPLE])
def test_deliverable_documents_exist(path):
    assert path.is_file(), path
    assert path.read_text(encoding="utf-8").strip()


def test_assumptions_register_uses_the_required_table_shape():
    text = ASSUMPTIONS.read_text(encoding="utf-8")
    assert "| ID | Area | Assumption | Why / Risk if Wrong | Where in Code |" in text


@pytest.mark.parametrize(
    "topic",
    [
        "qualifying interaction",
        "Follow-up interactions count",
        "Duplicate `interaction_id`",
        "exactly 10 qualifying interactions",
        "Explain-differently threshold = 2",
        "Follow-up threshold = 2",
        "Low-rating threshold = 1",
        "topic-description registry",
        "Partial speciality data",
        "complete, all-time history",
        "report_version",
        "analysis_version",
        "re-evaluates the source data",
    ],
)
def test_assumptions_register_documents_each_required_assumption(topic):
    assert topic in ASSUMPTIONS.read_text(encoding="utf-8")


def test_every_assumption_row_names_a_real_source_file():
    text = ASSUMPTIONS.read_text(encoding="utf-8")
    referenced = {
        token.strip("`")
        for token in text.replace("|", " ").split()
        if token.startswith("`uc07/") or token.startswith("uc07/")
    }
    for reference in referenced:
        path = Path(reference.split("`")[0].rstrip("`,;"))
        if path.suffix in {".py", ".json"}:
            assert path.is_file(), reference


def test_shared_contract_states_what_is_read_and_written():
    text = SHARED_CONTRACT.read_text(encoding="utf-8")
    assert "**UC-07 READS:**" in text
    assert "**UC-07 WRITES:**" in text
    assert "generated gap reports only" in text
    assert "**UC-07 NEVER writes upstream data.**" in text
    for marker in ("SPECIFIED BY COMPANY", "ASSUMED BY US"):
        assert marker in text
    for typename in (
        "GapReport",
        "Gap",
        "GapEvidence",
        "Recommendation",
        "Enrolment",
        "LearnerProfile",
        "InteractionRecord",
        "FeedbackRecord",
        "SourceStatus",
        "ThresholdProgress",
        "Error envelope",
    ):
        assert typename in text, typename
    assert "Extension points" in text or "Extension point" in text


def test_integration_runbook_covers_every_dependency_and_the_closing_rules():
    text = INTEGRATION.read_text(encoding="utf-8")
    for dependency in (
        "InteractionLogProvider",
        "FeedbackProvider",
        "LearnerProfileProvider",
        "CoursesProvider",
        "GapReportRepository",
        "CurrentUserProvider",
    ):
        assert dependency in text, dependency
    for item in (
        "Adapter file to create",
        "Template to copy",
        "Port interface",
        "Registry line",
        "Environment variable",
        "Conformance command",
        "Assumptions to verify first",
    ):
        assert item in text, item
    assert "Worked example" in text
    for rule in (
        "adapter is the ONLY location containing upstream payload knowledge",
        "adapter never invents data",
        "Authorization remains server-side",
        "Contract mismatches require a contract discussion",
    ):
        assert rule in text, rule


def test_real_adapter_template_has_every_required_todo_marker():
    text = Path("uc07/adapters/real/_template.py").read_text(encoding="utf-8")
    for marker in (
        "TODO(endpoint)",
        "TODO(authentication)",
        "TODO(payload mapping)",
        "TODO(error translation)",
        "TODO(status mapping)",
    ):
        assert marker in text, marker


def test_env_example_contains_placeholders_only():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for key in (
        "INTERACTION_LOG_PROVIDER=mock",
        "FEEDBACK_PROVIDER=mock",
        "PROFILE_PROVIDER=mock",
        "COURSES_PROVIDER=mock",
        "GAP_REPORT_THRESHOLD=10",
        "MIN_TOPIC_AREAS=3",
        "EXPLAIN_DIFFERENTLY_STRUGGLE_THRESHOLD=2",
        "LOW_RATING_STRUGGLE_THRESHOLD=1",
        "FOLLOW_UP_STRUGGLE_THRESHOLD=2",
    ):
        assert key in text, key
    # Only assignment lines matter; prose comments may mention secrets to forbid them.
    assignments = [
        line
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#") and "=" in line
    ]
    for line in assignments:
        _, _, value = line.partition("=")
        assert "http://" not in value and "https://" not in value, line
        assert value.strip() != "", line
    for secret_marker in ("secret=", "password=", "apikey=", "api_key="):
        assert secret_marker not in text.lower()


def test_configuration_defaults_match_the_documented_thresholds():
    thresholds = Settings(
        interaction_log_provider="mock",
        feedback_provider="mock",
        profile_provider="mock",
        courses_provider="mock",
    ).thresholds()
    assert thresholds.gap_report_threshold == 10
    assert thresholds.min_topic_areas == 3
    assert thresholds.explain_differently_struggle_threshold == 2
    assert thresholds.low_rating_struggle_threshold == 1
    assert thresholds.follow_up_struggle_threshold == 2


def test_no_threshold_literal_is_hard_coded_in_business_logic():
    for path in (
        Path("uc07/application/signals.py"),
        Path("uc07/application/service.py"),
        Path("uc07/application/report_builder.py"),
        Path("uc07/domain/counting.py"),
    ):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            if "threshold" in code and "=" in code:
                # thresholds are read from AnalysisThresholds, never literals
                assert "= 10" not in code and "= 2" not in code and "= 3" not in code, (
                    f"{path}: {line}"
                )
