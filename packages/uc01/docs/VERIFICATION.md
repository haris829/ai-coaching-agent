# Verification record

Recorded output of `python scripts/verify_states.py`, which drives every UC-01 interface
state through the real API (in-memory store, mock adapters, no server required).

Re-run it any time — after an adapter swap especially:

```bash
python scripts/verify_states.py
```

The equivalent checks against a live server are in the README setup section; the four
required UI states are also asserted in `tests/test_api_contract.py`
(`test_ui_state_normal`, `test_ui_state_no_case_files`,
`test_ui_state_courses_unavailable`,
`test_ui_state_naric_unavailable_does_not_disable_the_session`).

---

## Recorded output

```text
UC-01 interface state verification
integrations: {'naric': 'mock', 'courses': 'mock', 'cases': 'mock', 'profile': 'mock', 'identity': 'dev', 'persistence': 'memory'}

1. Normal state (dev-alice)
---------------------------
  free-form      Available
  course-linked  Available
  case-linked    Available
  NARIC: level=8 source=naric fallback=False offer_continue=False
  greeting preview: Hi Alice Osei! Welcome back to your coaching session. Ask me anything you would like to work through. Explanations are calibrated to your NARIC Level 8.

2. No accessible case files (dev-bob)
-------------------------------------
  free-form      Available
  course-linked  Available
  case-linked    Disabled    "No accessible case files."
  NARIC: level=5 source=default fallback=True offer_continue=True
  NARIC notice: Your NARIC calibration is still being worked out. You can continue without calibration — your coaching explanations will use Level 5 by default.
  notice[warning] naric_calibration_unavailable: Your NARIC calibration is still being worked out. You can continue without calibration — your coaching explanations will use Level 5 by default.
  notice[info] cases_empty: No accessible case files.
  greeting preview: Hi Bob Ryan! Welcome back to your coaching session. Ask me anything you would like to work through. Explanations will use Level 5 by default, because calibrated NARIC data was not available.

3. Courses unavailable
----------------------
  free-form      Available
  course-linked  Disabled    "Courses are temporarily unavailable."
  case-linked    Available
  NARIC: level=8 source=naric fallback=False offer_continue=False
  notice[warning] courses_unavailable: Courses are temporarily unavailable.
  greeting preview: Hi Alice Osei! Welcome back to your coaching session. Ask me anything you would like to work through. Explanations are calibrated to your NARIC Level 8.

4. NARIC unavailable (session must NOT be disabled)
---------------------------------------------------
  free-form      Available
  course-linked  Available
  case-linked    Available
  NARIC: level=5 source=default fallback=True offer_continue=True
  NARIC notice: NARIC calibration is unavailable right now. You can continue without calibration — your coaching explanations will use Level 5 by default.
  notice[warning] naric_calibration_unavailable: NARIC calibration is unavailable right now. You can continue without calibration — your coaching explanations will use Level 5 by default.
  greeting preview: Hi Alice Osei! Welcome back to your coaching session. Ask me anything you would like to work through. Explanations will use Level 5 by default, because calibrated NARIC data was not available.

5. Profile unavailable (generic greeting)
-----------------------------------------
  free-form      Available
  course-linked  Available
  case-linked    Available
  NARIC: level=8 source=naric fallback=False offer_continue=False
  notice[warning] personalisation_unavailable: We could not load your personalised profile information right now, but you can continue your session normally.
  greeting preview: Hi! Welcome back to your coaching session. Ask me anything you would like to work through. Explanations are calibrated to your NARIC Level 8. We could not load your personalised profile information right now, but you can continue your session normally.

6. Everything down (free-form must survive)
-------------------------------------------
  free-form      Available
  course-linked  Disabled    "Courses are temporarily unavailable."
  case-linked    Disabled    "Case files are temporarily unavailable."
  NARIC: level=5 source=default fallback=True offer_continue=True
  NARIC notice: NARIC calibration is unavailable right now. You can continue without calibration — your coaching explanations will use Level 5 by default.
  notice[warning] naric_calibration_unavailable: NARIC calibration is unavailable right now. You can continue without calibration — your coaching explanations will use Level 5 by default.
  notice[warning] personalisation_unavailable: We could not load your personalised profile information right now, but you can continue your session normally.
  notice[warning] courses_unavailable: Courses are temporarily unavailable.
  notice[warning] cases_unavailable: Case files are temporarily unavailable.
  greeting preview: Hi! Welcome back to your coaching session. Ask me anything you would like to work through. Explanations will use Level 5 by default, because calibrated NARIC data was not available. We could not load your personalised profile information right now, but you can continue your session normally.

7. Continue without calibration opens the session
-------------------------------------------------
  status=degraded level=5 source=default_user_acknowledged
  greeting: Hi Alice Osei! Welcome back to your coaching session. Ask me anything you would like to work through. Explanations will use Level 5 by default, because calibrated NARIC data was not available.

8. Partial initialisation: rejected open is still recorded
----------------------------------------------------------
  HTTP 409 {'code': 'session_mode_unavailable', 'message': 'Courses are temporarily unavailable.'}
  recovery: {'session_id': 'sess_ddad15d0fc5c4b2b97c461afbf242327', 'available_modes': ['free-form', 'case-linked'], 'suggested_mode': 'free-form'}
  stored record: status=failed failure_code=session_mode_unavailable naric_level=8 degraded=['courses']
  events: session.initializing, session.dependency_degraded, session.failed

9. Downgrade to free-form on dependency failure
-----------------------------------------------
  requested=course-linked effective=free-form status=degraded

10. Invalid authorization attempts
----------------------------------
  no credentials                 HTTP 401  authentication_required
  unknown token                  HTTP 401  authentication_required
  another user's session         HTTP 404  session_not_found
  another user's course          HTTP 403  selection_not_accessible
  another user's case            HTTP 403  selection_not_accessible
  client-supplied NARIC level    HTTP 422  invalid_request
  client-supplied system prompt  HTTP 422  invalid_request
  disabled mode bypass           HTTP 409  session_mode_unavailable

All states rendered from API responses. No real integration was contacted.
```

---

## What this demonstrates

| Requirement | Evidence above |
| --- | --- |
| Three modes, availability driven by the server | Sections 1–6 |
| Free-form always available | Section 6 — every dependency down, free-form still Available |
| Course-linked disables gracefully | Section 3 — "Courses are temporarily unavailable." |
| Case-linked disables gracefully with no accessible cases | Section 2 — "No accessible case files." |
| NARIC failure never blocks the session | Section 4 — all three modes Available, level 5 / `default`, offer present |
| Level 5 fallback labelled as a fallback | Sections 4, 6, 7 — `source=default`, `fallback=True`, greeting says "by default" |
| Continue without calibration opens the session | Section 7 — `status=degraded`, `source=default_user_acknowledged` |
| Generic greeting on profile failure | Section 5 — no name used, non-technical notice |
| Session record for a rejected attempt | Section 8 — `status=failed`, `failure_code`, `naric_level`, degraded list, three events |
| Partial initialisation is diagnosable | Section 8 — `session.initializing`, `session.dependency_degraded`, `session.failed` |
| Mode downgrade path | Section 9 — requested `course-linked`, effective `free-form`, `degraded` |
| Server-side authorization | Section 10 — 401/404/403/422/409 for the eight attempts |
| Client cannot set the NARIC level or a system prompt | Section 10 — both 422 `invalid_request` |
| Client cannot bypass a disabled mode | Section 10 — 409 `session_mode_unavailable` |
