"""Composition root.

The single place implementations are chosen. Selection is a registry lookup keyed on config;
this file contains no conditional on provider name and no import of a concrete adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adapters.memory.clock import SequentialIdGenerator, SystemClock
from .adapters.registry import resolve
from .config import Settings
from .core.coaching_service import CoachingService, ServiceDependencies
from .ports import Clock, CurrentUserProvider, IdGenerator


@dataclass
class Container:
    settings: Settings
    service: CoachingService
    current_user: CurrentUserProvider
    courses: Any
    learner_context: Any
    generator: Any
    quiz_classifier: Any
    concept_tagger: Any
    interactions: Any
    framings: Any


def build_container(
    settings: Settings | None = None,
    *,
    clock: Clock | None = None,
    ids: IdGenerator | None = None,
    overrides: dict[str, Any] | None = None,
) -> Container:
    """Assemble UC-04.

    ``overrides`` exists for tests only: it injects an already-constructed adapter in place of
    a registry lookup. Production paths always go through the registry, so a misconfigured
    provider name fails loudly at startup rather than falling back to a mock.
    """
    settings = settings or Settings.from_env()
    overrides = overrides or {}

    def pick(config_key: str, configured: str) -> Any:
        if config_key in overrides:
            return overrides[config_key]
        return resolve(config_key, configured)

    courses = pick("COURSES_PROVIDER", settings.courses_provider)
    learner_context = pick("LEARNER_CONTEXT_PROVIDER", settings.learner_context_provider)
    generator = pick("ANSWER_GENERATOR", settings.answer_generator)
    quiz_classifier = pick("QUIZ_CLASSIFIER", settings.quiz_classifier)
    concept_tagger = pick("CONCEPT_TAGGER", settings.concept_tagger)
    interactions = pick("INTERACTION_LOG_REPOSITORY", settings.interaction_log_repository)
    framings = pick("FRAMING_REGISTRY", settings.framing_registry)
    current_user = pick("CURRENT_USER_PROVIDER", settings.current_user_provider)

    service = CoachingService(
        ServiceDependencies(
            courses=courses,
            learner_context=learner_context,
            generator=generator,
            quiz_classifier=quiz_classifier,
            concept_tagger=concept_tagger,
            interactions=interactions,
            framings=framings,
            clock=clock or SystemClock(),
            ids=ids or SequentialIdGenerator(),
            quiz_match_threshold=settings.quiz_match_threshold,
            allow_dev_session_ids=settings.allow_dev_session_ids,
        )
    )

    return Container(
        settings=settings,
        service=service,
        current_user=current_user,
        courses=courses,
        learner_context=learner_context,
        generator=generator,
        quiz_classifier=quiz_classifier,
        concept_tagger=concept_tagger,
        interactions=interactions,
        framings=framings,
    )
