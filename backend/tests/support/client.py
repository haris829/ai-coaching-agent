"""A thin, readable HTTP client for the UC-03 API.

Tests exercise the service through real HTTP requests, so what they assert is exactly
what a future frontend will receive: status codes, error codes and payload shapes. This
wrapper keeps that intent legible instead of scattering URL strings through the tests.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

API = "/api/v1"


class ApiClient:
    """Issues learner-scoped requests against the UC-03 API.

    Authentication goes through the merged system's one identity seam: a bearer token that resolves
    to a learner in ``qa_users``. UC-03 previously trusted an ``X-Learner-Id`` header on the
    assumption a gateway had already authenticated; now that a real seam exists, using anything else
    would be a second, weaker way in.
    """

    __slots__ = ("_client", "learner_id", "_token", "_tokens")

    def __init__(
        self,
        client: TestClient,
        learner_id: str,
        token: str,
        *,
        tokens: dict[str, str] | None = None,
    ) -> None:
        self._client = client
        self.learner_id = learner_id
        self._token = token
        # A learner id -> bearer token directory. UC-03 authenticated by a header carrying the
        # learner id, so switching learner was free; real authentication needs a credential, and the
        # ownership tests should not have to thread one through every call site.
        self._tokens = {learner_id: token, **(tokens or {})}

    def as_learner(self, learner_id: str, token: str | None = None) -> ApiClient:
        """A client for a different learner, for ownership tests."""
        resolved = token or self._tokens.get(learner_id)
        if resolved is None:
            raise KeyError(
                f"No bearer token known for learner {learner_id!r}. "
                "Pass one explicitly, or register it in the `tokens` directory."
            )
        return ApiClient(self._client, learner_id, resolved, tokens=self._tokens)

    # ---- raw ---------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        authenticated: bool = True,
    ) -> Response:
        merged = dict(headers or {})
        if authenticated:
            merged.setdefault("Authorization", f"Bearer {self._token}")
        return self._client.request(method, path, json=json, params=params, headers=merged)

    # ---- attempts ----------------------------------------------------------

    def eligibility(self, quiz_id: str) -> Response:
        return self.request("GET", f"{API}/quizzes/{quiz_id}/attempt-eligibility")

    def create_attempt(self, quiz_id: str, **kwargs: Any) -> Response:
        return self.request("POST", f"{API}/attempts", json={"quizId": quiz_id}, **kwargs)

    def get_attempt(self, attempt_id: str) -> Response:
        return self.request("GET", f"{API}/attempts/{attempt_id}")

    def list_attempts(self, quiz_id: str) -> Response:
        return self.request("GET", f"{API}/attempts", params={"quizId": quiz_id})

    def active_attempt(self, quiz_id: str) -> Response:
        return self.request("GET", f"{API}/attempts/active", params={"quizId": quiz_id})

    def state(self, attempt_id: str) -> Response:
        return self.request("GET", f"{API}/attempts/{attempt_id}/state")

    def timing(self, attempt_id: str, *, client_time: str | None = None) -> Response:
        params = {"clientTime": client_time} if client_time else None
        return self.request("GET", f"{API}/attempts/{attempt_id}/timing", params=params)

    def set_cursor(self, attempt_id: str, position: int) -> Response:
        return self.request(
            "PUT", f"{API}/attempts/{attempt_id}/cursor", json={"position": position}
        )

    # ---- questions ---------------------------------------------------------

    def questions(self, attempt_id: str) -> Response:
        return self.request("GET", f"{API}/attempts/{attempt_id}/questions")

    def current_question(self, attempt_id: str) -> Response:
        return self.request("GET", f"{API}/attempts/{attempt_id}/questions/current")

    def question_at(self, attempt_id: str, position: int) -> Response:
        return self.request("GET", f"{API}/attempts/{attempt_id}/questions/at/{position}")

    def question(self, attempt_id: str, question_id: str) -> Response:
        return self.request("GET", f"{API}/attempts/{attempt_id}/questions/{question_id}")

    # ---- answers -----------------------------------------------------------

    def save_answer(
        self,
        attempt_id: str,
        question_id: str,
        response: Any,
        *,
        source: str | None = None,
        expected_revision: int | None = None,
    ) -> Response:
        body: dict[str, Any] = {"response": response}
        if source is not None:
            body["source"] = source
        if expected_revision is not None:
            body["expectedRevision"] = expected_revision
        return self.request(
            "PUT", f"{API}/attempts/{attempt_id}/questions/{question_id}/answer", json=body
        )

    def clear_answer(self, attempt_id: str, question_id: str) -> Response:
        return self.request(
            "DELETE", f"{API}/attempts/{attempt_id}/questions/{question_id}/answer"
        )

    def autosave(
        self, attempt_id: str, answers: list[dict[str, Any]], *, source: str | None = None
    ) -> Response:
        body: dict[str, Any] = {"answers": answers}
        if source is not None:
            body["source"] = source
        return self.request("POST", f"{API}/attempts/{attempt_id}/answers", json=body)

    def answers(self, attempt_id: str) -> Response:
        return self.request("GET", f"{API}/attempts/{attempt_id}/answers")

    def answer_revisions(self, attempt_id: str) -> Response:
        return self.request("GET", f"{API}/attempts/{attempt_id}/answers/revisions")

    # ---- flags -------------------------------------------------------------

    def set_flag(self, attempt_id: str, question_id: str, flagged: bool) -> Response:
        return self.request(
            "PUT",
            f"{API}/attempts/{attempt_id}/questions/{question_id}/flag",
            json={"flagged": flagged},
        )

    def unflag(self, attempt_id: str, question_id: str) -> Response:
        return self.request("DELETE", f"{API}/attempts/{attempt_id}/questions/{question_id}/flag")

    def flags(self, attempt_id: str) -> Response:
        return self.request("GET", f"{API}/attempts/{attempt_id}/flags")

    # ---- submission --------------------------------------------------------

    def preview_submission(self, attempt_id: str) -> Response:
        return self.request("GET", f"{API}/attempts/{attempt_id}/submission/preview")

    def submit(
        self,
        attempt_id: str,
        *,
        confirmed: bool = True,
        idempotency_key: str | None = None,
        header_key: str | None = None,
    ) -> Response:
        body: dict[str, Any] = {"confirmed": confirmed}
        if idempotency_key is not None:
            body["idempotencyKey"] = idempotency_key
        headers = {"Idempotency-Key": header_key} if header_key else None
        return self.request(
            "POST", f"{API}/attempts/{attempt_id}/submission", json=body, headers=headers
        )

    def retry_submission(self, attempt_id: str, *, idempotency_key: str | None = None) -> Response:
        body = {"idempotencyKey": idempotency_key} if idempotency_key else {}
        return self.request(
            "POST", f"{API}/attempts/{attempt_id}/submission/retry", json=body
        )

    def submission(self, attempt_id: str) -> Response:
        return self.request("GET", f"{API}/attempts/{attempt_id}/submission")


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------


def error_code(response: Response) -> str:
    """Extract the machine-readable error code, failing loudly if absent."""
    payload = response.json()
    assert "error" in payload, f"Expected an error envelope, received: {payload}"
    return payload["error"]["code"]


def assert_error(response: Response, status: int, code: str) -> dict[str, Any]:
    """Assert an error response and return its ``error`` object."""
    assert response.status_code == status, (
        f"Expected HTTP {status}, got {response.status_code}: {response.text}"
    )
    payload = response.json()
    assert payload["error"]["code"] == code, (
        f"Expected error code {code}, got {payload['error']['code']}: {payload}"
    )
    # Every error carries the correlation id and never a traceback.
    assert "requestId" in payload["error"]
    assert "traceback" not in response.text.lower()
    return payload["error"]


def assert_ok(response: Response, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, (
        f"Expected HTTP {status}, got {response.status_code}: {response.text}"
    )
    return response.json()
