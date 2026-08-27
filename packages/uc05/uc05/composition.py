"""Composition root.

The only place in UC-05 that knows which implementation of a port is in use.

Adding a real adapter is exactly three edits, none of which is here except the
second:

1.  One new file under ``uc05/adapters/real/`` (copy ``_template.py``).
2.  **One line** in ``ADAPTER_MODULES`` below.
3.  One environment variable changed.

No other file learns that the adapter exists -- not the service, not the API,
not the domain, not another adapter, not an existing test.
"""

from __future__ import annotations

import importlib
from functools import lru_cache

from .application.socratic_service import SocraticService
from .config import Settings, load_settings
from .domain.errors import UnknownProvider
from .ports import CurrentUserProvider
from .registry import (
    ANSWER_REGISTRY,
    CURRENT_USER_REGISTRY,
    DIALOGUE_REPOSITORY_REGISTRY,
    GUIDING_QUESTION_REGISTRY,
    INTENT_REGISTRY,
    INTERACTION_LOG_REPOSITORY_REGISTRY,
    LEARNER_CONTEXT_REGISTRY,
    SESSION_MODE_REPOSITORY_REGISTRY,
)

# --------------------------------------------------------------------------
# ADAPTER MODULES
#
# Importing a module runs its @REGISTRY.register(...) decorators.  To add a
# real adapter, add ONE line to this tuple.  That is the whole registry half of
# the integration swap.
#
#     "uc05.adapters.real.company_learner_context",   # <- one line
# --------------------------------------------------------------------------
ADAPTER_MODULES: tuple[str, ...] = (
    "uc05.adapters.fake",
    "uc05.adapters.memory",
    "uc05.adapters.real",
    "uc05.adapters.foreign",  # <- an added adapter family looks exactly like this
    "uc05.adapters.local",
)


def load_adapter_modules(modules: tuple[str, ...] = ADAPTER_MODULES) -> None:
    for module in modules:
        importlib.import_module(module)


class Container:
    """Holds one instance of each port for the process lifetime.

    Repositories in particular must be singletons: the in-memory ones *are*
    the store, so a fresh instance per request would lose every dialogue.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        load_adapter_modules()
        self.settings = settings or load_settings()

        self.learner_context = LEARNER_CONTEXT_REGISTRY.create(
            self.settings.learner_context_provider, settings=self.settings
        )
        self.guiding_generator = GUIDING_QUESTION_REGISTRY.create(
            self.settings.generator, settings=self.settings
        )
        self.answer_generator = ANSWER_REGISTRY.create(
            self.settings.generator, settings=self.settings
        )
        self.intent_classifier = INTENT_REGISTRY.create(
            self.settings.intent_classifier, settings=self.settings
        )
        self.dialogues = DIALOGUE_REPOSITORY_REGISTRY.create(
            self.settings.dialogue_repository, settings=self.settings
        )
        self.modes = SESSION_MODE_REPOSITORY_REGISTRY.create(
            self.settings.session_mode_repository, settings=self.settings
        )
        self.interactions = INTERACTION_LOG_REPOSITORY_REGISTRY.create(
            self.settings.interaction_log_repository, settings=self.settings
        )
        self.current_user: CurrentUserProvider = CURRENT_USER_REGISTRY.create(
            self.settings.current_user_provider, settings=self.settings
        )

        self.service = SocraticService(
            settings=self.settings,
            learner_context=self.learner_context,
            guiding_generator=self.guiding_generator,
            answer_generator=self.answer_generator,
            intent_classifier=self.intent_classifier,
            dialogues=self.dialogues,
            modes=self.modes,
            interactions=self.interactions,
        )

    def describe(self) -> dict[str, str]:
        """Which provider key is bound to which port.  Operators only.

        Never returned to a client: knowing that ``GENERATOR=acme`` tells an
        attacker which upstream to probe, and provider names must not cross the
        API boundary.
        """
        return {
            "learner_context_provider": self.settings.learner_context_provider,
            "guiding_question_generator": self.settings.generator,
            "answer_generator": self.settings.generator,
            "intent_classifier": self.settings.intent_classifier,
            "dialogue_repository": self.settings.dialogue_repository,
            "session_mode_repository": self.settings.session_mode_repository,
            "interaction_log_repository": self.settings.interaction_log_repository,
            "current_user_provider": self.settings.current_user_provider,
        }


@lru_cache(maxsize=1)
def get_container() -> Container:
    """Process-wide container.

    ``UnknownProvider`` raised in here propagates out of application startup,
    which is the point: a service configured to use a provider that does not
    exist must refuse to start, loudly, rather than serve fake data.
    """
    return Container()


def reset_container() -> None:
    """Drop the cached container.  Tests and reconfiguration only."""
    get_container.cache_clear()


def verify_configuration(settings: Settings) -> None:
    """Fail fast, before any request is served.

    Called from the FastAPI lifespan hook so a misconfiguration surfaces at
    boot with a message naming the missing implementation, rather than as a
    500 on the first learner's first question.
    """
    load_adapter_modules()
    checks = (
        (LEARNER_CONTEXT_REGISTRY, settings.learner_context_provider),
        (GUIDING_QUESTION_REGISTRY, settings.generator),
        (ANSWER_REGISTRY, settings.generator),
        (INTENT_REGISTRY, settings.intent_classifier),
        (DIALOGUE_REPOSITORY_REGISTRY, settings.dialogue_repository),
        (SESSION_MODE_REPOSITORY_REGISTRY, settings.session_mode_repository),
        (INTERACTION_LOG_REPOSITORY_REGISTRY, settings.interaction_log_repository),
        (CURRENT_USER_REGISTRY, settings.current_user_provider),
    )
    problems = [
        registry._missing_message(key)
        for registry, key in checks
        if key not in registry.keys()
    ]
    if problems:
        raise UnknownProvider("\n\n".join(problems))
