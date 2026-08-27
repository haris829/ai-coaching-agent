# UC-03 — Legal Concept Q&A

Standalone backend implementation of UC-03. Backend only: no frontend, no
production database. UC-01 and UC-02 are consumed through an interface and
mocked here; nothing in this repository imports or assumes them.

## Architecture

```
HTTP API  (uc03/api.py)
    |
UC-03 Q&A Service  (uc03/service.py)   <- depends ONLY on the contracts below
    |
Internal Contracts  (uc03/contracts.py)
    |-- ContextProvider        -> mock now / company NARIC + Legal Footprints later
    |-- LegalAuthorityProvider -> mock now / approved legal source later
    |-- QuestionClassifier     -> rule based now / LLM or company classifier later
    |-- AnswerGenerator        -> template now / Claude or company generator later
    |-- TopicTagger            -> rule based now
    |-- QuestionLogger         -> in-memory now / company database + event log later
    |-- InteractionReader      -> in-memory now / read path over the same store
    |-- FramingRegistry        -> in-memory now / company framing storage later
    |-- SessionAuthorizer      -> static now / company auth + session system later
    |-- Clock
```

`uc03/service.py` imports no concrete adapter — enforced by a test. Swapping a
mock for a company adapter is a change in `uc03/factory.py` only.

## Layout

| Path | Purpose |
|---|---|
| `uc03/contracts.py` | The whole integration surface — ten Protocols |
| `uc03/service.py` | UC-03 business logic (orchestration, deadlines, logging) |
| `uc03/domain/` | Enums, response contract, controlled topic vocabulary |
| `uc03/explanation.py` | NARIC level → explanation depth profile (pure function) |
| `uc03/citation_guard.py` | Redacts unverified citation-shaped prose |
| `uc03/distinctness.py` | Paraphrase detection for the never-repeat-a-framing rule |
| `uc03/conformance/` | Reusable per-port conformance suites for integrators |
| `uc03/adapters/rule_based.py` | Deterministic classifier / tagger / generator |
| `uc03/adapters/mocks.py` | Mock integrations with every failure mode |
| `uc03/adapters/llm.py` | Optional Claude-backed classifier + generator |
| `uc03/api.py` | FastAPI surface |
| `uc03/factory.py` | Composition root — the only place adapters are chosen |
| `bench/p95.py` | Real-wall-clock P95 latency benchmark |

## Running

```bash
pip install -e ".[test]"
python -m pytest                 # full suite
python -m pytest -m "not slow"   # skip the real 1.5s / 10s / P95 timing tests
python -m bench.p95              # latency benchmark
uvicorn uc03.api:app --reload    # serve
```

## API

### `POST /uc03/questions`

Request — these two fields and nothing else. Unknown fields are **rejected**
(422), so a client cannot supply NARIC level, practice area, identity, prompts
or authority data.

```json
{ "question": "What is negligence in tort law?", "session_id": "session-alice-1" }
```

Header: `Authorization: Bearer <token>`

Response:

```json
{
  "question_id": "…",
  "session_id": "session-alice-1",
  "classification": "legal_concept",
  "status": "answered",
  "parts": {
    "plain_english": "…",
    "formal_definition": "…",
    "practice_example": "…",
    "authority": {
      "status": "verified",
      "authority": {
        "citation": "Donoghue v Stevenson [1932] UKHL 100",
        "title": "…", "source": "BAILII", "url": "…",
        "verified_by": "…", "verification_id": "…", "retrieved_at": "…"
      },
      "message": null,
      "verification_routes": ["Westlaw", "BAILII"]
    }
  },
  "clarification_question": null,
  "message": null,
  "follow_up_actions": ["explain_differently", "another_example", "go_deeper"],
  "rating_state": "pending",
  "retry_available": false,
  "meta": {
    "elapsed_ms": 812, "thinking_after_ms": 1500, "timeout_ms": 10000,
    "thinking_state_emitted": false, "explanation_depth": "advanced",
    "naric_level": "LEVEL_7", "naric_level_source": "retrieved",
    "practice_area_availability": "provided",
    "personalisation_applied": true, "topic_tag": "negligence",
    "topic_tag_accepted": true, "framing": "analogy", "framings_remaining": 5,
    "citation_guard_violations": 0, "log_status": "recorded", "degraded": []
  }
}
```

`status` is one of `ANSWERED`, `CLARIFICATION_NEEDED`, `OUT_OF_SCOPE`,
`TIMEOUT`, `ERROR`, `FRAMINGS_EXHAUSTED`. `parts` is present only for
`ANSWERED`; `follow_up_actions`
is non-empty only for `ANSWERED`. `retry_available` is true for `TIMEOUT` and
`ERROR`.

Status codes: `401` bad/missing credential, `403` session not owned by the
caller, `422` malformed body or unknown field. Timeouts and internal failures
return `200` with a `TIMEOUT` / `ERROR` body so the frontend can offer retry.

### `POST /uc03/questions/{question_id}/follow-up`

Body: `{"action": "explain_differently" | "another_example" | "go_deeper",
"session_id": "..."}`. Unknown fields rejected — the client cannot choose the
framing.

A real operation, not a label. Re-explains the same concept using an
explanation framing not yet used for it in this session; `GO_DEEPER` also moves
one step up the depth ladder. The response carries `follow_up_of` (the original
`question_id`) and `meta.framing`. When every framing has been used, the status
is `FRAMINGS_EXHAUSTED` and the message says so rather than cycling back to the
first framing. Returns `404` for an unknown interaction or one belonging to
another caller — deliberately indistinguishable.

### `POST /uc03/questions/stream`

Same request, delivered as SSE. Emits a `thinking` event if the answer passes
the 1.5s mark, then a `result` event with the response above. Exists so the
frontend can drive its thinking state from a real server signal.

### `GET /health`

## Behaviour notes

**Authority integrity.** Three independent defences: the generator contract has
no authority field, so a model *cannot* supply one; only a
`LegalAuthorityProvider` can mint a `VERIFIED` result (and must record
`verified_by` + `verification_id`); and the citation guard redacts any
citation-shaped prose that isn't one of the verified citations. With no verified
authority, the response carries the configured no-authority message and directs
the learner to Westlaw or BAILII.

**Degradation.** Dependency failures degrade rather than fail: the response is
still produced, `meta.degraded` names what was lost, and the log records it.
A logging failure never fails a request — the record falls back to stderr.

**Context.** `naric_level` is a closed enum (`LEVEL_3`..`LEVEL_7_PLUS`) and
always carries a real level; `meta.naric_level_source` (`retrieved` | `default`)
is what says whether it was retrieved or defaulted, so a stored level is never
ambiguous. Practice area uses `meta.practice_area_availability`. A speciality is
never invented.

## Not in scope

Frontend rendering, the production database, UC-01/UC-02 themselves, and the
rating workflow (UC-10 — this service only emits `rating_state: pending`).

See [INTEGRATION.md](INTEGRATION.md) for the handoff,
[docs/SHARED_CONTRACT.md](docs/SHARED_CONTRACT.md) for the field-by-field
contract, and [docs/assumptions.md](docs/assumptions.md) for everything UC-03
invented rather than was told.
