# UC-06 — Case-Linked Legal Advice Coaching

A standalone backend service. When a learner links a coaching session to a real
case file, UC-06 explains how the relevant law applies to the specific facts of
that matter — **as education, never as legal advice.**

No frontend. No production database. No production authentication. No agent
framework, RAG, embeddings or vector store. Every external interaction goes
through a port that is defined and mocked here.

---

## Run it

```bash
python -m pip install -r requirements.txt
python -m pytest -q                      # 818 tests, no network, no API key, no cost
python -m uvicorn uc06.api.app:app --port 8000
```

```bash
curl -s -X POST http://localhost:8000/api/v1/case-coaching/questions \
  -H 'content-type: application/json' \
  -H 'x-uc06-user-id: user-alice' \
  -d '{"session_id":"sess-level-7","case_file_id":"CASE-FULL-001",
       "question":"How does the defence of duress apply to the account in this file?"}'
```

Configuration: copy `.env.example`. Every default is a mock, and
`ANSWER_GENERATOR=fake` means the service never contacts a model provider.

---

## The one thing to understand first

**The disclaimer cannot be removed.** Three independent layers enforce it, and
they fail independently:

| Layer | Where | Guarantee |
|---|---|---|
| **Type** | `uc06/domain/responses.py` | A response type has no `disclaimer` constructor parameter and no setter. Business logic cannot express omission because there is no code path for it. |
| **Serialisation boundary** | `uc06/application/boundary.py` | An independent byte-exact check on the outgoing payload. It never sees the response object, so a correct type cannot rescue a corrupt payload. |
| **Output scan** | `tests/test_output_scan.py` | 35 enumerated response paths — success, guard, degraded, every error, and the boundary-failure path itself — scanned for the exact string. |

There is **no configuration key** that could disable it. Not a flag, not an
environment variable, not a request field, not a test mode. The absence is the
guarantee, and `tests/test_config_surface.py` asserts it by scanning the whole
configuration surface both by name and by effect.

If the boundary check ever fails, the service **fails closed**: no response is
emitted, the session is halted, the admin is alerted, and a security incident is
recorded. An unlabelled case-linked answer reaching a practising lawyer is the
outcome this component exists to prevent.

> ⚠ **The scope document states the disclaimer twice with different wording.** We
> use the Overview's full three-sentence text. This is unresolved and needs the
> company's confirmation — see `docs/assumptions.md` **A-01**.

---

## Documents

| | |
|---|---|
| `docs/assumptions.md` | Every invented shape and behaviour, with the risk if it is wrong. **Read A-01 first.** |
| `docs/SHARED_CONTRACT.md` | The published contract, for an integration engineer. Every field marked **[COMPANY]** or **[ASSUMED]**. |
| `docs/INTEGRATION.md` | How to swap a mock for a real system: one file, one registry line, one config value. With a worked example. |
| `tests/conformance/README.md` | The reusable conformance kit any adapter must pass. |

---

## Layout

```
uc06/
  domain/           disclaimer, enums, models, errors, guard vocabulary, legal tests
  ports/            every external interaction, as Protocols
  application/      boundary check, emitter, coaching service, fact verification, prompts
  adapters/
    mock/           deterministic scenarios - the whole suite runs against these
    memory/         in-process persistence and sinks
    identity/       minimal replaceable identity
    real/           _template.py to copy, and the disabled configured generator
    foreign/        a deliberately foreign upstream, for the swap proof
  api/              FastAPI app, request schemas, error envelope
  composition.py    THE PROVIDER REGISTRY - the only file that knows an adapter exists
  config.py         every configuration key, in one place
tests/
  conformance/      reusable, adapter-agnostic suites, parameterised on the adapter
```

## Endpoints

```
POST /api/v1/case-coaching/questions               ask a case-linked question
GET  /api/v1/case-coaching/sessions/{id}/status    halt state, for a caller to render
GET  /api/v1/healthz
```

`user_id` is resolved server-side and never read from the request body. Request
schemas reject unknown fields outright — sending `disclaimer`, `naric_level`,
`guard_triggered` or `system_prompt` produces a visible `422`, not a silent
ignore.
