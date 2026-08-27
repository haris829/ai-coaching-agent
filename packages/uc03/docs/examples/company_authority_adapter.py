"""EXAMPLE company adapter - the Integration Swap Proof for the authority port.

This is the complete new file a company engineer writes to replace
`MockLegalAuthorityProvider` with the approved legal authority source. It
imports nothing from `uc03` except the two domain types it must return, and it
does not subclass anything: `LegalAuthorityProvider` is a structural Protocol.

It is not wired in by default. See docs/examples/README.md for the two-line
swap and the command that grades it.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx

from uc03.domain.enums import AuthorityStatus
from uc03.domain.models import AuthorityLookupResult, VerifiedAuthority


class CompanyAuthorityAdapter:
    """Adapter over the company's approved legal authority service.

    Contract obligations (see docs/SHARED_CONTRACT.md §3):
      * return VERIFIED only when the source affirmatively verified the citation
      * populate `verified_by` and `verification_id` so the claim is auditable
      * never derive VERIFIED from model output
      * translate every upstream failure into an exception - no upstream payload,
        field name or error string may escape this boundary
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_s: float = 3.0,
    ) -> None:
        self._base_url = base_url or os.environ["COMPANY_AUTHORITY_URL"]
        self._api_key = api_key or os.environ["COMPANY_AUTHORITY_KEY"]
        self._timeout_s = timeout_s

    async def lookup(
        self, *, question: str, topic_tag: str, practice_area: str | None
    ) -> AuthorityLookupResult:
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            response = await client.post(
                f"{self._base_url}/v1/authority/resolve",
                headers={"X-Api-Key": self._api_key},
                json={
                    "query": question,
                    "topic": topic_tag,
                    "practice_area": practice_area,
                },
            )
            # Raise on transport/HTTP failure. UC-03 degrades to
            # NO_VERIFIED_AUTHORITY and records the degradation itself; deciding
            # what a failure *means* is the service's job, not the adapter's.
            response.raise_for_status()
            payload = response.json()

        # The upstream shape stops here. Anything it sends that is not an
        # explicit verification becomes NO_VERIFIED_AUTHORITY - never a guess.
        if payload.get("verification_state") != "VERIFIED":
            return AuthorityLookupResult(
                status=AuthorityStatus.NO_VERIFIED_AUTHORITY
            )

        record = payload["authority"]
        return AuthorityLookupResult(
            status=AuthorityStatus.VERIFIED,
            authority=VerifiedAuthority(
                citation=record["citation"],
                title=record["case_title"],
                source=record["repository"],
                url=record.get("permalink"),
                verified_by=record["verified_by_system"],
                verification_id=record["verification_ref"],
                retrieved_at=datetime.fromisoformat(
                    record["retrieved_at"]
                ).astimezone(timezone.utc),
            ),
        )
