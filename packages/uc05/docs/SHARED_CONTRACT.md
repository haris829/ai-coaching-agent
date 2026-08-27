# UC-05 — Published contract

For an integration engineer who has not read this code and is holding a
component we have never seen.

Every field below is marked **[SPECIFIED]** — fixed by the platform contract we
were given, and safe to depend on — or **[ASSUMED]** — invented by us, with a
row in [`assumptions.md`](./assumptions.md). Treat every **[ASSUMED]** field as
negotiable and every **[SPECIFIED]** field as not.

UC-05 owns **Socratic dialogue only**. It does not create sessions, assemble
learner context, link lessons, protect quizzes, reason over case files, produce
gap reports, track streaks, write summaries, or rate feedback. Several of those
consume what UC-05 writes; §8 lists where they attach.

---

## 1. Session identity

UC-05 **receives** an opaque `session_id: string` and **never creates one** on a
production path. It does not own session lifecycle, does not validate that a
session exists, and attaches no meaning to the value beyond equality.

A development-only endpoint (`POST /api/v1/socratic/dev/sessions`) mints an id
for standalone runs. It is gated by `ALLOW_DEV_SESSION_IDS`, which defaults to
`false`, and returns `404` when off. Do not enable it in a deployed
environment. **[SPECIFIED]**

`user_id: string` is resolved **server-side** from the transport on every
request and is never read from a request body. A body containing `user_id` is
rejected with `422`. **[SPECIFIED]**

---

## 2. The interaction log record UC-05 writes

Written to `InteractionLogRepository.append()`. One record per system response.

```
InteractionLogRecord {
  interaction_id   : string (uuid4)            [SPECIFIED]
  session_id       : string                    [SPECIFIED]
  user_id          : string                    [SPECIFIED]
  asked_at         : datetime (UTC, ISO 8601)  [SPECIFIED]
  question_text    : string                    [SPECIFIED]
  topic_tag        : string                    [SPECIFIED] (value derivation ASSUMED — A-TOPIC-TAG)
  naric_level      : NaricLevel                [SPECIFIED]
  response_id      : string (uuid4)            [SPECIFIED]
  mode             : "socratic"                [SPECIFIED] literal, never varies
  dialogue_id      : string (uuid4)            [SPECIFIED]
  exchange_number  : integer >= 1              [SPECIFIED] (numbering rule ASSUMED — A-EXCHANGE-NUMBERING)
  response_kind    : ResponseKind              [SPECIFIED]
  resolution       : Resolution | null         [SPECIFIED] (triggers ASSUMED — A-RESOLUTION-TRIGGERS)
  follow_up_of     : interaction_id | null     [SPECIFIED]
  rating_state     : "pending" | "rated"       [SPECIFIED]
}
```

**`rating_state` is always written as `"pending"` and is never changed by
UC-05.** Rating belongs to another component.

**`follow_up_of`** chains the records of one dialogue in order. The first record
of a dialogue has `null`; every later record points at the previous record's
`interaction_id`.

**`resolution` is `null` on every record except the one that closes the
dialogue.** An exit *offer* resolves nothing and carries `null`.

### `response_kind` — complete vocabulary **[SPECIFIED]**

| Value | Emitted when | Carries a four-part answer |
|---|---|---|
| `guiding_question` | The first response in a dialogue. | No |
| `acknowledgement_and_guiding_question` | Every later guiding response: neutral acknowledgement plus a question. Also used for a redirect, a resumed dialogue after a declined exit, and (see A-CLOSURE-KIND) the closing turn when the learner reasoned it out. | No |
| `exit_offer` | The learner asked for the answer directly and is being offered the exit. | No |
| `direct_answer` | A confirmed exit on request, a frustration exit, or a response produced while Socratic mode was off. | **Yes** |
| `capped_answer` | The five-exchange cap was reached, or loop detection forced it early. | **Yes** (plus a reasoning chain) |

### `resolution` — complete vocabulary **[SPECIFIED]**

`null` means the dialogue is still open.

