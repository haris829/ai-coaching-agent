"""COPY-PASTE ADAPTER TEMPLATE.

You should be able to copy this file, fill in the TODOs, and have a working
adapter without reading anything else in this repository.

    cp uc05/adapters/real/_template.py uc05/adapters/real/company_learner_context.py

Then:

1.  Fill in every ``TODO`` below.  There are exactly four kinds:
    endpoint, auth, payload mapping, error translation.
2.  Add your module to ``ADAPTER_MODULES`` in ``uc05/composition.py`` -- one line.
3.  Set the environment variable named by the registry -- one value.
4.  Run the conformance suite for this port.  No new test is needed.

Nothing else in the repository changes.  If your integration needs a change
anywhere else, that is a defect in this architecture -- raise it rather than
working around it.

------------------------------------------------------------------------------
THE FOUR NON-NEGOTIABLES
------------------------------------------------------------------------------

1.  **This file is the only place upstream payload shapes are known.**  No
    upstream field name, no nesting, no upstream error string escapes it.  The
    method that maps the payload is deliberately separated from the method that
    fetches it, so the mapping is unit-testable and obviously self-contained.

2.  **Never invent data.**  A missing value maps to the documented default with
    its source field marked accordingly -- ``naric_level_source="default"``,
    ``source_status={"naric_level": "empty"}`` -- never to a plausible-looking
    guess.  A guess is indistinguishable from a real value downstream, which
    makes it worse than an absence.

3.  **Authorisation stays server-side, inside this adapter.**  Credentials come
    from ``Settings``.  They never travel in a request from a client and never
    appear in a response or a log.

4.  **If the real payload cannot be mapped onto the platform contract, that is
    a contract conversation, not an adapter workaround.**  Do not widen an
    enum, do not add a field to a domain model, do not smuggle an upstream
    value through in a string.  Raise it.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...domain.enums import NaricLevelSource, SourceStatus
from ...domain.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from ...domain.models import LearnerContext
from ...domain.profiles import coerce_naric_level
from ...registry import LEARNER_CONTEXT_REGISTRY

#: Names the port in errors and logs.  Never the vendor: a provider name must
#: not reach a client, and operators attribute failure by port.
PORT = "learner_context_provider"


# TODO(registry key): choose the key an operator will put in the environment
# variable.  Convention is the company or system name, lower-case.
@LEARNER_CONTEXT_REGISTRY.register("company")
class CompanyLearnerContextAdapter:
    """TODO(docstring): name the upstream system and its owning team."""

    def __init__(self, settings: Settings, **_: object) -> None:
        # TODO(endpoint): read the base URL from Settings.  Never hard-code a
        # URL here -- there is no default that could reach a network.
        self._base_url = settings.learner_context_base_url
        if not self._base_url:
            raise ProviderUnavailable(PORT, "no base url configured")

        # TODO(auth): read the credential from Settings.  Never from a request.
        self._api_key = settings.learner_context_api_key

        # TODO(timeout): honour the configured budget.  The application also
        # wraps every call in asyncio.wait_for, but an adapter that sets its
        # own client timeout fails faster and more cleanly.
        self._timeout_seconds = settings.generation_timeout_seconds

    # -- transport -------------------------------------------------------

    async def get_context(self, session_id: str, user_id: str) -> LearnerContext:
        try:
            payload = await self._fetch(session_id, user_id)
        except TimeoutError as exc:
            # TODO(error translation): translate the client library's timeout.
            # Note that ``str(exc)`` is NOT passed on: upstream error text must
            # not escape this boundary.
            raise ProviderTimeout(PORT, "upstream did not answer in budget") from exc
        except Exception as exc:  # translated immediately below
            # TODO(error translation): narrow this to the client library's own
            # exception types.  The bare catch here exists only so that the
            # template cannot leak an untranslated exception; your adapter
            # should list the specific exceptions it expects.
            raise ProviderUnavailable(PORT, "upstream unreachable") from exc

        return self._map(payload)

    async def _fetch(self, session_id: str, user_id: str) -> Any:
        # TODO(endpoint + auth): perform the real call, e.g.
        #
        #   async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
        #       response = await client.get(
        #           f"{self._base_url}/learners/{user_id}/context",
        #           params={"session_id": session_id},
        #           headers={"Authorization": f"Bearer {self._api_key}"},
        #       )
        #       response.raise_for_status()
        #       return response.json()
        raise NotImplementedError("TODO: implement the upstream call")

    # -- mapping ---------------------------------------------------------

    @staticmethod
    def _map(payload: Any) -> LearnerContext:
        """Upstream payload -> platform contract.

        Kept separate from ``_fetch`` so it can be unit-tested against a
        captured payload with no network at all.
        """
        if not isinstance(payload, dict):
            # Structurally unusable: a contract violation, not an outage.
            raise ProviderInvalidResponse(PORT, "unexpected payload type")

        # TODO(payload mapping): replace these key paths with the real ones.
        raw_level = payload.get("naric_level")
        raw_provenance = payload.get("naric_level_source")
        raw_area = payload.get("practice_area")

        # ``coerce_naric_level`` implements the platform rule for a value that
        # maps to no enum member: apply the default, mark the source
        # ``default``, record status ``invalid``.  Do not write your own.
        level, source, status = coerce_naric_level(raw_level)

        if status is SourceStatus.AVAILABLE and raw_provenance == "default":
            source, status = NaricLevelSource.DEFAULT, SourceStatus.PARTIAL

        # Never invent a practice area.  Absent means absent.
        area = raw_area if isinstance(raw_area, str) and raw_area.strip() else None

        return LearnerContext(
            naric_level=level,
            naric_level_source=source,
            practice_area=area,
            source_status={
                "naric_level": status,
                "practice_area": (
                    SourceStatus.AVAILABLE if area else SourceStatus.EMPTY
                ),
            },
        )
