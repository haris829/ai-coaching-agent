"""Provider registry, and the proof that the service runs unmodified on a foreign adapter."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from uc04.adapters.memory.clock import FixedClock, SequentialIdGenerator
from uc04.adapters.real import foreign_demo as foreign
from uc04.adapters.registry import REGISTRIES, ProviderNotRegistered, resolve
from uc04.api.app import API_PREFIX, create_app
from uc04.composition import build_container
from uc04.config import Settings
from uc04.domain.enums import ExplanationProfile, Grounding, NaricLevel


# ------------------------------------------------------------------------------ registry


def test_every_port_has_a_registry_keyed_on_a_config_variable() -> None:
    assert set(REGISTRIES) == {
        "COURSES_PROVIDER",
        "LEARNER_CONTEXT_PROVIDER",
        "ANSWER_GENERATOR",
        "QUIZ_CLASSIFIER",
        "CONCEPT_TAGGER",
        "INTERACTION_LOG_REPOSITORY",
        "FRAMING_REGISTRY",
        "CURRENT_USER_PROVIDER",
    }


def test_registry_entries_are_dotted_paths_so_adding_one_needs_no_import() -> None:
    """Registering an adapter is one line: a key and a 'module:Class' string."""
    for registry, _ in REGISTRIES.values():
        for name, target in registry.items():
            assert ":" in target, f"{name} -> {target!r} should be 'module:Attribute'"
            module, _, attribute = target.partition(":")
            assert module.startswith("uc04.") and attribute[:1].isupper()


def test_resolving_a_registered_name_constructs_the_adapter() -> None:
    adapter = resolve("COURSES_PROVIDER", "mock")
    assert hasattr(adapter, "get_lesson")


def test_an_unregistered_name_fails_loudly_and_says_how_to_fix_it() -> None:
    with pytest.raises(ProviderNotRegistered) as exc:
        resolve("COURSES_PROVIDER", "company_real")
    message = str(exc.value)
    assert "company_real" in message
    assert "CoursesProvider" in message
    assert "registry.py" in message
    assert "_template.py" in message
    assert "mock" in message, "the message should list what IS registered"


def test_there_is_no_silent_fallback_to_a_mock() -> None:
    """A service quietly running on fake data is worse than one that refuses to start."""
    with pytest.raises(ProviderNotRegistered):
        build_container(Settings(courses_provider="not_a_real_provider"))


def test_a_registry_entry_pointing_at_a_missing_module_fails_at_startup() -> None:
    from uc04.adapters import registry

    registry.COURSES_PROVIDERS["broken_demo"] = "uc04.adapters.real.no_such_module:Adapter"
    try:
        with pytest.raises(ProviderNotRegistered) as exc:
            resolve("COURSES_PROVIDER", "broken_demo")
        assert "could not be imported" in str(exc.value)
    finally:
        del registry.COURSES_PROVIDERS["broken_demo"]


# ----------------------------------------------------- the foreign adapter family


def _foreign_container():
    return build_container(
        Settings(courses_provider="foreign_demo", learner_context_provider="foreign_demo"),
        clock=FixedClock(),
        ids=SequentialIdGenerator(),
    )


def test_the_unmodified_service_answers_against_a_foreign_adapter_family() -> None:
    """Different field names, different nesting, different value representation.

    Nothing in uc04/core or uc04/domain knows this adapter exists.
    """
    container = _foreign_container()
    response = container.service.ask(
        session_id="coach-sess-abc",
        user_id=foreign.FOREIGN_USER,
        course_id=foreign.FOREIGN_COURSE,
        lesson_id=foreign.FOREIGN_LESSON,
        question="What is a fire risk assessment?",
    )

    assert response.grounding is Grounding.LESSON
    assert response.section_reference.lesson_section_id == "BLK-1"
    assert response.course_id == foreign.FOREIGN_COURSE
    assert response.explanation.strip()


def test_the_foreign_upstreams_value_representation_is_normalised() -> None:
    """``{"band": "masters"}`` becomes the platform enum, not a passed-through string."""
    container = _foreign_container()
    response = container.service.ask(
        session_id="coach-sess-abc",
        user_id=foreign.FOREIGN_USER,
        course_id=foreign.FOREIGN_COURSE,
        lesson_id=foreign.FOREIGN_LESSON,
        question="What is a fire risk assessment?",
    )
    assert response.naric_level is NaricLevel.LEVEL_7
    assert response.explanation_profile is ExplanationProfile.ADVANCED


def test_the_foreign_progress_float_is_normalised_to_an_integer_percentage() -> None:
    assert foreign.foreign_progress_percent(0.42) == 42
    assert foreign.foreign_progress_percent(0.0) == 0
    assert foreign.foreign_progress_percent(1.0) == 100
    assert isinstance(foreign.foreign_progress_percent(0.425), int)


def test_quiz_protection_still_works_against_the_foreign_family() -> None:
    container = _foreign_container()
    response = container.service.ask(
        session_id="coach-sess-abc",
        user_id=foreign.FOREIGN_USER,
        course_id=foreign.FOREIGN_COURSE,
        lesson_id=foreign.FOREIGN_LESSON,
        question="Which of the following is the first step of a fire risk assessment?",
    )
    record = container.interactions.get(response.interaction_id)
    assert record.quiz_intent_detected is True
    assert record.quiz_detection_confirmed is True
    assert "keyedChoice" not in response.model_dump_json()


def test_explain_differently_still_works_against_the_foreign_family() -> None:
    container = _foreign_container()
    first = container.service.ask(
        session_id="coach-sess-abc",
        user_id=foreign.FOREIGN_USER,
        course_id=foreign.FOREIGN_COURSE,
        lesson_id=foreign.FOREIGN_LESSON,
        question="What is a fire risk assessment?",
    )
    second = container.service.explain_differently(
        interaction_id=first.interaction_id, user_id=foreign.FOREIGN_USER
    )
    assert second.framing_used is not first.framing_used
    assert second.explanation != first.explanation


def test_enrolment_is_still_enforced_against_the_foreign_family() -> None:
    from uc04.domain.errors import NotEnrolled

    container = _foreign_container()
    with pytest.raises(NotEnrolled):
        container.service.ask(
            session_id="coach-sess-abc",
            user_id="staff-0000",
            course_id=foreign.FOREIGN_COURSE,
            lesson_id=foreign.FOREIGN_LESSON,
            question="What is a fire risk assessment?",
        )


def test_no_foreign_field_name_or_error_string_escapes_the_adapter() -> None:
    container = _foreign_container()
    response = container.service.ask(
        session_id="coach-sess-abc",
        user_id=foreign.FOREIGN_USER,
        course_id=foreign.FOREIGN_COURSE,
        lesson_id=foreign.FOREIGN_LESSON,
        question="What is a fire risk assessment?",
    )
    serialised = response.model_dump_json()
    for upstream_token in ("unitRef", "moduleRef", "bodyText", "topicRef", "assessmentItems", "SERVICE_DOWN", "band"):
        assert upstream_token not in serialised


def test_the_foreign_family_works_over_http_too() -> None:
    container = _foreign_container()
    client = TestClient(create_app(container), raise_server_exceptions=False)
    response = client.post(
        f"{API_PREFIX}/questions",
        json={
            "session_id": "coach-sess-abc",
            "course_id": foreign.FOREIGN_COURSE,
            "lesson_id": foreign.FOREIGN_LESSON,
            "question": "What is a fire risk assessment?",
        },
        headers={"x-user-id": foreign.FOREIGN_USER},
    )
    assert response.status_code == 200
    assert response.json()["grounding"] == "lesson"


def test_core_and_domain_never_import_an_adapter() -> None:
    """The structural guarantee behind the swap rule, checked mechanically."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "uc04"
    offenders: list[str] = []
    for package in ("core", "domain", "ports"):
        for path in (root / package).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith(("import ", "from ")) and "adapters" in stripped:
                    offenders.append(f"{path.name}: {stripped}")
    assert offenders == [], offenders
