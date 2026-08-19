"""Adapters that connect UC-01 to its collaborators.

Everything in this package translates between UC-01's vocabulary
(:mod:`app.modules.quiz_configuration.ports`) and somebody else's. Keeping the translation here —
rather than sprinkling question-bank imports through the services — is what makes the question
bank replaceable and keeps the configuration rules free of persistence detail.
"""

from __future__ import annotations
