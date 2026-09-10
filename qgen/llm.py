"""The model boundary: one prompt in, one block of text out.

Everything above this file works on strings and dataclasses, so the whole pipeline can be tested
without a network. That is the only reason this is a separate module.

FAILURE IS NOT PAPERED OVER
---------------------------
No provider, no key, an outage, a timeout: every one of those raises. Nothing here falls back to
a canned bank of questions, because a "generated" question nobody can trace to a model call is
worse than no question at all.

A THROTTLE IS AN OUTAGE, NOT A CONTRACT FAILURE
------------------------------------------------
Five simultaneous requests are fine on this account; a sustained stream is throttled. A 429 that
survives its retries is reported as :class:`GenerationUnavailable` - 503, retryable - and never
as "the model would not follow the output contract". The difference is an operator waiting two
minutes versus an operator spending an afternoon debugging a prompt that was working.
"""

from __future__ import annotations

import contextlib
import time
import urllib.parse
from typing import Any, Protocol, runtime_checkable

import httpx

from .config import Settings
from .errors import GenerationFailed, GenerationUnavailable


@runtime_checkable
class QuestionLLM(Protocol):
    """What generation needs from a model. Deliberately one method."""

    configured: bool

    def complete(self, prompt: str, *, max_tokens: int) -> str: ...


class UnconfiguredLLM:
    """The default when no provider is set. Refuses, rather than inventing questions."""

    configured = False

    def complete(self, prompt: str, *, max_tokens: int) -> str:
        raise GenerationUnavailable(reason="NO_PROVIDER_CONFIGURED")


class BedrockLLM:
    """:class:`QuestionLLM` over Bedrock's ``InvokeModel``.

    Synchronous. The concurrency that makes a fifty-question run fast is a thread pool in
    :mod:`qgen.service`, not async here: five HTTP calls in five threads is the whole
    requirement, and an async client would add an event loop for no gain.
    """

    __slots__ = ("_settings", "_sleep")

    #: Bedrock's version marker for Anthropic models.
    ANTHROPIC_VERSION = "bedrock-2023-05-31"

    #: How many times to try a throttled request, and how long to wait between attempts.
    #:
    #: Concurrent batches are what make a large run fast and are also what earns a 429. Losing a
    #: batch to a limit that clears in a second is the wrong outcome when waiting a second is
    #: available. Three attempts with a widening pause, then it gives up rather than hammering a
    #: provider that is asking for quiet.
    THROTTLE_ATTEMPTS = 3
    THROTTLE_BACKOFF_SECONDS = (1.0, 3.0)

    #: Worth retrying: 429 is the throttle, 5xx is the provider having a moment. Everything else -
    #: a bad key, a model that does not exist, a malformed request - fails identically next time,
    #: so retrying only wastes the operator's wait.
    RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

    def __init__(self, settings: Settings, *, sleep=time.sleep) -> None:
        self._settings = settings
        # Injected so the retry tests do not actually wait four seconds each.
        self._sleep = sleep

    @property
    def configured(self) -> bool:
        return self._settings.llm_configured

    def complete(self, prompt: str, *, max_tokens: int) -> str:
        if not self.configured:
            raise GenerationUnavailable(reason="NO_API_KEY_OR_MODEL")

        region = self._settings.llm_region
        model = urllib.parse.quote(self._settings.llm_model, safe="")
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
        headers = {
            "Authorization": f"Bearer {self._settings.llm_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        timeout = self._settings.llm_timeout_seconds

        response = None
        for attempt in range(self.THROTTLE_ATTEMPTS):
            try:
                response = httpx.post(url, json=body, headers=headers, timeout=timeout)
            except httpx.TimeoutException as exc:
                raise GenerationUnavailable(reason="TIMEOUT") from exc
            except httpx.HTTPError as exc:
                raise GenerationUnavailable(reason=type(exc).__name__) from exc

            if response.status_code not in self.RETRYABLE_STATUSES:
                break
            if attempt == self.THROTTLE_ATTEMPTS - 1:
                break
            self._sleep(self._wait_for(response, attempt))

        if response is None:  # pragma: no cover - the loop always assigns or raises
            raise GenerationUnavailable(reason="NO_RESPONSE")

        if response.status_code >= 400:
            # The status is reported; the body is not. A provider error can echo the prompt back,
            # and the prompt is not something to put in an API response.
            raise GenerationUnavailable(reason=f"HTTP_{response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise GenerationFailed(reason="NOT_JSON") from exc

        blocks = payload.get("content")
        text = ""
        if isinstance(blocks, list):
            text = "".join(
                str(block.get("text") or "")
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            )
        if not text.strip():
            raise GenerationFailed(reason="NO_TEXT_CONTENT")
        return text

    def _wait_for(self, response: httpx.Response, attempt: int) -> float:
        """How long to pause before the next attempt.

        ``Retry-After`` wins when the provider sends one, because a provider saying how long to
        wait knows better than a fixed table - but capped, so a header asking for an hour does
        not hang the run. A non-numeric value (the HTTP-date form) is ignored rather than parsed:
        the table is a reasonable wait either way.
        """
        wait = self.THROTTLE_BACKOFF_SECONDS[
            min(attempt, len(self.THROTTLE_BACKOFF_SECONDS) - 1)
        ]
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            with contextlib.suppress(ValueError):
                wait = max(wait, min(float(retry_after), 30.0))
        return wait


def build_llm(settings: Settings) -> QuestionLLM:
    """The model this configuration binds, or one that honestly refuses."""
    if settings.llm_provider == "bedrock" and settings.llm_configured:
        return BedrockLLM(settings)
    return UnconfiguredLLM()


def max_tokens_for(count: int) -> int:
    """An output budget for ``count`` questions.

    A four-option question with an explanation runs to roughly 300 tokens. The allowance is
    generous because a reply truncated mid-JSON parses to nothing and the whole batch is wasted -
    the cost of over-asking is zero, since only what is generated is billed.
    """
    return max(1500, count * 450 + 500)
