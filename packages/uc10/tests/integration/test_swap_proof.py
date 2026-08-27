"""INTEGRATION SWAP PROOF.

The unmodified service is run against a deliberately foreign adapter family: a fictional
upstream with different field names, deeper nesting, epoch-millisecond timestamps, its own
attainment scheme ("EQF band 7+"), percentages as strings and uppercase category codes.

Nothing in the domain, application, API, persistence or test layers is adapted for it.
The only difference between the two runs is the value of INTERACTION_PROVIDER.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import ADMIN_HEADERS
from uc10.adapters.foreign.interaction_provider import ForeignInteractionProvider
from uc10.adapters.memory.support import StaticThresholdConfigProvider
from uc10.adapters.mock.identity import ConfiguredAdminIdentityProvider
from uc10.api.app import create_app
from uc10.api.deps import build_container
from uc10.domain.enums import ExplanationProfile, NaricLevel, ResponseCategory, SourceStatus

# The same scenario in two unrelated upstream vocabularies.
SCENARIOS = {
    "answer": ("int_answer", "TXN-ANSWER", ResponseCategory.ANSWER),
    "redirect": ("int_redirect", "TXN-SIGNPOST", ResponseCategory.REDIRECT),
    "refusal": ("int_refusal", "TXN-DECLINED", ResponseCategory.REFUSAL),
    "clarifying": ("int_clarifying", "TXN-QUERYBACK", ResponseCategory.CLARIFYING_QUESTION),
    "degraded": ("int_degraded", "TXN-FALLBACK", ResponseCategory.DEGRADED_FALLBACK),
    "unknown_category": ("int_unknown_category", "TXN-NEWKIND", ResponseCategory.UNKNOWN),
}

FAMILIES = {
    "mock": {"provider": "mock", "owner": "user_alice", "index": 0},
    "foreign": {"provider": "foreign_demo", "owner": "LRN-ALICE", "index": 1},
}


@pytest.fixture
def make_family_client(settings, clock):
    """Build the service on a provider chosen by configuration alone."""

    def _make(family: str, *, provider=None) -> TestClient:
        container = build_container(
            settings=settings.model_copy(
                update={"interaction_provider": FAMILIES[family]["provider"]}
            ),
            clock=clock,
            interactions=provider,
            policy_config=StaticThresholdConfigProvider(
                down_rate_threshold=0.30, minimum_sample_size=10
            ),
            admin_identity=ConfiguredAdminIdentityProvider(lambda: settings),
        )
        return TestClient(create_app(container=container))

    return _make


def _id(family: str, scenario: str) -> str:
    return SCENARIOS[scenario][FAMILIES[family]["index"]]


# --------------------------------------------------- identical platform behaviour


@pytest.mark.parametrize("family", list(FAMILIES))
@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_every_response_category_is_rateable_on_both_adapter_families(
    make_family_client, family, scenario
):
    client = make_family_client(family)
    response = client.post(
        f"/api/v1/interactions/{_id(family, scenario)}/rating",
        json={"rating": "down"},
        headers={"X-User-Id": FAMILIES[family]["owner"]},
    )
    assert response.status_code == 201, response.text
    assert response.json()["rating"]["rating"] == "down"


@pytest.mark.parametrize("family", list(FAMILIES))
def test_the_24_hour_window_behaves_identically_on_both_families(make_family_client, family):
    client = make_family_client(family)
    headers = {"X-User-Id": FAMILIES[family]["owner"]}
    recent = "int_delivered_23h" if family == "mock" else "TXN-23H"
    stale = "int_delivered_25h" if family == "mock" else "TXN-25H"

    assert client.post(
        f"/api/v1/interactions/{recent}/rating", json={"rating": "up"}, headers=headers
    ).status_code == 201
    refused = client.post(
        f"/api/v1/interactions/{stale}/rating", json={"rating": "up"}, headers=headers
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "rejected_window_expired"


@pytest.mark.parametrize("family", list(FAMILIES))
def test_cross_user_rating_is_refused_identically_on_both_families(make_family_client, family):
    client = make_family_client(family)
    other = "int_other_learner" if family == "mock" else "TXN-OTHER"
    response = client.post(
        f"/api/v1/interactions/{other}/rating",
        json={"rating": "down"},
        headers={"X-User-Id": FAMILIES[family]["owner"]},
    )
    assert response.status_code == 404


@pytest.mark.parametrize("family", list(FAMILIES))
def test_upstream_failures_map_to_the_same_contract_behaviour_on_both_families(
    make_family_client, family
):
    client = make_family_client(family)
    headers = {"X-User-Id": FAMILIES[family]["owner"]}
    cases = (
        [("int_unavailable", 503), ("int_timeout", 503), ("int_invalid", 502)]
        if family == "mock"
        else [("TXN-DOWN", 503), ("TXN-SLOW", 503), ("TXN-GARBLED", 502)]
    )
    for interaction_id, expected in cases:
        response = client.post(
            f"/api/v1/interactions/{interaction_id}/rating", json={"rating": "up"}, headers=headers
        )
        assert response.status_code == expected, (interaction_id, response.text)
        assert "TXN" not in response.text, "no upstream identifier vocabulary leaks into an error"


def test_the_foreign_upstreams_own_value_representations_are_normalised_to_the_contract(clock):
    """'EQF band 7+' becomes the platform enum; '40%' becomes an integer; epoch millis
    become a UTC datetime -- all inside the adapter."""
    provider = ForeignInteractionProvider(clock)

    answer = provider.get("TXN-ANSWER")
    assert answer.naric_level is NaricLevel.LEVEL_7_PLUS
    assert answer.explanation_profile is ExplanationProfile.ADVANCED
    assert answer.naric_source_status is SourceStatus.AVAILABLE
    assert answer.course_completion_percent == 40
    assert answer.topic_tag == "contract_formation"  # from "Contract Formation"
    assert answer.session_mode == "coaching_mode"
    assert answer.delivered_at.tzinfo is not None

    unmappable = provider.get("TXN-BADLEVEL")
    assert unmappable.naric_level is NaricLevel.LEVEL_5
    assert unmappable.naric_source_status is SourceStatus.INVALID


def test_a_rating_taken_through_the_foreign_adapter_carries_platform_vocabulary(
    make_family_client, settings, clock
):
    client = make_family_client("foreign")
    container = client.app.state.container
    client.post(
        "/api/v1/interactions/TXN-ANSWER/rating",
        json={"rating": "down"},
        headers={"X-User-Id": "LRN-ALICE"},
    )
    record = container.ratings_repository.all_records()[0]
    assert record.naric_level is NaricLevel.LEVEL_7_PLUS
    assert record.topic_tag == "contract_formation"
    assert record.session_id == "THR-1001"  # opaque, passed through, never minted
    assert record.rating.value == "down"


def test_flagging_works_unchanged_against_the_foreign_adapter(make_family_client, clock):
    """Ten foreign interactions, all rated down: the same rule raises the same flag."""
    payloads = {
        f"TXN-{index:03d}": {
            "envelope": {
                "txnRef": f"TXN-{index:03d}",
                "learnerRef": f"LRN-{index:03d}",
                "threadRef": "THR-2002",
            },
            "content": {
                "prompt": {"body": f"FOREIGN_QUESTION_TEXT_DO_NOT_LOG::{index}"},
                "reply": {"body": f"FOREIGN_RESPONSE_TEXT_DO_NOT_LOG::{index}", "kind": "RESPONSE"},
            },
            "classification": {"subject": "Money Laundering Regs", "mode": "Coaching Mode"},
            "attainment": {"scheme": "EQF", "band": "7", "provenance": "LOOKUP"},
            "progress": {"completionPct": "55%"},
            "integrity": "OK",
            "_offset_seconds": 600,
        }
        for index in range(10)
    }
    client = make_family_client("foreign", provider=ForeignInteractionProvider(clock, payloads))

    for index in range(10):
        response = client.post(
            f"/api/v1/interactions/TXN-{index:03d}/rating",
            json={"rating": "down"},
            headers={"X-User-Id": f"LRN-{index:03d}"},
        )
        assert response.status_code == 201, response.text

    flags = client.get("/api/v1/admin/flags", headers=ADMIN_HEADERS).json()
    assert flags["count"] == 1
    flag = flags["flags"][0]
    assert flag["topic_tag"] == "money_laundering_regs"
    assert (flag["total_ratings"], flag["down_ratings"]) == (10, 10)
    assert flag["threshold_applied"] == 0.30
    assert len(flag["flagging_interaction_ids"]) == 10
    assert "FOREIGN_QUESTION_TEXT_DO_NOT_LOG" not in str(flags)


def test_no_upstream_field_name_appears_outside_its_adapter():
    """The foreign upstream's vocabulary is confined to one file."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "uc10"
    adapter = root / "adapters" / "foreign" / "interaction_provider.py"
    offenders = []
    for path in root.rglob("*.py"):
        if path == adapter:
            continue
        text = path.read_text(encoding="utf-8")
        for token in ("txnRef", "learnerRef", "threadRef", "servedAtEpochMillis", "completionPct"):
            if token in text:
                offenders.append((path.name, token))
    assert offenders == []
