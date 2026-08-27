"""Composition root.

PROVIDER_REGISTRY below is the ONLY place in the repository that knows which
adapter implements which port. Integrating a real upstream system is:

  1. one new adapter file (copy uc06/adapters/real/_template.py),
  2. ONE line added to the relevant port entry below,
  3. one environment variable changed.

Nothing else changes: not the domain models, not the application services, not
the API layer, not the existing adapters, not persistence, and not one existing
test. See docs/INTEGRATION.md for the worked example and
tests/test_integration_swap.py for the proof.

Entries are dotted "module:Attribute" paths, resolved lazily, so adding a
provider needs no import line here either.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .application.case_coaching_service import CaseCoachingService
from .application.emitter import ResponseEmitter
from .config import PROVIDER_KEYS, Settings
from .domain.errors import ConfigurationError
from .registry import ProviderRegistry

# ---------------------------------------------------------------------------
# THE REGISTRY. One line per provider. Add real adapters here and nowhere else.
# ---------------------------------------------------------------------------
PROVIDER_REGISTRY: Mapping[str, Mapping[str, str]] = {
    "case_file_provider": {
        "mock": "uc06.adapters.mock.case_file:MockCaseFileProvider",
        "foreign": "uc06.adapters.foreign.case_file:ForeignCaseFileAdapter",
        "newco": "uc06.adapters.real.newco_case_file:NewCoCaseFileAdapter",
    },
    "learner_context_provider": {
        "mock": "uc06.adapters.mock.learner_context:MockLearnerContextProvider",
        "foreign": "uc06.adapters.foreign.learner_context:ForeignLearnerContextAdapter",
    },
    "answer_generator": {
        "fake": "uc06.adapters.mock.answer_generator:FakeAnswerGenerator",
        "configured": "uc06.adapters.real.configured_generator:ConfiguredAnswerGenerator",
        "foreign": "uc06.adapters.foreign.answer_generator:ForeignAnswerGeneratorAdapter",
    },
    "guard_classifier": {
        "mock": "uc06.adapters.mock.guard_classifier:MockGuardClassifier",
    },
    "interaction_log_repository": {
        "memory": "uc06.adapters.memory.storage:InMemoryInteractionLogRepository",
    },
    "session_halt_repository": {
        "memory": "uc06.adapters.memory.storage:InMemorySessionHaltRepository",
    },
    "admin_alert_sink": {
        "memory": "uc06.adapters.memory.sinks:InMemoryAdminAlertSink",
    },
    "security_incident_sink": {
        "memory": "uc06.adapters.memory.sinks:InMemorySecurityIncidentSink",
    },
    "current_user_provider": {
        "header": "uc06.adapters.identity.header_user:HeaderCurrentUserProvider",
    },
}

REGISTRY = ProviderRegistry(PROVIDER_REGISTRY)


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    case_files: Any
    learner_context: Any
    generator: Any
    guard: Any
    interactions: Any
    halts: Any
    admin_alerts: Any
    security_incidents: Any
    current_user: Any
    service: CaseCoachingService
    emitter: ResponseEmitter


def build_container(settings: Settings | None = None) -> Container:
    """Resolve every port eagerly, so a misconfigured provider fails at startup
    rather than on the first request that happens to need it.

    There is no silent fallback to a mock: an unregistered provider name raises
    ConfigurationError naming the port, the value, the registry file and the
    template to copy.
    """
    settings = settings or Settings.from_env()

    resolved: dict[str, Any] = {}
    for attribute, port_key in PROVIDER_KEYS:
        provider_name = getattr(settings, attribute)
        resolved[port_key] = REGISTRY.resolve(port_key, provider_name, settings)

    service = CaseCoachingService(
        settings=settings,
        case_files=resolved["case_file_provider"],
        learner_context=resolved["learner_context_provider"],
        generator=resolved["answer_generator"],
        guard=resolved["guard_classifier"],
        interactions=resolved["interaction_log_repository"],
        halts=resolved["session_halt_repository"],
        security_incidents=resolved["security_incident_sink"],
    )
    response_emitter = ResponseEmitter(
        halts=resolved["session_halt_repository"],
        admin_alerts=resolved["admin_alert_sink"],
        security_incidents=resolved["security_incident_sink"],
    )
    return Container(
        settings=settings,
        case_files=resolved["case_file_provider"],
        learner_context=resolved["learner_context_provider"],
        generator=resolved["answer_generator"],
        guard=resolved["guard_classifier"],
        interactions=resolved["interaction_log_repository"],
        halts=resolved["session_halt_repository"],
        admin_alerts=resolved["admin_alert_sink"],
        security_incidents=resolved["security_incident_sink"],
        current_user=resolved["current_user_provider"],
        service=service,
        emitter=response_emitter,
    )


def registered_names(port_key: str) -> tuple[str, ...]:
    return REGISTRY.names_for(port_key)


def assert_registry_complete() -> None:
    """Every port UC-06 selects on must have at least one registered provider."""
    missing = [port for _, port in PROVIDER_KEYS if not REGISTRY.names_for(port)]
    if missing:
        raise ConfigurationError(
            "PROVIDER_REGISTRY in uc06/composition.py has no implementations for: " + ", ".join(missing)
        )