| Value | Trigger **[ASSUMED — A-RESOLUTION-TRIGGERS]** | Terminal state | Answer delivered |
|---|---|---|---|
| `learner_reasoned` | The learner stated the conclusion themselves. | `resolved` | No |
| `capped` | The cap was reached without resolution. | `capped` | Yes, with reasoning chain |
| `exited_on_request` | The learner asked for the answer **and confirmed** the exit offer. | `exited_for_question` | Yes |
| `exited_on_frustration` | The learner explicitly stated they were stuck. | `exited_for_question` | Yes |
| `loop_detected` | The same guiding question was generated twice; the cap was forced early. | `capped` | Yes, with reasoning chain |
| `abandoned` | Socratic mode was toggled off while the dialogue was in flight. | `abandoned` | No |

**Exactly four of these six accompany a direct answer**: `exited_on_request`,
`exited_on_frustration`, `capped`, `loop_detected`. There is no fifth path to a
direct answer inside Socratic mode. This is enforced structurally by the
transition table and asserted exhaustively in
`tests/test_never_reverts.py::test_the_transition_table_admits_no_fifth_answer_path`.

`loop_detected` is deliberately **distinct** from `capped`. They mean different
things for analysis: one says the learner needed more than five steps, the other
says the generator stopped making progress.

---

## 3. The dialogue record retained for the improvement pipeline

Written to `DialogueRepository.save()`. One per question. Retained in full.

```
Dialogue {
  dialogue_id         : string (uuid4)                      [ASSUMED]
  session_id          : string                              [SPECIFIED]
  user_id             : string                              [SPECIFIED]
  question_text       : string                              [SPECIFIED]
  topic_tag           : string                              [SPECIFIED]
  naric_level         : NaricLevel                          [SPECIFIED]
  naric_level_source  : "retrieved" | "default"             [SPECIFIED]
  explanation_profile : "basic"|"intermediate"|"advanced"   [SPECIFIED]
  practice_area       : string | null                       [SPECIFIED]
  source_status       : { <source_name>: SourceStatus }     [SPECIFIED]
  state               : DialogueState                       [SPECIFIED]
  resolution          : Resolution | null                   [SPECIFIED]
  exchange_cap        : integer (default 5)                 [SPECIFIED]
  exchanges           : ExchangeRecord[]                    [ASSUMED]
  prompt_version      : string                              [ASSUMED]
  created_at          : datetime (UTC)                      [ASSUMED]
  updated_at          : datetime (UTC)                      [ASSUMED]
  closed_at           : datetime | null                     [ASSUMED]
  last_interaction_id : string | null                       [ASSUMED]
  loop_matched_exchange : integer | null                    [ASSUMED]
}

ExchangeRecord {                                            [ASSUMED — A-EXCHANGE-DEF]
  exchange_number      : integer >= 1
  guiding_question     : string          # verbatim, as asked
  probing_focus        : string          # what it was probing, as recorded then
  question_fingerprint : string          # normalised identity, for loop analysis
  asked_at             : datetime (UTC)
  learner_messages     : LearnerMessage[]
}

LearnerMessage {                                            [ASSUMED]
  text        : string
  intent      : IntentKind
  received_at : datetime (UTC)
}
```

Notes an integration engineer will care about:

- **The full guiding sequence and every learner response are retained**,
  including messages that did not open an exchange — exit requests, declines,
  off-topic asides. Those are attributed to the exchange that was open when
  they arrived.
- `exchanges_used` is `len(exchanges)`; `exchanges_remaining` is
  `max(0, exchange_cap - exchanges_used)`. An exchange is **opened** when a
  guiding question is emitted (A-EXCHANGE-DEF).
- `prompt_version` records which server-side prompt revision the dialogue ran
  under, so behaviour can be attributed to a prompt change.
- `loop_matched_exchange` names *which* earlier exchange was repeated, not
  merely that one was.

### `DialogueState` — complete vocabulary **[SPECIFIED]**

| State | Meaning | Terminal |
|---|---|---|
| `awaiting_learner_response` | A guiding question is on the table. | No |
| `awaiting_exit_confirmation` | An exit offer is outstanding. | No |
| `resolved` | The learner reached the conclusion. | Yes |
| `capped` | Cap reached, or forced early by loop detection. | Yes |
| `exited_for_question` | Exited for this question, on request or on frustration. | Yes |
| `abandoned` | Mode toggled off mid-dialogue. | Yes |

Terminal states accept no further events. A reply to a closed dialogue is
`409 invalid_state`. There is no "reopen".

---

## 4. The mode state UC-05 persists

```
ModeState {                                                 [ASSUMED — A-MODE-STATE]
  session_id     : string
  enabled        : boolean
  source         : "persisted" | "default"
  owner_user_id  : string | null
  updated_at     : datetime | null
}
```

