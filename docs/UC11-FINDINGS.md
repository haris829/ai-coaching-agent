# UC-11 findings

What the Global DoD suites turned up about the *integrated* system — the questions no single
capability's own tests can answer.

Findings are numbered `U-nn` to keep them distinct from `F-nn`, which are the findings task11's
earlier integration attempt recorded in its own `docs/INTEGRATION-FINDINGS.md`. Severity is judged
by what would happen in production.

Where a finding was fixed, the fix is in the capability that owns the behaviour, never in UC-11 —
the validation layer introduces no domain logic, and `tests/global_dod/test_no_new_domain_logic.py`
enforces that.

---

## Fixed

### F-02 (from task11) — the question bank handed answer keys to unauthenticated callers
**Severity: critical.** UC-02.

Verified still present in this merge and fixed. `GET /api/question-bank/questions`,
`/questions/{id}`, `/versions`, `/versions/{v}` and `/usages` took no identity; the payload carries
`isCorrect` on every option, `correctPosition` for a drag-to-order and `isPrimary` on a scenario's
sub-questions. With the learner API and the admin API behind one gateway, an authenticated learner
could read the answer to every question in the bank before sitting the quiz.

UC-02 deferred this deliberately — its own test read *"Reads stay open — the platform's real auth
decides that policy at merge time."* This is that merge. All reads now carry the administrator
guard, and that test asserts the new policy. The import history, delivery pool and attempt report
were guarded at the same time: the pool withholds the answer key by design but still exposes the
question text and options, and UC-03 reaches the bank in-process rather than over HTTP, so nothing
legitimate lost access.

The only endpoints now reachable anonymously are `/api/health`, `/api/health/live`, `/api/meta` and
the two static CSV-template documents.

---

## Accepted — reported, not changed

### U-01 — coaching eligibility distinguishes "not yours" from "does not exist"
**Severity: low.** UC-07.

`GET /api/v1/attempts/{id}/coaching/eligibility` answers `200` for another learner's attempt, with
`coachingAvailable: false` and `reason: NOT_ATTEMPT_OWNER`. For an id that does not exist it answers
`200` with `reason: ATTEMPT_NOT_FOUND`. The two are distinguishable, so a caller can confirm that an
attempt id exists.

**No attempt data crosses over** — no mark, no percentage, no verdict, no question — which
`test_immutability.py::test_another_learner_cannot_reach_a_submitted_attempt_at_all` asserts on the
response body of every learner-facing route.

Not changed, for three reasons. UC-07 specifies this endpoint as one that never fails for an
ineligible attempt, because a learner opening their report should read "coaching is not available"
rather than see an error. The 403-for-attempts / 404-for-sessions split is a distinction UC-07 drew
deliberately and its 263 tests assert. And attempt ids are UUID4, so enumeration is not feasible —
the disclosure requires already knowing the id.

Worth revisiting if attempt ids ever become guessable, or if the system adopts a single
cross-capability convention for another learner's data. UC-08 and UC-09 chose *absent* over
*forbidden* for exactly this reason, so the system is not currently consistent.

---

## Verified as not applying to this merge

task11 recorded fifteen findings against an earlier integration. Each was checked here:

