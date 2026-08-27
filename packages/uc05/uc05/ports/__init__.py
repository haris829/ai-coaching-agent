"""Ports.

Every interaction with anything outside UC-05 crosses one of these.  No
business logic knows the name of a vendor, the shape of an upstream payload,
or the URL of anything.

All ports are ``Protocol`` classes and are async: generation has a latency
budget (``GENERATION_TIMEOUT_MS``) that the application enforces with
``asyncio.wait_for``, which requires awaitables.

Failure is typed.  An adapter may raise **only** ``ProviderUnavailable``,
``ProviderTimeout`` or ``ProviderInvalidResponse`` past its boundary; the
conformance suite enforces this.
"""

from .answer import AnswerGenerator
from .guiding_question import GuidingQuestionGenerator
from .identity import CurrentUserProvider
from .intent import IntentClassifier
from .learner_context import LearnerContextProvider
from .repositories import (
    DialogueRepository,
    InteractionLogRepository,
    SessionModeRepository,
)

__all__ = [
    "AnswerGenerator",
    "CurrentUserProvider",
    "DialogueRepository",
    "GuidingQuestionGenerator",
    "IntentClassifier",
    "InteractionLogRepository",
    "LearnerContextProvider",
    "SessionModeRepository",
]
