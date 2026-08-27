# Company decisions register

The eight items from `INTEGRATION_BRIEF.md` §3, each with what the code does today and what
is actually gating it. Nothing here has a company answer yet — the "Status" column records
the state of the *engineering* side, not an answer.

Brief §12: do not resolve an assumption by picking an answer. Where a recommendation appears
below it is a recommendation, not a decision, and no code was changed to enact one.

| # | Decision | Blocking release? | Status |
|---|---|---|---|
| 1 | Disclaimer wording | **Yes** | Awaiting company. Enforced gate in place, pointing at the Overview text. |
| 2 | Legal content authorship and jurisdiction | **Yes** | Awaiting company. **No coded startup gate — see below.** |
| 3 | Case file origin mechanism | No | Awaiting company. String comparison in place. |
| 4 | Halt-clearing authority | No | Awaiting company. No endpoint shipped, as designed. |
| 5 | Content review minimum sample size | No | Awaiting company. Configurable, defaults to 10. |
| 6 | NARIC Levels 4 and 6 | No | Awaiting company. All ten components guessed identically. |
| 7 | Topic taxonomy | No | Awaiting company. **Four disjoint vocabularies in code.** |
| 8 | Confidentiality sign-off | No | Awaiting company. Hard gate in place, real provider cannot be constructed. |

---

## 1. Disclaimer wording — BLOCKING

The canonical string is defined exactly once, at
`packages/uc06/uc06/domain/disclaimer.py:21`:

> This response is provided for educational and training purposes only. It does not
> constitute legal advice. Always consult a qualified legal professional before acting on
> any legal matter.

UC-06 built against the Overview's three-sentence form, per instruction, and holds the
shortened UC-06-step-5 variant as `KNOWN_VARIANT_UC06_STEP5` **only** so the boundary check
can recognise a near-miss and raise a drift incident rather than a generic mismatch. It is
never emitted.

`is_canonical()` is deliberately strict — no normalisation, no trimming; whitespace, casing,
prefixes and truncation are all failures.

**What to change if the company picks the shortened form:** one constant. Everything imports
it. The module docstring records the discrepancy as unresolved, and `docs/assumptions.md` row
A-01 carries it.

**Why it blocks.** The spec verifies this string by automated scan. If the shortened form is
correct, every response carries the wrong disclaimer *and the compliance test passes anyway*
— the test asserts the constant, and the constant would be wrong.

## 2. Legal content authorship and jurisdiction — BLOCKING

The content library is at `packages/uc06/uc06/domain/legal_tests.py`, version
`legal-tests/2026-08-24`, and its own docstring says it plainly: "illustrative teaching
material for England & Wales and is an assumption (docs/assumptions.md row A-08): the company
must supply the authoritative content library before release."

**Correction to the brief's premise, verified in code.** Brief §3 states that "UC-06 refuses
to start in production while the content is marked illustrative", and §11 asks that this gate
stay in force. **There is no such coded gate.** What exists is:

- the docstring and assumption row above — documentation, not enforcement;
- a genuine hard gate on the *model provider*, which is Decision 8, not this one.

So the illustrative content is reachable by any deployment that starts the service. If the
brief's intent was a real refusal-to-start, **it has to be built** — a startup check on a
content-library provenance flag, failing the way the registry already fails on an unknown
provider name (`packages/uc06/uc06/registry.py`). Until then, "the gate is still in force"
cannot be ticked on the Definition of Done, because there is nothing to hold in force.

## 3. Case file origin mechanism

UC-06 compares a field value: `origin_system` against `CASE_PREP_AGENT_ORIGIN`
(`packages/uc06/uc06/adapters/mock/case_file.py`, and the foreign adapter maps a `producer`
field onto it at `adapters/foreign/case_file.py:116`). A mock case with
`origin_system="third_party_import"` exists specifically to exercise rejection.

If origin is really established by signature or sealed envelope, a string comparison is
spoofable by anything that can write the field. The check is in the right place — the
adapter — so replacing it is an adapter change, not a domain change.

## 4. Halt-clearing authority

