"""COPY THIS FILE to write a real upstream adapter.

    cp uc08/adapters/real/_template.py uc08/adapters/real/activity.py

You need nothing else from this repository. Fill in every ``TODO`` and you have
a working adapter. There are exactly four kinds of TODO:

  1. the endpoint         -- where the upstream lives
  2. the auth mechanism   -- how this service proves who it is (server-side only)
  3. the payload mapping  -- which upstream field means what
  4. the error translation-- which upstream failure is which contract error

Then two more lines outside this file, and you are done:

  * one line in ``uc08/registry.py`` adding your class to the table for its port
  * one environment variable, e.g. ``ACTIVITY_PROVIDER=company``

Run the conformance suite and it covers your adapter automatically:

    python -m pytest tests/conformance -q

Non-negotiables
---------------
* This file is the **only** place upstream payload shapes are known. No upstream
  field name, nesting or error string may escape it. Callers see platform types
  and the typed errors in ``uc08.domain.errors`` -- nothing else.
* **Never invent data.** A missing value maps to the documented default with its
  source field marked accordingly (``naric_level_source="default"``,
  ``status="invalid"`` or ``"empty"``), never to a plausible-looking guess.
* Authorisation stays server-side, inside this adapter. No credential is
  accepted from, or echoed to, a caller.
* ``empty`` and ``unavailable`` are different states. A successful read with
  nothing in it is ``empty``; a source that did not answer raises
  ``ProviderUnavailable``.
* If the real payload cannot be mapped onto the platform contract, that is a
  **contract conversation, not an adapter workaround.** Raise it. Do not bend
  the domain model to fit an upstream quirk, and do not widen an enum locally.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from uc08.domain.enums import SourceStatus
from uc08.domain.errors import ProviderInvalidResponse, ProviderTimeout, ProviderUnavailable
from uc08.domain.models import (
    ActivityInteraction,
    ActivityWindowRead,
    QuestionCountRead,
    TopicMention,
    TopicsRead,
)
from uc08.domain.naric import normalise_completion_percent, normalise_naric_level  # noqa: F401
from uc08.domain.time_utils import ensure_utc
from uc08.logging_setup import get_logger
from uc08.ports.clock import Clock
from uc08.ports.conformance import CONFORMANCE_USER_ID
from uc08.ports.upstream import ActivityProvider

_log = get_logger(__name__)


class TemplateActivityAdapter(ActivityProvider):
    """TODO rename to e.g. ``CompanyActivityAdapter``.

    The construction signature is fixed by the composition root and must not
    change: ``(clock, *, timeout_seconds)``. Everything else this adapter needs
    it reads for itself, from the environment.
    """

    def __init__(self, clock: Clock, *, timeout_seconds: float = 5.0) -> None:
        self._clock = clock
        self._timeout_seconds = timeout_seconds

        # TODO(1) endpoint. Read it from the environment; never hard-code a URL
        # and never invent one. Fail loudly at startup if it is missing.
        self._base_url = os.environ.get("UPSTREAM_ACTIVITY_BASE_URL", "")

        # TODO(2) auth. A token, a signed assertion, a mesh identity -- whatever
        # the platform uses. It is read here, used here, and never leaves here.
        self._credential = os.environ.get("UPSTREAM_ACTIVITY_TOKEN", "")

    @property
    def timeout_seconds(self) -> float:
        """The deadline this adapter honours. Pass it to your HTTP client."""
        return self._timeout_seconds

    # ----------------------------------------------------------------------
    # Reads. Four methods, no writes. Do not add a fifth that mutates: the
    # architecture test in tests/architecture/test_ports_read_only.py fails.
    # ----------------------------------------------------------------------
    def last_activity_at(self, user_id: str) -> datetime | None:
        body = self._get(f"/activity/{user_id}/last")
        # TODO(3) mapping. Replace "lastActivityAt" with the real field.
        raw = body.get("lastActivityAt")
        if raw is None:
            return None
        return self._to_utc(raw)

    def interactions_in_window(self, user_id: str, since: datetime) -> ActivityWindowRead:
        boundary = ensure_utc(since)
        body = self._get(f"/activity/{user_id}/interactions", {"since": boundary.isoformat()})

        # TODO(3) mapping. Replace the field names and the container shape.
        rows = body.get("interactions")
        if rows is None:
            return ActivityWindowRead(interactions=(), status=SourceStatus.EMPTY)
        if not isinstance(rows, list):
            raise ProviderInvalidResponse(self.port_name, "interaction collection is not a list")

        interactions: list[ActivityInteraction] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ProviderInvalidResponse(self.port_name, "interaction entry is not an object")
            identifier = row.get("id")
            occurred_at = row.get("occurredAt")
            if not identifier or occurred_at is None:
                raise ProviderInvalidResponse(
                    self.port_name, "interaction entry is missing an identifier or a timestamp"
                )
            moment = self._to_utc(occurred_at)
            if moment >= boundary:
                interactions.append(
                    ActivityInteraction(interaction_id=str(identifier), occurred_at=moment)
                )

        found = tuple(interactions)
        # empty is a real answer, not a failure. Do not raise here.
        return ActivityWindowRead(
            interactions=found,
            status=SourceStatus.AVAILABLE if found else SourceStatus.EMPTY,
        )

    def question_count(self, user_id: str) -> QuestionCountRead:
        body = self._get(f"/activity/{user_id}/question-count")

        # TODO(3) mapping. The count may arrive as a string, or nested.
        raw = body.get("questionCount")
        if raw is None:
            return QuestionCountRead(count=0, status=SourceStatus.EMPTY)
        try:
            count = int(str(raw).strip())
        except (TypeError, ValueError) as exc:
            raise ProviderInvalidResponse(self.port_name, "question count is not an integer") from exc
        if count < 0:
            raise ProviderInvalidResponse(self.port_name, "question count is negative")
        return QuestionCountRead(count=count, status=SourceStatus.AVAILABLE)

    def topics_in_window(self, user_id: str, since: datetime) -> TopicsRead:
        boundary = ensure_utc(since)
        body = self._get(f"/activity/{user_id}/topics", {"since": boundary.isoformat()})

        # TODO(3) mapping. Keep the first mention timestamp: the weekly summary
        # needs it to close a window the port signature only opens.
        rows = body.get("topics")
        if not rows:
            return TopicsRead(topics=(), status=SourceStatus.EMPTY)
        if not isinstance(rows, list):
            raise ProviderInvalidResponse(self.port_name, "topic collection is not a list")

        first_seen: dict[str, datetime] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ProviderInvalidResponse(self.port_name, "topic entry is not an object")
            name = row.get("name")
            occurred_at = row.get("firstMentionedAt")
            if not name or occurred_at is None:
                raise ProviderInvalidResponse(self.port_name, "topic entry is missing a name or a timestamp")
            moment = self._to_utc(occurred_at)
            if moment >= boundary:
                existing = first_seen.get(str(name))
                if existing is None or moment < existing:
                    first_seen[str(name)] = moment

        mentions = tuple(
            TopicMention(name=name, first_mentioned_at=moment) for name, moment in first_seen.items()
        )
        return TopicsRead(
            topics=mentions,
            status=SourceStatus.AVAILABLE if mentions else SourceStatus.EMPTY,
        )

    # ----------------------------------------------------------------------
    # Transport and error translation
    # ----------------------------------------------------------------------
    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """TODO(1)+(2)+(4). Perform the call and translate every failure.

        Suggested shape with ``httpx``::

            import httpx

            try:
                response = httpx.get(
                    f"{self._base_url}{path}",
                    params=params,
                    timeout=self._timeout_seconds,
                    headers={"Authorization": f"Bearer {self._credential}"},
                )
            except httpx.TimeoutException as exc:
                raise ProviderTimeout(self.port_name, "deadline exceeded") from exc
            except httpx.HTTPError as exc:
                raise ProviderUnavailable(self.port_name, "activity read model did not answer") from exc

            if response.status_code >= 500:
                raise ProviderUnavailable(self.port_name, "activity read model did not answer")
            if response.status_code == 404:
                return {}          # absent, not broken: an empty answer
            if response.status_code >= 400:
                raise ProviderInvalidResponse(self.port_name, "activity read model rejected the request")
            try:
                body = response.json()
            except ValueError as exc:
                raise ProviderInvalidResponse(self.port_name, "response body is not JSON") from exc
            if not isinstance(body, dict):
                raise ProviderInvalidResponse(self.port_name, "response body is not an object")
            return body

        Note what the messages do **not** contain: no URL, no vendor name, no
        upstream error text, no request id from the other side. Log those here
        if you need them; do not put them in the exception.
        """
        raise ProviderUnavailable(
            self.port_name,
            "template adapter has no transport: fill in TODO(1), TODO(2) and TODO(4)",
        )

    def _to_utc(self, raw: Any) -> datetime:
        """TODO(3). Translate the upstream time representation to UTC.

        Whatever arrives -- ISO-8601 with an offset, epoch seconds, epoch
        milliseconds -- it leaves here timezone-aware and UTC. A naive value is
        an invalid response, not an assumption: guessing an offset is how a
        streak silently dies.
        """
        if isinstance(raw, str):
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ProviderInvalidResponse(self.port_name, "timestamp is not parseable") from exc
            if parsed.tzinfo is None:
                raise ProviderInvalidResponse(self.port_name, "timestamp carries no timezone")
            return ensure_utc(parsed)
        raise ProviderInvalidResponse(self.port_name, "timestamp is not a supported representation")

    # ----------------------------------------------------------------------
    # Conformance harness -- keep this, it is how the existing suite covers you
    # ----------------------------------------------------------------------
    @classmethod
    def conformance_scenarios(cls) -> Mapping[str, Callable[[Clock], ActivityProvider]]:
        """Build this adapter in each contract state, against a stub upstream.

        TODO(5) point these at a local stub of your upstream -- a recorded
        response set, a fake transport, a test double of your HTTP client.
        Never at a live system: the suite must pass with no network.

        The five keys in ``REQUIRED_CONFORMANCE_SCENARIOS`` are mandatory.
        Adding the scope-named keys (``activity_23h59m_ago``,
        ``activity_24h01m_ago``, ``multiple_interactions_same_day``,
        ``no_activity``, ``question_count_<n>``) opts your adapter into the
        behavioural equivalence proof as well -- see
        ``tests/integration/test_foreign_adapter_swap.py``. That is the
        strongest evidence available that your integration is correct, and it
        costs you only fixture data.
        """
        raise NotImplementedError(
            "TODO(5): return {'available': ..., 'empty': ..., 'unavailable': ..., "
            f"'timeout': ..., 'invalid': ...}} built against a local stub for user "
            f"{CONFORMANCE_USER_ID!r}"
        )
