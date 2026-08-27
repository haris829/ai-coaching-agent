"""A real ``SummaryGenerator`` over HTTP. Wired, and disabled by default.

Registered as ``http``; the shipped default is ``UC09_SUMMARY_GENERATOR=fake``,
and the whole test suite runs on the deterministic generator with no API key.

**No URL is imagined here.** The base URL and the path both come from
configuration, and the adapter refuses to run when they are unset rather than
guessing an endpoint. What it does define is the *wire contract this component
needs* - documented in ``docs/SHARED_CONTRACT.md`` - so that whoever stands a
generation service up knows exactly what to accept and return.

The important property: output from this adapter goes through exactly the same
grounding check as output from the fake. A generator does not become trusted by
being real. If it returns a topic that is not in the tag record or an authority
that was not cited, the response is rejected whole and the summary falls back to
the question log.

Request body sent to ``{base_url}{path}``::

    {
      "session": {"session_id", "naric_level", "explanation_profile",
                  "started_at", "ended_at", "course_title"},
      "covers_interactions_through": "<iso8601>",
      "interactions": [{"interaction_id", "occurred_at", "question_text",
                        "topic_tags": [...], "concept_tags": [...]}],
      "citations":    [{"resource_id", "kind", "citation", "title",
                        "cited_in_interaction_ids": [...]}],
      "gap_suggestions": [{"suggestion_id", "label", "rationale"}] | null
    }

Expected 200 response::

    {
      "topics_covered":       [Topic],
      "key_concepts":         [Concept],
      "resources_referenced": [Resource],
      "next_steps":           [Suggestion],
      "section_notes":        {"<section key>": "<text>"}
    }

with the element shapes of ``docs/SHARED_CONTRACT.md`` sections 2.2 to 2.5.
"""

from __future__ import annotations

from typing import Any

from uc09_summary.config import Settings
from uc09_summary.domain.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from uc09_summary.domain.grounding import SessionData
from uc09_summary.domain.models import SummaryContent
from uc09_summary.domain.naric import explanation_profile_for

PORT = "summary_generator"


class ConfiguredHttpSummaryGenerator:
    """Calls a configured generation service and maps its answer onto the contract."""

    @classmethod
    def from_settings(cls, settings: Settings) -> ConfiguredHttpSummaryGenerator:
        """Build from configuration.

        Raises:
            ProviderUnavailable: the endpoint is not configured. Refusing at
                startup is deliberate: a generation service that silently does
                nothing would produce question-log fallbacks that look like an
                upstream outage.
        """
        if not settings.upstream_base_url.strip():
            raise ProviderUnavailable(
                PORT,
                "summary_generator=http requires UC09_UPSTREAM_BASE_URL to be set. "
                "No endpoint is assumed.",
            )
        return cls(
            base_url=settings.upstream_base_url.rstrip("/"),
            path=settings.summary_generator_path,
            api_key=settings.upstream_api_key,
            timeout=settings.provider_timeout_seconds,
        )

    def __init__(self, *, base_url: str, path: str, api_key: str, timeout: float) -> None:
        self._base_url = base_url
        self._path = path
        self._api_key = api_key
        self._timeout = timeout

    def generate(self, session_data: SessionData) -> SummaryContent:
        """Ask the configured service for the four sections."""
        import httpx  # imported here so the mock configuration never needs it

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            # Authorisation stays server-side, inside the adapter.
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            response = httpx.post(
                f"{self._base_url}{self._path}",
                json=_request_body(session_data),
                headers=headers,
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(PORT, "upstream_deadline_exceeded") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(PORT, "upstream_transport_error") from exc

        if response.status_code >= 500:
            raise ProviderUnavailable(PORT, "upstream_error_response")
        if response.status_code != 200:
            raise ProviderInvalidResponse(PORT, "upstream_unexpected_status")

        try:
            return SummaryContent.model_validate(response.json())
        except Exception as exc:
            raise ProviderInvalidResponse(PORT, "payload_mapping_failed") from exc

    @classmethod
    def conformance_profile(cls) -> dict[str, object]:
        return {
            # Cannot be contract-tested without its service. An integration
            # engineer runs the suite against it with
            # UC09_CONFORMANCE_ONLY=http once the endpoint is reachable.
            "offline": False,
            "requires": ("UC09_UPSTREAM_BASE_URL", "UC09_SUMMARY_GENERATOR_PATH"),
            "upstream_tokens": ("ConfiguredHttpSummaryGenerator",),
        }


def _request_body(data: SessionData) -> dict[str, Any]:
    """Serialise the session data. Only what the generator needs to ground its answer."""
    session = data.session
    return {
        "session": {
            "session_id": session.session_id,
            "naric_level": session.naric_level.value,
            "explanation_profile": explanation_profile_for(session.naric_level).value,
            "started_at": session.started_at.isoformat(),
            "ended_at": session.ended_at.isoformat() if session.ended_at else None,
            "course_title": session.course_title,
        },
        "covers_interactions_through": data.covers_interactions_through.isoformat(),
        "interactions": [
            {
                "interaction_id": i.interaction_id,
                "occurred_at": i.occurred_at.isoformat(),
                "question_text": i.question_text,
                "topic_tags": list(i.topic_tags),
                "concept_tags": list(i.concept_tags),
            }
            for i in data.interactions
        ],
        "citations": [
            {
                "resource_id": r.resource_id,
                "kind": r.kind.value,
                "citation": r.citation,
                "title": r.title,
                "cited_in_interaction_ids": list(r.cited_in_interaction_ids),
            }
            for r in data.citations
        ],
        "gap_suggestions": (
            None
            if data.gap_suggestions is None
            else [
                {
                    "suggestion_id": s.suggestion_id,
                    "label": s.label,
                    "rationale": s.rationale,
                }
                for s in data.gap_suggestions
            ]
        ),
    }
