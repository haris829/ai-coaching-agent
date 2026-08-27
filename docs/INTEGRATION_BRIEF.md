# AI Coaching Agent — Integration Brief (UC-01 to UC-10)

> Stored verbatim as the specification this repository was merged against.
> Progress against it: [MERGE_NOTES.md](MERGE_NOTES.md) (what is done),
> [../PLATFORM_CONTRACT.md](../PLATFORM_CONTRACT.md) (Phase 1),
> [DECISIONS.md](DECISIONS.md) (Part 3).

## READ THIS FIRST — integrity check

This document has **14 numbered parts** and ends with the line `=== END OF INTEGRATION BRIEF ===`.

Before starting, confirm you can see the final line and all fourteen part headings. If anything is missing, stop and report that the document was truncated. Do not begin from a partial brief.

---

## Part 1 — What you have been given

Ten backend components, each built in its own repository, each independently tested, each with no knowledge that the others exist.

| Component | Responsibility |
|---|---|
| UC-01 | Coaching session initiation — creates the session record |
| UC-02 | Contextual awareness setup — assembles the learner context |
| UC-03 | Legal concept Q&A — the four-part answer |
| UC-04 | Course content coaching — lesson-grounded answers, quiz protection |
| UC-05 | Socratic method coaching — guiding-question dialogue |
| UC-06 | Case-linked coaching — educational answers over a case file, mandatory disclaimer |
| UC-07 | Knowledge gap report — read-only aggregation |
| UC-08 | Streaks and milestones — no AI, arithmetic over timestamps |
| UC-09 | Session summary and CPD export |
| UC-10 | Feedback capture and content review flagging |

Every component follows the same architecture:

- **Ports and adapters.** Every external dependency is a typed interface with a deterministic mock behind it. No component calls a URL directly.
- **A provider registry.** Adapter selection is a config value resolved through a registry. An unknown value fails loudly at startup and never falls back to a mock.
- **A conformance kit.** A reusable, adapter-agnostic test suite per port. You point it at your adapter and it tells you whether your integration is correct. **You do not write these tests.**
- **A foreign-adapter proof.** Each repository contains a deliberately alien adapter family — different field names, nesting, value representations — that the unmodified service passes against. Replaceability is demonstrated, not claimed.
- **Three documents.** `docs/SHARED_CONTRACT.md` (what the component emits and expects), `docs/assumptions.md` (everything invented because the company had not specified it), `docs/INTEGRATION.md` (the adapter runbook with a worked example).

**Read all ten `assumptions.md` files before writing any code.** They are the record of every guess made in the absence of company specifications, and they are where integration will hurt.

---

## Part 2 — What integration is, and what it is not

**Integration is:** replacing mock adapters with real ones, wiring components to each other, reconciling ten published contracts into one, and building the four things nobody built (Part 7).

**Integration is not:** rewriting business logic, restructuring layers, merging codebases into one service by copy-paste, or "improving" behaviour that was built to specification.

The swap guarantee each component was built to hold is: **one new adapter file, one registry line, one config value.** Nothing else changes — no edits to domain models, application services, API layers, existing adapters, or existing tests.

If you find yourself editing a component's `domain/` or `application/` directory to make integration work, **stop.** Either the adapter is doing too little, or you have found a genuine contract conflict that belongs in Part 4 rather than in a patch.

---

## Part 3 — Decisions required from the company

Eight items are unresolved. Two block release. Get them in writing before or during Phase 1 — they are not engineering choices.

