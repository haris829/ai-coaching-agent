"""Local, server-side greeting/template layer.

This is the shipped implementation of the ``GreetingGenerator`` contract
(``uc01.contracts.greeting``). It is deliberately deterministic and dependency-free:
UC-01 does not require an AI service to open a session. If one is introduced later it
plugs in behind the same contract — see ``docs/ADAPTER_REPLACEMENT.md``.

Guarantees enforced here:

* No learner name, course, lesson or case file is ever invented. If the data is absent,
  the wording changes; it is not filled in.
* The level sentence never attributes a defaulted level to NARIC.
* All externally sourced text is sanitised through
  :func:`uc01.domain.prompts.sanitize_untrusted_text` before it is rendered.
"""

from __future__ import annotations

from collections.abc import Mapping

from . import messages
from .enums import NaricLevelSource, SessionMode
from .models import Greeting, SessionContext
from .prompts import (
    GREETING_SYSTEM_PROMPT_ID,
    PromptPayload,
    SystemPromptRegistry,
    sanitize_untrusted_text,
)


class LocalTemplateGreetingGenerator:
    """Composes the session-opening greeting from server-side templates."""

    def __init__(self, prompts: SystemPromptRegistry | None = None) -> None:
        self._prompts = prompts or SystemPromptRegistry()

    # -- public contract ---------------------------------------------------- #

    def generate(self, context: SessionContext) -> Greeting:
        prompt = self._prompts.get(GREETING_SYSTEM_PROMPT_ID)

        # Built even though the local generator does not call a model: it is the exact
        # payload a future generation adapter would receive, and building it here proves
        # the trusted/untrusted separation is part of the greeting path.
        payload = self.build_prompt_payload(context)
        assert payload.system_prompt is prompt  # noqa: S101 - internal invariant

        name = self._display_name(context)
        personalised = name is not None

        sentences = [self._opening_sentence(name)]

        focus = self._focus_sentence(context)
        if focus:
            sentences.append(focus)

        sentences.append(self._level_sentence(context))

        # The apology sentence appears only when the profile could not be loaded at
        # all. A loaded-but-incomplete profile simply produces a generic greeting.
        if context.profile is None:
            sentences.append(messages.PROFILE_UNAVAILABLE_NOTICE)

        return Greeting(
            text=" ".join(sentences),
            variant=self._variant(context, personalised=personalised),
            system_prompt_id=prompt.prompt_id,
            system_prompt_version=prompt.version,
            personalised=personalised,
        )

    def build_prompt_payload(self, context: SessionContext) -> PromptPayload:
        """Assemble the privileged prompt payload for this session context.

        Server-side only. Never returned by the API.
        """
        untrusted: dict[str, str] = {}
        if context.profile and context.profile.display_name:
            untrusted["learner_name"] = sanitize_untrusted_text(
                context.profile.display_name
            )
        if context.course:
            untrusted["course_title"] = sanitize_untrusted_text(context.course.title)
        if context.lesson:
            untrusted["lesson_title"] = sanitize_untrusted_text(context.lesson.title)
        if context.case_file:
            untrusted["case_title"] = sanitize_untrusted_text(context.case_file.title)

        facts: Mapping[str, object] = {
            "session_mode": context.session_mode.value,
            "explanation_level": context.naric_level,
            "explanation_level_source": context.naric_level_source.value,
            "personalisation_available": context.personalisation_available,
            "degraded_dependencies": [
                dependency.value for dependency in context.degraded_dependencies
            ],
        }
        return PromptPayload(
            system_prompt=self._prompts.get(GREETING_SYSTEM_PROMPT_ID),
            untrusted=untrusted,
            session_facts=facts,
        )

    # -- internals ---------------------------------------------------------- #

    @staticmethod
    def _display_name(context: SessionContext) -> str | None:
        if not context.personalisation_available or context.profile is None:
            return None
        name = sanitize_untrusted_text(context.profile.display_name)
        return name or None

    @staticmethod
    def _opening_sentence(name: str | None) -> str:
        if name:
            return f"Hi {name}! Welcome back to your coaching session."
        return "Hi! Welcome back to your coaching session."

    @staticmethod
    def _focus_sentence(context: SessionContext) -> str | None:
        if context.session_mode is SessionMode.COURSE_LINKED and context.course:
            course = sanitize_untrusted_text(context.course.title)
            if context.lesson:
                lesson = sanitize_untrusted_text(context.lesson.title)
                return f"We are working on {lesson} from {course}."
            return f"We are working on {course}."
        if context.session_mode is SessionMode.CASE_LINKED and context.case_file:
            case_title = sanitize_untrusted_text(context.case_file.title)
            return f"We are working on your case file {case_title}."
        if context.session_mode is SessionMode.FREE_FORM:
            if context.downgraded_from is not None:
                return (
                    "We have opened a free-form session so you can keep going while "
                    "that is unavailable."
                )
            return "Ask me anything you would like to work through."
        return None

    @staticmethod
    def _level_sentence(context: SessionContext) -> str:
        level = context.naric_level
        if context.naric_level_source is NaricLevelSource.NARIC:
            return f"Explanations are calibrated to your NARIC Level {level}."
        return (
            f"Explanations will use Level {level} by default, because calibrated NARIC "
            "data was not available."
        )

    @staticmethod
    def _variant(context: SessionContext, *, personalised: bool) -> str:
        prefix = "personalised" if personalised else "generic"
        mode = context.session_mode.value.replace("-", "_")
        return f"{prefix}.{mode}"


__all__ = ["LocalTemplateGreetingGenerator"]
