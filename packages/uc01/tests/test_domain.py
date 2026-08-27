"""Domain-level unit tests: mode availability policy and the greeting templates."""

from __future__ import annotations

import pytest

from uc01.domain import messages
from uc01.domain.enums import (
    DependencyName,
    DependencyState,
    NaricAssessmentState,
    NaricLevelSource,
    SessionMode,
)
from uc01.domain.greeting import LocalTemplateGreetingGenerator
from uc01.domain.models import (
    CaseFile,
    Course,
    DependencyStatus,
    Lesson,
    NaricResolution,
    SessionContext,
    UserContext,
    UserProfile,
)
from uc01.domain.policy import (
    available_modes,
    evaluate_mode_availability,
    find_mode_availability,
)

USER = UserContext(user_id="u_domain")
COURSE = Course(
    course_id="c1",
    title="Contract Law",
    lessons=(Lesson(lesson_id="l1", course_id="c1", title="Consideration", ordinal=1),),
)
CASE = CaseFile(case_id="k1", title="Smith v. Jones")


def status(name: DependencyName, state: DependencyState) -> DependencyStatus:
    return DependencyStatus(dependency=name, state=state)


def dependencies(*pairs) -> dict:
    return {name: status(name, state) for name, state in pairs}


# --------------------------------------------------------------------------- #
# Mode availability policy
# --------------------------------------------------------------------------- #


def test_all_modes_available_when_everything_is_healthy():
    result = evaluate_mode_availability(
        dependencies(
            (DependencyName.COURSES, DependencyState.AVAILABLE),
            (DependencyName.CASES, DependencyState.AVAILABLE),
        )
    )
    assert available_modes(result) == (
        SessionMode.FREE_FORM,
        SessionMode.COURSE_LINKED,
        SessionMode.CASE_LINKED,
    )
    assert all(entry.reason is None for entry in result)


@pytest.mark.parametrize(
    ("state", "expected_reason"),
    [
        (DependencyState.UNAVAILABLE, messages.CASES_UNAVAILABLE),
        (DependencyState.EMPTY, messages.CASES_EMPTY),
    ],
)
def test_case_mode_is_disabled_with_the_right_explanation(state, expected_reason):
    result = evaluate_mode_availability(
        dependencies(
            (DependencyName.COURSES, DependencyState.AVAILABLE),
            (DependencyName.CASES, state),
        )
    )
    case_mode = find_mode_availability(result, SessionMode.CASE_LINKED)
    assert case_mode.available is False
    assert case_mode.reason == expected_reason
    assert find_mode_availability(result, SessionMode.FREE_FORM).available is True
    assert find_mode_availability(result, SessionMode.COURSE_LINKED).available is True


@pytest.mark.parametrize(
    ("state", "expected_reason"),
    [
        (DependencyState.UNAVAILABLE, messages.COURSES_UNAVAILABLE),
        (DependencyState.EMPTY, messages.COURSES_EMPTY),
    ],
)
def test_course_mode_is_disabled_with_the_right_explanation(state, expected_reason):
    result = evaluate_mode_availability(
        dependencies(
            (DependencyName.COURSES, state),
            (DependencyName.CASES, DependencyState.AVAILABLE),
        )
    )
    course_mode = find_mode_availability(result, SessionMode.COURSE_LINKED)
    assert course_mode.available is False
    assert course_mode.reason == expected_reason
    assert find_mode_availability(result, SessionMode.CASE_LINKED).available is True


def test_free_form_survives_an_empty_dependency_map():
    result = evaluate_mode_availability({})
    assert available_modes(result) == (SessionMode.FREE_FORM,)


def test_naric_state_never_affects_mode_availability():
    for state in DependencyState:
        result = evaluate_mode_availability(
            dependencies(
                (DependencyName.COURSES, DependencyState.AVAILABLE),
                (DependencyName.CASES, DependencyState.AVAILABLE),
                (DependencyName.NARIC, state),
                (DependencyName.PROFILE, state),
            )
        )
        assert len(available_modes(result)) == 3, state


# --------------------------------------------------------------------------- #
# Greeting templates
# --------------------------------------------------------------------------- #