| # | Decision | Blocking? | Why it matters |
|---|---|---|---|
| 1 | **Disclaimer wording** | **Yes** | The scope document states the mandatory case-linked disclaimer twice with different wording. The Overview gives three sentences; UC-06 step 5 gives a shortened two-sentence form. UC-06 built against the Overview's text. The spec calls this string non-negotiable and verifies it by automated scan — so if the shortened form is correct, every response carries the wrong disclaimer *and the compliance test passes anyway*. One constant to change. |
| 2 | **Legal content authorship** | **Yes** | The legal explanations are illustrative — built to demonstrate the mechanism, not to be relied on by a practising solicitor. They need a qualified author and a confirmed jurisdiction. UC-06 refuses to start in production while the content is marked illustrative; that gate must not be removed until real content is in place. |
| 3 | **Case file origin mechanism** | No | UC-06 must verify a case file "originated from the Case Prep Agent." It currently compares a field value. If origin is really established by signature or sealed envelope, a string comparison is spoofable. |
| 4 | **Halt-clearing authority** | No | A disclaimer failure halts the case-linked session. Nobody specified who may clear the halt or what audit trail is required, so no endpoint was shipped. Needs an authorisation model. |
| 5 | **Content review minimum sample size** | No | The 30% thumbs-down threshold has no minimum sample. One negative rating is 100% and flags instantly. UC-10 implemented a configurable minimum; the company must confirm the number, since it decides when the platform accuses its own content of being wrong. |
| 6 | **NARIC Levels 4 and 6** | No | The scope maps three templates to Levels 3, 5 and 7 and is silent on 4 and 6. Every component assumed 4→basic and 6→intermediate, on the basis that Level 6 is an undergraduate law degree rather than Masters level. Confirm or correct — it must be identical across all ten. |
| 7 | **Topic taxonomy** | No | Gap reports, content review flags and progress logging all aggregate by legal topic. Each component invented a vocabulary. Changing it after launch invalidates historical data. |
| 8 | **Confidentiality sign-off** | No | Case-linked coaching transmits case facts to a model provider. Case files may contain privileged client information. UC-06 refuses to enable a real provider without documented sign-off. |

Decisions 6 and 7 are the ones that must be **identical across all ten components**. Resolve them in Phase 1, not later.

---

## Part 4 — Phase 1: Contract reconciliation

**This is the crux of the integration and the phase most likely to be skipped. Do not skip it.**

Ten components published ten `SHARED_CONTRACT.md` files describing overlapping data. They were written independently. Where they disagree, nothing fails at build time — it fails silently at runtime, in production, on data that looks plausible.

### 4.1 Build the platform contract register

Create one document — `PLATFORM_CONTRACT.md` — that is the single authority. For every shared concept, record the agreed name, type, allowed values, and which component owns it.

Reconcile at minimum:

- **Session identity.** UC-01 creates `session_id`. Every other component receives it and never creates one. Confirm the type and format, and that every dev-mode minting path is disabled.
- **NARIC level.** Closed enum `LEVEL_3 … LEVEL_7_PLUS`, with a separate `naric_level_source` of `retrieved` or `default`. Confirm every component agrees, and that a value outside the enum is treated as an invalid response rather than accepted or rounded.
- **Explanation profiles.** The three-way grouping, identical everywhere.
- **Source status vocabulary.** `available`, `empty`, `partial`, `unavailable`, `invalid`. Confirm no component conflates `empty` with `unavailable` — several depend on that distinction to avoid making false claims about a learner.
- **Course progress.** Completion percentage as an integer 0–100.
- **Enum casing.** Every emitted value lowercase, across every component, on both API responses and stored records.
- **The interaction log record.** See 4.3 — this is the hard one.
- **Topic and concept vocabularies.** One list, everywhere.

### 4.2 Known divergences to close first

These are already identified and must be closed before wiring:

- **UC-04 runtime.** Built in TypeScript against an incomplete brief. A port to Python is instructed and in progress. Until it lands, treat UC-04 as a separate service (Part 5) and expect no shared types across its boundary.
- **UC-04 qualification level.** Used an invented three-point scale (`BEGINNER`/`INTERMEDIATE`/`ADVANCED`) with no provenance field. Being corrected to the platform enum.
- **UC-04 enum casing.** Uppercase throughout; being lowercased.
- **UC-03 enum casing.** A rename to lowercase across `status`, `classification`, `follow_up_actions`, `AuthorityStatus`, `ExplanationDepth`, `FieldAvailability`, `LogStatus` is instructed.
- **UC-05 `response_kind`.** Gains a sixth member, `closing_acknowledgement`, for a learner who reasons their way to the answer.
- **UC-05 `mode` field.** Becomes a closed enum (`free_form`, `course_linked`, `case_linked`, `socratic`) rather than the fixed literal `"socratic"`. Note the known limitation: this conflates session type with response mode, so a Socratic turn does not carry its underlying session type. Flagged deliberately; decide whether to split into orthogonal fields now or accept it.

