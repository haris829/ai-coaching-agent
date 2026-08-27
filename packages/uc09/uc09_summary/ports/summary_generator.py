"""SummaryGenerator port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from uc09_summary.domain.grounding import SessionData
from uc09_summary.domain.models import SummaryContent


@runtime_checkable
class SummaryGenerator(Protocol):
    """Builds the four sections from session data.

    A generator is given the session record and may use nothing else. Whatever
    it returns is put through :func:`uc09_summary.domain.grounding.check_grounding`
    before it can be stored, and a response carrying a topic or an authority
    with no source in session data is rejected whole as a
    :class:`ProviderInvalidResponse`.

    Implementations must not pad a section to hit a count. Three to five key
    concepts is a target, not a quota; a single-topic session produces a
    single-topic summary with deeper concepts, never an inflated topic list.
    """

    def generate(self, session_data: SessionData) -> SummaryContent:
        """Produce the four sections.

        Args:
            session_data: the session, its interactions within the cover
                window, its citations, and the gap-report suggestions if any.

        Returns:
            :class:`SummaryContent` holding exactly the four sections.

        Raises:
            ProviderUnavailable: generator unreachable or refused.
            ProviderTimeout: generator exceeded the configured deadline.
            ProviderInvalidResponse: generator returned something unusable.

        Any of the three sends the service to the question-log fallback. None
        of them may result in nothing being produced for the learner.
        """
        ...
