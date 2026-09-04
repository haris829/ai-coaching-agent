"""The model boundary for question generation.

A port and one adapter, exactly as UC-07 has for coaching. Two providers already exist for
*coaching*; generation needs its own boundary rather than borrowing UC-07's, because a capability
that imported another capability's port outside its own ``integration/`` package would be the
boundary violation the architecture tests exist to catch.

The transport is the same three lines against Bedrock. What differs is everything around it: no
conversation, no answer-key sanitiser, no coaching policy — and, critically, the opposite
requirement about answers. UC-07 must never learn the correct answer; generation must *produce* it.
Sharing one adapter between two requirements that inverted would be how the sanitiser eventually
gets bypassed.

FAILURE IS NOT PAPERED OVER
---------------------------
No provider, no key, an outage, a timeout, or a reply that parses to nothing: every one of those
raises. Nothing here falls back to a canned question bank, because a generated quiz nobody can trace
to a model call is worse than no quiz.
"""

from __future__ import annotations

import contextlib
import time
import urllib.parse
from typing import Any, Protocol, runtime_checkable

import httpx

from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)


class QuestionGenerationUnavailableError(AppError):
    """503 — the model could not be reached, or none is configured.

    Retryable: nothing has been written, so repeating the request is safe.
    """

    status_code = 503
    code = "QUESTION_GENERATION_UNAVAILABLE"
    retryable = True

    def __init__(self, *, reason: str) -> None:
        super().__init__(
            "Question generation is temporarily unavailable. Nothing was saved — please try "
            "again shortly.",
            log_context={"reason": reason},
        )


class QuestionGenerationFailedError(AppError):
    """502 — the model answered, but with nothing usable.

    Distinct from unavailable because the operational response differs: an outage clears itself, a
    model that will not follow the output contract needs a person to look at the prompt.
    """

    status_code = 502
    code = "QUESTION_GENERATION_FAILED"
    retryable = True

    def __init__(self, *, reason: str, accepted: int = 0, wanted: int = 0) -> None:
        super().__init__(
            "The model did not return usable questions. Nothing was saved.",
            context={"accepted": accepted, "wanted": wanted},
            log_context={"reason": reason},
        )


@runtime_checkable
class QuestionGeneratorLLM(Protocol):
    """What generation needs from a model: one prompt in, one block of text out."""

    def complete(self, prompt: str, *, max_tokens: int) -> str: ...


class UnconfiguredGenerator:
    """The default. Refuses, rather than inventing questions from nowhere."""

    configured = False

    def complete(self, prompt: str, *, max_tokens: int) -> str:
        raise QuestionGenerationUnavailableError(reason="NO_PROVIDER_CONFIGURED")