def context(
    *,
    mode: SessionMode = SessionMode.FREE_FORM,
    profile: UserProfile | None = UserProfile(user_id="u_domain", display_name="Ada Byron"),
    profile_state: DependencyState = DependencyState.AVAILABLE,
    course: Course | None = None,
    lesson: Lesson | None = None,
    case_file: CaseFile | None = None,
    naric: NaricResolution | None = None,
    downgraded_from: SessionMode | None = None,
) -> SessionContext:
    return SessionContext(
        user=USER,
        session_mode=mode,
        profile=profile,
        course=course,
        lesson=lesson,
        case_file=case_file,
        naric=naric
        or NaricResolution(level=7, source=NaricLevelSource.NARIC, calibration_offer=False),
        dependencies={DependencyName.PROFILE: status(DependencyName.PROFILE, profile_state)},
        downgraded_from=downgraded_from,
    )


GREETER = LocalTemplateGreetingGenerator()


def test_personalised_free_form_greeting():
    greeting = GREETER.generate(context())
    assert greeting.text.startswith("Hi Ada Byron!")
    assert greeting.personalised is True
    assert greeting.variant == "personalised.free_form"
    assert "NARIC Level 7" in greeting.text


def test_course_linked_greeting_references_course_and_lesson():
    greeting = GREETER.generate(
        context(mode=SessionMode.COURSE_LINKED, course=COURSE, lesson=COURSE.lessons[0])
    )
    assert "Consideration" in greeting.text
    assert "Contract Law" in greeting.text
    assert greeting.variant == "personalised.course_linked"


def test_case_linked_greeting_references_the_case():
    greeting = GREETER.generate(context(mode=SessionMode.CASE_LINKED, case_file=CASE))
    assert "Smith v. Jones" in greeting.text
    assert greeting.variant == "personalised.case_linked"


def test_generic_greeting_when_profile_is_missing():
    greeting = GREETER.generate(
        context(profile=None, profile_state=DependencyState.UNAVAILABLE)
    )
    assert greeting.text.startswith("Hi! Welcome back")
    assert greeting.personalised is False
    assert greeting.variant == "generic.free_form"
    assert messages.PROFILE_UNAVAILABLE_NOTICE in greeting.text


def test_generic_greeting_when_profile_has_no_name():
    greeting = GREETER.generate(
        context(
            profile=UserProfile(user_id="u_domain", display_name=None),
            profile_state=DependencyState.INCOMPLETE,
        )
    )
    assert greeting.text.startswith("Hi! Welcome back")
    assert messages.PROFILE_UNAVAILABLE_NOTICE not in greeting.text


def test_defaulted_level_is_never_attributed_to_naric():
    greeting = GREETER.generate(
        context(
            naric=NaricResolution(
                level=5, source=NaricLevelSource.DEFAULT, calibration_offer=True
            )
        )
    )
    assert "Level 5 by default" in greeting.text
    assert "NARIC Level" not in greeting.text


def test_downgraded_session_explains_itself():
    greeting = GREETER.generate(
        context(mode=SessionMode.FREE_FORM, downgraded_from=SessionMode.COURSE_LINKED)
    )
    assert "free-form session" in greeting.text


def test_greeting_never_invents_missing_context():
    """Course-linked mode without a resolved course must not name one."""
    greeting = GREETER.generate(context(mode=SessionMode.COURSE_LINKED))
    assert "Contract" not in greeting.text
    assert "lesson" not in greeting.text.lower()


def test_naric_assessment_usable_property():
    from uc01.domain.models import NaricAssessment

    assert NaricAssessment(state=NaricAssessmentState.COMPLETE, level=6).usable is True
    assert NaricAssessment(state=NaricAssessmentState.COMPLETE, level=None).usable is False
    assert NaricAssessment(state=NaricAssessmentState.INCOMPLETE, level=6).usable is False


def test_session_context_linked_resource_mapping():
    course_context = context(
        mode=SessionMode.COURSE_LINKED, course=COURSE, lesson=COURSE.lessons[0]
    )
    linked = course_context.linked_resource()
    assert linked.resource_type.value == "course"
    assert linked.secondary_label == "Consideration"

    case_context = context(mode=SessionMode.CASE_LINKED, case_file=CASE)
    assert case_context.linked_resource().resource_type.value == "case_file"

    assert context().linked_resource() is None
