"""CaseFileProvider - READ ONLY, structurally.

Read-only is enforced by the shape of the port, not by discipline. There is no
create, update, delete, patch or write method here, so there is nothing to call.
tests/test_readonly_architecture.py asserts that this Protocol and every
registered adapter expose no mutating method, so a future engineer cannot quietly
add one without the suite failing.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import AccessRecord, CaseFile


@runtime_checkable
class CaseFileProvider(Protocol):
    """Read access to Case Prep Agent case files.

    Failure contract - implementations raise only:
      ProviderUnavailable      upstream unreachable, refused, or down
      ProviderTimeout          upstream exceeded the caller's budget
      ProviderInvalidResponse  upstream answered with an unmappable shape

    Neither method may raise anything else, log case content, or return partial
    data silently: a case file missing sections is returned with
    source_status=partial, never with fabricated sections.
    """

    def verify_read_access(self, user_id: str, case_file_id: str) -> AccessRecord:
        """Authoritative, server-side read-access decision.

        Called on EVERY request, before any case content is loaded. The result is
        never cached across requests by UC-06, and an adapter must not cache it
        either: authorisation may be revoked between two questions in the same
        session.
        """
        ...

    def get_case_file(self, case_file_id: str) -> CaseFile:
        """Load charges, facts, evidence and applicable legislation notes.

        Every fact carries a stable identifier so that references in explanations
        can be verified and logged without reproducing fact text.
        """
        ...
