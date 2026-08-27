"""Configuration surface.

Every key UC-06 reads is declared here, in one place, so that the configuration
surface can be enumerated and asserted over by a test.

WHAT IS NOT HERE, AND WILL NOT BE ADDED:

* No key affects the disclaimer. Not a feature flag, not an environment
  variable, not an admin setting, not a request parameter, not a test mode. A
  flag defaulted to false is still a suppression path, and it is exactly the kind
  of thing that gets flipped in an incident at 2am. The absence is the guarantee,
  and tests/test_config_surface.py asserts it.
* No key affects the outcome-prediction or litigation-strategy redirect. The
  specification states the redirect is always the correct response in coaching
  mode, so there is nothing to configure.

Provider selection, timeouts and dev-mode session minting are configurable.
Safety controls are not.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from typing import Any, Mapping

from .domain.errors import ConfigurationError

#: Every environment variable UC-06 reads. Enumerated for the config-surface test.
ENV_KEYS: tuple[str, ...] = (
    "ANSWER_GENERATOR",
    "CASE_FILE_PROVIDER",
    "LEARNER_CONTEXT_PROVIDER",
    "GUARD_CLASSIFIER",
    "INTERACTION_LOG_REPOSITORY",
    "SESSION_HALT_REPOSITORY",
    "ADMIN_ALERT_SINK",
    "SECURITY_INCIDENT_SINK",
    "CURRENT_USER_PROVIDER",
    "GENERATION_TIMEOUT_MS",
    "GENERATION_TARGET_P95_MS",
    "ALLOW_DEV_SESSION_IDS",
    "ANSWER_GENERATOR_PROVIDER",
    "ANSWER_GENERATOR_MODEL",
    "ANSWER_GENERATOR_BASE_URL",
    "ANSWER_GENERATOR_API_KEY",
)


def _as_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(raw: str, key: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{key} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    # --- provider selection: which adapter implements each port -------------
    answer_generator: str = "fake"
    case_file_provider: str = "mock"
    learner_context_provider: str = "mock"
    guard_classifier: str = "mock"
    interaction_log_repository: str = "memory"
    session_halt_repository: str = "memory"
    admin_alert_sink: str = "memory"
    security_incident_sink: str = "memory"
    current_user_provider: str = "header"

    # --- budgets ------------------------------------------------------------
    generation_timeout_ms: int = 10_000
    generation_target_p95_ms: int = 3_000

    # --- session identity ---------------------------------------------------
    #: UC-06 receives an opaque session_id and never creates one on a production
    #: path. Dev-mode minting is gated by this key and defaults off.
    allow_dev_session_ids: bool = False

    # --- ConfiguredAnswerGenerator only; unused while ANSWER_GENERATOR=fake --
    answer_generator_provider: str | None = None
    answer_generator_model: str | None = None
    answer_generator_base_url: str | None = None
    answer_generator_api_key: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        src = os.environ if env is None else env

        def get(key: str, default: str | None = None) -> str | None:
            value = src.get(key)
            return default if value is None or value == "" else value

        return cls(
            answer_generator=get("ANSWER_GENERATOR", "fake") or "fake",
            case_file_provider=get("CASE_FILE_PROVIDER", "mock") or "mock",
            learner_context_provider=get("LEARNER_CONTEXT_PROVIDER", "mock") or "mock",
            guard_classifier=get("GUARD_CLASSIFIER", "mock") or "mock",
            interaction_log_repository=get("INTERACTION_LOG_REPOSITORY", "memory") or "memory",
            session_halt_repository=get("SESSION_HALT_REPOSITORY", "memory") or "memory",
            admin_alert_sink=get("ADMIN_ALERT_SINK", "memory") or "memory",
            security_incident_sink=get("SECURITY_INCIDENT_SINK", "memory") or "memory",
            current_user_provider=get("CURRENT_USER_PROVIDER", "header") or "header",
            generation_timeout_ms=_as_int(get("GENERATION_TIMEOUT_MS", "10000"), "GENERATION_TIMEOUT_MS"),
            generation_target_p95_ms=_as_int(
                get("GENERATION_TARGET_P95_MS", "3000"), "GENERATION_TARGET_P95_MS"
            ),
            allow_dev_session_ids=_as_bool(get("ALLOW_DEV_SESSION_IDS", "false") or "false"),
            answer_generator_provider=get("ANSWER_GENERATOR_PROVIDER"),
            answer_generator_model=get("ANSWER_GENERATOR_MODEL"),
            answer_generator_base_url=get("ANSWER_GENERATOR_BASE_URL"),
            answer_generator_api_key=get("ANSWER_GENERATOR_API_KEY"),
        )

    # -- introspection used by tests/test_config_surface.py -------------------
    @classmethod
    def field_names(cls) -> tuple[str, ...]:
        return tuple(f.name for f in fields(cls))

    def as_dict(self) -> dict[str, Any]:
        """Never returned to a client: secrets and provider names stay internal."""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def as_dict_public(self) -> dict[str, Any]:
        """Non-secret view, still internal-only."""
        return {k: v for k, v in self.as_dict().items() if "api_key" not in k}


#: Provider-selection keys, in the order the composition root resolves them.
#: (Settings attribute, port key)
PROVIDER_KEYS: tuple[tuple[str, str], ...] = (
    ("answer_generator", "answer_generator"),
    ("case_file_provider", "case_file_provider"),
    ("learner_context_provider", "learner_context_provider"),
    ("guard_classifier", "guard_classifier"),
    ("interaction_log_repository", "interaction_log_repository"),
    ("session_halt_repository", "session_halt_repository"),
    ("admin_alert_sink", "admin_alert_sink"),
    ("security_incident_sink", "security_incident_sink"),
    ("current_user_provider", "current_user_provider"),
)
