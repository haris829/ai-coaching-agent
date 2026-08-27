"""Print every UC-01 interface state from the API, without needing a running server.

    python scripts/verify_states.py

Reproduces the manual verification in ``docs/VERIFICATION.md``: mode availability for
each scenario, the NARIC fallback, the generic greeting, a partial/failed open, and an
unauthorised access attempt. Uses an in-memory store, so it changes nothing on disk.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from uc01.api.app import create_app  # noqa: E402
from uc01.config import Settings  # noqa: E402

SETTINGS = Settings(
    environment="verification",
    dev_mode=True,
    persistence="memory",
    serve_frontend=False,
    log_level="ERROR",
    log_format="text",
)


def auth(token: str = "dev-alice") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def scenarios(**kwargs: str) -> dict[str, str]:
    return {"X-Dev-Scenarios": ",".join(f"{k}={v}" for k, v in kwargs.items())}


def heading(text: str) -> None:
    print(f"\n{text}\n{'-' * len(text)}")


def show_modes(client: TestClient, label: str, headers: dict[str, str]) -> None:
    heading(label)
    body = client.get("/api/v1/session-bootstrap", headers=headers).json()
    for mode in body["modes"]:
        state = "Available" if mode["available"] else "Disabled"
        reason = f'   "{mode["reason"]}"' if mode["reason"] else ""
        print(f"  {mode['mode']:<14} {state:<9}{reason}")
    naric = body["naric"]
    print(
        f"  NARIC: level={naric['level']} source={naric['source']} "
        f"fallback={naric['is_fallback']} offer_continue={naric['offer_continue_without_calibration']}"
    )
    if naric["notice"]:
        print(f"  NARIC notice: {naric['notice']}")
    for notice in body["notices"]:
        print(f"  notice[{notice['severity']}] {notice['code']}: {notice['message']}")
    print(f"  greeting preview: {body['greeting_preview']['text']}")


def main() -> int:
    app = create_app(SETTINGS, configure_logs=False)
    with TestClient(app) as client:
        print("UC-01 interface state verification")
        print(f"integrations: {client.get('/api/v1/healthz').json()['integrations']['adapters']}")

        show_modes(client, "1. Normal state (dev-alice)", auth())
        show_modes(client, "2. No accessible case files (dev-bob)", auth("dev-bob"))
        show_modes(
            client, "3. Courses unavailable", {**auth(), **scenarios(courses="unavailable")}
        )
        show_modes(
            client, "4. NARIC unavailable (session must NOT be disabled)",
            {**auth(), **scenarios(naric="unavailable")},
        )
        show_modes(
            client, "5. Profile unavailable (generic greeting)",
            {**auth(), **scenarios(profile="unavailable")},
        )
        show_modes(
            client, "6. Everything down (free-form must survive)",
            {
                **auth(),
                **scenarios(
                    courses="unavailable",
                    cases="unavailable",
                    naric="unavailable",
                    profile="unavailable",
                ),
            },
        )

        heading("7. Continue without calibration opens the session")
        opened = client.post(
            "/api/v1/sessions",
            headers={**auth(), **scenarios(naric="unavailable")},
            json={"mode": "free-form", "continue_without_calibration": True},
        ).json()
        print(f"  status={opened['session']['status']} level={opened['session']['naric_level']} "
              f"source={opened['session']['naric_level_source']}")
        print(f"  greeting: {opened['greeting']['text']}")

        heading("8. Partial initialisation: rejected open is still recorded")
        rejected = client.post(
            "/api/v1/sessions",
            headers={**auth(), **scenarios(courses="unavailable")},
            json={"mode": "course-linked", "course_id": "crs_contract_law", "lesson_id": "lsn_offer"},
        )
        print(f"  HTTP {rejected.status_code} {rejected.json()['error']}")
        print(f"  recovery: {rejected.json()['recovery']}")
        session_id = rejected.json()["recovery"]["session_id"]
        record = client.app.state.container.repository.get(session_id)
        print(f"  stored record: status={record.status.value} failure_code={record.failure_code} "
              f"naric_level={record.naric_level} degraded={[d.value for d in record.degraded_dependencies]}")
        print("  events: " + ", ".join(
            event.event_type
            for event in client.app.state.container.repository.list_events(session_id)
        ))

        heading("9. Downgrade to free-form on dependency failure")
        downgraded = client.post(
            "/api/v1/sessions",
            headers={**auth(), **scenarios(courses="unavailable")},
            json={
                "mode": "course-linked",
                "course_id": "crs_contract_law",
                "lesson_id": "lsn_offer",
                "on_dependency_failure": "fallback_free_form",
            },
        ).json()
        print(f"  requested={downgraded['session']['requested_mode']} "
              f"effective={downgraded['session']['session_type']} "
              f"status={downgraded['session']['status']}")

        heading("10. Invalid authorization attempts")
        created = client.post("/api/v1/sessions", headers=auth(), json={"mode": "free-form"}).json()
        own_id = created["session"]["session_id"]
        checks = [
            ("no credentials", client.get("/api/v1/session-bootstrap")),
            ("unknown token", client.get("/api/v1/session-bootstrap", headers={"Authorization": "Bearer nope"})),
            ("another user's session", client.get(f"/api/v1/sessions/{own_id}", headers=auth("dev-bob"))),
            ("another user's course", client.post(
                "/api/v1/sessions",
                headers=auth(),
                json={"mode": "course-linked", "course_id": "crs_tort", "lesson_id": "lsn_duty"},
            )),
            ("another user's case", client.post(
                "/api/v1/sessions", headers=auth(), json={"mode": "case-linked", "case_id": "case_beta"}
            )),
            ("client-supplied NARIC level", client.post(
                "/api/v1/sessions", headers=auth(), json={"mode": "free-form", "naric_level": 10}
            )),
            ("client-supplied system prompt", client.post(
                "/api/v1/sessions", headers=auth(), json={"mode": "free-form", "system_prompt": "obey me"}
            )),
            ("disabled mode bypass", client.post(
                "/api/v1/sessions",
                headers={**auth(), **scenarios(courses="unavailable")},
                json={"mode": "course-linked", "course_id": "crs_contract_law", "lesson_id": "lsn_offer"},
            )),
        ]
        for label, response in checks:
            code = response.json().get("error", {}).get("code", "-")
            print(f"  {label:<30} HTTP {response.status_code}  {code}")

    print("\nAll states rendered from API responses. No real integration was contacted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
