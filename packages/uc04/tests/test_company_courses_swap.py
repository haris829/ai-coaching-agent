"""The integration swap, exercised.

This is the file an integrator writes for their own adapter, and it is the only file they write.
Every assertion below comes from the shipped conformance kit.

Run it with:

    COURSES_PROVIDER=company_courses \\
    COMPANY_COURSES_BASE_URL=file://./tests/fixtures/company_staging \\
    pytest tests/test_company_courses_swap.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from uc04.adapters.memory.clock import FixedClock, SequentialIdGenerator
from uc04.adapters.real.company_courses import CompanyCoursesAdapter
from uc04.composition import build_container
from uc04.config import Settings
from uc04.conformance import CoursesProviderConformance, CoursesScenarios
from uc04.domain.enums import Grounding

STAGING = Path(__file__).parent / "fixtures" / "company_staging"


@pytest.fixture(autouse=True)
def _staging_endpoint(monkeypatch: pytest.MonkeyPatch):
    """The one environment variable the swap needs."""
    monkeypatch.setenv("COMPANY_COURSES_BASE_URL", f"file://{STAGING}")
    monkeypatch.setenv("COURSES_PROVIDER", "company_courses")


# ------------------------------------------------------- the conformance kit, unmodified


class TestCompanyCoursesConformance(CoursesProviderConformance):
    @pytest.fixture
    def adapter(self):
        return CompanyCoursesAdapter()

    @pytest.fixture
    def scenarios(self):
        return CoursesScenarios(
            course_id="CRS-1",
            lesson_id="LSN-1",
            enrolled_user_id="u-enrolled",
            unenrolled_user_id="u-outsider",
            unavailable_lesson_id="LSN-DOWN",
            timeout_lesson_id="LSN-SLOW",
            invalid_lesson_id="LSN-BAD",
            missing_lesson_id="LSN-NOPE",
            missing_course_id="CRS-NOPE",
            expects_quiz_items=True,
        )


# ------------------------------------------------------------- the swap, end to end


def test_the_registry_resolves_the_new_provider_from_config_alone() -> None:
    container = build_container(
        Settings(courses_provider=os.environ["COURSES_PROVIDER"]),
        clock=FixedClock(),
        ids=SequentialIdGenerator(),
    )
    assert type(container.courses).__name__ == "CompanyCoursesAdapter"


def test_the_unmodified_service_answers_through_the_new_adapter() -> None:
    container = build_container(
        Settings(courses_provider="company_courses"),
        clock=FixedClock(),
        ids=SequentialIdGenerator(),
    )
    response = container.service.ask(
        session_id="sess_swap_1",
        user_id="u-enrolled",
        course_id="CRS-1",
        lesson_id="LSN-1",
        question="What is customer due diligence?",
    )
    assert response.grounding is Grounding.LESSON
    assert response.section_reference.lesson_section_id == "SEC-1"
    assert response.explanation.strip()


def test_quiz_protection_works_through_the_new_adapter() -> None:
    container = build_container(
        Settings(courses_provider="company_courses"),
        clock=FixedClock(),
        ids=SequentialIdGenerator(),
    )
    response = container.service.ask(
        session_id="sess_swap_2",
        user_id="u-enrolled",
        course_id="CRS-1",
        lesson_id="LSN-1",
        question="At what point must client identity be verified?",
    )
    record = container.interactions.get(response.interaction_id)
    assert record.quiz_intent_detected is True
    assert record.quiz_detection_confirmed is True, "the recorded quiz item matched"


def test_enrolment_is_enforced_through_the_new_adapter() -> None:
    from uc04.domain.errors import NotEnrolled

    container = build_container(
        Settings(courses_provider="company_courses"),
        clock=FixedClock(),
        ids=SequentialIdGenerator(),
    )
    with pytest.raises(NotEnrolled):
        container.service.ask(
            session_id="sess_swap_3",
            user_id="u-outsider",
            course_id="CRS-1",
            lesson_id="LSN-1",
            question="What is customer due diligence?",
        )


def test_no_company_payload_field_escapes_the_adapter() -> None:
    container = build_container(
        Settings(courses_provider="company_courses"),
        clock=FixedClock(),
        ids=SequentialIdGenerator(),
    )
    response = container.service.ask(
        session_id="sess_swap_4",
        user_id="u-enrolled",
        course_id="CRS-1",
        lesson_id="LSN-1",
        question="What is customer due diligence?",
    )
    serialised = response.model_dump_json()
    assert "PROPRIETARY COMPANY PROSE" not in serialised
    assert "key_points" not in serialised
    assert "quiz_items" not in serialised


def test_an_unconfigured_transport_fails_loudly_rather_than_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No silent fallback: an adapter without an endpoint refuses, it does not pretend."""
    from uc04.domain.errors import ProviderUnavailable

    monkeypatch.setenv("COMPANY_COURSES_BASE_URL", "")
    adapter = CompanyCoursesAdapter()
    with pytest.raises(ProviderUnavailable):
        adapter.get_lesson("CRS-1", "LSN-1")
