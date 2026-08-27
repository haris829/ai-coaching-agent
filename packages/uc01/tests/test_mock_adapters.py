"""The mock adapters themselves: every required scenario is present and normalises."""

from __future__ import annotations

import pytest

from uc01.adapters.mock import (
    CaseScenario,
    CoursesScenario,
    MockCaseFileAdapter,
    MockCoursesAdapter,
    MockNaricAdapter,
    MockProfileAdapter,
    NaricScenario,
    ProfileScenario,
)
from uc01.adapters.mock.scenarios import ScenarioSet, parse_scenario_header
from uc01.contracts.exceptions import (
    DependencyUnavailableError,
    InvalidUpstreamResponseError,
    ResourceNotAccessibleError,
)
from uc01.domain.enums import NaricAssessmentState
from uc01.domain.models import UserContext

ALICE = UserContext(user_id="u_alice")
BOB = UserContext(user_id="u_bob")
CAROL = UserContext(user_id="u_carol")
STRANGER = UserContext(user_id="u_unknown")


# --------------------------------------------------------------------------- #
# NARIC scenarios: success / incomplete / unavailable / invalid
# --------------------------------------------------------------------------- #


def test_naric_success_scenario():
    assessment = MockNaricAdapter(NaricScenario.SUCCESS).get_assessment(ALICE)
    assert assessment.state is NaricAssessmentState.COMPLETE
    assert assessment.level == 7  # normalised from the string "7"
    assert assessment.assessed_at is not None


def test_naric_incomplete_scenario():
    assessment = MockNaricAdapter(NaricScenario.INCOMPLETE).get_assessment(ALICE)
    assert assessment.state is NaricAssessmentState.INCOMPLETE
    assert assessment.level is None
    assert assessment.usable is False
    assert "missing_sections" in (assessment.detail_code or "")


def test_naric_calibrating_scenario():
    assessment = MockNaricAdapter(NaricScenario.CALIBRATING).get_assessment(ALICE)
    assert assessment.state is NaricAssessmentState.CALIBRATING
    assert assessment.level is None


def test_naric_unavailable_scenario_raises_a_contract_error():
    with pytest.raises(DependencyUnavailableError) as excinfo:
        MockNaricAdapter(NaricScenario.UNAVAILABLE).get_assessment(ALICE)
    assert excinfo.value.dependency == "naric"
    assert excinfo.value.technical_detail  # kept for server-side logging


def test_naric_invalid_scenario_raises_an_invalid_response_error():
    with pytest.raises(InvalidUpstreamResponseError):
        MockNaricAdapter(NaricScenario.INVALID).get_assessment(ALICE)


def test_naric_per_user_states():
    assert MockNaricAdapter().get_assessment(ALICE).level == 8
    assert MockNaricAdapter().get_assessment(BOB).state is NaricAssessmentState.CALIBRATING
    assert MockNaricAdapter().get_assessment(CAROL).state is NaricAssessmentState.INCOMPLETE
    # An unknown learner is an incomplete assessment, not a crash.
    assert MockNaricAdapter().get_assessment(STRANGER).usable is False


# --------------------------------------------------------------------------- #
# Courses scenarios
# --------------------------------------------------------------------------- #


def test_courses_available_with_lessons():
    courses = MockCoursesAdapter().list_accessible_courses(ALICE)
    by_id = {course.course_id: course for course in courses}
    assert set(by_id) == {"crs_contract_law", "crs_evidence", "crs_no_lessons"}
    # Lessons are normalised and ordered.
    assert [lesson.ordinal for lesson in by_id["crs_contract_law"].lessons] == [1, 2, 3]
    assert by_id["crs_no_lessons"].lessons == ()


def test_courses_empty_scenario():
    assert MockCoursesAdapter(CoursesScenario.EMPTY).list_accessible_courses(ALICE) == ()


def test_courses_empty_per_user():
    assert MockCoursesAdapter().list_accessible_courses(CAROL) == ()


def test_courses_unavailable_scenario():
    with pytest.raises(DependencyUnavailableError):
        MockCoursesAdapter(CoursesScenario.UNAVAILABLE).list_accessible_courses(ALICE)


