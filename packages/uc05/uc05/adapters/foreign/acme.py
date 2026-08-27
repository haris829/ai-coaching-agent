"""A deliberately foreign adapter family.

This exists for one reason: to prove the swap rather than assert it.

Everything about the fictional "Acme" upstream is chosen to be *unlike* the
mocks -- different field names, different nesting depth, different value
representation, different error vocabulary:

===================  ===============================  =========================
Concept              Mock family                      Acme family
===================  ===============================  =========================
NARIC level          ``naricLevel: "LEVEL_6"``        ``...tier.code: "RQF6"``
Level provenance     ``provenance: "retrieved"``      ``origin: "LOOKUP"``
Practice area        ``area: "Employment"``           ``specialism.primary.label``
Nesting              flat dict                        four levels deep
Guiding question     ``{question, probing_focus}``    ``segments[]`` by ``type``
Four-part answer     four named fields                ``blocks[]`` by ``tag``
Intent               platform ``IntentKind``          ``prediction.code`` codes
Failure signalling   raises typed errors              ``{"status": "ERR_..."}``
===================  ===============================  =========================

The Acme adapters make no network calls: the "upstream" is a dict handed to the
constructor.  What is being demonstrated is not HTTP, it is that the *mapping*
is the only thing that differs, and that the service, domain, API, persistence
and every existing test are indifferent to which family is bound.

``tests/test_foreign_adapter_swap.py`` runs the same end-to-end dialogues
against both families and asserts identical platform-level outcomes.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ...domain.enums import IntentKind, NaricLevel, NaricLevelSource, SourceStatus
from ...domain.errors import (
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from ...domain.models import (
    Dialogue,
    FourPartAnswer,
    GuidingQuestionResult,
    IntentResult,
    LearnerContext,
)
from ...registry import (
    ANSWER_REGISTRY,
    GUIDING_QUESTION_REGISTRY,
    INTENT_REGISTRY,
    LEARNER_CONTEXT_REGISTRY,
)

PORT_CONTEXT = "learner_context_provider"
PORT_GUIDING = "guiding_question_generator"
PORT_ANSWER = "answer_generator"
PORT_INTENT = "intent_classifier"

#: Long enough that any realistic budget expires first, so a hanging
#: upstream is decided by the caller's timeout and never by a race.
HANG_SECONDS = 30.0

#: Acme signals failure in the payload rather than by transport status.
_ACME_ERRORS: dict[str, type[Exception]] = {
    "ERR_BACKEND_DOWN": ProviderUnavailable,
    "ERR_DEADLINE": ProviderTimeout,
    "ERR_SCHEMA": ProviderInvalidResponse,
}


def _raise_if_error(payload: Any, port: str) -> None:
    """Translate Acme's in-band error vocabulary into the port contract.

    Note what does not happen: Acme's own message is not forwarded.  Upstream
    error text must not escape this boundary.
    """
    if isinstance(payload, dict):
        status = payload.get("status")
        if isinstance(status, str) and status in _ACME_ERRORS:
            raise _ACME_ERRORS[status](port, "upstream reported a failure")


# --------------------------------------------------------------------------
# Learner context
# --------------------------------------------------------------------------

#: Acme's own level vocabulary.  Nothing outside this module knows these
#: strings exist.
_ACME_TIER_TO_NARIC: dict[str, NaricLevel] = {
    "RQF3": NaricLevel.LEVEL_3,
    "RQF4": NaricLevel.LEVEL_4,
    "RQF5": NaricLevel.LEVEL_5,
    "RQF6": NaricLevel.LEVEL_6,
    "RQF7": NaricLevel.LEVEL_7,
    "RQF7D": NaricLevel.LEVEL_7_PLUS,
}


@LEARNER_CONTEXT_REGISTRY.register("acme")
class AcmeLearnerContextAdapter:
    def __init__(
        self,
        upstream: dict[str, Any] | None = None,
        hang: bool = False,
        **_: object,
    ) -> None:
        self._upstream = upstream or {}
        self._hang = hang

    async def get_context(self, session_id: str, user_id: str) -> LearnerContext:
        if self._hang:
            await asyncio.sleep(HANG_SECONDS)
        payload = self._upstream
        _raise_if_error(payload, PORT_CONTEXT)
        return self.map_payload(payload)

    @staticmethod
    def map_payload(payload: Any) -> LearnerContext:
        if not isinstance(payload, dict):
            raise ProviderInvalidResponse(PORT_CONTEXT, "unexpected payload type")

        attributes = (
            payload.get("data", {}).get("attributes", {})
            if isinstance(payload.get("data"), dict)
            else {}
        )
        academic = attributes.get("academicProfile") or {}
        specialism = attributes.get("specialism") or {}

        tier_code = (academic.get("tier") or {}).get("code")
        origin = academic.get("origin")

        # A tier Acme sends that maps to no platform enum member is an invalid
        # response, not a level: default, source "default", status "invalid".
        # Never a guess, and never a widened enum.
        if tier_code in _ACME_TIER_TO_NARIC:
            level = _ACME_TIER_TO_NARIC[tier_code]
            source = (
                NaricLevelSource.RETRIEVED
                if origin == "LOOKUP"
                else NaricLevelSource.DEFAULT
            )
            status = (
                SourceStatus.AVAILABLE
                if origin == "LOOKUP"
                else SourceStatus.PARTIAL
            )
        elif tier_code is None:
            level = NaricLevel.LEVEL_5
            source = NaricLevelSource.DEFAULT
            status = SourceStatus.EMPTY
        else:
            level = NaricLevel.LEVEL_5
            source = NaricLevelSource.DEFAULT
            status = SourceStatus.INVALID

        label = (specialism.get("primary") or {}).get("label")
        area = label if isinstance(label, str) and label.strip() else None

        return LearnerContext(
            naric_level=level,
            naric_level_source=source,
            practice_area=area,
            source_status={
                "naric_level": status,
                "practice_area": (
                    SourceStatus.AVAILABLE if area else SourceStatus.EMPTY
                ),
            },
        )


# --------------------------------------------------------------------------
# Guiding questions
# --------------------------------------------------------------------------


@GUIDING_QUESTION_REGISTRY.register("acme")
class AcmeGuidingQuestionGenerator:
    """Acme returns a message with typed segments; UC-05 wants two strings."""

    def __init__(
        self,
        transcript: list[dict[str, Any]] | None = None,
        hang: bool = False,
        **_: object,
    ) -> None:
        self._transcript = list(transcript or [])
        self._hang = hang
        self.calls = 0

    async def generate(
        self,
        dialogue_state: Dialogue,
        question: str,
        context: LearnerContext,
    ) -> GuidingQuestionResult:
        if self._hang:
            await asyncio.sleep(HANG_SECONDS)
        index = min(self.calls, len(self._transcript) - 1) if self._transcript else -1
        self.calls += 1
        if index < 0:
            raise ProviderUnavailable(PORT_GUIDING, "no upstream transcript")

        payload = self._transcript[index]
        _raise_if_error(payload, PORT_GUIDING)
        return self.map_payload(payload, dialogue_state.prompt_version)

    @staticmethod
    def map_payload(payload: Any, prompt_version: str) -> GuidingQuestionResult:
        if not isinstance(payload, dict):
            raise ProviderInvalidResponse(PORT_GUIDING, "unexpected payload type")

        messages = (payload.get("result") or {}).get("messages") or []
        if not messages:
            raise ProviderInvalidResponse(PORT_GUIDING, "no message in payload")

        segments = messages[0].get("segments") or []
        probe = next((s.get("text") for s in segments if s.get("type") == "probe"), None)
        meta = next((s.get("text") for s in segments if s.get("type") == "meta"), None)

        if not probe:
            raise ProviderInvalidResponse(PORT_GUIDING, "no probe segment in payload")

        return GuidingQuestionResult(
            question=probe,
            # Never invent: an absent focus becomes the documented placeholder,
            # not a plausible-looking guess.
            probing_focus=meta or "unstated",
            prompt_version=prompt_version,
        )


# --------------------------------------------------------------------------
# Four-part answer
# --------------------------------------------------------------------------

_ACME_BLOCK_TO_FIELD: dict[str, str] = {
    "LAYMAN": "plain_english_explanation",
    "DEFN": "formal_legal_definition",
    "SCENARIO": "practical_example",
    "CITE": "authority_reference",
}


@ANSWER_REGISTRY.register("acme")
class AcmeAnswerGenerator:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        hang: bool = False,
        **_: object,
    ) -> None:
        self._payload = payload if payload is not None else _DEFAULT_ACME_ANSWER
        self._hang = hang

    async def generate(self, question: str, context: LearnerContext) -> FourPartAnswer:
        if self._hang:
            await asyncio.sleep(HANG_SECONDS)
        _raise_if_error(self._payload, PORT_ANSWER)
        return self.map_payload(self._payload)

    @staticmethod
    def map_payload(payload: Any) -> FourPartAnswer:
        if not isinstance(payload, dict):
            raise ProviderInvalidResponse(PORT_ANSWER, "unexpected payload type")

        blocks = payload.get("blocks")
        if not isinstance(blocks, list):
            # An upstream shape we cannot walk is a contract violation, and it
            # must leave this method as a typed error -- never as whatever
            # AttributeError the traversal would otherwise raise.
            raise ProviderInvalidResponse(PORT_ANSWER, "unexpected payload type")

        fields: dict[str, str] = {}
        for block in blocks:
            if not isinstance(block, dict):
                raise ProviderInvalidResponse(PORT_ANSWER, "unexpected payload type")
            field = _ACME_BLOCK_TO_FIELD.get(block.get("tag", ""))
            body = block.get("body")
            if field and isinstance(body, str) and body.strip():
                fields[field] = body

        if len(fields) != len(_ACME_BLOCK_TO_FIELD):
            # A missing part is a contract violation, never a partial answer.
            raise ProviderInvalidResponse(
                PORT_ANSWER, "four-part answer incomplete"
            )
        return FourPartAnswer(**fields)


_DEFAULT_ACME_ANSWER: dict[str, Any] = {
    "blocks": [
        {
            "tag": "LAYMAN",
            "body": (
                "In practical terms the question turns on whether every element "
                "the rule requires is present on these facts."
            ),
        },
        {
            "tag": "DEFN",
            "body": (
                "Each constituent element must be established on the balance of "
                "probabilities before the obligation arises."
            ),
        },
        {
            "tag": "SCENARIO",
            "body": (
                "Where one element is missing the obligation does not arise, "
                "however clear the parties' intentions were."
            ),
        },
        {
            "tag": "CITE",
            "body": "The leading appellate authority and the provision it construes.",
        },
    ]
}


# --------------------------------------------------------------------------
# Intent
# --------------------------------------------------------------------------

_ACME_CODE_TO_INTENT: dict[str, IntentKind] = {
    "ANSWER_ATTEMPT": IntentKind.SUBSTANTIVE_RESPONSE,
    "REQ_DIRECT": IntentKind.DIRECT_ANSWER_REQUEST,
    "AFFIRM": IntentKind.EXIT_CONFIRMATION,
    "DECLINE": IntentKind.EXIT_DECLINED,
    "BLOCKED_EXPLICIT": IntentKind.EXPLICIT_FRUSTRATION,
    "BLOCKED_CASUAL": IntentKind.CASUAL_DIFFICULTY,
    "CONCLUSION": IntentKind.LEARNER_REASONED_CONCLUSION,
    "IRRELEVANT": IntentKind.OFF_TOPIC,
}


@INTENT_REGISTRY.register("acme")
class AcmeIntentClassifier:
    """Acme returns a code and a confidence; UC-05 wants a platform intent.

    The confidence is deliberately *dropped*.  UC-05's contract has no notion
    of classifier confidence, and smuggling one through would put an upstream
    concept into the domain.  If confidence ever needs to affect behaviour,
    that is a contract conversation, not an adapter change.
    """

    def __init__(
        self,
        predictions: list[dict[str, Any]] | None = None,
        hang: bool = False,
        **_: object,
    ) -> None:
        self._predictions = list(predictions or [])
        self._hang = hang
        self.calls = 0

    async def classify(self, message: str, dialogue_state: Dialogue) -> IntentResult:
        if self._hang:
            await asyncio.sleep(HANG_SECONDS)
        index = min(self.calls, len(self._predictions) - 1) if self._predictions else -1
        self.calls += 1
        if index < 0:
            raise ProviderUnavailable(PORT_INTENT, "no upstream predictions")
        payload = self._predictions[index]
        _raise_if_error(payload, PORT_INTENT)
        return self.map_payload(payload)

    @staticmethod
    def map_payload(payload: Any) -> IntentResult:
        if not isinstance(payload, dict):
            raise ProviderInvalidResponse(PORT_INTENT, "unexpected payload type")
        code = (payload.get("prediction") or {}).get("code")
        if code not in _ACME_CODE_TO_INTENT:
            raise ProviderInvalidResponse(PORT_INTENT, "unmappable intent code")
        return IntentResult(
            kind=_ACME_CODE_TO_INTENT[code],
            matched_phrase=None,
            # Not the vendor's name: a provider name must not cross the
            # boundary, in a result any more than in an error.
            rule="upstream_classifier",
        )
