"""The AWS Bedrock coaching adapter.

    CoachingLLM  ->  Bedrock Runtime InvokeModel  ->  Claude

A sibling of ``llm_anthropic.py``, for deployments whose Claude access is through their own AWS
account rather than through Anthropic directly. Everything the port promises is identical; three
things about the transport are not, and they are the whole of this file:

============  ==========================================  =====================================
              Anthropic direct                            Bedrock
============  ==========================================  =====================================
Host          ``api.anthropic.com/v1/messages``            ``bedrock-runtime.{region}.amazonaws
                                                           .com/model/{id}/invoke``
Auth          ``x-api-key`` + ``anthropic-version`` header ``Authorization: Bearer`` (a long-term
                                                           Bedrock API key)
Model         ``model`` in the body                        in the **URL path** — usually an
                                                           inference-profile ARN, and the body
                                                           carries ``anthropic_version`` instead
============  ==========================================  =====================================

The request body is otherwise the Messages body verbatim — ``system``, ``messages``, ``max_tokens``
— and the response is the same shape, so the parsing and the failure mapping are the same decisions
made the same way. That near-identity is why a second provider costs one file rather than a rewrite,
and it is exactly what the ``CoachingLLM`` port was for.

WHY NOT boto3
-------------
A long-term Bedrock API key is a bearer token: one ``POST`` with a header. Pulling in the AWS SDK to
send it would add a large dependency, its credential-resolution chain and its own release cadence,
for no behaviour we need. ``httpx`` is already a runtime dependency for the Anthropic adapter.

Everything the sibling module says about **never inventing a reply** applies here unchanged. Every
failure raises; the service turns that into a controlled "coaching is temporarily unavailable".
No provider text reaches the learner or the error envelope, because an AWS error body can quote
the request back — and the request contains the coaching prompt.
"""

from __future__ import annotations

import urllib.parse
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
from app.modules.coaching.integration.llm_anthropic import (
    _ROLES,
    COOL_OFF_SECONDS,
    FAILURE_THRESHOLD,
)

logger = get_logger(__name__)

PROVIDER_BEDROCK = "bedrock"

#: The Bedrock-side version marker for Anthropic models. Pinned for the reason its Anthropic
#: counterpart is: an unpinned API version is a silent behaviour change in front of learners.
BEDROCK_ANTHROPIC_VERSION = "bedrock-2023-05-31"


class BedrockCoachingLLM:
    """``CoachingLLM`` over the Bedrock Runtime ``InvokeModel`` endpoint."""

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
        #: Injectable so a test can drive this against a transport double rather than a network.
        self._client = client
        self._failures = 0
        self._unavailable_until = 0.0

    #: Read by the health endpoint. Bedrock needs a model id as well as a key — the id is an ARN,
    #: and there is no sensible default for somebody else's inference profile.
    @property
    def configured(self) -> bool:
        return bool(self._settings.coaching_llm_api_key) and bool(
            self._settings.coaching_llm_model
        )

    # ---- CoachingLLM ------------------------------------------------------

    async def is_available(self) -> bool:
        """Whether coaching can currently be offered. Never raises, never calls the provider."""
        if not self.configured:
            return False
        return self._clock.now().timestamp() >= self._unavailable_until

    async def generate_response(self, request: CoachingRequest) -> CoachingCompletion:
        """Produce the coach's next turn, or raise."""
        if not self.configured:
            raise CoachingServiceUnavailableError(
                reason="NO_API_KEY_OR_MODEL", session_id=request.session_id
            )

        timeout = request.timeout_seconds or self._settings.coaching_llm_timeout_seconds
        try:
            response = await self._post(self._payload(request), timeout=timeout)
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
            # The status code is operational; the body is never forwarded, logged or shown.
            raise CoachingServiceUnavailableError(
                reason=f"HTTP_{response.status_code}", session_id=request.session_id
            )

        completion = self._completion(response, session_id=request.session_id)
        self._failures = 0
        self._unavailable_until = 0.0
        return completion

    # ---- internals --------------------------------------------------------

    def _url(self) -> str:
        """The InvokeModel endpoint for the configured model.

        The model id goes in the path and is usually an ARN, so it must be percent-encoded whole —
        the colons and slashes in ``arn:aws:bedrock:…/profile-id`` are not path separators here.
        """
        region = self._settings.coaching_llm_region.strip() or "us-east-1"
        model = urllib.parse.quote(self._settings.coaching_llm_model.strip(), safe="")
        host = f"https://bedrock-runtime.{region}.amazonaws.com"
        return f"{host}/model/{model}/invoke"

    async def _post(self, payload: dict[str, Any], *, timeout: float) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self._settings.coaching_llm_api_key or ''}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        url = self._url()
        if self._client is not None:
            return await self._client.post(url, json=payload, headers=headers, timeout=timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(url, json=payload, headers=headers)

    def _payload(self, request: CoachingRequest) -> dict[str, Any]:
        """The request body. Identical to the Messages body but for two fields — see the docstring.

        As in the Anthropic adapter, the sanitised context travels inside ``system`` and never as a
        learner turn, so nothing a learner types can arrive as an instruction.
        """
        import json

        system = request.system_prompt
        if request.context:
            system = (
                f"{system}\n\nQUESTION MATERIAL (sanitised; contains no answer key):\n"
                f"{json.dumps(dict(request.context), indent=2, default=str, ensure_ascii=False)}"
            )

        messages = [
            {
                "role": _ROLES.get(str(turn.get("role")), "user"),
                "content": str(turn.get("content")),
            }
            for turn in request.conversation
            if str(turn.get("content") or "").strip()
        ]
        if not messages:
            # Bedrock, like the Messages API, requires at least one turn, and the opening coach
            # question has no learner turn before it.
            messages = [{"role": "user", "content": "Begin coaching me on this question."}]

        return {
            "anthropic_version": BEDROCK_ANTHROPIC_VERSION,
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
            # A 2xx with nothing usable in it. Reported as unusable rather than passed on as an
            # empty coaching turn.
            raise InvalidCoachingResponseError(reason="NO_TEXT_CONTENT", session_id=session_id)

        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        return CoachingCompletion(
            text=text,
            model=body.get("model"),
            provider=PROVIDER_BEDROCK,
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
                extra={
                    "provider": PROVIDER_BEDROCK,
                    "failures": self._failures,
                    "cool_off_seconds": COOL_OFF_SECONDS,
                },
            )