Socratic mode is **per session**, set by the learner, and persisted so it
survives a page refresh. `source` tells a caller whether the value was read
from the store (`persisted`) or is UC-05's default (`default`).

**Default when unset: `enabled = false`** (A-MODE-DEFAULT). A repository must
return `null` for a session it has never seen and must **not** invent a default
of its own — otherwise two implementations of the port could disagree about
what "unset" means.

`owner_user_id` records the first learner to set a mode on the session, and
UC-05 refuses another user thereafter (A-MODE-OWNER). This exists only because
UC-05 cannot ask a session store it has not been given. A company adapter that
*can* consult the real session store should do so.

### Where to repoint this at the company session store

`uc05/ports/repositories.py::SessionModeRepository` — three methods:

```python
async def get_mode(self, session_id: str) -> ModeState | None
async def set_mode(self, session_id: str, enabled: bool, owner_user_id: str) -> ModeState
```

Implement it, register it under a key, set `SESSION_MODE_REPOSITORY=<key>`. The
in-memory implementation in `uc05/adapters/memory/repositories.py` is the
reference. Nothing else in UC-05 changes — see
[`INTEGRATION.md`](./INTEGRATION.md).

---

## 5. The `LearnerContext` UC-05 expects to receive

UC-05 **does not assemble learner context.** It receives it through
`LearnerContextProvider.get_context(session_id, user_id)`.

```
LearnerContext {
  naric_level        : NaricLevel                        [SPECIFIED]
  naric_level_source : "retrieved" | "default"           [SPECIFIED]
  practice_area      : string | null                     [SPECIFIED]
  source_status      : { <source_name>: SourceStatus }   [SPECIFIED]
}
```

UC-05 reads the `source_status` keys `naric_level` and `practice_area`. Other
keys are permitted and ignored — other components own other sources.

### Rules a provider must honour

| Situation | Required outcome |
|---|---|
| Level retrieved successfully | `naric_level_source = "retrieved"`, `source_status.naric_level = "available"` |
| Upstream returned a value mapping to **no** enum member | `naric_level = LEVEL_5`, `naric_level_source = "default"`, `source_status.naric_level = "invalid"`. **Not** a widened enum, **not** a guess, **not** an exception. |
| Upstream answered and had nothing | `source_status.naric_level = "empty"` |
| Upstream unreachable / refused | raise `ProviderUnavailable` |
| Upstream exceeded the budget | raise `ProviderTimeout` |
| Upstream payload unparseable | raise `ProviderInvalidResponse` |
| Practice area absent | `practice_area = null`, `source_status.practice_area = "empty"`. **Never** a plausible-looking guess. |

### When the provider fails

UC-05 proceeds. It substitutes `LEVEL_5` with `naric_level_source = "default"`,
no practice area, and the failure recorded in `source_status`, then continues
the dialogue. **A context failure never leaves the learner without a
response.** `ProviderInvalidResponse` records status `invalid`;
`ProviderUnavailable` and `ProviderTimeout` record `unavailable`.

Learner context is fetched **once per dialogue**, when it opens, and reused for
every exchange in it (A-CONTEXT-ONCE).

---

## 6. The four-part answer UC-05 emits

On any direct answer — a confirmed exit, a frustration exit, a cap, a loop, or
a response produced while the mode is off — UC-05 emits the platform's four-part
structure as **four discrete fields**. UC-05 does not invent a different answer
format.

```
FourPartAnswer {                                            [SPECIFIED]
  plain_english_explanation : string   # non-empty
  formal_legal_definition   : string   # non-empty
  practical_example         : string   # non-empty
  authority_reference       : string   # non-empty
}
```

All four are required and must be non-blank. **A response missing any part is a
`ProviderInvalidResponse`, never a partial answer.**

At the cap (and on loop detection) the answer is accompanied by a reasoning
chain assembled from the dialogue record:

```
ReasoningChainStep {                                        [ASSUMED — A-REASONING-CHAIN]
  exchange_number      : integer
  guiding_question     : string   # verbatim from the record
  probing              : string   # verbatim from the record
  learner_response     : string | null
  connection_to_answer : string   # composed from recorded fields only
}
```

No generator is consulted when building the chain, so it always reflects what
was actually asked.

---

## 7. Explanation profiles and source status

