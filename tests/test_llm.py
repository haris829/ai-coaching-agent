"""The model client: what it retries, what it gives up on, and how it classifies a failure.

The classification is the part with operational consequences. A throttle reported as a contract
failure sends somebody to debug a prompt that was working perfectly.
"""

from __future__ import annotations

import httpx
import pytest

from qgen.config import Settings
from qgen.errors import GenerationFailed, GenerationUnavailable
from qgen.llm import BedrockLLM, UnconfiguredLLM, build_llm, max_tokens_for

CONFIGURED = Settings(
    database_url="postgresql://x/y",
    llm_provider="bedrock",
    llm_api_key="test-key",
    llm_model="arn:aws:bedrock:us-east-1:1:application-inference-profile/abc",
)


def text_reply(body: str = '{"questions": []}') -> httpx.Response:
    return httpx.Response(200, json={"content": [{"type": "text", "text": body}]})


class Calls(list):
    """Every request the client made, with the replies it will be given queued up."""

    def __init__(self) -> None:
        super().__init__()
        self.queue: list[httpx.Response | Exception] = []


@pytest.fixture
def calls(monkeypatch) -> Calls:
    made = Calls()

    def fake_post(url, *, json, headers, timeout):
        made.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        result = made.queue.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(httpx, "post", fake_post)
    return made


class Client(BedrockLLM):
    """The real client, with its pauses recorded instead of waited out."""

    __slots__ = ("slept",)

    def __init__(self, settings: Settings = CONFIGURED) -> None:
        slept: list[float] = []
        super().__init__(settings, sleep=slept.append)
        self.slept = slept


def client() -> Client:
    return Client()


# ---------------------------------------------------------------------------
# Not configured
# ---------------------------------------------------------------------------


def test_with_no_provider_configured_the_client_refuses_rather_than_inventing_anything():
    with pytest.raises(GenerationUnavailable) as caught:
        UnconfiguredLLM().complete("anything", max_tokens=100)

    assert caught.value.status_code == 503
    assert caught.value.reason == "NO_PROVIDER_CONFIGURED"


@pytest.mark.parametrize(
    "settings",
    [
        Settings(database_url="x"),
        Settings(database_url="x", llm_provider="bedrock"),
        Settings(database_url="x", llm_provider="bedrock", llm_api_key="k"),
        Settings(database_url="x", llm_provider="openai", llm_api_key="k", llm_model="m"),
    ],
    ids=["nothing", "no-key", "no-model", "unknown-provider"],
)
def test_a_half_configured_provider_binds_the_refusing_client(settings):
    """Better a clean refusal now than a confusing failure thirty seconds into a run."""
    assert isinstance(build_llm(settings), UnconfiguredLLM)


def test_a_fully_configured_bedrock_provider_binds_the_real_client():
    assert isinstance(build_llm(CONFIGURED), BedrockLLM)


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_the_text_blocks_of_the_reply_are_returned_joined(calls):
    calls.queue.append(
        httpx.Response(
            200,
            json={
                "content": [
                    {"type": "text", "text": "part one "},
                    {"type": "thinking", "text": "ignored"},
                    {"type": "text", "text": "part two"},
                ]
            },
        )
    )

    assert client().complete("prompt", max_tokens=500) == "part one part two"


def test_the_request_carries_the_prompt_the_key_and_the_region(calls):
    calls.queue.append(text_reply())

    client().complete("write me questions", max_tokens=999)

    request = calls[0]
    assert "bedrock-runtime.us-east-1.amazonaws.com" in request["url"]
    assert request["json"]["messages"] == [{"role": "user", "content": "write me questions"}]
    assert request["json"]["max_tokens"] == 999
    assert request["headers"]["Authorization"] == "Bearer test-key"


def test_the_model_arn_is_url_encoded_because_it_contains_slashes(calls):
    calls.queue.append(text_reply())

    client().complete("prompt", max_tokens=100)

    assert "application-inference-profile%2Fabc" in calls[0]["url"]


# ---------------------------------------------------------------------------
# Retrying
# ---------------------------------------------------------------------------


def test_a_throttled_request_is_retried_and_succeeds(calls):
    calls.queue.extend([httpx.Response(429), text_reply("ok")])
    llm = client()

    assert llm.complete("prompt", max_tokens=100) == "ok"
    assert len(calls) == 2
    assert llm.slept == [1.0]