| Finding | Status in this merge |
|---|---|
| F-01 certificate issued before assessor approval | **Fixed during UC-09.** The gate is at UC-05's single certificate funnel; `test_formal_assessment_chain.py` covers it |
| F-02 question bank leaked answer keys | **Was present. Fixed** — see above |
| F-03 retakes could not honour questions already seen | **Fixed during UC-08.** `deprioritised_question_ids` on UC-03's selector |
| F-04 advisory scoring anomaly treated as blocking | **Does not apply.** UC-04 has no WARNING tier: every `ScoreAnomaly` is a real data defect that blocks confirmation by design, and the negative-mark clamp raises none |
| F-05 seven modules define a top-level `app` | **Resolved structurally.** One tree, one `app`; no loader, no `MetaPathFinder` |
| F-13 UC-05 served results to anonymous callers | **Does not apply.** Every endpoint carries `Authorization`; audited across the whole OpenAPI document |
| F-14 UC-06/UC-07 took the learner identity from the URL | **Does not apply.** No learner path segment survives, except UC-08's admin grants listing, which is behind the administrator guard by design |
| F-15 five modules' HTTP surfaces untested | **Closed.** `tests/integration/` drives UC-07…UC-10 over HTTP; `tests/global_dod/` drives the whole surface |
| F-06 scenario modelled differently on each side | Reconciled in `attempt_delivery/integration/uc02/`, as before |
| F-07 UC-02 has no concept of a quiz or course | Unchanged and correct: the bank is global, and the topic scope frozen on the configuration version is what narrows it |
| F-08 UC-10's vocabulary does not cover the system's question types | Reconciled in `analytics/integration/question_types.py`, with the exact name carried in `question_type_label` |
| F-09 UC-03 calls its downstream hand-off inside its own transaction | Unchanged: `ResultsPipeline` is the documented seam and the submission is durable before it runs |
| F-10 "UC-01 does not exist" | **No longer true.** UC-01 is real here (98 tests), which is why UC-09's configuration flags could be added to an immutable version rather than faked |
| F-11 five modules own no persistence | **No longer true** for UC-08/09/10, each of which now owns real tables |
| F-12 UC-10 reads through an adapter, not a warehouse | Still true by design; `AnalyticsPorts.merged()` is the one line to change |

---

## Deployment requirements that fall out of the above

Neither is a defect; both are things that must be true of a deployed environment.

1. **`ADMIN_API_TOKEN` must be set.** The administrator guard is a no-op while it is unset — a
   sensible local default and an unsafe production one. Since the F-02 fix it gates question-bank
   *reads* as well as writes, so an unset token now exposes the answer key rather than only
   permitting unauthenticated writes.
2. **`SYSTEM_API_TOKEN` must be set.** UC-09's system endpoints — disconnect reporting, certificate
   eligibility, queue recovery — fall back to accepting an administrator credential while it is
   unset. Those callers are not administrators, and a learner must never reach them.

Both are values the operator generates; neither is a credential obtained from a third party. The
only genuinely external credential is `COACHING_LLM_API_KEY`, and with it unset UC-07 honestly
reports coaching unavailable while the other nine capabilities work normally.

---

## Defects UC-11 found in this merge

Three, all found by the sweep and the scenario suites rather than by any capability's own tests.
The pattern in each is the same: a rule enforced somewhere the per-capability suites cannot see.

### F-16 — the disconnect auto-submit path could never commit (critical)

`POST /api/v1/formal-attempts/{id}/disconnect` returned **500** and wrote nothing. UC-09 added
`DISCONNECT_AUTO_SUBMIT` as a third submission reason and widened the CHECK constraint on
`qd_attempts` (revision `c156bd33962a`) but not the matching one on `qd_attempt_submissions`, which
UC-03 writes in the same transaction. Every disconnect failed at the flush.

The consequence is the exact loss the auto-submit rule exists to prevent: a learner whose device
dropped out of a supervised sitting lost the attempt, and a formal assessment cannot be resumed.

Invisible to UC-09's own suite, which drives UC-03 through port fakes — and a fake has no CHECK
constraints. No chain test disconnected. `tests/global_dod/test_scenarios.py` scenario E does.

*Fixed:* the constraint widened in `attempt_delivery/models.py` and in migration `7b41c0d9e5a2`.

### F-17 — every UC-09 provider-outage error raised `TypeError` instead (high)

`ProviderUnavailableError` gained a required `provider` argument during UC-10. UC-09's seven
subclasses declared only a `code`, so `AttemptDeliveryUnavailableError()` — eighteen call sites —
raised `TypeError`, which the handler reports as an opaque 500. The failure path was itself broken:
a real dependency outage was reported as a bug in the application rather than as a retryable 503,
and the original exception was lost.

Three call sites passed a *message* as the first positional argument, which bound it to `provider`.
Those produced a 503 whose body named the sentence as the boundary and showed the generic text.

*Fixed:* `NamedProviderUnavailableError` in `app/core/errors.py` — a subclass declares its boundary
once and is raised bare or with a message. UC-08 had solved this by hand in two classes; those two
hand-written constructors are now gone, which is one less place for the two to diverge.

### F-18 — UC-01's configuration immutability trigger was missing from every migrated database (critical)

