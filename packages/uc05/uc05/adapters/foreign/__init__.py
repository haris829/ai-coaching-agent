"""Deliberately foreign adapter family, shipped as replaceability evidence.

Makes no network calls and is never selected by the default configuration
(its registry keys are all ``acme``).  See ``acme.py`` for why it exists.
"""

from .acme import (  # noqa: F401
    AcmeAnswerGenerator,
    AcmeGuidingQuestionGenerator,
    AcmeIntentClassifier,
    AcmeLearnerContextAdapter,
)