### 4.3 The interaction log — single store, multiple writers

**This is the single largest integration risk.**

UC-03, UC-04, UC-05 and UC-06 each write interaction records. UC-07 reads them to build gap reports. UC-08 reads them as activity. UC-09 reads them to build summaries. UC-10 attaches ratings to them.

At integration these must resolve to **one store**. That means:

1. Reconcile the four writers' record shapes into one superset schema, with mode-specific fields nullable.
2. Confirm every reader can consume every writer's records. UC-07's gap report depends on `topic_tag`, `explain_differently_count` and follow-up linkage being present and consistently populated across all four writers — if UC-04 and UC-03 tag topics differently, gap reports are wrong in a way nobody will notice.
3. Preserve the single-writer rule per record type. `rating_state` is written as `pending` by whichever component creates the interaction, and changed **only** by UC-10. No other component touches it.
4. Confirm `interaction_id` is globally unique across all four writers, not unique per component.

Do this on paper before writing an adapter.

### 4.4 Assumption reconciliation

Collect every `assumptions.md` row tagged as requiring verification. Check each against the real system now available. Anything that turns out wrong is an adapter change — but only if you catch it here rather than in production.

Pay particular attention to rows marked as **thresholds tuned against fake generators**: loop-detection similarity, restatement thresholds, quiz-classifier confidence bands, retrieval cutoffs, and P95 latency figures. A real language model has far higher phrasing variance than a template generator. **Every one of these must be re-measured against the real generator, not carried across.** They are documented precisely so the re-measure is cheap.

---

## Part 5 — Phase 2: Topology

Three viable shapes. Choose one deliberately and record why.

**A — Ten services.** Each component deployed independently, communicating over HTTP. Highest operational cost, cleanest isolation, and the only option that works while UC-04 remains TypeScript. Latency accumulates across hops — see Part 10.

**B — One application, ten packages.** Each component imported as a package, composed behind one API layer, with its ports wired to sibling services in-process. Lowest latency, simplest deployment, requires a single runtime. Every component was built to support this: `domain/` and `application/` import nothing outside themselves, so the merge touches only the API and adapter layers.

**C — Hybrid.** Core coaching path in-process (UC-01 through UC-06), read-only aggregation as separate services (UC-07 through UC-10). Reasonable middle ground, since the aggregators are latency-tolerant and independently schedulable.

**Recommendation: B, once UC-04's port lands.** It is what the architecture was built for, it removes the shared-type problem entirely, and it makes the latency budget in Part 10 achievable. Take A only if the runtime split persists or organisational boundaries demand it.

Whichever you choose, **the composition root is the only place that knows which implementation backs which port.** That rule holds in all three topologies.

---

## Part 6 — Phase 3: The wiring map

Each component declares ports for things it does not own. Integration resolves those ports — most of them onto sibling components rather than onto company systems.

**This is the insight that makes integration tractable: UC-02 is not a service other components call ad hoc. It is the implementation behind everyone's `LearnerContextProvider` port.** The same pattern applies throughout.

### Ports that resolve onto sibling components

| Consumer | Port | Resolves to |
|---|---|---|
| UC-03, UC-04, UC-05, UC-06 | `LearnerContextProvider` | **UC-02** |
| UC-05 | `SessionModeRepository` | **UC-01**'s session record |
| UC-05 | `AnswerGenerator` (four-part) | **UC-03** |
| UC-07 | `InteractionLogProvider` | the shared interaction store (Part 4.3) |
| UC-07 | `FeedbackProvider` | **UC-10** |
| UC-08 | `ActivityProvider` | the shared interaction store |
| UC-08 | `GapReportProvider` | **UC-07** |
| UC-09 | `SessionProvider` | **UC-01** |
| UC-09 | `InteractionProvider` | the shared interaction store |
| UC-09 | `CitationProvider` | **UC-03** and **UC-06** authority records |
| UC-09 | `GapReportProvider` | **UC-07** |
| UC-10 | `InteractionProvider` | the shared interaction store |

### Ports that resolve onto company systems