`trg_qc_config_version_no_update` did not exist on any database built by the migrations. UC-09's
revision `108e83e56e69` adds three columns to `qc_configuration_versions`; on SQLite that is a batch
rebuild, which silently drops the table's triggers. Revision `f2edce6a1ae0` knew this and reinstated
the trigger — with a comment explaining exactly this hazard — and `108e83e56e69` did not.

So a deployed instance would accept an `UPDATE` to a published configuration version: the single
invariant every locked attempt, every stored result and every certificate depends on. Nothing
failed and nothing logged.

Invisible to the whole Python suite, which builds its schema from the models and therefore had the
trigger. Only `npm run verify:e2e`, which migrates, saw it — which is what that gate is for.

*Fixed:* `108e83e56e69` reinstates the trigger on the way up and on the way down; `7b41c0d9e5a2`
repairs a database that already ran it. And `tests/test_schema_migration.py` now compares each
table's **triggers** alongside its columns and constraints, so the next batch rebuild that drops one
fails in the fast suite rather than in the live gate — with a companion test asserting the
comparison is not silently comparing empty lists.

---

## Defects found during the deployment audit

Two more, both found by driving UC-08, UC-09 and UC-10 over real HTTP against a migrated database
for the first time — the gap recorded as A1 in
[DEPLOYMENT-AUDIT.md](DEPLOYMENT-AUDIT.md). Both are the same shape as F-17, which is why the third
entry below is a permanent gate rather than another fix.

### F-19 — eight raise sites in UC-09's persistence layer could not be constructed (critical)

Every uniqueness and concurrency path in `formal_assessment/repositories/sqlalchemy.py` called its
error class with arguments the class did not declare, so each raised **`TypeError`** instead:

| Error | Sites | What it guards |
|---|---|---|
| `ConcurrentModificationError` | 3 | the compare-and-set on formal attempts, device sessions and reviews |
| `DuplicateFormalAttemptError` | 2 | one formal attempt per learner and quiz |
| `DeviceSessionAlreadyHeldError` | 2 | **the single-device lock** |
| `DuplicateReviewError` | 1 | one review per formal attempt |

The guarantees themselves held — the database refused every duplicate — but the *refusal* was an
opaque 500 with the real cause discarded. Concretely: a learner opening a supervised examination in a
second browser saw "an unexpected internal error occurred", and
`DeviceSessionService.register` — which is written to catch `DeviceSessionAlreadyHeldError`, record
the rejected device as evidence, and raise a message naming the learner's other device — never ran
at all. The same applies to `handle_disconnect`, whose duplicate-disconnect idempotency depends on
catching `ConcurrentModificationError`: under a genuine race that `except` block was unreachable.

Invisible to UC-09's suite because its repositories are in-memory doubles that detect a conflict
with a Python check rather than a unique index, so the `IntegrityError` branch is never taken.

*Fixed:* `ConcurrentModificationError` now accepts the two versions every caller was already trying
to pass — an expected-3-actual-5 is the diagnosis, and dropping it lost real information. The other
five call sites now pass the keywords their classes declare, and supply the context the service on
the other side reads: which session holds the lock, which attempt already occupies the slot.

### F-20 — UC-10's immutability trigger did not follow the convention (low)

Ten of the eleven immutability triggers raise a message prefixed `IMMUTABLE_<THING>:`, which is what
lets a caller reading a raw database error distinguish an immutability refusal from a missing column
or a broken connection without matching on prose. `trg_qy_review_action_no_update` said only
"review actions are append-only". UC-10 was integrated last and had drifted.

Not a hole in the guarantee — the trigger works — but it defeats the recognition the convention
exists for, and it was found by a check that could not assert the refusal was the *right* refusal.

*Fixed:* aligned to `IMMUTABLE_REVIEW_ACTION:`. The migration reads its trigger SQL from the models
module, so a fresh migration produces the new wording.

### The gate that stops this recurring

Three defects (F-17, F-19, and the device-lock case that surfaced F-19) were all "this `raise` cannot
be constructed". None was visible to any test, for a structural reason: they are on **error paths**,
reached only when a constraint fires or a dependency is down, and the doubles used by the unit suites
have neither constraints nor outages.

`tests/test_error_signatures.py` now binds every `raise SomeError(...)` in `app/` against that
class's `__init__` — 292 sites — and fails on any mismatch. It would have caught all three on the
commit that introduced them, and it carries a companion test proving the binding check still rejects
what it is supposed to reject.
