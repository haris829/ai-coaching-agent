"""The Claude coaching adapter — one file, activated by configuration.

    CoachingLLM  ->  Anthropic Messages API

This is the single file a deployment replaces to use a different provider, and the only place in
the system that speaks to one. There is no vendor SDK: the Messages API is one ``POST`` with a JSON
body, and adding a dependency to make that call would put a package's release cadence between this
system and its coach.

WHAT IT IS ALLOWED TO DO
------------------------
Forward ``CoachingRequest`` as it was given. ``request.context`` has already been through the
sanitiser, and an adapter that "enriched" it — reaching back into UC-02 for a bit more detail about
the question — would walk straight around the answer-key boundary. If an adapter needs something it
does not have, the field belongs in ``SafeCoachingContext`` where the sanitiser can vouch for it.

Two things it adds, both structural rather than content:

* the sanitised context is sent as a JSON block inside the system prompt, so the model sees the
  policy and the question material as one instruction and the learner's turns as conversation. A
  learner message can then never be mistaken for an instruction (§25);
* the conversation is mapped onto the API's ``user``/``assistant`` roles. UC-07 names its roles
  LEARNER and COACH, and the mapping lives here so the domain never learns a vendor's schema.

WHAT IT MUST NEVER DO
---------------------
Return a placeholder, a cached reply or an apology dressed as teaching. Every failure raises, and
``CoachingService`` turns that into a controlled "coaching is temporarily unavailable" state (§6,
§27). Specifically:

* unreachable, refused, rate-limited, or a non-2xx response → ``CoachingServiceUnavailableError``
* no answer within ``timeout_seconds``                      → ``CoachingTimeoutError``
* a 2xx with nothing usable in it                           → ``InvalidCoachingResponseError``

**No provider text ever reaches the learner or the error envelope.** An AI provider's error body
can echo back the prompt it was sent, so forwarding one would open an error-path route around the
sanitiser. Status codes and error *types* go to ``log_context``; bodies are read only to decide
which of the three failures above it is, and are then dropped.

AVAILABILITY IS CHEAP AND DOES NOT CALL OUT
-------------------------------------------
``is_available`` is on the path of every eligibility read, including the one a result screen makes
before showing a coaching button. It answers from configuration and from the last observed failure —
never by calling the provider, which would put a network round trip behind a page load and bill for
it. A run of consecutive failures suppresses further attempts for a short cool-off, so an outage
degrades to "coaching unavailable" quickly instead of making every learner wait for a timeout.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.time import Clock, SystemClock
from app.modules.coaching.domain.errors import (
    CoachingServiceUnavailableError,
    CoachingTimeoutError,
    InvalidCoachingResponseError,
)
from app.modules.coaching.integration.llm import CoachingCompletion, CoachingRequest

logger = get_logger(__name__)

PROVIDER_ANTHROPIC = "anthropic"

#: The Messages API version header. Pinned, because an unpinned API version is a silent behaviour
#: change waiting to happen in front of learners.
ANTHROPIC_VERSION = "2023-06-01"

#: Consecutive failures after which ``is_available`` reports false without trying again.
FAILURE_THRESHOLD = 3

#: Seconds to stay unavailable once the threshold is reached. Short enough that a brief provider
#: blip clears itself without an operator, long enough that an outage is not re-probed per request.
COOL_OFF_SECONDS = 60.0

#: How UC-07's roles map onto the API's. There is no system *turn*: the policy is a request field,
#: so nothing a learner types can arrive as an instruction.
_ROLES = {"LEARNER": "user", "COACH": "assistant"}


class AnthropicCoachingLLM:
    """``CoachingLLM`` over the Anthropic Messages API."""

    __slots__ = ("_settings", "_clock", "_client", "_failures", "_unavailable_until")

    def __init__(
        self,
        settings: Settings,
        *,
        clock: Clock | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock or SystemClock()
        #: Injectable so a test can drive this adapter against a transport double rather than a
        #: network. Left as ``None`` in production, where a client is built per call.
        self._client = client
        self._failures = 0
        self._unavailable_until = 0.0

    #: Read by the health endpoint so an operator can see at a glance that a provider is bound.
    @property
    def configured(self) -> bool:
        return bool(self._settings.coaching_llm_api_key)

    # ---- CoachingLLM ------------------------------------------------------

    async def is_available(self) -> bool:
        """Whether coaching can currently be offered. Never raises, never calls the provider."""
        if not self.configured:
            return False
        return self._clock.now().timestamp() >= self._unavailable_until

    async def generate_response(self, request: CoachingRequest) -> CoachingCompletion:
        """Produce the coach's next turn, or raise. See the module docstring."""
        if not self.configured:
            raise CoachingServiceUnavailableError(
                reason="NO_API_KEY", session_id=request.session_id
            )

        payload = self._payload(request)
        timeout = request.timeout_seconds or self._settings.coaching_llm_timeout_seconds

        try:
            response = await self._post(payload, timeout=timeout)
        except httpx.TimeoutException as exc:
            self._record_failure()
            raise CoachingTimeoutError(
                session_id=request.session_id, timeout_seconds=timeout
            ) from exc
        except httpx.HTTPError as exc:
            self._record_failure()
            raise CoachingServiceUnavailableError(
                reason=type(exc).__name__, session_id=request.session_id
            ) from exc

        if response.status_code >= 400:
            self._record_failure()
            # The status code is operational; the body is not forwarded, logged or shown.
            raise CoachingServiceUnavailableError(
                reason=f"HTTP_{response.status_code}", session_id=request.session_id
            )

        completion = self._completion(response, session_id=request.session_id)
        self._failures = 0
        self._unavailable_until = 0.0
        return completion

    # ---- internals --------------------------------------------------------

    async def _post(self, payload: dict[str, Any], *, timeout: float) -> httpx.Response:
        headers = {
            "x-api-key": self._settings.coaching_llm_api_key or "",
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        url = f"{self._settings.coaching_llm_base_url.rstrip('/')}/v1/messages"
        if self._client is not None:
            return await self._client.post(url, json=payload, headers=headers, timeout=timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(url, json=payload, headers=headers)

    def _payload(self, request: CoachingRequest) -> dict[str, Any]:
        """The request body. The context travels inside ``system``, never as a learner turn."""
        system = request.system_prompt
        if request.context:
            system = (
                f"{system}\n\nQUESTION MATERIAL (sanitised; contains no answer key):\n"
                f"{json.dumps(dict(request.context), indent=2, default=str, ensure_ascii=False)}"
            )

        messages = [
            {"role": _ROLES.get(str(turn.get("role")), "user"), "content": str(turn.get("content"))}
            for turn in request.conversation
            if str(turn.get("content") or "").strip()
        ]
        if not messages:
            # The Messages API requires at least one turn, and the opening coach question has no
            # learner turn before it. Asking for the opening move explicitly is the honest way to
            # say "begin": the policy in ``system`` decides what that move looks like.
            messages = [{"role": "user", "content": "Begin coaching me on this question."}]

        return {
            "model": self._settings.coaching_llm_model,
            "max_tokens": self._settings.coaching_llm_max_output_tokens,
            "system": system,
            "messages": messages,
        }

    def _completion(
        self, response: httpx.Response, *, session_id: str | None
    ) -> CoachingCompletion:
        try:
            body = response.json()
        except ValueError as exc:
            raise InvalidCoachingResponseError(
                reason="NOT_JSON", session_id=session_id
            ) from exc

        blocks = body.get("content")
        text = ""
        if isinstance(blocks, list):
            text = "".join(
                str(block.get("text") or "")
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            )
        if not text.strip():
            # A 2xx with no text in it — a refusal, a tool-use-only reply, an empty completion.
            # Reported as unusable rather than passed on as an empty coaching turn.
            raise InvalidCoachingResponseError(reason="NO_TEXT_CONTENT", session_id=session_id)

        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        return CoachingCompletion(
            text=text,
            model=body.get("model"),
            provider=PROVIDER_ANTHROPIC,
            finish_reason=body.get("stop_reason"),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )

    def _record_failure(self) -> None:
        self._failures += 1
        if self._failures >= FAILURE_THRESHOLD:
            self._unavailable_until = self._clock.now().timestamp() + COOL_OFF_SECONDS
            logger.warning(
                "coaching.provider_cooling_off",
                extra={"failures": self._failures, "cool_off_seconds": COOL_OFF_SECONDS},
            )