| Port | Company system | Consumers |
|---|---|---|
| `NaricProvider` | NARIC Assessment data store | UC-01, UC-02 |
| `CoursesProvider` | Courses Agent | UC-01, UC-02, UC-04, UC-07 |
| `LegalFootprintsProvider` | Legal Foot Prints profile | UC-01, UC-02, UC-07 |
| `CaseFileProvider` | Case Prep Agent (read-only) | UC-01, UC-06 |
| `CurrentUserProvider` | Company authentication | all ten |
| Persistence repositories | Company database | all ten |
| `AnswerGenerator` / `GuidingQuestionGenerator` | Model provider | UC-03, UC-04, UC-05, UC-06 |
| `AdminAlertSink`, `EngineeringAlertSink`, `SecurityIncidentSink`, `NotificationSink` | Company alerting and notification | UC-06, UC-08 |
| `DocumentRenderer` | PDF rendering | UC-09 |

### Sequential dependencies

From the scope document, and they constrain the order in which a session can proceed:

- NARIC must be complete before calibration is available; without it, Level 5 default applies.
- The Courses Agent must be active for course-linked and lesson-linked sessions.
- Case-linked sessions require a case record produced by the Case Prep Agent.
- Post-submission quiz review is owned by the Courses Quiz Agent's callback. **No component here owns that integration** — do not build it.

---

## Part 7 — Phase 4: What nobody built

Four things fall outside every component's scope. They are unowned, and integration will stall on them if they are not planned now.

### 7.1 The coaching router — build this first

Nothing decides which component handles a learner's turn. That decision is:

```
Socratic mode on?           → UC-05
else case-linked session?   → UC-06
else course-linked session? → UC-04
else                        → UC-03
```

The router owns: reading session mode and type, dispatching the turn, and returning a uniform response envelope. It must **not** own coaching logic, prompts, or guardrails — those live in the components.

Two rules the router must hold:

- **A case-linked turn must reach UC-06 and nothing else.** If a routing bug sends a case question to UC-03, the response carries no disclaimer. Test this specifically.
- **UC-05's direct answers come from UC-03**, so a Socratic turn that hits the cap involves two components. Budget latency accordingly.

### 7.2 The weekly summary scheduler

UC-08 deliberately ships no scheduler — generation is an explicit callable operation, because scheduling is an infrastructure concern. You must drive it every Monday, and honour the rule that missed weeks are never batch-sent.

### 7.3 The admin surfaces

- Content review flags (UC-10) need an admin dashboard with separate authorisation.
- Case-linked session halts (UC-06) need a clearing mechanism — blocked on Decision 4.
- Both must be unreachable by any learner path.

### 7.4 The frontend

Excluded from every component by instruction. Each publishes the data a frontend needs and stops there. The interfaces the scope document describes but nobody built: the mode selector and greeting, the Socratic toggle and indicator, thumbs up/down controls, the streak sidebar and badge collection, the "Larry Insights" panel, the summary card and PDF preview, and the "Larry is thinking" indicator driven by the timing metadata UC-03 exposes.

---

## Part 8 — Phase 5: Real adapters

For each port in Part 6, follow the component's own `docs/INTEGRATION.md`. Every one contains a worked example.

The procedure is identical everywhere:

1. Copy `adapters/real/_template.py`. Fill the TODOs: endpoint, auth, payload mapping, error translation.
2. Add one line to the registry.
3. Set one config value.
4. Run that component's conformance kit against your adapter.

**Rules that hold for every adapter:**

- The adapter is the **only** place the upstream payload shape is known. No upstream field name, nesting, or error string escapes it — including in error messages. Two components caught real leaks of exactly this kind during their builds, both in error strings rather than success payloads.
- The adapter **never invents data.** A missing value maps to the documented default with its source field marked accordingly, never to a plausible guess.
- Adapters raise only the three contract exceptions. A bare exception crossing a port boundary is a defect the conformance kit will catch.
- **Never fall back silently to a mock.** A service running on fabricated NARIC levels in production is worse than one that refuses to start.
- Authorisation stays server-side, inside the adapter.
- If the real payload cannot be mapped onto the platform contract, that is a **contract conversation, not an adapter workaround.** Raise it. Do not bend a domain model to fit an upstream quirk.

