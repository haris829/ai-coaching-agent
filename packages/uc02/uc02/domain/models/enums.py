"""Vocabularies shared across the UC-02 domain.

These string enums are part of the contract downstream use cases consume.
Adding a member is a minor change; renaming or removing one requires a
``context_version`` bump (see ``uc02/domain/models/context.py``).
"""

from __future__ import annotations

from enum import Enum


class SourceName(str, Enum):
    """The four upstream systems UC-02 assembles context from."""

    NARIC = "naric"
    COURSES = "courses"
    LEGAL_PROFILE = "legal_profile"
    QUESTION_HISTORY = "question_history"


class SourceStatus(str, Enum):
    """Outcome vocabulary for a single upstream source (see docs/integration.md).

    ``EMPTY`` and ``UNAVAILABLE`` are deliberately distinct: a learner with no
    question history is not the same thing as a history service that is down.
    Collapsing them would make downstream gap analysis draw wrong conclusions.
    """

    AVAILABLE = "available"
    EMPTY = "empty"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class ErrorCategory(str, Enum):
    """Why a source did not return usable data. Safe to log."""

    NONE = "none"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    INVALID_RESPONSE = "invalid_response"
    BUDGET_EXCEEDED = "budget_exceeded"
    UNEXPECTED = "unexpected"


class LevelSource(str, Enum):
    """Whether the NARIC level was retrieved or fell back to the default."""

    RETRIEVED = "retrieved"
    DEFAULT = "default"


class ExplanationDomain(str, Enum):
    """Whether explanations can be pitched at a declared speciality."""

    SPECIALITY = "speciality"
    GENERAL_LEGAL = "general_legal"


class ExplanationTemplateId(str, Enum):
    """The three templates defined by the scope document."""

    BASIC = "basic"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class ExplanationDepth(str, Enum):
    A_LEVEL_EQUIVALENT = "a_level_equivalent"
    PRACTITIONER_FOUNDATION = "practitioner_foundation"
    MASTERS_LEVEL = "masters_level"


class TerminologyLevel(str, Enum):
    PLAIN_LANGUAGE = "plain_language"
    MIXED = "mixed"
    TECHNICAL = "technical"


class AssumedPriorKnowledge(str, Enum):
    MINIMAL = "minimal"
    FOUNDATIONAL = "foundational"
    SUBSTANTIAL = "substantial"


class ContextStatus(str, Enum):
    """Whether this initialize call built the context or reused a stored one."""

    CREATED = "created"
    EXISTING = "existing"
    REFRESHED = "refreshed"
