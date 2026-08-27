"""Read-only upstream ports.

``ActivityProvider`` and ``GapReportProvider`` are the two systems UC-08 reads
and never writes. They are read-only **by shape**: no mutating method exists on
either interface, and ``READ_ONLY_PORTS`` below is the whitelist that
``tests/architecture/test_ports_read_only.py`` enforces against the interfaces
*and* against every registered adapter.

Every implementation raises only the typed contract errors from
``uc08.domain.errors``:

* :class:`~uc08.domain.errors.ProviderUnavailable` -- the upstream did not answer
* :class:`~uc08.domain.errors.ProviderTimeout` -- the upstream exceeded the deadline
* :class:`~uc08.domain.errors.ProviderInvalidResponse` -- the answer cannot be
  mapped onto the platform contract

Nothing else crosses the boundary: no vendor exception type, no upstream field
name, no upstream error text, no provider name.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from uc08.domain.models import ActivityWindowRead, QuestionCountRead, Topic, TopicsRead


class ActivityProvider(ABC):
    """Read model of a learner coaching activity. READ ONLY."""

    #: Abstract port name used in logs and typed errors. Never a vendor name.
    port_name = "activity"

    @abstractmethod
    def last_activity_at(self, user_id: str) -> datetime | None:
        """Most recent known interaction for the account, or ``None``.

        UTC, timezone-aware. Reported for observability and for the weekly
        summary; the streak boundary is decided by
        :meth:`interactions_in_window`, which lets UC-08 exclude the
        interaction currently being recorded.
        """

    @abstractmethod
    def interactions_in_window(self, user_id: str, since: datetime) -> ActivityWindowRead:
        """Interactions with ``occurred_at >= since``, newest-first not required.

        The status distinguishes an empty window (``empty``) from a source that
        could not answer -- which is raised as ``ProviderUnavailable`` rather
        than reported as emptiness.
        """

    @abstractmethod
    def question_count(self, user_id: str) -> QuestionCountRead:
        """Total lifetime question count for the account."""

    @abstractmethod
    def topics_in_window(self, user_id: str, since: datetime) -> TopicsRead:
        """Distinct topic names touched at or after ``since``."""


class GapReportProvider(ABC):
    """Read model of a learner knowledge gaps. READ ONLY."""

    port_name = "gap_report"

    @abstractmethod
    def suggested_topic(self, user_id: str) -> Topic | None:
        """A topic to suggest for the coming week, or ``None`` if the report has
        no suggestion.

        ``None`` means "the report answered and had nothing". A report that
        cannot be reached raises ``ProviderUnavailable``. UC-08 never invents a
        suggestion for either case.
        """


#: The complete set of methods each read-only port may expose, by port class
#: name. The architecture test fails if an interface or any registered adapter
#: grows a method outside its entry.
READ_ONLY_PORTS: dict[str, frozenset[str]] = {
    "ActivityProvider": frozenset(
        {"last_activity_at", "interactions_in_window", "question_count", "topics_in_window"}
    ),
    "GapReportProvider": frozenset({"suggested_topic"}),
}

#: Verb fragments that would indicate a write capability. Checked against every
#: public attribute name on the read-only ports and their adapters.
MUTATING_NAME_FRAGMENTS: tuple[str, ...] = (
    "save",
    "write",
    "create",
    "update",
    "delete",
    "remove",
    "insert",
    "upsert",
    "put",
    "post",
    "patch",
    "set_",
    "award",
    "revoke",
    "emit",
    "publish",
    "send",
    "record",
    "mark",
    "store",
    "commit",
    "flush",
    "reset",
    "increment",
    "decrement",
    "mint",
)