Run the conformance kit for every port before wiring anything together. A failing adapter caught in isolation costs minutes; the same failure caught in an end-to-end test costs a day.

---

## Part 9 — Phase 6: Shared concerns

### 9.1 Identity

Every component resolves `user_id` server-side and refuses to read it from a request body. Several ship a development header adapter that **must be replaced before any deployment touching real data.**

After wiring, verify across all ten: no endpoint accepts a user identifier as a parameter, and no learner can reach another learner's session, dialogue, gap report, streak, summary, rating or case file.

### 9.2 Privacy

Each component enforces a logging allow-list. Preserve them.

Specifically, and these are not negotiable:

- Case facts and case-linked question text never reach any log.
- Socratic dialogue content — a professional's reasoning, including where they were wrong — stays in the dialogue store, never in application logs.
- Gap report content never reaches logs; a report names where a named professional is weak.
- Question, response and comment text never reach logs, even though UC-10 stores them for the improvement pipeline.

When you centralise logging, **re-run each component's privacy test against the centralised logger.** An allow-list enforced in a component is worthless if the aggregator wraps it.

### 9.3 The disclaimer

Three independent layers in UC-06: a response type that cannot be constructed without it, a boundary check that does not trust the type, and an output scan. **All three survive integration.**

If UC-06 is composed behind a shared API layer, verify the boundary check still runs at the true outermost point. A shared response wrapper that reserialises payloads can defeat a check that assumed it was last.

Re-run the adversarial injection suite after integration, not only before.

### 9.4 Latency

The scope requires **P95 ≤ 3 seconds** end to end, verified by load test.

Every component measured its own P95 against a fake generator. Those figures do not survive integration: a real model is the dominant term, and routing adds hops. A Socratic turn at the cap involves the router, UC-02, UC-05 and UC-03.

Allocate an explicit budget across the path, measure it under load, and re-measure every threshold in Part 4.4 at the same time.

The scope also requires a thinking indicator after 1.5 seconds and a hard timeout at 10 seconds. Both must survive routing.

---

## Part 10 — Phase 7: End-to-end verification

Component suites passing is not evidence of integration. Verify complete journeys.

**J1 — Free-form.** Open a session → context loads → ask a question → four-part answer with a verified authority → follow up "explain differently" → different framing, no repeat → rate it → interaction and rating both land in the shared store.

**J2 — Course-linked.** Open linked to a lesson → enrolment verified → ask a concept question → grounded answer with a section reference → request the quiz answer, directly and indirectly → declined, concept explained, never a bare refusal.

**J3 — Socratic.** Toggle on → indicator state exposed → guiding question, not an answer → four exchanges → ask for a direct answer → **offer**, not an answer → confirm → four-part answer arrives → next question returns to Socratic mode.

**J4 — Case-linked.** Select a case file → read access verified → ask how a defence applies → educational answer citing verified case facts → **disclaimer present, verbatim** → ask "will my client win" → substantive redirect to the legal test, no prediction.

**J5 — Ten interactions.** Accumulate exactly ten across sessions → gap report becomes available at ten, not nine → at least three topic areas, each with resolvable evidence → recommendations link to real lessons.

**J6 — Streak and CPD.** Interactions across consecutive days → streak increments once per day → badge at ten questions → generate a session summary → PDF carries branding, name, date, duration, four sections, the CPD label and the session ID.

**J7 — Degradation.** Take each upstream down in turn — NARIC, Courses, Legal Foot Prints, Case Prep, the model provider, feedback — and confirm every component degrades as documented: session still opens, questions still answered at Level 5 marked `default`, case-linked responses still carry the disclaimer, gap reports state which signal is dark, streaks never reset on a write error.

**J8 — Cross-user.** For every read surface across all ten components, confirm one learner cannot reach another's data.

**J9 — Routing.** Confirm each session type reaches the correct component, and specifically that **no case-linked turn can reach a component that does not apply the disclaimer.**

---

## Part 11 — Global Definition of Done

From the scope document. All must pass before production, with Husnain's sign-off recorded.

