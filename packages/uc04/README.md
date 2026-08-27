# UC-04 — Course Content Coaching

Lesson-grounded coaching for a learner working through a specific course lesson. Answers their
questions from that lesson's content, calibrates to their qualification level, varies its
approach when asked to explain differently, and never hands over a quiz answer.

## Run it

```bash
pip install -e ".[dev]"
pytest                                    # 381 tests, no network, no API key, no cost
python -m uvicorn uc04.main:app --port 8000
```

```bash
curl -X POST http://localhost:8000/api/v1/lesson-coaching/questions \
  -H 'content-type: application/json' -H 'x-user-id: user_solicitor_1' \
  -d '{"session_id":"sess_main_1","course_id":"course_evi_201",
       "lesson_id":"lesson_evi_01","question":"What does hearsay actually mean?"}'
```

## Layout

| Path | Responsibility |
| --- | --- |
| `src/uc04/domain/` | Enums, models, closed vocabularies, error taxonomy |
| `src/uc04/ports/` | The interfaces every external dependency arrives through |
| `src/uc04/core/` | Business logic. Imports ports and domain only - never an adapter |
| `src/uc04/adapters/` | Mocks, in-memory persistence, the fake generator, real adapters |
| `src/uc04/adapters/registry.py` | Provider selection. One line per adapter |
| `src/uc04/adapters/real/_template.py` | Copy this to start a real adapter |
| `src/uc04/conformance/` | The reusable conformance kit, shipped for integrators |
| `src/uc04/api/` | FastAPI surface. Thin: authenticate, validate, delegate |
| `src/uc04/composition.py` | Composition root |

## Documentation

- `../docs/SHARED_CONTRACT.md` — what this component emits and expects, field by field
- `../docs/assumptions.md` — every invented field, value and threshold, with its risk
- `../docs/INTEGRATION.md` — how to replace a mock with a real service