### Explanation profile mapping **[SPECIFIED, with two ASSUMED groupings]**

| NARIC level | Profile | |
|---|---|---|
| `LEVEL_3` | `basic` | SPECIFIED |
| `LEVEL_4` | `basic` | **ASSUMED — A-PROFILE-4-6** |
| `LEVEL_5` | `intermediate` | SPECIFIED |
| `LEVEL_6` | `intermediate` | **ASSUMED — A-PROFILE-4-6.** Level 6 is an undergraduate law degree, not Masters level, and is deliberately **not** `advanced`. |
| `LEVEL_7` | `advanced` | SPECIFIED |
| `LEVEL_7_PLUS` | `advanced` | SPECIFIED |

Default when no level can be established: `LEVEL_5` → `intermediate`, with
`naric_level_source = "default"`.

### Source status vocabulary **[SPECIFIED]**

| Value | Meaning |
|---|---|
| `available` | The source answered with usable data. |
| `empty` | The source answered and **had nothing**. |
| `partial` | The source answered with some of what was asked for. |
| `unavailable` | The source **did not answer**. |
| `invalid` | The source answered with something that cannot be mapped onto the contract. |

**`empty` and `unavailable` are different states and are never conflated.**
`tests/test_resilience.py::test_empty_and_unavailable_are_not_conflated` asserts
this directly.

---

## 8. Extension points — where behaviour UC-05 does not own attaches

UC-05 writes data; these components consume it. None of them is implemented
here.

| What attaches | Where it attaches | What UC-05 already provides |
|---|---|---|
| **Feedback rating** | `InteractionLogRecord.rating_state` | UC-05 writes `"pending"` and never changes it. The rating component flips it to `"rated"` and stores the rating in its own store. |
| **Gap reports / competency analysis** | `Dialogue.exchanges` + `resolution` | The full guiding sequence, every learner message with its classified intent, and a distinct `resolution` per ending. `loop_detected` vs `capped` is the discriminator that matters here. |
| **Streaks and engagement** | `InteractionLogRecord` stream per session | `asked_at`, `dialogue_id`, `exchange_number`, `resolution`. |
| **Session summaries** | `DialogueRepository.for_session(session_id)` | Every dialogue for a session with state, resolution and full transcript. |
| **Lesson-linked coaching** | `topic_tag` on the record, `LearnerContext.practice_area` | UC-05 records both; it does not resolve a lesson. Supply a `topic_tag` on `POST /questions` and UC-05 uses it verbatim. |
| **Quiz protection** | Upstream of UC-05 | UC-05 has no notion of a quiz. A caller that must not offer Socratic coaching during a quiz simply does not call UC-05, or sets the mode off for that session. |
| **Case-file reasoning** | `question_text` | UC-05 treats the question as opaque text. |
| **Session creation / lifecycle** | `session_id` | UC-05 receives it and never creates one. |
| **Learner-context assembly** | `LearnerContextProvider` port | UC-05 receives context; it never assembles it. |
| **Production authentication** | `CurrentUserProvider` port | UC-05 resolves `user_id` server-side through this port. Replace the header adapter. |
| **Mode indicator UI** | `GET /api/v1/socratic/mode/{session_id}` | UC-05 exposes `enabled` and `source`. It builds no toolbar, toggle or indicator. |
| **Exchange progress UI** | `exchanges: {used, remaining, cap}` on every response | UC-05 exposes the numbers. Rendering is presentation. |

---

## 9. Ports, with exact signatures

All ports are `async`. All are `Protocol` classes in `uc05/ports/`.

```python
class LearnerContextProvider(Protocol):
    async def get_context(self, session_id: str, user_id: str) -> LearnerContext: ...

class GuidingQuestionGenerator(Protocol):
    async def generate(self, dialogue_state: Dialogue, question: str,
                       context: LearnerContext) -> GuidingQuestionResult: ...

class AnswerGenerator(Protocol):
    async def generate(self, question: str,
                       context: LearnerContext) -> FourPartAnswer: ...

class IntentClassifier(Protocol):
    async def classify(self, message: str,
                       dialogue_state: Dialogue) -> IntentResult: ...

class DialogueRepository(Protocol):
    async def save(self, dialogue: Dialogue) -> None: ...
    async def get(self, dialogue_id: str) -> Dialogue | None: ...
    async def for_session(self, session_id: str) -> list[Dialogue]: ...

class SessionModeRepository(Protocol):
    async def get_mode(self, session_id: str) -> ModeState | None: ...
    async def set_mode(self, session_id: str, enabled: bool,
                       owner_user_id: str) -> ModeState: ...

class InteractionLogRepository(Protocol):
    async def append(self, record: InteractionLogRecord) -> None: ...
    async def get(self, interaction_id: str) -> InteractionLogRecord | None: ...
    async def list_for_session(self, session_id: str) -> list[InteractionLogRecord]: ...

class CurrentUserProvider(Protocol):
    async def resolve(self, request: Any) -> str: ...
```

