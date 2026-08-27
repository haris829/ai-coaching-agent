"""Ports: every interaction with anything outside this component.

There is no other way out. No module outside ``adapters`` knows a URL, a
payload shape, an upstream field name or an upstream error string.

Upstream ports are **read-only by shape**: they declare retrieval methods only,
and :mod:`tests.test_readonly_architecture` asserts that neither the port nor
any registered adapter exposes a mutating method. That is a structural
guarantee rather than a convention - this component reads a session, it never
changes one.

Consequently the ``summary_generated`` status transition is recorded on the
summary record this component owns, not written back upstream. Publishing that
transition to the wider platform is a documented extension point, not a write
this component performs against an API it was never given. See
docs/SHARED_CONTRACT.md, "Extension points".
"""

from uc09_summary.ports.citation_provider import CitationProvider
from uc09_summary.ports.clock import Clock
from uc09_summary.ports.document_renderer import DocumentRenderer
from uc09_summary.ports.gap_report_provider import GapReportProvider
from uc09_summary.ports.identity import CurrentUserProvider
from uc09_summary.ports.interaction_provider import InteractionProvider
from uc09_summary.ports.repositories import DownloadLogRepository, SummaryRepository
from uc09_summary.ports.session_provider import SessionProvider
from uc09_summary.ports.summary_generator import SummaryGenerator

#: Logical names of the ports whose adapters must never mutate upstream state.
UPSTREAM_READ_ONLY_PORTS = (
    "session_provider",
    "interaction_provider",
    "citation_provider",
    "gap_report_provider",
)

__all__ = [
    "UPSTREAM_READ_ONLY_PORTS",
    "CitationProvider",
    "Clock",
    "CurrentUserProvider",
    "DocumentRenderer",
    "DownloadLogRepository",
    "GapReportProvider",
    "InteractionProvider",
    "SessionProvider",
    "SummaryGenerator",
    "SummaryRepository",
]
