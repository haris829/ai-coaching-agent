"""Adapter registry for the conformance kit.

A new adapter joins the suite by adding ONE case to the list for its port. No
conformance test is modified, which is the point: a real company adapter must
pass exactly the same suite as the mock and the foreign adapter.

Each case supplies:

* ``build()``            - a healthy adapter;
* ``build_unavailable()``/``build_timeout()``/``build_invalid()`` - the same
  adapter wired to a source that fails in that specific way;
* ``build_empty()``      - a source that answers with nothing (empty != unavailable);
* ``upstream_tokens``    - vocabulary that must never leak out of the adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from uc07.adapters.foreign import NEXUS_PAYLOAD, EXTERNAL_LEARNER_ID
from uc07.adapters.foreign.adapters import (
    ForeignCoursesProvider,
    ForeignFeedbackProvider,
    ForeignInteractionLogProvider,
    ForeignLearnerProfileProvider,
)
from uc07.adapters.mock.courses import MockCoursesPayload, MockCoursesProvider
from uc07.adapters.mock.feedback import MockFeedbackPayload, MockFeedbackProvider
from uc07.adapters.mock.interaction_log import (
    MockInteractionLogProvider,
    MockInteractionPayload,
)
from uc07.adapters.mock.profile import MockLearnerProfileProvider, MockProfilePayload
from uc07.adapters.mock.scenarios import (
    CATALOGUE,
    CANDIDATES,
    ENROLMENTS,
    LEARNER,
    PROFILE_WITH_UNEXPLORED,
    STRUGGLE_MIXED_FEEDBACK,
    STRUGGLE_MIXED_RECORDS,
)

#: Vocabulary that must never appear in domain data or error text.
MOCK_TOKENS = ("MockInteraction", "MockFeedback", "MockCourses", "MOCK_SCENARIO")
FOREIGN_TOKENS = (
    "entryRef",
    "occurredAtEpochMs",
    "eqfBand",
    "taxonomy",
    "reexplainTally",
    "verdictLifecycle",
    "programmeRef",
    "moduleRef",
    "focusAreas",
    "suggestionFeed",
    "Nexus",
    "nexus",
    "POSITIVE",
    "NEGATIVE",
    "AWAITING",
    "COMPLETE",
    "FULL",
    "PARTIAL",
)


@dataclass(frozen=True)
class AdapterCase:
    """One adapter under conformance test."""

    id: str
    build: Callable[[], Any]
    user_id: str
    upstream_tokens: tuple[str, ...]
    build_unavailable: Callable[[], Any] | None = None
    build_timeout: Callable[[], Any] | None = None
    build_invalid: Callable[[], Any] | None = None
    build_empty: Callable[[], Any] | None = None
    extras: dict[str, Any] = field(default_factory=dict)


def _foreign_payload_with(**overrides: Any) -> dict[str, Any]:
    """A shallow copy of the Nexus payload with a mutated dossier section."""
    import copy

    payload = copy.deepcopy(NEXUS_PAYLOAD)
    dossier = payload["learnerDossiers"][0]
    for key, value in overrides.items():
        dossier[key] = value
    return payload


# ---------------------------------------------------------------------------
# InteractionLogProvider
# ---------------------------------------------------------------------------

INTERACTION_LOG_CASES: tuple[AdapterCase, ...] = (
    AdapterCase(
        id="mock",
        user_id=LEARNER,
        upstream_tokens=MOCK_TOKENS,
        build=lambda: MockInteractionLogProvider(
            {LEARNER: MockInteractionPayload(records=STRUGGLE_MIXED_RECORDS)}
        ),
        build_unavailable=lambda: MockInteractionLogProvider(
            {LEARNER: MockInteractionPayload(failure="unavailable")}
        ),
        build_timeout=lambda: MockInteractionLogProvider(
            {LEARNER: MockInteractionPayload(failure="timeout")}
        ),
        build_invalid=lambda: MockInteractionLogProvider(
            {
                LEARNER: MockInteractionPayload(
                    records=({"interaction_id": "broken"},)
                )
            }
        ),
        build_empty=lambda: MockInteractionLogProvider(
            {LEARNER: MockInteractionPayload(records=(), status="empty")}
        ),
    ),
    AdapterCase(
        id="foreign",
        user_id=EXTERNAL_LEARNER_ID,
        upstream_tokens=FOREIGN_TOKENS,
        build=lambda: ForeignInteractionLogProvider(NEXUS_PAYLOAD),
        build_unavailable=lambda: ForeignInteractionLogProvider(
            NEXUS_PAYLOAD, failure="unavailable"
        ),
        build_timeout=lambda: ForeignInteractionLogProvider(
            NEXUS_PAYLOAD, failure="timeout"
        ),
        build_invalid=lambda: ForeignInteractionLogProvider(
            _foreign_payload_with(
                coachingLedger={
                    "completeness": "FULL",
                    "tally": 1,
                    "entries": [{"entryRef": "x", "eqfBand": "EQF-99"}],
                }
            )
        ),
        build_empty=lambda: ForeignInteractionLogProvider(
            _foreign_payload_with(
                coachingLedger={"completeness": "NONE", "tally": 0, "entries": []}
            )
        ),
    ),
    # A real company adapter joins here with ONE entry:
    # AdapterCase(id="acme", user_id=..., upstream_tokens=(...), build=...),
)


# ---------------------------------------------------------------------------
# FeedbackProvider
# ---------------------------------------------------------------------------

FEEDBACK_CASES: tuple[AdapterCase, ...] = (
    AdapterCase(
        id="mock",
        user_id=LEARNER,
        upstream_tokens=MOCK_TOKENS,
        build=lambda: MockFeedbackProvider(
            MockFeedbackPayload(records=STRUGGLE_MIXED_FEEDBACK)
        ),
        build_unavailable=lambda: MockFeedbackProvider(
            MockFeedbackPayload(failure="unavailable")
        ),
        build_timeout=lambda: MockFeedbackProvider(
            MockFeedbackPayload(failure="timeout")
        ),
        build_invalid=lambda: MockFeedbackProvider(
            MockFeedbackPayload(
                records=({"rating_id": "r", "interaction_id": "interaction-101"},)
            )
        ),
        build_empty=lambda: MockFeedbackProvider(
            MockFeedbackPayload(records=(), status="empty")
        ),
        extras={"known_interaction_ids": ("interaction-101", "interaction-301")},
    ),
    AdapterCase(
        id="foreign",
        user_id=EXTERNAL_LEARNER_ID,
        upstream_tokens=FOREIGN_TOKENS,
        build=lambda: ForeignFeedbackProvider(
            NEXUS_PAYLOAD, external_id=EXTERNAL_LEARNER_ID
        ),
        build_unavailable=lambda: ForeignFeedbackProvider(
            NEXUS_PAYLOAD, external_id=EXTERNAL_LEARNER_ID, failure="unavailable"
        ),
        build_timeout=lambda: ForeignFeedbackProvider(
            NEXUS_PAYLOAD, external_id=EXTERNAL_LEARNER_ID, failure="timeout"
        ),
        build_invalid=lambda: ForeignFeedbackProvider(
            _foreign_payload_with(
                verdictLedger={
                    "completeness": "FULL",
                    "entries": [
                        {
                            "verdictRef": "v",
                            "entryRef": "interaction-101",
                            "sentiment": "SHRUG",
                            "atEpochMs": 1,
                        }
                    ],
                }
            ),
            external_id=EXTERNAL_LEARNER_ID,
        ),
        build_empty=lambda: ForeignFeedbackProvider(
            _foreign_payload_with(
                verdictLedger={"completeness": "NONE", "entries": []}
            ),
            external_id=EXTERNAL_LEARNER_ID,
        ),
        extras={"known_interaction_ids": ("interaction-101", "interaction-301")},
    ),
)


# ---------------------------------------------------------------------------
# LearnerProfileProvider
# ---------------------------------------------------------------------------

PROFILE_CASES: tuple[AdapterCase, ...] = (
    AdapterCase(
        id="mock",
        user_id=LEARNER,
        upstream_tokens=MOCK_TOKENS,
        build=lambda: MockLearnerProfileProvider(
            {LEARNER: MockProfilePayload(profile=PROFILE_WITH_UNEXPLORED)}
        ),
        build_unavailable=lambda: MockLearnerProfileProvider(
            {LEARNER: MockProfilePayload(failure="unavailable")}
        ),
        build_timeout=lambda: MockLearnerProfileProvider(
            {LEARNER: MockProfilePayload(failure="timeout")}
        ),
        build_invalid=lambda: MockLearnerProfileProvider(
            {
                LEARNER: MockProfilePayload(
                    profile={
                        "speciality_areas": ("alpha",),
                        "speciality_status": "empty",
                    }
                )
            }
        ),
        build_empty=lambda: MockLearnerProfileProvider(
            {
                LEARNER: MockProfilePayload(
                    profile={"speciality_areas": (), "speciality_status": "empty"}
                )
            }
        ),
    ),
    AdapterCase(
        id="foreign",
        user_id=EXTERNAL_LEARNER_ID,
        upstream_tokens=FOREIGN_TOKENS,
        build=lambda: ForeignLearnerProfileProvider(NEXUS_PAYLOAD),
        build_unavailable=lambda: ForeignLearnerProfileProvider(
            NEXUS_PAYLOAD, failure="unavailable"
        ),
        build_timeout=lambda: ForeignLearnerProfileProvider(
            NEXUS_PAYLOAD, failure="timeout"
        ),
        build_invalid=lambda: ForeignLearnerProfileProvider(
            _foreign_payload_with(
                profileDoc={
                    "focusAreas": [{"tag": "alpha"}],
                    "focusCompleteness": "NONE",
                    "eqfBand": "EQF-6",
                    "eqfOrigin": "LOOKUP",
                }
            )
        ),
        build_empty=lambda: ForeignLearnerProfileProvider(
            _foreign_payload_with(
                profileDoc={
                    "focusAreas": [],
                    "focusCompleteness": "NONE",
                    "eqfBand": "EQF-6",
                    "eqfOrigin": "LOOKUP",
                }
            )
        ),
    ),
)


# ---------------------------------------------------------------------------
# CoursesProvider
# ---------------------------------------------------------------------------

COURSES_CASES: tuple[AdapterCase, ...] = (
    AdapterCase(
        id="mock",
        user_id=LEARNER,
        upstream_tokens=MOCK_TOKENS,
        build=lambda: MockCoursesProvider(
            MockCoursesPayload(
                catalogue=CATALOGUE, candidates=CANDIDATES, enrolments=ENROLMENTS
            )
        ),
        build_unavailable=lambda: MockCoursesProvider(
            MockCoursesPayload(failure="unavailable")
        ),
        build_timeout=lambda: MockCoursesProvider(
            MockCoursesPayload(failure="timeout")
        ),
        build_invalid=lambda: MockCoursesProvider(
            MockCoursesPayload(catalogue=({"title": "no course id"},))
        ),
        build_empty=lambda: MockCoursesProvider(MockCoursesPayload(status="empty")),
        extras={"topic_tags": ("contract_formation", "negligence")},
    ),
    AdapterCase(
        id="foreign",
        user_id=EXTERNAL_LEARNER_ID,
        upstream_tokens=FOREIGN_TOKENS,
        build=lambda: ForeignCoursesProvider(NEXUS_PAYLOAD),
        build_unavailable=lambda: ForeignCoursesProvider(
            NEXUS_PAYLOAD, failure="unavailable"
        ),
        build_timeout=lambda: ForeignCoursesProvider(NEXUS_PAYLOAD, failure="timeout"),
        build_invalid=lambda: ForeignCoursesProvider(
            {
                **NEXUS_PAYLOAD,
                "curriculum": {
                    "completeness": "FULL",
                    "programmes": [{"label": "no programme ref"}],
                },
            }
        ),
        build_empty=lambda: ForeignCoursesProvider(
            {**NEXUS_PAYLOAD, "curriculum": {"completeness": "NONE", "programmes": []}}
        ),
        extras={"topic_tags": ("contract_formation", "negligence")},
    ),
)