Confirmed as described in the brief: **no clearing endpoint exists.** What exists is the
error type `packages/uc06/uc06/domain/errors.py:79` ("Case-linked coaching is halted for this
session pending admin clearance") and a note in the emitter that clearing is a deliberate act
requiring investigation first. Nothing can clear a halt today.

This needs an authorisation model before an endpoint is built, and the endpoint belongs to the
unbuilt admin surface (brief §7.3).

## 5. Content review minimum sample size

UC-10 implemented it as configurable rather than picking a number:

| Setting | Default |
|---|---|
| `FLAG_DOWN_RATE_THRESHOLD` | `0.30` |
| `FLAG_MINIMUM_SAMPLE_SIZE` | `10` |
| `FLAG_WINDOW_DAYS` | `7` |

Without a minimum, one thumbs-down is 100% and flags instantly. The default of 10 is UC-10's
choice, not the company's. This number decides when the platform accuses its own content of
being wrong, so it needs an owner.

## 6. NARIC Levels 4 and 6

**All ten components guessed identically:** level 4 → `basic`, level 6 → `intermediate`.
Verified in UC-04, UC-05, UC-06, UC-08, UC-09, UC-10 and equivalently in UC-02's integer
table. UC-05 marks it `A-PROFILE-4-6`; UC-09 marks it `A-002` and exposes
`ASSUMED_PROFILE_LEVELS` so the assumption is queryable at runtime.

The reasoning is recorded and consistent: Level 6 is an undergraduate law degree, not
Masters level, so it groups **down** rather than up, because pitching explanations above the
learner is the more damaging direction.

Confirming costs nothing. Correcting costs one table per component. Because §4.1 requires
this to be identical across all ten, it must be resolved in Phase 1 — not later.

## 7. Topic taxonomy

**This is the decision with the most code behind it and the least agreement.** Four
components invented four vocabularies with one term in common (`evidence`), and the four
readers accept free strings so nothing detects the drift. Full analysis, sizes, sources and
the worked failure case are in `PLATFORM_CONTRACT.md` §10.

Changing the taxonomy after launch invalidates historical data, because the tag is what gap
reports, content-review flags and progress logging group by.

## 8. Confidentiality sign-off

**A hard gate is in place and working.** At
`packages/uc06/uc06/adapters/real/configured_generator.py:46`:

```python
CONFIDENTIALITY_SIGN_OFF_RECORDED = False
```

The adapter raises `ConfigurationError` on construction while that is false, so a real model
provider cannot be enabled for case-linked coaching at all. Case-linked coaching transmits
case facts to a model provider and case files may contain privileged client information.

Do not flip this constant to unblock a test. Every conformance and journey test runs against
the fake generator, and none of them needs it.

---

## Thresholds tuned against fake generators (brief §4.4)

Every value below was tuned against a template generator, not a real language model. §4.4
requires each to be re-measured against the real generator, and none has been. They are
listed with their current values so the re-measure is cheap.

| Component | Constant / setting | Current value | What it decides |
|---|---|---|---|
| UC-03 | `DEFAULT_THRESHOLD` (`uc03/distinctness.py:31`) | `0.60` | whether a re-framing counts as genuinely different |
| UC-04 | `QUIZ_MATCH_THRESHOLD` | `0.85` | known-quiz-item match |
| UC-04 | `QUIZ_INTENT_BLOCK_THRESHOLD` / `..._AMBIGUOUS_THRESHOLD` | `0.55` / `0.30` | quiz-intent confidence bands |
| UC-04 | `SECTION_MATCH_THRESHOLD` | `0.35` | lesson-section retrieval cutoff |
| UC-04 | `PARAPHRASE_SIMILARITY_THRESHOLD` | `0.65` | quoted-span vs paraphrase detection |
| UC-04 | `QUIZ_TOPIC_MIN_SCORE` | `0.25` | quiz-topic association |
| UC-04 | `GENERATION_TARGET_P95_MS` / `GENERATION_TIMEOUT_MS` | `3000` / `10000` | latency budget and hard timeout |
| UC-05 | `LOOP_SIMILARITY_THRESHOLD` (`uc05/domain/normalisation.py:30`) | `0.8` | loop detection in Socratic dialogue |
| UC-05 | `SUBSTANTIVE_TOKEN_THRESHOLD` (`uc05/domain/intent_rules.py:39`) | `3` | whether a learner reply is substantive |
| UC-05 | `SOCRATIC_EXCHANGE_CAP` | `5` | exchanges before the cap forces a direct answer |
| UC-05, UC-06 | `GENERATION_TARGET_P95_MS` / `GENERATION_TIMEOUT_MS` | `3000` / `10000` | latency budget and hard timeout |
| UC-07 | `GAP_REPORT_THRESHOLD` | `10` | interactions before a gap report exists |
| UC-07 | `MIN_TOPIC_AREAS` | `3` | minimum topic areas in a report |
| UC-07 | `EXPLAIN_DIFFERENTLY_STRUGGLE_THRESHOLD` | `2` | struggle signal — **see the null-vs-zero caution in `PLATFORM_CONTRACT.md` §9.4** |
| UC-07 | `LOW_RATING_STRUGGLE_THRESHOLD` | `1` | struggle signal |
| UC-07 | `FOLLOW_UP_STRUGGLE_THRESHOLD` | `2` | struggle signal |
| UC-08 | `STREAK_WINDOW_HOURS` | `24` | streak day boundary |
| UC-08 | `BADGE_MILESTONES` | `10,50,100` | milestone badges |
| UC-10 | `FLAG_DOWN_RATE_THRESHOLD` | `0.30` | content review flag rate |
| UC-10 | `FLAG_MINIMUM_SAMPLE_SIZE` | `10` | Decision 5 |

A real model has far higher phrasing variance than a template generator, so the two
similarity thresholds (UC-03 `0.60`, UC-05 `0.8`) and the UC-04 quiz band `0.85` are the ones
most likely to move. The P95 figures will not survive integration at all: routing adds hops
and the real generator becomes the dominant term.
