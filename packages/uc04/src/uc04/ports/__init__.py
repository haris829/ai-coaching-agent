"""Ports: the interfaces UC-04 depends on.

Every external interaction goes through one of these. Business logic imports from here and from
``uc04.domain`` only - never from an adapter. An adapter is the single place an upstream payload
shape is known, and it maps that payload onto the domain models at the boundary.

All ports are ``typing.Protocol`` classes, so an adapter satisfies one by shape. There is no
base class to inherit and no framework to register with beyond one registry line.

Failure contract, uniform across every port:

* ``ProviderUnavailable``     - could not be reached, or reported itself down
* ``ProviderTimeout``         - did not answer inside its budget
* ``ProviderInvalidResponse`` - answered with something unmappable
* ``NotFound``                - answered correctly; the thing does not exist

An adapter must raise one of those. It must not leak an upstream exception type, an upstream
error string, or the provider's name past its own boundary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from ..domain.models import (
    ConceptTag,
    CourseStructure,
    EnrolmentRecord,
    FalsePositiveRecord,
    FramingAttempt,
    FramingStrategy,
    GenerationRequest,
    GenerationResult,
    InteractionRecord,
    LearnerContext,
    LessonContent,
    QuizIntentResult,
)


@runtime_checkable
class CoursesProvider(Protocol):
    """Course catalogue, lesson content and enrolment."""

    def get_lesson(self, course_id: str, lesson_id: str) -> LessonContent:
        """Full lesson content. ``NotFound`` when the lesson does not exist."""
        ...

    def get_course_structure(self, course_id: str) -> CourseStructure:
        """The lessons that really exist in this course - the cross-reference whitelist."""
        ...

    def verify_enrolment(self, user_id: str, course_id: str) -> EnrolmentRecord:
        """Authoritative enrolment check. Re-run on every request, never cached upstream."""
        ...


@runtime_checkable
class LearnerContextProvider(Protocol):
    """NARIC level, its provenance and practice area. UC-04 does not assemble this."""

    def get_context(self, session_id: str, user_id: str) -> LearnerContext: ...


@runtime_checkable
class AnswerGenerator(Protocol):
    """Produces structure, not prose. A malformed return is ``ProviderInvalidResponse``."""

    def generate(self, request: GenerationRequest) -> GenerationResult: ...


@runtime_checkable
class QuizIntentClassifier(Protocol):
    """Answer-seeking intent detection. One of the two independent quiz signals."""

    def classify(self, question: str, lesson: LessonContent | None) -> QuizIntentResult: ...


@runtime_checkable
class ConceptTagger(Protocol):
    """Maps a question onto the closed vocabularies. Unmatched becomes ``unclassified``."""

    def tag(self, question: str, lesson: LessonContent | None) -> ConceptTag: ...


@runtime_checkable
class InteractionLogRepository(Protocol):
    """Persistence for the published interaction record."""

    def append(self, record: InteractionRecord) -> None: ...

    def get(self, interaction_id: str) -> InteractionRecord | None: ...

    def list_for_session(self, session_id: str) -> list[InteractionRecord]: ...

    def append_false_positive(self, record: FalsePositiveRecord) -> None: ...

    def list_false_positives(self, session_id: str | None = None) -> list[FalsePositiveRecord]: ...


@runtime_checkable
class FramingRegistry(Protocol):
    """Which framings have been spent, per session and concept. Deterministic, not remembered
    by the generator."""

    def used_framings(self, session_id: str, concept_tag: str) -> list[FramingAttempt]: ...

    def record(self, session_id: str, concept_tag: str, framing: FramingStrategy, fingerprint: str,
               fingerprint_tokens: tuple[str, ...], recorded_at: datetime) -> None: ...

    def explain_differently_count(self, session_id: str, concept_tag: str) -> int: ...


@runtime_checkable
class CurrentUserProvider(Protocol):
    """Resolves the authenticated principal server-side. Never reads the request body."""

    def resolve(self, headers: dict[str, str]) -> str: ...


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


@runtime_checkable
class IdGenerator(Protocol):
    def next_id(self, prefix: str) -> str: ...


__all__ = [
    "AnswerGenerator",
    "Clock",
    "ConceptTagger",
    "CoursesProvider",
    "CurrentUserProvider",
    "FramingRegistry",
    "IdGenerator",
    "InteractionLogRepository",
    "LearnerContextProvider",
    "QuizIntentClassifier",
]
