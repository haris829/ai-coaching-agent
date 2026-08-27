"""Conformance suite for the ``CoursesProvider`` port."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from ..domain.errors import (
    NotFound,
    ProviderError,
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from ..domain.models import CourseStructure, EnrolmentRecord, LessonContent
from ._shared import assert_no_upstream_leakage


@dataclass(frozen=True)
class CoursesScenarios:
    """Identifiers that drive each behaviour in the adapter under test.

    Set a failure id to ``None`` when the upstream genuinely cannot produce that mode; that
    check is then skipped rather than failed.
    """

    course_id: str
    lesson_id: str
    enrolled_user_id: str
    unenrolled_user_id: str
    unavailable_lesson_id: str | None = None
    timeout_lesson_id: str | None = None
    invalid_lesson_id: str | None = None
    missing_lesson_id: str | None = None
    missing_course_id: str | None = None
    #: Set when the upstream is known to expose quiz items on ``lesson_id``.
    expects_quiz_items: bool = False


class CoursesProviderConformance:
    """Subclass and provide the ``adapter`` and ``scenarios`` fixtures."""

    @pytest.fixture
    def adapter(self):  # pragma: no cover - overridden by the implementer
        raise NotImplementedError("provide an `adapter` fixture")

    @pytest.fixture
    def scenarios(self) -> CoursesScenarios:  # pragma: no cover - overridden
        raise NotImplementedError("provide a `scenarios` fixture")

    # ------------------------------------------------------------------------- lessons

    def test_get_lesson_returns_domain_model(self, adapter, scenarios: CoursesScenarios) -> None:
        lesson = adapter.get_lesson(scenarios.course_id, scenarios.lesson_id)
        assert isinstance(lesson, LessonContent)
        assert lesson.lesson_id == scenarios.lesson_id
        assert lesson.course_id == scenarios.course_id
        assert lesson.title, "a lesson must carry a title"

    def test_lesson_structure_is_internally_consistent(self, adapter, scenarios: CoursesScenarios) -> None:
        """Every concept must point at a section that exists. No dangling references."""
        lesson = adapter.get_lesson(scenarios.course_id, scenarios.lesson_id)
        section_ids = {section.section_id for section in lesson.sections}
        for concept in lesson.concepts:
            assert concept.section_id in section_ids, (
                f"concept {concept.concept_tag} points at section {concept.section_id}, "
                "which the adapter did not return"
            )

    def test_quiz_items_are_mapped_when_the_upstream_exposes_them(
        self, adapter, scenarios: CoursesScenarios
    ) -> None:
        """Quiz items are the deterministic half of quiz protection. Map them if they exist."""
        lesson = adapter.get_lesson(scenarios.course_id, scenarios.lesson_id)
        if not scenarios.expects_quiz_items:
            pytest.skip("upstream is not expected to expose quiz items for this lesson")
        assert lesson.quiz_items, "expected quiz items to be mapped"
        for item in lesson.quiz_items:
            assert item.quiz_item_id and item.question_text

    def test_get_lesson_is_deterministic(self, adapter, scenarios: CoursesScenarios) -> None:
        first = adapter.get_lesson(scenarios.course_id, scenarios.lesson_id)
        second = adapter.get_lesson(scenarios.course_id, scenarios.lesson_id)
        assert first == second

    # ----------------------------------------------------------------------- structure

    def test_get_course_structure_returns_domain_model(self, adapter, scenarios: CoursesScenarios) -> None:
        structure = adapter.get_course_structure(scenarios.course_id)
        assert isinstance(structure, CourseStructure)
        assert structure.course_id == scenarios.course_id

    def test_structure_contains_the_lesson_under_test(self, adapter, scenarios: CoursesScenarios) -> None:
        """The structure is the whitelist cross-lesson references are verified against.

        A structure that omits the lesson the learner is actually in means every reference will
        be stripped.
        """
        structure = adapter.get_course_structure(scenarios.course_id)
        assert structure.contains(scenarios.lesson_id), (
            "the course structure must list the lesson being taught"
        )

    def test_structure_lesson_ids_are_unique(self, adapter, scenarios: CoursesScenarios) -> None:
        structure = adapter.get_course_structure(scenarios.course_id)
        ids = [lesson.lesson_id for lesson in structure.lessons]
        assert len(ids) == len(set(ids))

    # ----------------------------------------------------------------------- enrolment

    def test_enrolment_returns_domain_model(self, adapter, scenarios: CoursesScenarios) -> None:
        record = adapter.verify_enrolment(scenarios.enrolled_user_id, scenarios.course_id)
        assert isinstance(record, EnrolmentRecord)
        assert record.enrolled is True
        assert record.user_id == scenarios.enrolled_user_id

    def test_unenrolled_user_is_reported_not_enrolled(self, adapter, scenarios: CoursesScenarios) -> None:
        record = adapter.verify_enrolment(scenarios.unenrolled_user_id, scenarios.course_id)
        assert record.enrolled is False, "an unenrolled user must never be reported as enrolled"

    def test_enrolment_is_a_boolean_not_a_truthy_string(self, adapter, scenarios: CoursesScenarios) -> None:
        """Upstream status vocabularies must be normalised, not passed through."""
        record = adapter.verify_enrolment(scenarios.unenrolled_user_id, scenarios.course_id)
        assert isinstance(record.enrolled, bool)

    # ------------------------------------------------------------------ failure modes

    def test_unavailable_raises_provider_unavailable(self, adapter, scenarios: CoursesScenarios) -> None:
        if scenarios.unavailable_lesson_id is None:
            pytest.skip("no unavailable scenario for this adapter")
        with pytest.raises(ProviderUnavailable) as exc:
            adapter.get_lesson(scenarios.course_id, scenarios.unavailable_lesson_id)
        assert_no_upstream_leakage(exc.value)

    def test_timeout_raises_provider_timeout(self, adapter, scenarios: CoursesScenarios) -> None:
        if scenarios.timeout_lesson_id is None:
            pytest.skip("no timeout scenario for this adapter")
        with pytest.raises(ProviderTimeout) as exc:
            adapter.get_lesson(scenarios.course_id, scenarios.timeout_lesson_id)
        assert_no_upstream_leakage(exc.value)

    def test_unmappable_payload_raises_provider_invalid_response(
        self, adapter, scenarios: CoursesScenarios
    ) -> None:
        if scenarios.invalid_lesson_id is None:
            pytest.skip("no invalid-payload scenario for this adapter")
        with pytest.raises(ProviderInvalidResponse) as exc:
            adapter.get_lesson(scenarios.course_id, scenarios.invalid_lesson_id)
        assert_no_upstream_leakage(exc.value)

    def test_missing_lesson_raises_not_found(self, adapter, scenarios: CoursesScenarios) -> None:
        if scenarios.missing_lesson_id is None:
            pytest.skip("no missing-lesson scenario for this adapter")
        with pytest.raises(NotFound) as exc:
            adapter.get_lesson(scenarios.course_id, scenarios.missing_lesson_id)
        assert_no_upstream_leakage(exc.value)

    def test_missing_course_raises_not_found(self, adapter, scenarios: CoursesScenarios) -> None:
        if scenarios.missing_course_id is None:
            pytest.skip("no missing-course scenario for this adapter")
        with pytest.raises(NotFound):
            adapter.get_course_structure(scenarios.missing_course_id)

    def test_failures_are_contract_exceptions_only(self, adapter, scenarios: CoursesScenarios) -> None:
        """No raw upstream exception may cross the boundary."""
        for lesson_id in (
            scenarios.unavailable_lesson_id,
            scenarios.timeout_lesson_id,
            scenarios.invalid_lesson_id,
            scenarios.missing_lesson_id,
        ):
            if lesson_id is None:
                continue
            try:
                adapter.get_lesson(scenarios.course_id, lesson_id)
            except ProviderError:
                continue
            except Exception as exc:  # noqa: BLE001 - that is exactly what is being caught
                raise AssertionError(
                    f"{type(exc).__name__} escaped the adapter boundary for {lesson_id!r}; "
                    "translate it into a ProviderError subclass"
                ) from exc