def test_courses_invalid_scenario():
    with pytest.raises(InvalidUpstreamResponseError):
        MockCoursesAdapter(CoursesScenario.INVALID).list_accessible_courses(ALICE)


def test_courses_authorization_is_enforced_in_the_adapter():
    adapter = MockCoursesAdapter()
    assert adapter.get_accessible_course(BOB, "crs_tort").title == "Tort Law Essentials"
    with pytest.raises(ResourceNotAccessibleError):
        adapter.get_accessible_course(ALICE, "crs_tort")
    with pytest.raises(ResourceNotAccessibleError):
        adapter.get_accessible_course(ALICE, "crs_nonexistent")


def test_missing_lesson_lookup_returns_none_not_an_error():
    course = MockCoursesAdapter().get_accessible_course(ALICE, "crs_contract_law")
    assert course.lesson("lsn_offer") is not None
    assert course.lesson("lsn_hearsay") is None


# --------------------------------------------------------------------------- #
# Case scenarios
# --------------------------------------------------------------------------- #


def test_case_files_available():
    cases = MockCaseFileAdapter().list_accessible_case_files(ALICE)
    assert [case.case_id for case in cases] == ["case_alpha"]
    assert cases[0].matter_reference == "AH-2026-0142"


def test_no_accessible_case_files_per_user():
    assert MockCaseFileAdapter().list_accessible_case_files(BOB) == ()


def test_case_service_unavailable_scenario():
    with pytest.raises(DependencyUnavailableError):
        MockCaseFileAdapter(CaseScenario.UNAVAILABLE).list_accessible_case_files(ALICE)


def test_case_service_invalid_scenario():
    with pytest.raises(InvalidUpstreamResponseError):
        MockCaseFileAdapter(CaseScenario.INVALID).list_accessible_case_files(ALICE)


def test_case_empty_scenario():
    assert MockCaseFileAdapter(CaseScenario.EMPTY).list_accessible_case_files(ALICE) == ()


def test_case_authorization_is_enforced_in_the_adapter():
    adapter = MockCaseFileAdapter()
    assert adapter.get_accessible_case_file(CAROL, "case_beta").title == "Re: Beta Estate"
    with pytest.raises(ResourceNotAccessibleError):
        adapter.get_accessible_case_file(ALICE, "case_beta")


# --------------------------------------------------------------------------- #
# Profile scenarios
# --------------------------------------------------------------------------- #


def test_profile_available():
    profile = MockProfileAdapter().get_profile(ALICE)
    assert profile.display_name == "Alice Osei"  # normalised from first + last
    assert profile.preferred_language == "en-GB"
    assert profile.current_course_id == "crs_contract_law"
    assert profile.is_complete is True


def test_profile_incomplete_per_user_has_no_invented_name():
    profile = MockProfileAdapter().get_profile(CAROL)
    assert profile.display_name is None
    assert profile.is_complete is False


def test_profile_incomplete_scenario():
    profile = MockProfileAdapter(ProfileScenario.INCOMPLETE).get_profile(ALICE)
    assert profile.display_name is None


def test_profile_unavailable_scenario():
    with pytest.raises(DependencyUnavailableError):
        MockProfileAdapter(ProfileScenario.UNAVAILABLE).get_profile(ALICE)


def test_unknown_user_profile_is_incomplete_not_an_error():
    assert MockProfileAdapter().get_profile(STRANGER).display_name is None


# --------------------------------------------------------------------------- #
# Scenario plumbing
# --------------------------------------------------------------------------- #


def test_scenario_header_parsing():
    assert parse_scenario_header("courses=unavailable,naric=incomplete") == {
        "courses": "unavailable",
        "naric": "incomplete",
    }
    assert parse_scenario_header("") == {}
    assert parse_scenario_header(None) == {}
    assert parse_scenario_header("garbage;;;") == {}


def test_scenario_merge_ignores_unknown_values():
    base = ScenarioSet()
    merged = base.merged_with({"courses": "nonsense", "cases": "unavailable", "zzz": "x"})
    assert merged.courses is base.courses
    assert merged.cases is CaseScenario.UNAVAILABLE


def test_mock_package_is_labelled_as_mock():
    import uc01.adapters.mock as mock_package

    assert mock_package.IS_MOCK is True
    assert "NOT PRODUCTION" in (mock_package.__doc__ or "").upper()
