"""Drive a real coaching conversation through the real provider, and check the boundary held.

    python -m scripts.probe_coaching_live

The unit tests prove the coaching rules against a fake model. The Bedrock probe proves the
credential works. Neither proves the thing that actually matters: that when a **real** model is
answering, the answer key still does not reach it and the reply is still Socratic.

So this runs the genuine ``CoachingService`` — real sanitiser, real prompts, real policy check —
against the configured provider, using UC-07's own test world for the upstream records. That world
deliberately carries answer keys in every shape a real record would: UC-04's key, UC-06's correct
answer and explanation, and metadata blobs with more of the same.

The provider is wrapped in a recorder, so every ``CoachingRequest`` that actually left the process
can be searched afterwards. That is the difference between asserting the boundary and hoping for it:
the check reads what was **sent**, not only what came back.

Four things it then asserts:

1. no answer-key string appears in anything UC-07 sent to the model;
2. nor in anything the model said back;
3. both turns ask a question — the reply came through ``response_policy``, so a turn that announced
   an answer would have been discarded and regenerated rather than shown;
4. exactly one exchange was counted.

Reads its credentials from the environment, exactly as the application does::

    COACHING_LLM_PROVIDER=bedrock
    COACHING_LLM_API_KEY=...
    COACHING_LLM_MODEL=arn:aws:bedrock:...:application-inference-profile/...
    COACHING_LLM_REGION=us-east-1

Costs a handful of tokens per run. Nothing is written to any database.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
os.environ.setdefault("ENVIRONMENT", "test")


class Recorder:
    """The real provider, with every request it was given kept for inspection.

    Deliberately not a stand-in: it forwards to the genuine adapter and records on the way past, so
    what the checks read is what the model was actually sent.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.requests: list[Any] = []

    @property
    def configured(self) -> bool:
        return bool(getattr(self._inner, "configured", True))

    async def is_available(self) -> bool:
        return await self._inner.is_available()

    async def generate_response(self, request: Any) -> Any:
        self.requests.append(request)
        return await self._inner.generate_response(request)


async def run() -> int:
    from app.core.config import Settings
    from app.modules.coaching.container import create_container
    from app.modules.coaching.integration.llm_factory import build_coaching_llm
    from tests.coaching.fakes import request_strings
    from tests.coaching.world import ANSWER_KEY_SECRETS, ATTEMPT_1, LEARNER, Q_MULTI, build_world

    settings = Settings()
    provider = build_coaching_llm(settings)
    if provider is None:
        sys.exit(
            "no AI provider configured — set COACHING_LLM_PROVIDER, COACHING_LLM_API_KEY and "
            "(for bedrock) COACHING_LLM_MODEL"
        )
    recorder = Recorder(provider)
    print(f"provider: {type(provider).__name__}")
    print(f"model   : {settings.coaching_llm_model[:70]}")

    # UC-07's own world: a submitted attempt, confirmed outcomes, released feedback — and answer
    # keys planted in every upstream record, which is the point of using it here.
    world = build_world(settings=settings)
    world.given_standard_quiz()
    container = create_container(
        settings=settings,
        clock=world.clock,
        attempts=world.attempts,
        scores=world.scores,
        feedback=world.feedback,
        llm=recorder,
        sessions_repository=world.sessions,
        transcripts_repository=world.transcripts,
    )
    coaching = container.services.coaching

    print("\n--- coaching an incorrectly answered multi-select question ---\n")
    started = await coaching.start_coaching(
        learner_id=LEARNER, attempt_id=ATTEMPT_1, question_id=Q_MULTI
    )
    if started.unavailable_reason:
        print(f"the coach could not speak: {started.unavailable_reason}")
        return 1
    opening = started.state.transcript.messages[-1].content.strip()
    print(f"Larry: {opening}\n")

    learner_says = (
        "I picked recording it and investigating it myself, because I wanted the facts first."
    )
    exchange = await coaching.send_message(
        learner_id=LEARNER,
        session_id=started.state.session.session_id,
        text=learner_says,
    )
    if exchange.reply is None:
        print(f"the exchange failed: {exchange.unavailable_reason}")
        return 1
    reply = exchange.reply.content.strip()
    print(f"Learner: {learner_says}\n")
    print(f"Larry: {reply}\n")

    # ---- the checks -------------------------------------------------------
    failures: list[str] = []

    # 1. Nothing UC-07 *sent* carried the answer key. The learner's own turns are excluded: a
    #    learner who types a secret has disclosed it themselves, which proves nothing about us.
    sent = "\n".join(
        string
        for request in recorder.requests
        for string in request_strings(request, include_learner=False)
    ).lower()
    leaked_out = [secret for secret in ANSWER_KEY_SECRETS if secret.lower() in sent]
    if leaked_out:
        failures.append(f"answer-key material was SENT to the model: {leaked_out}")

    # 2. Nor did anything the model said back.
    said = f"{opening}\n{reply}".lower()
    leaked_back = [secret for secret in ANSWER_KEY_SECRETS if secret.lower() in said]
    if leaked_back:
        failures.append(f"the coach's replies contain answer-key material: {leaked_back}")

    # 3. It coached rather than answered.
    if "?" not in opening or "?" not in reply:
        failures.append("a turn did not ask a question")

    # 4. One learner message, answered once, counts once.
    if exchange.state.session.exchange_count != 1:
        failures.append(f"exchange_count was {exchange.state.session.exchange_count}, expected 1")

    report = started.sanitization or {}
    removed = report.get("removed_fields") or []

    print("--- checks ---")
    print(f"requests sent to the provider          : {len(recorder.requests)}")
    print(f"answer-bearing fields removed on entry : {len(removed)}")
    for field in removed:
        print(f"    - {field}")
    print(f"secrets guarded against                : {len(ANSWER_KEY_SECRETS)}")
    print(f"contamination findings                 : {report.get('contamination_findings')}")
    print()
    if failures:
        for failure in failures:
            print(f"FAIL  {failure}")
        return 1
    print("PASS  no answer-key string was sent to the model")
    print("PASS  no answer-key string came back from it")
    print("PASS  both turns asked a question rather than answering one")
    print("PASS  exactly one exchange was counted")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
