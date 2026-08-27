"""Conformance harnesses.

The conformance suite is **adapter-agnostic**: it asserts the behavioural
contract of a port, never the data of any particular implementation.  What it
needs from an implementation is a way to put that implementation into each
documented state -- happy, unavailable, timed out, malformed, and so on -- and
that is what a harness supplies.

An integration engineer writes ONE harness (about twenty lines, template in
``_template_harness.py``), appends it to the list at the bottom of this file,
and every conformance test in the suite runs against their adapter.  No new
test is written.

``leak_markers`` is the important field.  It lists strings that belong to the
upstream and must never escape the adapter: upstream field names, upstream
error text, the vendor's name.  The suite searches every returned value and
every raised exception for them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from uc05.adapters.fake.generators import (
    FakeAnswerGenerator,
    FakeGuidingQuestionGenerator,
)
from uc05.adapters.fake.intent import MockIntentClassifier
from uc05.adapters.fake.learner_context import MockLearnerContextProvider
from uc05.adapters.foreign.acme import (
    AcmeAnswerGenerator,
    AcmeGuidingQuestionGenerator,
    AcmeIntentClassifier,
    AcmeLearnerContextAdapter,
)

Factory = Callable[[], Any]


@dataclass(frozen=True)
class PortHarness:
    """How to drive one adapter into each documented state.

    A ``None`` factory means "this implementation cannot be driven into that
    state" and the corresponding test skips *that state only* -- it does not
    skip the contract.  Every failure mode an adapter documents must have a
    factory, or the suite cannot vouch for it.
    """

    name: str
    port: str
    #: Strings belonging to the upstream that must never cross the boundary.
    leak_markers: tuple[str, ...]
    happy: Factory
    unavailable: Factory | None = None
    timeout: Factory | None = None
    malformed: Factory | None = None
    #: Upstream sent a value that maps to no platform enum member.
    invalid_value: Factory | None = None
    #: Upstream answered and had nothing.  Distinct from unavailable.
    empty: Factory | None = None
    #: Answers, but far too slowly -- used to prove the timeout budget binds.
    slow: Factory | None = None
    #: Extra per-port expectations, e.g. the level the happy case must yield.
    expectations: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Fictional Acme payloads.  These shapes exist only inside the Acme adapter;
# the harness knows them because it is playing the part of the upstream.
# --------------------------------------------------------------------------

ACME_CONTEXT_OK: dict[str, Any] = {
    "data": {
        "attributes": {
            "academicProfile": {"tier": {"code": "RQF6"}, "origin": "LOOKUP"},
            "specialism": {"primary": {"label": "Employment"}},
        }
    }
}
ACME_CONTEXT_INVALID: dict[str, Any] = {
    "data": {
        "attributes": {
            "academicProfile": {"tier": {"code": "DIPLOMA-X"}, "origin": "LOOKUP"},
            "specialism": {"primary": {"label": "Employment"}},
        }
    }
}
ACME_CONTEXT_EMPTY: dict[str, Any] = {
    "data": {"attributes": {"academicProfile": {}, "specialism": {}}}
}

ACME_GUIDING_OK: dict[str, Any] = {
    "result": {
        "messages": [
            {
                "role": "tutor",
                "segments": [
                    {
                        "type": "probe",
                        "text": "Which element of the rule is missing on these facts?",
                    },
                    {"type": "meta", "text": "the missing element"},
                ],
            }
        ]
    }
}

ACME_ANSWER_MISSING_PART: dict[str, Any] = {
    "blocks": [
        {"tag": "LAYMAN", "body": "Something."},
        {"tag": "DEFN", "body": "Something formal."},
    ]
}

ACME_INTENT_OK: dict[str, Any] = {"prediction": {"code": "REQ_DIRECT", "confidence": 0.97}}
ACME_INTENT_UNMAPPABLE: dict[str, Any] = {"prediction": {"code": "WHO_KNOWS"}}


def _acme_error(status: str) -> dict[str, Any]:
    return {"status": status, "message": "acme internal fault 0x41: shard unavailable"}


ACME_LEAK_MARKERS = (
    "acme",
    "Acme",
    "RQF6",
    "naricLevel",
    "academicProfile",
    "specialism",
    "segments",
    "LAYMAN",
    "prediction",
    "shard unavailable",
    "0x41",
)

#: The mock family's upstream-shaped artefacts.  Note that the mock's own
#: scenario words ("normal", "scripted") are deliberately NOT listed: they are
#: the mock's vocabulary, not an upstream payload shape, and listing them would
#: make the leak check assert something it is not for.
MOCK_LEAK_MARKERS = ("naricLevel", "provenance", "RQF Level 6 (Hons)")


# --------------------------------------------------------------------------
# LearnerContextProvider
# --------------------------------------------------------------------------

LEARNER_CONTEXT_HARNESSES: tuple[PortHarness, ...] = (
    PortHarness(
        name="mock",
        port="learner_context_provider",
        leak_markers=MOCK_LEAK_MARKERS,
        happy=lambda: MockLearnerContextProvider(scenario="level_6"),
        unavailable=lambda: MockLearnerContextProvider(scenario="unavailable"),
        timeout=lambda: MockLearnerContextProvider(scenario="timeout"),
        malformed=lambda: MockLearnerContextProvider(scenario="malformed"),
        invalid_value=lambda: MockLearnerContextProvider(scenario="invalid_level"),
        empty=lambda: MockLearnerContextProvider(scenario="empty"),
        slow=lambda: MockLearnerContextProvider(scenario="slow"),
        expectations={"level": "LEVEL_6", "practice_area": "Employment"},
    ),
    PortHarness(
        name="acme",
        port="learner_context_provider",
        leak_markers=ACME_LEAK_MARKERS,
        happy=lambda: AcmeLearnerContextAdapter(upstream=ACME_CONTEXT_OK),
        unavailable=lambda: AcmeLearnerContextAdapter(
            upstream=_acme_error("ERR_BACKEND_DOWN")
        ),
        timeout=lambda: AcmeLearnerContextAdapter(upstream=_acme_error("ERR_DEADLINE")),
        malformed=lambda: AcmeLearnerContextAdapter(upstream=_acme_error("ERR_SCHEMA")),
        invalid_value=lambda: AcmeLearnerContextAdapter(upstream=ACME_CONTEXT_INVALID),
        empty=lambda: AcmeLearnerContextAdapter(upstream=ACME_CONTEXT_EMPTY),
        slow=lambda: AcmeLearnerContextAdapter(upstream=ACME_CONTEXT_OK, hang=True),
        expectations={"level": "LEVEL_6", "practice_area": "Employment"},
    ),
)


# --------------------------------------------------------------------------
# GuidingQuestionGenerator
# --------------------------------------------------------------------------

GUIDING_HARNESSES: tuple[PortHarness, ...] = (
    PortHarness(
        name="fake",
        port="guiding_question_generator",
        leak_markers=MOCK_LEAK_MARKERS,
        happy=lambda: FakeGuidingQuestionGenerator(scenario="normal"),
        unavailable=lambda: FakeGuidingQuestionGenerator(scenario="unavailable"),
        timeout=lambda: FakeGuidingQuestionGenerator(scenario="timeout"),
        malformed=lambda: FakeGuidingQuestionGenerator(scenario="malformed"),
        slow=lambda: FakeGuidingQuestionGenerator(scenario="slow"),
    ),
    PortHarness(
        name="acme",
        port="guiding_question_generator",
        leak_markers=ACME_LEAK_MARKERS,
        happy=lambda: AcmeGuidingQuestionGenerator(transcript=[ACME_GUIDING_OK]),
        unavailable=lambda: AcmeGuidingQuestionGenerator(
            transcript=[_acme_error("ERR_BACKEND_DOWN")]
        ),
        timeout=lambda: AcmeGuidingQuestionGenerator(
            transcript=[_acme_error("ERR_DEADLINE")]
        ),
        malformed=lambda: AcmeGuidingQuestionGenerator(transcript=[{"result": {}}]),
        slow=lambda: AcmeGuidingQuestionGenerator(
            transcript=[ACME_GUIDING_OK], hang=True
        ),
    ),
)


# --------------------------------------------------------------------------
# AnswerGenerator
# --------------------------------------------------------------------------

ANSWER_HARNESSES: tuple[PortHarness, ...] = (
    PortHarness(
        name="fake",
        port="answer_generator",
        leak_markers=MOCK_LEAK_MARKERS,
        happy=lambda: FakeAnswerGenerator(scenario="well_formed"),
        unavailable=lambda: FakeAnswerGenerator(scenario="unavailable"),
        timeout=lambda: FakeAnswerGenerator(scenario="timeout"),
        malformed=lambda: FakeAnswerGenerator(scenario="malformed"),
        empty=lambda: FakeAnswerGenerator(scenario="missing_part"),
        slow=lambda: FakeAnswerGenerator(scenario="slow"),
    ),
    PortHarness(
        name="acme",
        port="answer_generator",
        leak_markers=ACME_LEAK_MARKERS,
        happy=lambda: AcmeAnswerGenerator(),
        unavailable=lambda: AcmeAnswerGenerator(payload=_acme_error("ERR_BACKEND_DOWN")),
        timeout=lambda: AcmeAnswerGenerator(payload=_acme_error("ERR_DEADLINE")),
        malformed=lambda: AcmeAnswerGenerator(payload={"blocks": "not a list of blocks"}),
        empty=lambda: AcmeAnswerGenerator(payload=ACME_ANSWER_MISSING_PART),
        slow=lambda: AcmeAnswerGenerator(hang=True),
    ),
)


# --------------------------------------------------------------------------
# IntentClassifier
# --------------------------------------------------------------------------

INTENT_HARNESSES: tuple[PortHarness, ...] = (
    PortHarness(
        name="mock",
        port="intent_classifier",
        leak_markers=MOCK_LEAK_MARKERS,
        happy=lambda: MockIntentClassifier(),
        unavailable=lambda: MockIntentClassifier(failure="unavailable"),
        timeout=lambda: MockIntentClassifier(failure="timeout"),
        malformed=lambda: MockIntentClassifier(failure="malformed"),
        slow=lambda: MockIntentClassifier(failure="slow"),
    ),
    PortHarness(
        name="acme",
        port="intent_classifier",
        leak_markers=ACME_LEAK_MARKERS,
        happy=lambda: AcmeIntentClassifier(predictions=[ACME_INTENT_OK]),
        unavailable=lambda: AcmeIntentClassifier(
            predictions=[_acme_error("ERR_BACKEND_DOWN")]
        ),
        timeout=lambda: AcmeIntentClassifier(predictions=[_acme_error("ERR_DEADLINE")]),
        malformed=lambda: AcmeIntentClassifier(predictions=[ACME_INTENT_UNMAPPABLE]),
        slow=lambda: AcmeIntentClassifier(predictions=[ACME_INTENT_OK], hang=True),
    ),
)


def ids(harnesses: tuple[PortHarness, ...]) -> list[str]:
    return [harness.name for harness in harnesses]
