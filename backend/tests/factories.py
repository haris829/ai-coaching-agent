"""Valid API payloads for each of the five question types.

Each builder returns a *minimally valid* payload that the authoritative validator accepts, so a
test can express "valid except for X" by overriding just X.
"""

from __future__ import annotations

from typing import Any

API = "/api/question-bank"


def single_choice(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "SINGLE_CHOICE",
        "questionText": "Which OSI layer routes packets between networks?",
        "explanation": "Layer 3, the Network layer, performs logical addressing and routing.",
        "topics": ["Networking"],
        "difficulty": "EASY",
        "options": [
            {"label": "A", "text": "Layer 2 - Data Link", "isCorrect": False},
            {"label": "B", "text": "Layer 3 - Network", "isCorrect": True},
            {"label": "C", "text": "Layer 4 - Transport", "isCorrect": False},
            {"label": "D", "text": "Layer 7 - Application", "isCorrect": False},
        ],
        "scoring": {"points": 1, "scoringStrategy": "ALL_OR_NOTHING"},
    }
    payload.update(overrides)
    return payload


def true_false(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "TRUE_FALSE",
        "questionText": "TCP guarantees that data arrives in the order it was sent.",
        "explanation": "TCP sequences segments and reassembles them in order before delivery.",
        "topics": ["Transport Protocols"],
        "options": [
            {"label": "TRUE", "text": "True", "isCorrect": True},
            {"label": "FALSE", "text": "False", "isCorrect": False},
        ],
        "scoring": {"points": 1, "scoringStrategy": "ALL_OR_NOTHING"},
    }
    payload.update(overrides)
    return payload


def multi_select(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "MULTI_SELECT",
        "questionText": "Which of the following are private IPv4 address ranges?",
        "explanation": "RFC 1918 reserves 10/8, 172.16/12 and 192.168/16 for private use.",
        "topics": ["IP Addressing"],
        "options": [
            {"label": "A", "text": "10.0.0.0/8", "isCorrect": True},
            {"label": "B", "text": "172.16.0.0/12", "isCorrect": True},
            {"label": "C", "text": "192.168.0.0/16", "isCorrect": True},
            {"label": "D", "text": "8.8.8.0/24", "isCorrect": False},
            {"label": "E", "text": "203.0.113.0/24", "isCorrect": False},
        ],
        "scoring": {
            "points": 3,
            "scoringStrategy": "PARTIAL_CREDIT_WITH_PENALTY",
            "penaltyPerIncorrect": 0.5,
        },
    }
    payload.update(overrides)
    return payload


SCENARIO_VIGNETTE = (
    "A learner reports that the course portal is unreachable from the office network but loads "
    "correctly over mobile data. Other sites work normally from the office, the portal's status "
    "page reports all systems operational, and a colleague working from home can reach it."
)


def scenario(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "SCENARIO",
        "scenarioText": SCENARIO_VIGNETTE,
        "questionText": "What is the most likely cause of the outage?",
        "explanation": "The portal is reachable elsewhere, so the fault is local to the office.",
        "topics": ["Troubleshooting"],
        "options": [
            {"label": "A", "text": "The portal is down", "isCorrect": False},
            {"label": "B", "text": "An office DNS or firewall rule blocks it", "isCorrect": True, "isPrimary": True},
            {"label": "C", "text": "The learner's account is locked", "isCorrect": False},
            {"label": "D", "text": "The TLS certificate expired", "isCorrect": False},
        ],
        "scoring": {"points": 2, "scoringStrategy": "ALL_OR_NOTHING"},
    }
    payload.update(overrides)
    return payload


def drag_to_order(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "DRAG_TO_ORDER",
        "questionText": "Place the steps of the TCP handshake and data transfer in order.",
        "explanation": "SYN, then SYN-ACK, then ACK completes the handshake before data flows.",
        "topics": ["Transport Protocols"],
        "options": [
            # Presentation order (position) is deliberately NOT the correct order, to prove the
            # two concepts stay separate.
            {"label": "A", "text": "Client sends SYN", "position": 1, "correctPosition": 1},
            {"label": "B", "text": "Server replies SYN-ACK", "position": 2, "correctPosition": 2},
            {"label": "C", "text": "Client sends ACK", "position": 3, "correctPosition": 3},
            {"label": "D", "text": "Data transfer begins", "position": 4, "correctPosition": 4},
        ],
        "scoring": {"points": 4, "scoringStrategy": "PARTIAL_CREDIT"},
    }
    payload.update(overrides)
    return payload


ALL_BUILDERS = {
    "SINGLE_CHOICE": single_choice,
    "TRUE_FALSE": true_false,
    "MULTI_SELECT": multi_select,
    "SCENARIO": scenario,
    "DRAG_TO_ORDER": drag_to_order,
}


def create(client: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """POST a question and assert it was created, returning the response body."""
    response = client.post(f"{API}/questions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()
