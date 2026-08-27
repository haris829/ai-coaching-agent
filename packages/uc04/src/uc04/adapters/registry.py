"""Provider registry.

Selection is a single lookup keyed on configuration. There is no chain of conditionals, and no
file outside this one learns that a new adapter exists.

Entries are **dotted import paths**, not imported symbols, so registering a real adapter is
literally one line here and nothing else - no import statement to add, no factory to write::

    COURSES_PROVIDERS = {
        "mock": "uc04.adapters.mock.courses:MockCoursesProvider",
        "company": "uc04.adapters.real.company_courses:CompanyCoursesAdapter",   # <- one line
    }

A configured name with no registered implementation fails loudly at startup, naming the missing
key and the file expected to supply it. There is no silent fallback to a mock: a service quietly
running on fake data in production is worse than one that refuses to start.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


class ProviderNotRegistered(RuntimeError):
    """Raised at startup when configuration names an implementation that does not exist."""


# ---------------------------------------------------------------------------- registries

COURSES_PROVIDERS: dict[str, str] = {
    "mock": "uc04.adapters.mock.courses:MockCoursesProvider",
    "foreign_demo": "uc04.adapters.real.foreign_demo:ForeignCoursesAdapter",
    "company_courses": "uc04.adapters.real.company_courses:CompanyCoursesAdapter",
}

LEARNER_CONTEXT_PROVIDERS: dict[str, str] = {
    "mock": "uc04.adapters.mock.learner_context:MockLearnerContextProvider",
    "foreign_demo": "uc04.adapters.real.foreign_demo:ForeignLearnerContextAdapter",
}

ANSWER_GENERATORS: dict[str, str] = {
    "fake": "uc04.adapters.generators.fake:FakeAnswerGenerator",
    "configured": "uc04.adapters.generators.configured:ConfiguredAnswerGenerator",
}

QUIZ_CLASSIFIERS: dict[str, str] = {
    "mock": "uc04.adapters.mock.quiz_intent:HeuristicQuizIntentClassifier",
}

CONCEPT_TAGGERS: dict[str, str] = {
    "mock": "uc04.adapters.mock.concept_tagger:MockConceptTagger",
}

INTERACTION_LOG_REPOSITORIES: dict[str, str] = {
    "memory": "uc04.adapters.memory.interaction_log:InMemoryInteractionLog",
}

FRAMING_REGISTRIES: dict[str, str] = {
    "memory": "uc04.adapters.memory.framing_registry:InMemoryFramingRegistry",
}

CURRENT_USER_PROVIDERS: dict[str, str] = {
    "header": "uc04.adapters.mock.current_user:HeaderCurrentUserProvider",
}


#: Config variable name -> (registry, human-readable port name).
REGISTRIES: dict[str, tuple[dict[str, str], str]] = {
    "COURSES_PROVIDER": (COURSES_PROVIDERS, "CoursesProvider"),
    "LEARNER_CONTEXT_PROVIDER": (LEARNER_CONTEXT_PROVIDERS, "LearnerContextProvider"),
    "ANSWER_GENERATOR": (ANSWER_GENERATORS, "AnswerGenerator"),
    "QUIZ_CLASSIFIER": (QUIZ_CLASSIFIERS, "QuizIntentClassifier"),
    "CONCEPT_TAGGER": (CONCEPT_TAGGERS, "ConceptTagger"),
    "INTERACTION_LOG_REPOSITORY": (INTERACTION_LOG_REPOSITORIES, "InteractionLogRepository"),
    "FRAMING_REGISTRY": (FRAMING_REGISTRIES, "FramingRegistry"),
    "CURRENT_USER_PROVIDER": (CURRENT_USER_PROVIDERS, "CurrentUserProvider"),
}


# ------------------------------------------------------------------------------ resolution


def resolve(config_key: str, name: str) -> Any:
    """Instantiate the implementation registered under ``name``.

    Fails loudly and specifically. The message names the config variable, the value that was
    set, the values that are available, and the file that would have to supply the missing one.
    """
    try:
        registry, port_name = REGISTRIES[config_key]
    except KeyError:  # pragma: no cover - programming error, not configuration
        raise ProviderNotRegistered(f"{config_key} is not a known configuration key") from None

    target = registry.get(name)
    if target is None:
        available = ", ".join(sorted(registry)) or "(none)"
        raise ProviderNotRegistered(
            f"{config_key}={name!r} names no registered {port_name}. "
            f"Registered values: {available}. "
            f"To add one, create the adapter (copy uc04/adapters/real/_template.py) and add a "
            f"single line to the {config_key} registry in uc04/adapters/registry.py mapping "
            f"{name!r} to 'your.module:YourAdapter'."
        )

    module_path, _, attribute = target.partition(":")
    try:
        module = import_module(module_path)
    except ImportError as exc:
        raise ProviderNotRegistered(
            f"{config_key}={name!r} is registered as {target!r} but the module could not be "
            f"imported: {exc}"
        ) from exc
    try:
        factory = getattr(module, attribute)
    except AttributeError as exc:
        raise ProviderNotRegistered(
            f"{config_key}={name!r} is registered as {target!r} but {attribute!r} was not found "
            f"in {module_path}."
        ) from exc

    return factory()