class BedrockQuestionGenerator:
    """``QuestionGeneratorLLM`` over Bedrock's ``InvokeModel``.

    Synchronous on purpose. Generation runs inside an administrative request that is already slow —
    twenty questions is tens of seconds of model time — and the rest of this application's request
    handling is synchronous. An async adapter here would buy nothing and would need the thread
    offload UC-07's repositories carry.
    """

    __slots__ = ("_settings",)

    #: Bedrock's version marker for Anthropic models, pinned as it is in UC-07's adapter.
    ANTHROPIC_VERSION = "bedrock-2023-05-31"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def configured(self) -> bool:
        return bool(self._settings.coaching_llm_api_key) and bool(
            self._settings.coaching_llm_model
        )

    #: How many times to retry a throttled request, and how long to wait between attempts.
    #:
    #: Concurrent batches are what make a fifty-question request fast, and they are also what
    #: earns a 429: five simultaneous calls is well within Bedrock's per-account limit on a quiet
    #: account and over it on a busy one. Measured on a real account, a sweep of 33 one-question
    #: requests at five at a time lost 20 of them to throttling, and every one succeeded when
    #: retried.
    #:
    #: Losing a batch to a limit that clears in a second is the wrong outcome when waiting a second
    #: is available. Three attempts with a widening pause, and then it gives up rather than
    #: hammering a provider that is asking for quiet.
    THROTTLE_ATTEMPTS = 3
    THROTTLE_BACKOFF_SECONDS = (1.0, 3.0)

    #: Statuses worth retrying. 429 is the throttle; 500, 502, 503 and 504 are the provider having
    #: a moment. Everything else — a bad key, a model that does not exist, a malformed request — is
    #: a fault that will repeat identically, so retrying it only wastes time.
    RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

    def complete(self, prompt: str, *, max_tokens: int) -> str:
        if not self.configured:
            raise QuestionGenerationUnavailableError(reason="NO_API_KEY_OR_MODEL")

        region = self._settings.coaching_llm_region.strip() or "us-east-1"
        model = urllib.parse.quote(self._settings.coaching_llm_model.strip(), safe="")
        url = f"https://bedrock-runtime.{region}.amazonaws.com/model/{model}/invoke"
        body: dict[str, Any] = {
            "anthropic_version": self.ANTHROPIC_VERSION,
            "max_tokens": max_tokens,
            "system": (
                "You write assessment questions for professional qualifications. You return only "
                "the JSON object you were asked for, with no commentary."
            ),
            "messages": [{"role": "user", "content": prompt}],
        }

        # Generation is slow by nature, so it gets its own timeout rather than the coaching one:
        # twenty questions is not a chat turn.
        timeout = max(60.0, self._settings.coaching_llm_timeout_seconds * 6)
        headers = {
            "Authorization": f"Bearer {self._settings.coaching_llm_api_key or ''}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        response = None
        for attempt in range(self.THROTTLE_ATTEMPTS):
            try:
                response = httpx.post(url, json=body, headers=headers, timeout=timeout)
            except httpx.TimeoutException as exc:
                raise QuestionGenerationUnavailableError(reason="TIMEOUT") from exc
            except httpx.HTTPError as exc:
                raise QuestionGenerationUnavailableError(reason=type(exc).__name__) from exc

            if response.status_code not in self.RETRYABLE_STATUSES:
                break
            if attempt == self.THROTTLE_ATTEMPTS - 1:
                break
            # Widening pause. `Retry-After` is honoured when the provider sends one, because a
            # provider saying how long to wait knows better than a fixed table does.
            wait = self.THROTTLE_BACKOFF_SECONDS[
                min(attempt, len(self.THROTTLE_BACKOFF_SECONDS) - 1)
            ]
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                # A non-numeric `Retry-After` (the HTTP-date form) is ignored rather than parsed:
                # the table below is a reasonable wait either way, and a date is not worth the
                # code to handle for a value this provider sends in seconds.
                with contextlib.suppress(ValueError):
                    wait = max(wait, min(float(retry_after), 30.0))
            logger.info(
                "question_generation.retrying",
                extra={"status": response.status_code, "attempt": attempt + 1},
            )
            time.sleep(wait)

        if response is None:  # pragma: no cover - the loop always assigns or raises
            raise QuestionGenerationUnavailableError(reason="NO_RESPONSE")

        if response.status_code >= 400:
            # The status is operational. The body is not forwarded: a provider error can echo the
            # prompt back, and the prompt is not something to put in an API response.
            raise QuestionGenerationUnavailableError(reason=f"HTTP_{response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise QuestionGenerationFailedError(reason="NOT_JSON") from exc

        blocks = payload.get("content")
        text = ""
        if isinstance(blocks, list):
            text = "".join(
                str(block.get("text") or "")
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            )
        if not text.strip():
            raise QuestionGenerationFailedError(reason="NO_TEXT_CONTENT")
        return text


def build_generator(settings: Settings) -> QuestionGeneratorLLM:
    """The generator this deployment binds, or one that honestly refuses.

    Reuses UC-07's provider settings deliberately: an operator who has configured a model for
    coaching has configured *the* model, and asking them to name it twice is how the two drift
    apart.
    """
    provider = settings.coaching_llm_provider.strip().lower()
    if provider == "bedrock" and settings.coaching_llm_api_key:
        return BedrockQuestionGenerator(settings)
    return UnconfiguredGenerator()
