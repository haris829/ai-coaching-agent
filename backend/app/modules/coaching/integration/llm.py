"""The AI coaching port (§6, §23, §27).

    Domain logic must not be tightly coupled to a specific LLM provider.

Everything the model is asked for goes through ``CoachingLLM``. There is no vendor SDK in this
module, no HTTP client, no model name in a service, and no prompt string outside
``app.modules.coaching.prompts``. Binding a real provider is an adapter plus one line in the
composition root.

THE REQUEST IS ALREADY SAFE
---------------------------
``CoachingRequest.context`` is the *payload form of a* ``SafeCoachingContext`` — it has been
through the sanitiser (§13, §26). An adapter must forward it as-is and must not enrich it: reaching
back into UC-02 or UC-04 for "a bit more context" from inside an adapter would walk straight around
the security boundary. If an adapter needs something it does not have, the field belongs in
``SafeCoachingContext``, where the sanitiser can vouch for it.

WHAT AN IMPLEMENTATION MUST DO WHEN IT FAILS
--------------------------------------------
Raise. Specifically:

* unreachable / refused / provider error → ``CoachingServiceUnavailableError``
* no answer within ``timeout_seconds``   → ``CoachingTimeoutError``

It must **never** return a placeholder string, a cached reply or an apology dressed as coaching.
The service turns a raised error into a controlled unavailable state that says coaching is
temporarily off (§27); a fabricated reply would be indistinguishable from real coaching to
everyone, including the learner (§6).

THE SHIPPED DEFAULT REPORTS ITSELF UNAVAILABLE
----------------------------------------------
``UnconfiguredCoachingLLM`` answers ``is_available() → False`` and raises on generation. A
deployment with no provider bound tells learners coaching is unavailable, which is true — rather
than serving invented text, which would be the one failure mode this module exists to prevent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.modules.coaching.domain.errors import CoachingServiceUnavailableError


@dataclass(frozen=True, slots=True)
class CoachingRequest:
    """One call to the coach.

    ``system_prompt`` carries the coaching policy (§24) and ``context`` the sanitised question
    material (§11). They are separate because they have different trust levels and different
    lifetimes: the policy is authored by this module and rebuilt every request, while the context
    is derived from upstream data and is never allowed to contain instructions the model should
    obey.
    """

    system_prompt: str
    context: Mapping[str, Any]
    #: Prior turns, oldest first, each ``{"role": "LEARNER"|"COACH", "content": ...}``.
    conversation: tuple[Mapping[str, str], ...] = field(default_factory=tuple)
    #: SOCRATIC or DIRECT_EXPLANATION, as a plain string so adapters need no domain import.
    mode: str = "SOCRATIC"
    #: 1-based number of the coach turn being generated. Adapters may use it for tracing; the
    #: teaching policy that depends on it has already been applied to ``system_prompt``.
    turn: int = 1
    timeout_seconds: float | None = None
    max_output_chars: int | None = None
    #: Correlation only. Never sent to a provider as content.
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class CoachingCompletion:
    """What the coach said.

    ``text`` is the only field the domain reads. The rest is operational metadata for logs and
    cost attribution; none of it is shown to a learner.
    """

    text: str
    model: str | None = None
    provider: str | None = None
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@runtime_checkable
class CoachingLLM(Protocol):
    """The AI coaching service, as UC-07 sees it."""

    async def is_available(self) -> bool:
        """Whether coaching can currently be offered (§9's seventh check, §27).

        Should be cheap and must not raise: an availability probe that throws would turn a routine
        eligibility read into a 500. Implementations that cannot answer cheaply should return the
        last known state rather than calling the provider on every request.
        """
        ...

    async def generate_response(self, request: CoachingRequest) -> CoachingCompletion:
        """Produce the coach's next turn, or raise. See the module docstring."""
        ...


class UnconfiguredCoachingLLM:
    """The default binding: no AI provider is connected.

    Honest rather than helpful. It is what a standalone deployment of UC-07 runs with, and what an
    integration environment falls back to if the provider binding is forgotten — in both cases the
    module reports coaching as unavailable instead of inventing a coach (§6, §27).
    """

    #: Read by the health endpoint so an operator can see at a glance that nothing is bound.
    configured = False

    async def is_available(self) -> bool:
        return False

    async def generate_response(self, request: CoachingRequest) -> CoachingCompletion:
        raise CoachingServiceUnavailableError(
            reason="No AI coaching provider is bound in this deployment.",
            session_id=request.session_id,
        )


def conversation_from(messages: Sequence[Mapping[str, str]]) -> tuple[Mapping[str, str], ...]:
    """Normalise a conversation into the request's tuple form."""
    return tuple(dict(message) for message in messages)
