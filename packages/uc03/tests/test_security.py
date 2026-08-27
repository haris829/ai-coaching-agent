"""Requirement 14 - server-side validation and non-overridable context."""

from __future__ import annotations

import pytest

from uc03.adapters.mocks import MockContextProvider, context_without_practice_area
from uc03.config import Settings
from uc03.domain.enums import FieldAvailability
from uc03.domain.models import Principal
from uc03.errors import AuthenticationError, AuthorizationError, InputValidationError

from .conftest import ALICE_SESSION, BOB_SESSION, build_service


async def test_unknown_credential_is_rejected():
    svc = build_service()
    with pytest.raises(AuthenticationError):
        await svc.authenticate("not-a-real-token")


async def test_known_credential_resolves_a_principal():
    svc = build_service()
    principal = await svc.authenticate("dev-token-alice")
    assert principal.user_id == "user-alice"


async def test_user_cannot_use_another_users_session(alice):
    """Alice must not be able to address Bob's session by guessing its id."""
    svc = build_service()
    with pytest.raises(AuthorizationError):
        await svc.answer(
            question="What is negligence in tort law?",
            session_id=BOB_SESSION,
            principal=alice,
        )


async def test_unknown_session_is_rejected(alice):
    svc = build_service()
    with pytest.raises(AuthorizationError):
        await svc.answer(
            question="What is negligence in tort law?",
            session_id="session-does-not-exist",
            principal=alice,
        )


@pytest.mark.parametrize("question", ["", "  ", "ab"])
async def test_undersized_input_rejected(question, alice):
    svc = build_service()
    with pytest.raises(InputValidationError):
        await svc.answer(question=question, session_id=ALICE_SESSION, principal=alice)


async def test_oversized_input_rejected(alice):
    svc = build_service()
    with pytest.raises(InputValidationError) as exc:
        await svc.answer(
            question="x" * (Settings().max_question_chars + 1),
            session_id=ALICE_SESSION,
            principal=alice,
        )
    assert exc.value.reason == "input_too_long"


def test_service_exposes_no_parameter_for_client_supplied_context():
    """The client cannot override NARIC level, practice area, prompts or identity
    because `answer` has nowhere to put them."""
    import inspect

    from uc03.service import QAService

    params = set(inspect.signature(QAService.answer).parameters)
    assert params == {"self", "question", "session_id", "principal", "on_thinking"}
    for forbidden in (
        "naric_level",
        "practice_area",
        "system_prompt",
        "authority",
        "user_id",
        "classification",
    ):
        assert forbidden not in params


def test_principal_is_immutable():
    principal = Principal(user_id="user-alice")
    with pytest.raises(Exception):
        principal.user_id = "user-bob"  # type: ignore[misc]


async def test_context_always_comes_from_the_provider(alice):
    """Even when the caller wants otherwise, context is whatever the provider says."""
    provider = MockContextProvider(builder=context_without_practice_area)
    svc = build_service(context_provider=provider)
    response = await svc.answer(
        question="What is negligence in tort law?",
        session_id=ALICE_SESSION,
        principal=alice,
    )
    assert provider.calls == [(alice.user_id, ALICE_SESSION)]
    assert response.meta.practice_area_availability is FieldAvailability.MISSING
    assert response.meta.personalisation_applied is False


async def test_context_is_fetched_for_the_authenticated_user_only(alice):
    provider = MockContextProvider()
    svc = build_service(context_provider=provider)
    await svc.answer(
        question="What is negligence in tort law?",
        session_id=ALICE_SESSION,
        principal=alice,
    )
    assert provider.calls == [("user-alice", ALICE_SESSION)]


def test_prompts_and_guardrails_live_server_side():
    """Guardrail text is configuration, not request data."""
    settings = Settings()
    assert "Westlaw" in " ".join(settings.verification_routes)
    assert settings.no_authority_message
    assert settings.out_of_scope_message
    assert settings.citation_guard_enabled is True