- [ ] Explanation complexity calibrates to NARIC level — the same question at Level 3 and Level 7 produces demonstrably different depth
- [ ] Course-linked coaching references lesson content without giving quiz answers — direct and indirect phrasings
- [ ] Case-linked coaching includes the disclaimer on every response — zero exceptions, verified by automated test
- [ ] Socratic mode does not revert to direct answers without explicit user request or the five-exchange cap
- [ ] The gap report identifies at least three concrete topic areas after ten interactions
- [ ] The CPD summary PDF generates accurately with all four sections
- [ ] Daily streak tracking persists across devices — three sequential logins
- [ ] Feedback ratings log with full context metadata on every rating event
- [ ] P95 response latency ≤ 3 seconds under load
- [ ] Disclaimer bypass not possible via prompt injection or admin setting
- [ ] Full regression passing on all ten use cases
- [ ] Husnain sign-off received and recorded

Add, beyond the scope document:

- [ ] All eight Part 3 decisions resolved in writing; both blocking items closed
- [ ] `PLATFORM_CONTRACT.md` complete, with every divergence closed
- [ ] Every component's conformance kit passing against real adapters
- [ ] No development identity adapter reachable in production configuration
- [ ] No component can fall back silently to a mock
- [ ] UC-06's illustrative-content gate still in force, or real content in place
- [ ] Every threshold tuned against a fake generator re-measured against the real one

---

## Part 12 — Rules that hold throughout

**Do not modify component business logic.** If integration requires it, you have found a contract conflict — raise it.

**Do not bypass a port.** Calling a component's internals directly because it is faster than wiring an adapter destroys the property that makes the next change cheap.

**Do not remove a safety gate to unblock yourself.** The disclaimer layers, the illustrative-content gate, the confidentiality gate and the loud startup failures are all there because someone decided the failure mode was worse than the inconvenience.

**Do not weaken a test to make a suite green.** If an existing test fails after wiring, it is telling you something true.

**Do not resolve an assumption by picking an answer.** The registers exist so the company answers. Choosing quietly is how a wrong assumption becomes permanent.

---

## Part 13 — Suggested order

1. Company decisions raised; blocking items chased (Part 3)
2. Contract reconciliation on paper; `PLATFORM_CONTRACT.md` written (Part 4)
3. Known divergences closed — UC-04 port, casing renames, UC-05 enum additions (Part 4.2)
4. Topology chosen and recorded (Part 5)
5. Shared interaction store designed and agreed (Part 4.3)
6. Company-system adapters written, one port at a time, each verified by its conformance kit (Part 8)
7. Sibling wiring — UC-02 behind everyone's context port first, since four components depend on it (Part 6)
8. The coaching router (Part 7.1)
9. Remaining unbuilt pieces — scheduler, admin surfaces (Parts 7.2, 7.3)
10. Cross-cutting verification — identity, privacy, disclaimer, latency (Part 9)
11. End-to-end journeys (Part 10)
12. Global Definition of Done, then sign-off (Part 11)

Steps 1 and 2 are cheap and unblock everything. Do not start at step 6 because it feels like progress.

---

## Part 14 — What to report

1. **Decisions** — the eight items, each with the company's written answer or its current status.
2. **`PLATFORM_CONTRACT.md`** — in full, with every divergence and how it was closed.
3. **Topology** — chosen shape and the reasoning.
4. **Interaction store** — the reconciled schema, and confirmation that every reader consumes every writer's records.
5. **Adapters** — one row per port: the file, the registry entry, the config value, and the conformance result.
6. **Re-measured thresholds** — every constant tuned against a fake generator, with its old value, its new value, and whether behaviour changed.
7. **Unbuilt pieces** — what was built for the router, scheduler and admin surfaces, and what remains outstanding.
8. **Journeys** — J1 through J9, each with its result.
9. **Latency** — the budget allocation and the measured P95 under load.
10. **Safety re-verification** — disclaimer layers, adversarial injection, privacy allow-lists, cross-user access, all re-run post-integration.
11. **Global Definition of Done** — every line, with its evidence.
12. **Outstanding risks** — everything still resting on an unconfirmed assumption.

Do not report "integration complete" on the strength of passing suites. Report the journeys, the measured latency, and the re-verified safety controls.

=== END OF INTEGRATION BRIEF ===
