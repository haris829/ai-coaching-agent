"""Fake adapter family.  Importing the package performs registration.

Deterministic, offline, no API key.  The entire test suite runs against these.
"""

from .generators import (  # noqa: F401
    FakeAnswerGenerator,
    FakeGuidingQuestionGenerator,
)
from .identity import (  # noqa: F401
    HeaderCurrentUserProvider,
    StaticCurrentUserProvider,
)
from .intent import MockIntentClassifier  # noqa: F401
from .learner_context import MockLearnerContextProvider  # noqa: F401
