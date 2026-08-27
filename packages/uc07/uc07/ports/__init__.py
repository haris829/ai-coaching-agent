"""Ports: the only way UC-07 touches anything outside its own process.

Read-only upstream ports live in :mod:`uc07.ports.read_only`, the single
write-capable port in :mod:`uc07.ports.persistence`, identity/time seams in
:mod:`uc07.ports.identity`.
"""

from uc07.ports.identity import Clock, CurrentUserProvider, IdentityUnresolved
from uc07.ports.persistence import GapReportRepository
from uc07.ports.read_only import (
    READ_ONLY_PORTS,
    CoursesProvider,
    FeedbackProvider,
    InteractionLogProvider,
    LearnerProfileProvider,
    ReadOnlyPort,
)

__all__ = [
    "READ_ONLY_PORTS",
    "Clock",
    "CoursesProvider",
    "CurrentUserProvider",
    "FeedbackProvider",
    "GapReportRepository",
    "IdentityUnresolved",
    "InteractionLogProvider",
    "LearnerProfileProvider",
    "ReadOnlyPort",
]
