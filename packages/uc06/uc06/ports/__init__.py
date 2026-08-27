"""Port interfaces - every external interaction UC-06 has.

Ports are typing.Protocol classes: an adapter conforms by shape, so a real
adapter never needs to import from this package to be usable, and no adapter can
be forced to inherit behaviour it should not have.

Read the failure contract in each docstring: an adapter may raise
ProviderUnavailable, ProviderTimeout or ProviderInvalidResponse and nothing else.
No upstream exception type, error text, payload shape or provider name may cross
this boundary.
"""

from .case_file import CaseFileProvider
from .generator import AnswerGenerator
from .guard import GuardClassifier
from .identity import CurrentUserProvider
from .learner_context import LearnerContextProvider
from .sinks import AdminAlertSink, SecurityIncidentSink
from .storage import InteractionLogRepository, SessionHaltRepository

#: Mutating verbs no read-only port or adapter may expose. Enforced by
#: tests/test_readonly_architecture.py.
FORBIDDEN_MUTATION_PREFIXES = (
    "create",
    "update",
    "delete",
    "patch",
    "put",
    "write",
    "save",
    "store",
    "insert",
    "upsert",
    "remove",
    "set_",
    "add_",
    "edit",
    "modify",
    "mutate",
    "archive",
    "purge",
    "drop",
)

__all__ = [
    "AdminAlertSink",
    "AnswerGenerator",
    "CaseFileProvider",
    "CurrentUserProvider",
    "FORBIDDEN_MUTATION_PREFIXES",
    "GuardClassifier",
    "InteractionLogRepository",
    "LearnerContextProvider",
    "SecurityIncidentSink",
    "SessionHaltRepository",
]
