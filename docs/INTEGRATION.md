# Integration notes

Ten capabilities: three built separately and merged, then four built onto the seams that merge
left behind, then three more (UC-08, UC-09, UC-10) onto the seams *those* left — plus UC-11, which
builds nothing and validates all ten. This document is about those seams — where they are, why each is shaped the way it is,
and what changes when the company's own systems replace the placeholders.

1. [Ownership](#1-ownership--who-owns-what) — the map
2. [The shared kernel](#2-the-shared-kernel--owned-by-no-capability) — what may be shared, and what may not
3. [Every seam](#3-every-seam-between-the-capabilities) — the ports, and why each exists
4. [Decisions the merge forced](#4-decisions-the-merge-forced) — the conflicts, and how each was resolved
5. [Contracts that changed](#5-contracts-that-changed-at-merge-time) — the breaking changes, each with a reason
6. [The company's systems](#6-the-companys-systems--tomorrow) — database, identity, enrolment,
   marking, certificates, the AI coach

---

## 1. Ownership — who owns what

| | UC-01 Quiz Configuration | UC-02 Question Bank | UC-03 Attempt Delivery |
| --- | --- | --- | --- |
| **Tables** | `qc_*` | `qb_*` | `qd_*` |
| **API** | `/api/admin/quizzes`, `/api/quizzes` | `/api/question-bank` | `/api/v1` |
| **Package** | `app/modules/quiz_configuration/` | `app/modules/question_bank/` | `app/modules/attempt_delivery/` |
| **UI routes** | `/configuration`, `/rules` | `/questions`, `/topics`, `/import`, `/reports` | `/attempt` |
| **Owns** | configuration rules, immutable versions | question content, snapshots, topics, import | the attempt lifecycle |

| | UC-04 Scoring | UC-05 Pass/Fail & Certificate | UC-06 Feedback |
| --- | --- | --- | --- |
| **Tables** | `qr_*` | `qg_*` | `qf_*` |
| **API** | `/api/v1/attempts/{id}/result` | `/api/v1/attempts/{id}/outcome` | `/api/v1/attempts/{id}/feedback` |
| **Package** | `app/modules/scoring/` | `app/modules/certification/` | `app/modules/feedback/` |
| **UI routes** | `/attempt` (the result panel) | `/attempt` | `/attempt` |
| **Owns** | the marking rules and the marks | the verdict, the certificate, the CPD record | the report and its wording |

The chain's ownership rule is the one that keeps it a chain rather than a monolith: **each stage owns
its own decision and reads the previous stage's *persisted* output through a port.** UC-05 never
computes marks; UC-06 never decides pass/fail; neither can write to the other's tables, and there is no
method on any port that would let it.

Two ownership decisions are worth stating plainly, because they were the substance of the merge:

**There is exactly one owner of attempts.** UC-01 shipped an attempt table, an attempt service and
attempt endpoints; UC-03 shipped its own. Two records of "did this learner attempt this quiz" would
eventually disagree, and the one that disagreed would be whichever the reader happened to consult. So
UC-01's attempt layer was **removed** — table, repository, service, endpoints — and UC-03's kept. UC-01
still answers "2 of 3 attempts remaining" in its rules summary, but through `AttemptStatisticsPort`.
`tests/test_architecture.py::test_there_is_exactly_one_owner_of_attempts` fails if a second appears.

**There is exactly one question bank.** UC-03 shipped provisional `ext_questions` /
`ext_quiz_configurations` / `ext_enrolments` projection tables so it could run before the other two
existed. All three are **gone**, replaced by real adapters.
`test_no_provisional_stand_ins_for_a_merged_capability_remain` fails if any comes back.


UC-07 owns `qk_*` and the `/api/v1/attempts/{id}/coaching/…` and `/api/v1/coaching/…` surfaces. It owns
no attempt, no score, no verdict and no report, and cannot change any of them: every port it has onto
the other capabilities is read-only.

---

## 2. The shared kernel — owned by no capability

`app/core/` holds only what all three must agree on, plus plumbing:

| Module | Holds | Why shared rather than duplicated |
| ------ | ----- | --------------------------------- |
| `question_types.py` | `QuestionType`, `QuestionStatus`, `QuestionPresentation`, labels, canonical order | Three vocabularies is exactly the defect the merge existed to fix. UC-01 had `mcq`/`short_answer`; UC-02 had the five types; UC-03 had its own copy of the five |
| `coercion.py` | `to_int`, `to_number`, `is_blank`, `trimmed`, `truthy`, `round4`, `parse_enum` | Every validator parses the same messy inputs. Two implementations meant a `"10"` one accepted the other could reject |
| `time.py` | `Clock`, `SystemClock`, `FixedClock`, `utcnow`, `ensure_utc`, `parse_instant`, `to_iso` | UC-03's injected clock, generalised. "Every timestamp is UTC" was a convention scattered across call sites; now it is enforced, and a naive datetime is an error rather than an assumption |
| `errors.py` | `AppError` hierarchy, `FieldIssue`, `PLATFORM_ERROR_CODES` | One error taxonomy for one API |
| `schemas.py` | `CamelModel`, `ErrorResponse`, `PageMeta` | The error envelope is a property of the API, not of a capability |
| `deps.py` | `DbSession` | Was redeclared identically in six routers |
| `config.py`, `logging.py`, `exception_handlers.py`, `security.py` | Settings, structured logging, handlers, credential primitives | Cross-cutting infrastructure |

**Vocabulary is shared; rules are not.** `question_types.py` names the five types and says nothing
about them. That a `SINGLE_CHOICE` needs exactly four options, that only `ACTIVE` is deliverable — that
stays in `question_bank/domain/`. The pass-mark range and the exam time-limit rule stay in
`quiz_configuration/domain/rules.py`. When an answer counts as complete stays in
`attempt_delivery/domain/`. A shared kernel that acquired rules would become a fourth capability that
nobody owns.

Also shared: `app/db/` (base, session, metadata, the `UtcDateTime` type) and
`app/modules/identity/` (the authentication seam).

### The boundaries are enforced, not documented

`backend/tests/test_architecture.py` parses the real import statements and fails on:

* the question bank importing any other capability (**never** — it must stay independently deployable);
* any cross-capability import outside an `integration/` package;
* an adapter reaching into another capability's `api/` or `csv_import/` package;
* `app/core/` importing any capability;
* a `domain/` package importing FastAPI, SQLAlchemy or `app.db`;
* a second definition of `Clock`, `to_int`, `QuestionType`, `ErrorResponse` or `DbSession`;
* a direct `datetime.now(...)` call outside `app/core/time.py`;
* a second attempt model, or a surviving `ext_*` stand-in.

Verified negatively too: injecting a deliberate cross-capability import makes the suite fail.

---

## 3. Every seam between the capabilities

Every one is a `typing.Protocol` port with exactly one adapter behind it, and every adapter is the
only file on its side that imports the other capability.

| Consumer | Port | Adapter | Direction |
| -------- | ---- | ------- | --------- |
| UC-01 | `ports.QuestionBankPort` | `integration/question_bank_adapter.py` | → UC-02 |
| UC-01 | `ports.AttemptStatisticsPort` | `integration/attempt_statistics_adapter.py` | → UC-03 |
| UC-03 | `integration/uc01/port.py` | `integration/uc01/configuration_adapter.py` | → UC-01 |
| UC-03 | `integration/uc02/port.py` | `integration/uc02/question_bank_adapter.py` | → UC-02 |
| UC-03 | `integration/enrolment/port.py` | `integration/enrolment/platform_adapter.py` | → platform |
| UC-03 | `integration/submission_dispatch/port.py` | `app/composition.py::ResultsPipeline` | → the result chain |
| UC-04 | `integration/attempt_delivery/port.py` | `integration/attempt_delivery/attempt_adapter.py` | → UC-03 |
| UC-04 | `integration/question_bank/port.py` | `integration/question_bank/answer_key_adapter.py` | → UC-02 |
| UC-05 | `integration/scoring/port.py` | `integration/scoring/result_adapter.py` | → UC-04 |
| UC-05 | `integration/attempt_delivery/port.py` | `integration/attempt_delivery/attempt_policy_adapter.py` | → UC-03 |
| UC-05 | `integration/certificate/port.py` | `integration/certificate/local_adapter.py` | → certificate service |
| UC-05 | `integration/cpd/port.py` | `integration/cpd/local_adapter.py` | → CPD system |
| UC-06 | `integration/scoring/port.py` | `integration/scoring/score_adapter.py` | → UC-04 |
| UC-06 | `integration/certification/port.py` | `integration/certification/outcome_adapter.py` | → UC-05 |
| UC-06 | `integration/question_bank/port.py` | `integration/question_bank/content_adapter.py` | → UC-02 |
| UC-07 | `integration/uc03.py` | `integration/uc03_adapter.py` | → UC-03 |
| UC-07 | `integration/uc04.py` | `integration/uc04_adapter.py` | → UC-04 |
| UC-07 | `integration/uc06.py` | `integration/uc06_adapter.py` | → UC-06 |
| UC-07 | `integration/llm.py` | `integration/llm_anthropic.py` (unbound by default) | → the AI provider |
| UC-07 | `integration/knowledge_gaps.py` | `repositories/sqlalchemy.py` | → the knowledge-gap store |
| UC-07 | `integration/activity.py` | `repositories/sqlalchemy.py` | → the activity pipeline |

UC-02 has **no** dependency on any other capability. It does not import them and does not know they
exist.

### UC-01 → UC-02: how many questions are there?

```python
class QuestionBankPort(Protocol):
    def available_by_type(self, scope: BankScope) -> dict[QuestionType, int]: ...
    def resolve_topics(self, topic_ids: Sequence[str]) -> list[TopicRef]: ...
```

| Method | Answers | Used by |
| ------ | ------- | ------- |
| `available_by_type(scope)` | How many questions of each type can a future quiz use? | Capacity validation, the admin screen, the rules summary |
| `resolve_topics(ids)` | Do these topics exist, and what are they called? | Freezing a version's topic scope |

Two methods, and it used to be five. UC-01's port also declared `draw`,
`pin_questions_to_attempt` and `questions_for_attempt` — for the attempt layer UC-03 replaced. They
were **removed** rather than left in place: a port is a statement of what its owner requires, and three
methods nobody calls make it state something untrue and leave a future adapter author implementing dead
weight.

Everything the adapter builds comes from the bank's own
`delivery_service.deliverable_conditions()`, so *"only ACTIVE questions count"* is enforced once, by
the query builder, rather than restated by each caller.

### UC-01 → UC-03: how many attempts has this learner used?

```python
class AttemptStatisticsPort(Protocol):
    def count_by_configuration_version(self, version_ids: Sequence[int]) -> dict[int, int]: ...
    def count_for_learner(self, quiz_id: int, learner_id: str) -> int: ...
    def find_open_for_learner(self, quiz_id: int, learner_id: str) -> OpenAttempt | None: ...
```

This is what lets UC-01 keep two of its own requirements — the rules summary reports remaining
attempts, and the version history reports how many attempts locked onto each version — without owning
attempts. The adapter reads `qd_attempts`; UC-01's domain never sees an attempt model. Read-only by
construction: there is no method here that creates, modifies or submits anything.

`count_for_learner` counts attempts in **any** state, because every started attempt consumes the
allowance — otherwise abandoning and restarting would bypass the maximum-attempts rule.

### UC-03 → UC-01: which configuration am I locked to?

```python
class QuizConfigurationPort(Protocol):
    def get_quiz_availability(self, quiz_id: str) -> QuizAvailability | None: ...
    def get_active_configuration(self, quiz_id: str) -> QuizConfigurationVersion | None: ...
    def get_configuration_version(self, version_id: str) -> QuizConfigurationVersion | None: ...
```

The adapter translates three things, and only in that one file:

* **ids** — UC-01 numbers courses, quizzes and versions; UC-03 treats them as opaque strings;
* **units** — UC-01 stores a time limit in minutes, UC-03 works in seconds;
* **shape** — UC-01's per-type quotas are optional, so the adapter emits either quotas *or* an
  allowed-types list, never both, which is what UC-03's selection expects.

UC-01's own `deliveryMode` (`practice` / `assessment` / `exam`) has no meaning to UC-03, so it is
passed through in `extra` rather than dropped — visible in the attempt's stored snapshot for anyone
diagnosing an attempt, and ignored by the delivery logic.

### UC-03 → UC-02: give me a paper, and here is what I gave out

```python
class QuestionBankPort(Protocol):
    def find_eligible_questions(self, query: QuestionQuery) -> list[BankQuestion]: ...
    def get_questions_by_ids(self, ids: Sequence[str]) -> list[BankQuestion]: ...
    def record_delivery(self, attempt_ref, delivered, learner_ref=None) -> None: ...
```

Reads are the bulk of it. Two details in the adapter are worth knowing:

* `get_questions_by_ids` is deliberately **unfiltered by status**, so an in-flight or historical
  attempt can always be reconstructed — including questions retired since it started.
* UC-02 models a `SCENARIO` as a vignette plus one question; UC-03 models it as a stem plus
  sub-questions. UC-02's shape maps onto exactly one single-choice sub-question, and that mapping lives
  only in the adapter.

**Why there is a write.** `record_delivery` tells the bank that a question was delivered. That is a
fact about UC-02's *own* content, and three of its behaviours read it: per-question usage counts, its
refusal to hard-delete a question that has been used, and its historical attempt report. UC-03's frozen
snapshot answers a different question — "what exactly did this learner see" — so the two records are not
duplicates; each capability keeps the record its own rules depend on.

The write is **idempotent per attempt** (clients retry attempt creation) and **never fatal**: if it
fails it is logged and swallowed, because the learner already has a valid attempt with frozen
questions, and taking that away to protect a usage count would be the wrong trade.

### UC-03 → platform: is this learner enrolled?

`EnrolmentPort` over `qa_enrolments`, a placeholder. Which statuses permit an attempt is policy, so it
lives in `app/modules/identity/enums.py::ATTEMPT_ELIGIBLE_ENROLMENT_STATUSES` rather than in the
adapter — one place to change when the company's rule differs.

### UC-03 → the result chain: here is a completed attempt

`SubmissionDispatchPort` was written for a downstream that did not exist yet, and shipped with a no-op
default. UC-04, UC-05 and UC-06 are that downstream, and wiring them in was the whole of the
integration: `app/composition.py::ResultsPipeline` implements the port as *score → gate → feedback*, and
`create_app` substitutes it for the no-op. **UC-03's submission service is unchanged** — not adapted,
not extended; the seam it was built with was the right shape.

Two properties of that wiring are load-bearing.

**It runs after the commit.** UC-03 commits the attempt and freezes its answers *before* calling the
dispatcher, so the chain reads durable data and a failure anywhere in it cannot undo a submission.

**It cannot raise.** Every stage is guarded and every failure is recorded on that stage's own row. The
worst case a learner sees is "submitted, score pending, retry available". A caller that *wants* to know
whether a retry worked uses the per-stage endpoint, which reports the failure instead of swallowing it.

`create_app` only substitutes the pipeline when the dispatcher is still the documented no-op default. A
caller that supplied its own — a test driving a transient downstream failure, a deployment routing
submissions elsewhere — keeps it, and that is why UC-03's 241 tests never run the chain.

### UC-04 → UC-03: what did this learner answer?

`AttemptSourcePort` (`scoring/integration/attempt_delivery/`) answers with a submitted attempt: its
locked configuration snapshot, its frozen questions and its final answers. Read-only by construction —
there is no method on it that could create, time, unlock or re-submit an attempt.

Learner scoping lives in the port rather than in each route handler: it takes an optional `learner_id`
and answers `None` when the attempt belongs to somebody else, so "a learner sees only their own result"
is enforced once, in a query, exactly as UC-03 does it in its repositories.

`locked` is a field on the contract type rather than a status string UC-04 interprets, so UC-04 never
learns UC-03's lifecycle vocabulary.

### UC-04 → UC-02: what was the answer key for the version delivered?

`AnswerKeyPort` (`scoring/integration/question_bank/`) resolves the marking data — the marks, the
configured marking policy, the deduction per incorrect selection, the correct options, the correct
sequence, the scenario's primary answer — for the exact `(question_id, version)` the attempt was
delivered, from `qb_question_snapshots`.

Reading the *snapshot* rather than the live question is what makes a score reproducible: the snapshot
for version *n* never changes, so the same attempt scores the same today, after tomorrow's edit, and
after the question is retired. When a snapshot cannot be read at all, UC-04 falls back to the answer key
UC-03 froze onto the attempt, and records which copy it used on every question score. A question with no
usable key from either source is reported as `MISSING_ANSWER_KEY` rather than scored zero.

One translation lives here and nowhere else: UC-02's authored scoring strategy
(`ALL_OR_NOTHING` / `PARTIAL_CREDIT` / `PARTIAL_CREDIT_WITH_PENALTY`) becomes UC-04's **marking policy**
(`EXACT` / `PARTIAL` / `PARTIAL_WITH_DEDUCTION`) in
`scoring/integration/marking_policy.py`. UC-04's domain names its own policy rather than importing
UC-02's vocabulary — UC-02's enums module says plainly that its scoring strategies are its own business
— and rather than declaring a rival copy of it, which is the duplication this repository exists to
avoid.

**A note on two graders.** UC-02 has a grader of its own (`question_bank/domain/grading.py`) for the
bank's historical usage report, and it honours the authored strategy for every type — including partial
credit for a drag-to-order response. UC-04's rule for drag-to-order is exact-sequence-only. They are not
two implementations of one rule: UC-02's is authoritative for the bank's report, UC-04's for an
attempt's result, and neither reads the other.

### UC-05 → UC-04: what did this attempt score?

`ScoreResultPort` answers with the result and a `confirmed` flag. UC-05 gates on `confirmed` rather than
on a status string, so UC-04 keeps ownership of what "confirmed" means, and a pending score is refused
with `409 RESULT_NOT_CONFIRMED` (retryable) instead of being gated on marks nobody stood behind.

### UC-05 → UC-03: what were this attempt's rules, and how many attempts are left?

`AttemptPolicyPort` answers with the pass mark and maximum attempts **from the attempt's own frozen
configuration snapshot**, the learner's attempt count, and the frozen course and quiz names a
certificate and a CPD record have to carry. There is no method that could return the quiz's *current*
pass mark, which is why reconfiguring a quiz cannot move the bar under an attempt already sat.

Attempts-used is UC-03's count, read through the port — the same number UC-01's rules summary and UC-03's
eligibility check report. There is one attempt counter in the system, and UC-05 does not keep a second.

### UC-05 → the certificate service

`CertificateServicePort`. Same transient/permanent split as `SubmissionDispatchPort`, deliberately: one
failure vocabulary for outbound calls across the whole system. A `TransientCertificateError` leaves the
certificate `PENDING` and retryable; any other exception marks it `FAILED` with the reason. Neither
touches the score or the verdict, because the outcome is committed before the port is called.

Today's implementation (`certificate/local_adapter.py`) allocates a deterministic certificate number
from the attempt id. It is the honest local implementation of the port rather than a simulation: no
pretend PDF, no fake latency, no random failures. Because the number is derived from the attempt, a
retry cannot even look like a new certificate — and duplicate prevention does not rest on that anyway,
because the partial unique index on `qg_certificates` is the guarantee.

### UC-05 → the CPD system

`CpdSyncPort`, carrying exactly four facts: attempt date, score, pass/fail, course name. Keeping the
contract that narrow is the point — CPD records professional development activity, not quiz content, so
it has no business receiving answers or question text. A failure leaves a `PENDING` row; the quiz result
is untouched by construction, because nothing on this port can reach a score or an outcome.

### UC-06 → UC-04 and UC-05: what happened?

`ScoreDetailPort` and `OutcomePort`. UC-04's per-question rows already carry a frozen copy of the
question text, the learner's answer, the correct answer and the per-option mark contributions — frozen
when the attempt was scored — so a report assembled from them says the same thing in five years' time
as it does today. UC-06 reads neither UC-03 nor the live question bank for any of it.

### UC-06 → UC-02: the explanation and the lesson reference

`QuestionContentPort`. Two of the six things a feedback item must show are *authored* rather than
computed, and both are read per question **version** from `qb_question_snapshots`, so a later edit cannot
change a report — belt and braces, since a generated report is persisted in full and frozen by a trigger
anyway.

On lesson references, plainly: **the question bank has no lesson column today.** What it does have,
required on every question by its own policy, is at least one **topic**, frozen by name into each
version snapshot. That is the closest truthful thing to a lesson reference the system holds, so the
adapter returns it labelled as what it is (`"Topic: Evacuation"`). When the company's LMS supplies real
lesson identifiers, this adapter is what changes: UC-06 asks for a lesson reference and does not care
where one comes from. Where nothing can be resolved, the report uses its defined fallback — it never
invents one.


### UC-07 → UC-03, UC-04 and UC-06: what did this learner get wrong?

UC-07 reads three capabilities and writes to none of them. All three adapters are read-only by
construction — they issue `SELECT`s and there is no path from any of them to changing an attempt, a
mark or a report.

```python
class AttemptProvider(Protocol):       # → UC-03
    async def get_attempt(self, attempt_id: str) -> AttemptContext | None: ...
    async def get_delivered_questions(self, attempt_id: str) -> tuple[DeliveredQuestion, ...]: ...
    async def get_learner_answers(self, attempt_id: str) -> tuple[LearnerAnswer, ...]: ...

class ScoringResultProvider(Protocol):  # → UC-04
    async def get_score(self, attempt_id: str) -> AttemptScore | None: ...

class FeedbackProvider(Protocol):       # → UC-06
    async def get_attempt_feedback(self, attempt_id: str) -> AttemptFeedback | None: ...
```

**The delivered snapshot, not today's question bank.** UC-07 coaches the question *as the learner saw
it* — the same prompt, the same options, the same order — read from `qd_attempt_questions`. Coaching a
learner about a question they never saw would be worse than no coaching.

**UC-04 decides what "wrong" means.** Which questions enter the coaching queue is entirely
`QuestionOutcome`; UC-07 has no rule that could disagree with the score the learner was shown. The
translation between UC-04's five outcomes and UC-07's four lives in one table in
`integration/uc04_adapter.py` — including the decision that `PARTIALLY_CORRECT` is coachable and
`UNANSWERED` is not. If a deployment wants blanks coached, UC-04 is where that belongs.

**UC-06 is the release gate.** Coaching is a conversation *about the feedback*, so the report must be
`GENERATED` first. Offering coaching on a result the learner has not been shown would be backwards.

#### The one seam that carries poison on purpose

Two of these adapters hand over the answer key **deliberately**:

* `QuestionResult.answer_key` — assembled from UC-04's frozen correct-answer display, per-option
  breakdown and authored explanation;
* `QuestionFeedback.explanation`, `.correct_answer_text`, `.correct_option_ids`, `.metadata` — UC-06's
  record as UC-06 actually produced it.

Neither is an oversight, and no service reads either. The only code that touches them is
`domain/sanitizer.py::forbidden_values`, which uses them to build the list of values that must **not**
appear in the coaching context — the opposite of consuming them. An adapter that helpfully stripped
them would leave the sanitiser's guarantee untestable, which is the opposite of the goal.

Three stages then stand between that material and the model:

1. **Construct by allow-list.** `SafeCoachingContext` has no field capable of holding a correct answer,
   an answer key or an explanation. It is a whitelist that never reads them, not a filter that removes
   them — so an upstream module that starts returning a new answer-bearing field cannot leak it.
2. **Scrub narrative text.** Free text can carry an answer without a field being named after it. Exact
   answer-bearing values are removed from narrative fields, as are "the correct answer is …" spans.
3. **Verify, and fail closed.** The finished payload is walked for any key whose name suggests an
   answer key and any surviving answer-bearing value. A finding raises `AnswerKeyContaminationError`
   and coaching is refused for that question — never delivered with a smaller leak.

Stage 3 should never fire, which is exactly why it is worth having: if it does, an upstream change has
broken an assumption, and the right response is to stop rather than strip harder.

Why the *full option set* is still shown: the correct option's text is necessarily one of the options
the learner saw. What leaks is not its presence among the choices but anything that *distinguishes* it
— a correctness flag, a re-ordering, a per-option mark, a subset. None of those has a field in the
context, delivered positions are copied verbatim, and the list is copied whole.

### UC-07 → the AI provider

```python
class CoachingLLM(Protocol):
    async def is_available(self) -> bool: ...
    async def generate_response(self, request: CoachingRequest) -> CoachingCompletion: ...
```

`integration/llm_anthropic.py` is the only file in the system that speaks to a provider, and it is
bound **only** when `COACHING_LLM_PROVIDER` and `COACHING_LLM_API_KEY` are both set. With either empty
— the default, and what a stock deployment runs — the port falls back to `UnconfiguredCoachingLLM`,
which reports coaching unavailable and raises on generation.

That default is the important one. An implementation must **never** return a placeholder, a cached
reply or an apology dressed as teaching: a fabricated reply would be indistinguishable from real
coaching to everyone, including the learner. So every failure raises, and the service turns it into a
controlled "coaching is temporarily unavailable" state:

| Failure | Raised as |
| ------- | --------- |
| Unreachable, refused, rate-limited, non-2xx | `CoachingServiceUnavailableError` (503, retryable) |
| No answer within the timeout | `CoachingTimeoutError` (504, retryable) |
| A 2xx with nothing usable in it | `InvalidCoachingResponseError` (502, retryable) |

**No provider text reaches the learner or the error envelope.** A provider's error body can echo back
the prompt it was sent, so forwarding one would be an error-path route around the sanitiser. Status
codes and error *types* go to the log; bodies are read only to decide which of the three failures it
was, then dropped.

An adapter must also forward `request.context` **as given**. It has already been through the sanitiser;
an adapter that reached back into UC-02 for "a bit more context" would walk straight around the
boundary. If an adapter needs something it does not have, the field belongs in `SafeCoachingContext`
where the sanitiser can vouch for it.

### UC-07 → the knowledge-gap store and the activity pipeline

```python
class KnowledgeGapTracker(Protocol):
    async def record_gap(self, event: KnowledgeGapEvent) -> None: ...

class CoachingActivityLog(Protocol):
    async def record(self, event: CoachingActivityEvent) -> None: ...
```

Both are satisfied today by `qk_knowledge_gaps` and `qk_coaching_activity`, and both stay ports so the
company's own store and pipeline can replace them by changing the line in `CoachingPorts.merged()` that
names them.

Two properties hold whatever is behind them. **Neither can break coaching**: every call is isolated by
the caller, so a sink that is down produces a log line and nothing else — a learner does not lose their
session because an analytics pipeline is unreachable. And **neither can carry content**: the event types
have no field for an answer key, a correct answer or the conversation, which is a stronger guarantee
than a rule saying not to pass them.

A knowledge gap is recorded **once per session**, at creation, enforced by `UNIQUE (session_id)`. A
learner who spends twenty turns on one question has one gap in one topic; counting their persistence as
twenty would make the dataset actively misleading.

---

## 4. Decisions the merge forced

Each of these was a genuine conflict between two working implementations. They are recorded because
the reasoning matters more than the outcome.

### Two things called "delivery mode"

UC-01's `deliveryMode` is `practice` / `assessment` / `exam` — an *authoring* concept that decides
whether a time limit is mandatory. UC-03's was `ONE_AT_A_TIME` / `ALL_AT_ONCE` — a *presentation*
concept that decides how the paper is handed over. Two different things under one name is how a merged
system acquires its first real bug.

UC-03's was renamed **question presentation** everywhere: the field (`questionPresentation`), the
column (`question_presentation`), the error code (`QUESTION_PRESENTATION_VIOLATION`), the enum
(`QuestionPresentation`, now in the shared kernel because UC-01 authors it and UC-03 honours it) and
the UI label.

### Two error envelopes

UC-01/UC-02 used `details` as a **list** of `{field, code, message}` field problems. UC-03 used
`details` as a **dict** of contextual key/values. Both are useful; they are not the same thing.

Resolved by keeping both, named separately: `details` is the field-issue list, `context` is the
machine-readable context. UC-03's `AppError` is a thin adapter over the shared one that maps its
positional `details=` onto `context`, so its factories still read naturally and every failure renders in
the one envelope.

Also resolved: a request that could not be *understood* is `BAD_REQUEST`; one that was understood and
broke a rule carries the capability's own code. UC-03 previously called both `VALIDATION_ERROR`.
`PLATFORM_ERROR_CODES` names the codes the kernel itself can emit, and an error-taxonomy test asserts
nothing outside it plus the capability's own enum is ever returned.

### Two health-check conventions

UC-01/UC-02 had one `/api/health` that checked the database. UC-03 had `/healthz` (no dependencies)
and `/readyz` (a real query). UC-03's distinction is the correct one — a liveness probe that queries
the database restarts healthy processes during a database blip — so the merged service has
`/api/health/live` (touches nothing) and `/api/health` (checks the database, **answers 503** when it
fails, which the original did not).

### Two clocks

UC-01/UC-02 called `datetime.now(UTC)` at each site. UC-03 injected a `Clock`, which is what makes its
timing rules testable without sleeping. The injected clock won and moved to `app/core/time.py`; an
architecture test forbids a direct `datetime.now` anywhere else.

### Two demo seeders

UC-03's `app/seed.py` populated its `ext_*` tables. With those gone, `backend/scripts/seed.py` — which
seeds the **real** UC-01 and UC-02 — is the only demo seeder. UC-03's dataset was too useful to discard,
so it moved to `tests/support/demo_world.py`, where `test_end_to_end.py` walks the whole lifecycle
against a dataset nobody tuned for the assertion at hand.

---

## 5. Contracts that changed at merge time

Breaking, and each with a reason:

| Change | Why |
| ------ | --- |
| `questionTypes[].type` takes the five uppercase values, not `mcq` / `short_answer` | One vocabulary is the point of the integration. `mcq` maps to `SINGLE_CHOICE`; `short_answer` had no counterpart and was removed, because the bank cannot author, validate or score one |
| Field errors are `error.details` (`{field, code, message}`), not `error.fieldErrors` | Two error envelopes in one API is the inconsistency the merge had to remove; `details` also adds a machine-readable `code` |
| UC-03's contextual data moved from `error.details` to `error.context` | `details` is the field-error list. Two meanings for one key is not survivable |
| A malformed request is `BAD_REQUEST`, not `VALIDATION_ERROR` | Clients act differently on "I cannot read this" and "what you asked for is not allowed" |
| **`POST /api/quizzes/{id}/attempts` and its siblings are gone** | UC-03 owns attempts. Use `POST /api/v1/attempts` |
| `/healthz` and `/readyz` are `/api/health/live` and `/api/health` | One health contract for one service |
| Tables are prefixed: `users` → `qa_users`, `courses`/`quizzes` → `qc_*`, UC-03's tables → `qd_*` | `users`, `courses`, `quizzes` and `attempts` are the names most likely to collide with the company's own schema |
| `qc_attempts` is dropped by migration `f2edce6a1ae0` | One owner of attempts |
| `maxAttempts` above 50 reports one "between 1 and 50" message | One numeric-bounds helper for every setting, so messages cannot diverge per field |

Unchanged: every UC-02 URL, method, status code, request body and response body. Every UC-03 URL,
method and status code except the two health probes.

---

## 6. The company's systems — tomorrow

### The database

One line:

```bash
DATABASE_URL=postgresql+psycopg://user:password@host:5432/quizagent
```

then `alembic upgrade head`. See [DATABASE.md](DATABASE.md) for the checklist and the portability
guarantees. No business rule, service or domain module changes: they depend on Protocols.

| Interface | Today |
| --------- | ----- |
| `quiz_configuration.repositories.QuizRepository` | `SqlAlchemyQuizRepository` |
| `quiz_configuration.repositories.ConfigurationVersionRepository` | `SqlAlchemyConfigurationVersionRepository` |
| `attempt_delivery.repositories.*` (attempts, questions, answers, flags, submissions) | their SQLAlchemy implementations |
| `identity.repository.UserRepository` | `SqlAlchemyUserRepository` |

`quiz_configuration/context.py::build_context` and
`attempt_delivery/container.py::AppContext` are where they are assembled. Pointing either at company
implementations is a change to one function.

### Identity

`app/modules/identity/security.py::resolve_principal` resolves a bearer token to a `Principal` with a
role. All three capabilities depend only on that. Replacing that one function with the platform's real
dependency is the whole of the identity integration — and `qa_users` can then be dropped.

**No credentials are hard-coded and nothing secret is committed.** The development tokens live in the
seed script and are visible only under `ENVIRONMENT=development` or `test`. No secret, credential,
learner answer or answer key is written to a log.

### Enrolment

`qa_enrolments` is a placeholder behind `EnrolmentPort`. Replacing it means one adapter, plus deciding
which of the company's statuses permit an attempt — that list is
`ATTEMPT_ELIGIBLE_ENROLMENT_STATUSES`.

### Marking

Marking is no longer downstream: UC-04 does it, in this system, against frozen data. What *is* still
downstream is anything else that wants to know a submission happened — a data warehouse, an external
gradebook. `SubmissionDispatchPort` is still that seam, and a deployment that needs both passes its own
dispatcher as `ResultsPipeline`'s `inner`, which runs first and whose reference is reported back to
UC-03 unchanged.

### The AI coaching provider

`CoachingLLM` is where teaching leaves this system, and `integration/llm_anthropic.py` is the whole of
the provider integration — one file, one line in `CoachingPorts.merged()`. A different vendor is a new
adapter beside it; no domain rule, service or test moves.

Two things a replacement has to honour:

* **raise, never substitute.** The three failure classes in the table above are the contract. Returning
  a placeholder would put invented teaching in front of a learner, which is the one failure mode UC-07
  exists to prevent.
* **`is_available` must be cheap and must not raise.** It is on the path of every eligibility read,
  including the one a result screen makes before rendering a button. The shipped adapter answers from
  configuration and from its own recent failures rather than calling out, so an outage degrades to
  "coaching unavailable" quickly instead of making every learner wait for a timeout.

The API key is read from the environment only. It is never committed, never logged, and never returned
by any endpoint — `GET /api/health` reports only whether a provider is *configured*, never which key.

### Certificates

`CertificateServicePort` is where a certificate leaves this system. The company's service replaces
`LocalCertificateService` in one line of `ResultsPorts.merged()`, and needs to honour two things:

* raise `TransientCertificateError` for anything worth retrying — anything else is recorded as a
  permanent failure and a human has to look at it;
* respect the `idempotency_key` (stable per attempt), because a retry the service already received must
  return the same document rather than mint a second.

The obligation is durable before the port is called, so an outage delays a certificate rather than
losing one, and `POST /outcome/certificate/retry` is the supported way to drive it again.

### CPD

`CpdSyncPort`, same shape, four fields: attempt date, score, pass/fail, course name. Raise
`TransientCpdError` for a retryable failure. Nothing on this port can affect a quiz result, and that is a
property of the transaction boundary rather than a promise.

### Lesson references

UC-06 resolves a lesson reference from the question's frozen topic names today, because the question
bank has no lesson field. When the company's LMS supplies real lesson identifiers, implement
`QuestionContentPort` against it and point `ResultsPorts.merged()` at the new adapter. Nothing else in
UC-06 changes: it asks for a lesson reference and reports the defined fallback when there is none.

### Placeholders, in one table

| Placeholder | Replaced by | Read through |
| ----------- | ----------- | ------------ |
| `qa_users` | The company identity provider | `identity/security.py::resolve_principal` |
| `qa_enrolments` | The company enrolment system | `EnrolmentPort` |
| `qc_courses` | The company course catalogue | `SqlAlchemyQuizRepository` |
| `qc_quizzes` | The company quiz catalogue | `SqlAlchemyQuizRepository` |
| local submission recorder | Anything that also wants submissions | `SubmissionDispatchPort` |
| `LocalCertificateService` | The company certificate service | `CertificateServicePort` |
| `LocalCpdSyncService` | The company CPD system | `CpdSyncPort` |
| topic-derived lesson references | The company LMS's lesson identifiers | `QuestionContentPort` |

`qc_configuration_versions`, `qc_configuration_version_*` and every `qd_`, `qr_`, `qg_` and `qf_` table
are **ours to keep**: they hold configuration history, attempt state, scores, verdicts and reports that
no existing company table has.

### What a future capability must not do

* Read a quiz's *current* active configuration for an existing attempt. Always resolve from the
  attempt's own `configuration_snapshot`.
* Read a live question to render or score an attempt. Always read the frozen snapshot.
* Filter retired questions itself. Go through the port; a hand-rolled status query is how a retired
  question eventually reaches a learner.
* Recompute a score, a verdict or a report that has been confirmed. Read the stored one — the database
  will refuse the update anyway.
* Publish a number derived from data it could not read. Record the anomaly and leave the stage pending;
  `PENDING_SCORE` exists precisely so nothing has to guess.
* Generate wording a human did not author. A missing explanation or lesson reference has a defined
  fallback for exactly this reason.
* Trust a client-supplied time for anything. Report the skew, never act on it.
* Keep a second copy of an attempt, or of the question-type vocabulary. The architecture tests will
  fail, and they are right to.
