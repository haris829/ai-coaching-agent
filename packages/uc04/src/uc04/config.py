"""Configuration.

Every value has a default and comes from the environment. No hard-coded URL, key or timeout
appears in business logic; nothing here is read by ``uc04.core``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    answer_generator: str = "fake"
    courses_provider: str = "mock"
    learner_context_provider: str = "mock"
    quiz_classifier: str = "mock"
    concept_tagger: str = "mock"
    current_user_provider: str = "header"
    interaction_log_repository: str = "memory"
    framing_registry: str = "memory"

    generation_timeout_ms: int = 10_000
    generation_target_p95_ms: int = 3_000
    quiz_match_threshold: float = 0.85
    allow_dev_session_ids: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            answer_generator=os.environ.get("ANSWER_GENERATOR", "fake"),
            courses_provider=os.environ.get("COURSES_PROVIDER", "mock"),
            learner_context_provider=os.environ.get("LEARNER_CONTEXT_PROVIDER", "mock"),
            quiz_classifier=os.environ.get("QUIZ_CLASSIFIER", "mock"),
            concept_tagger=os.environ.get("CONCEPT_TAGGER", "mock"),
            current_user_provider=os.environ.get("CURRENT_USER_PROVIDER", "header"),
            interaction_log_repository=os.environ.get("INTERACTION_LOG_REPOSITORY", "memory"),
            framing_registry=os.environ.get("FRAMING_REGISTRY", "memory"),
            generation_timeout_ms=_int("GENERATION_TIMEOUT_MS", 10_000),
            generation_target_p95_ms=_int("GENERATION_TARGET_P95_MS", 3_000),
            quiz_match_threshold=_float("QUIZ_MATCH_THRESHOLD", 0.85),
            allow_dev_session_ids=_bool("ALLOW_DEV_SESSION_IDS", False),
        )