def test_the_pause_widens_between_attempts(calls):
    calls.queue.extend([httpx.Response(429), httpx.Response(429), text_reply("ok")])
    llm = client()

    llm.complete("prompt", max_tokens=100)

    assert llm.slept == [1.0, 3.0]


def test_retry_after_is_honoured_when_the_provider_sends_one(calls):
    calls.queue.extend([httpx.Response(429, headers={"Retry-After": "7"}), text_reply("ok")])
    llm = client()

    llm.complete("prompt", max_tokens=100)

    assert llm.slept == [7.0]


def test_an_absurd_retry_after_is_capped_so_a_run_cannot_hang(calls):
    calls.queue.extend([httpx.Response(429, headers={"Retry-After": "3600"}), text_reply("ok")])
    llm = client()

    llm.complete("prompt", max_tokens=100)

    assert llm.slept == [30.0]


def test_a_retry_after_date_is_ignored_rather_than_parsed(calls):
    header = {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
    calls.queue.extend([httpx.Response(429, headers=header), text_reply("ok")])
    llm = client()

    llm.complete("prompt", max_tokens=100)

    assert llm.slept == [1.0]


def test_a_throttle_that_never_clears_is_reported_as_retryable_and_not_as_a_prompt_problem(calls):
    calls.queue.extend([httpx.Response(429), httpx.Response(429), httpx.Response(429)])

    with pytest.raises(GenerationUnavailable) as caught:
        client().complete("prompt", max_tokens=100)

    assert len(calls) == 3
    assert caught.value.status_code == 503
    assert caught.value.retryable is True
    assert caught.value.reason == "HTTP_429"


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_a_provider_wobble_is_retried(calls, status):
    calls.queue.extend([httpx.Response(status), text_reply("ok")])

    assert client().complete("prompt", max_tokens=100) == "ok"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_a_fault_that_will_repeat_identically_is_not_retried(calls, status):
    """A bad key or a model that does not exist fails the same way three times running."""
    calls.queue.append(httpx.Response(status))

    with pytest.raises(GenerationUnavailable) as caught:
        client().complete("prompt", max_tokens=100)

    assert len(calls) == 1
    assert caught.value.reason == f"HTTP_{status}"


def test_a_provider_error_body_is_never_forwarded(calls):
    """It can echo the prompt back, and the prompt is not something to put in a response."""
    calls.queue.append(httpx.Response(400, text="prompt was: Write 10 questions for ..."))

    with pytest.raises(GenerationUnavailable) as caught:
        client().complete("prompt", max_tokens=100)

    assert "Write 10 questions" not in str(caught.value)


def test_a_timeout_is_unavailable_not_failed(calls):
    calls.queue.append(httpx.TimeoutException("too slow"))

    with pytest.raises(GenerationUnavailable) as caught:
        client().complete("prompt", max_tokens=100)

    assert caught.value.reason == "TIMEOUT"


def test_a_transport_error_is_unavailable(calls):
    calls.queue.append(httpx.ConnectError("no route"))

    with pytest.raises(GenerationUnavailable) as caught:
        client().complete("prompt", max_tokens=100)

    assert caught.value.status_code == 503


# ---------------------------------------------------------------------------
# A reply that arrives but says nothing
# ---------------------------------------------------------------------------


def test_a_reply_that_is_not_json_is_a_contract_failure_not_an_outage(calls):
    calls.queue.append(httpx.Response(200, text="<html>gateway</html>"))

    with pytest.raises(GenerationFailed) as caught:
        client().complete("prompt", max_tokens=100)

    assert caught.value.status_code == 502


def test_a_reply_with_no_text_content_is_a_contract_failure(calls):
    calls.queue.append(httpx.Response(200, json={"content": []}))

    with pytest.raises(GenerationFailed) as caught:
        client().complete("prompt", max_tokens=100)

    assert caught.value.reason == "NO_TEXT_CONTENT"


# ---------------------------------------------------------------------------
# The output budget
# ---------------------------------------------------------------------------


def test_the_token_budget_grows_with_the_number_of_questions():
    assert max_tokens_for(1) < max_tokens_for(10) < max_tokens_for(50)


def test_even_one_question_gets_room_to_finish_its_json():
    # A reply truncated mid-JSON parses to nothing, and the whole batch is wasted.
    assert max_tokens_for(1) >= 1500