### Error contract **[SPECIFIED]**

An adapter may raise **only** these past its boundary:

| Error | Meaning | `retryable` |
|---|---|---|
| `ProviderUnavailable` | Could not reach, or was refused. | `True` |
| `ProviderTimeout` | Did not answer inside `GENERATION_TIMEOUT_MS`. | `True` |
| `ProviderInvalidResponse` | Answered, but the answer violates the contract. | `False` |

Each carries `port` (the port name, **never** the vendor) and an internal
`detail` that is logged and **never** returned to a client. Anything else
escaping an adapter is a defect, and
`tests/conformance/shared.py::assert_only_contract_errors_raised` is the check.

### `IntentResult` vocabulary

```
IntentResult { kind: IntentKind, matched_phrase: string | null, rule: string }
```

`IntentKind` — the six the platform requires **[SPECIFIED]**:
`substantive_response`, `direct_answer_request`, `exit_confirmation`,
`exit_declined`, `explicit_frustration`, `off_topic`.

Plus two UC-05 adds **[ASSUMED — A-INTENT-VOCAB]**: `casual_difficulty` (so
casual difficulty is a *separable output* from explicit frustration, not folded
into it) and `learner_reasoned_conclusion` (so the `learner_reasoned` resolution
is reachable). A classifier returning only the six works unchanged — the two
extras simply never appear.

`matched_phrase` must come from the classifier's **own configured vocabulary**,
never from the learner's free text, because it is logged. There is deliberately
**no confidence field**: UC-05's contract has no notion of classifier
confidence, and adding one is a contract conversation, not an adapter change.

---

## 10. Configuration

| Variable | Default | Effect |
|---|---|---|
| `GENERATOR` | `fake` | Selects both generator implementations. |
| `LEARNER_CONTEXT_PROVIDER` | `mock` | |
| `INTENT_CLASSIFIER` | `mock` | |
| `DIALOGUE_REPOSITORY` | `memory` | |
| `SESSION_MODE_REPOSITORY` | `memory` | The session-store seam. |
| `INTERACTION_LOG_REPOSITORY` | `memory` | |
| `CURRENT_USER_PROVIDER` | `header` | Replace for production. |
| `SOCRATIC_EXCHANGE_CAP` | `5` | Exchanges per question. |
| `LOOP_SIMILARITY_THRESHOLD` | `0.8` | Loop-detection threshold. |
| `GENERATION_TIMEOUT_MS` | `10000` | **Enforced** via `asyncio.wait_for`. |
| `GENERATION_TARGET_P95_MS` | `3000` | A target: exceeding it is logged, not failed. |
| `ALLOW_DEV_SESSION_IDS` | `false` | Gates the dev session-minting endpoint. |

A provider key with no registered implementation **fails at startup**, naming
the missing key, the variable that selected it, and the file expected to supply
it. There is **no silent fallback to a mock**.

---

## 11. What UC-05 will refuse to do

Worth knowing before you integrate, because these are guarantees rather than
current behaviour:

- It will not produce a direct answer inside Socratic mode except through the
  four permitted resolutions.
- It will not pass through a generator response that is a direct answer, that
  contains a praise term, that restates the learner's question, or that is
  missing a part of the four-part answer. All are `ProviderInvalidResponse`.
- It will not treat a learner message as an instruction. A message asking it to
  abandon Socratic mode is classified as an intent and still has to pass through
  the state machine; at most it produces an exit *offer*.
- It will not return prompt content, system instructions, generator
  configuration, or a provider name to a client.
- It will not return another user's dialogue. Ownership is checked on every
  read.
- It will not write `question_text` or a learner response to an application log.
  The logger raises `DisallowedLogField` if asked to.
- It will not start if a configured provider does not exist.
