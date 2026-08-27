# PLATFORM_CONTRACT.md

The single authority for every data shape shared between UC-01 and UC-10.

Where this document and a component's `docs/SHARED_CONTRACT.md` disagree, this document
wins and the component is wrong. Where this document says **OPEN**, nothing is settled and
no adapter should be written against that row yet.

## What this document is, and what it is not

It is the Phase 1 deliverable of `docs/INTEGRATION_BRIEF.md` §4.1: one register of every
shared concept, its agreed name, type, allowed values, and owning component.

It is **not** a record of decisions taken during the merge. Ten components published ten
overlapping contracts; this register states what each actually emits today — read from the
source, not from the prose — and where they disagree. Brief §12 forbids resolving an
assumption by picking an answer, so a divergence that needs a company answer is recorded as
**OPEN** with a recommendation, and left open.

Every row was verified against code at the path given. Line numbers are as merged.

**Legend**

| Mark | Meaning |
|---|---|
| **AGREED** | All components that use the concept already agree. Safe to wire. |
| **INSTRUCTED** | The brief names the target shape; the change has not landed in code yet. |
| **OPEN** | Components disagree and no authority has chosen. Blocks the adapter. |

---

## 1. Session identity — AGREED (with three dev paths to disable)

| Property | Value |
|---|---|
| Field name | `session_id` |
| Type | opaque `str`, non-empty |
| Format | `sess_<uuid4-hex>` as minted |
| Owner | **UC-01**, exclusively |
| Consumers | all nine others, read-only |

UC-01 mints it in `packages/uc01/uc01/contracts/clock.py:32` and nothing else on a
production path creates one. Every consumer treats it as opaque: UC-07 and UC-09 constrain
it only to non-empty (`NonEmptyStr`, `Field(min_length=1)`), UC-10 documents it as "opaque;
never minted here" at `packages/uc10/uc10/domain/models.py:55`.

**Three dev-mode minting paths exist. All three are config-gated and default to off.**
They must be confirmed unreachable in production configuration (brief §4.1, §11).

| Component | Path | Gate | Default | Minted form |
|---|---|---|---|---|
| UC-05 | `packages/uc05/uc05/application/socratic_service.py:676` | `ALLOW_DEV_SESSION_IDS` | false | `dev-session-<uuid4>` |
| UC-06 | `packages/uc06/uc06/api/app.py:164` | `ALLOW_DEV_SESSION_IDS` | false | `<DEV_SESSION_PREFIX><uuid4hex>` |
| UC-09 | `packages/uc09/uc09_summary/api/app.py:255` | `UC09_ALLOW_DEV_SESSION_MINTING` | false | `dev-sess-<12 hex>` |

UC-02, UC-04 and UC-06 also read `ALLOW_DEV_SESSION_IDS` from one shared name — see §11.

---

## 2. NARIC level — OPEN. This is the largest type conflict in the platform.

Two incompatible representations are in production code today.

| Components | Representation | Verified at |
|---|---|---|
| **UC-01, UC-02** | `int` on the RQF scale, clamped to 3–9 | `packages/uc01/uc01/domain/models.py:108,156`; `packages/uc02/uc02/domain/models/provider_records.py:31,41` |
| **UC-03 – UC-10** | closed string enum `NaricLevel` | `packages/uc03/uc03/domain/enums.py` and the equivalent in each of uc04–uc10 |

And the eight enum users disagree on casing:

| Components | Emitted values |
|---|---|
| UC-03, UC-04, UC-05, UC-06, UC-07, UC-08 | `LEVEL_3` … `LEVEL_7_PLUS` (**uppercase**) |
| UC-09, UC-10 | `level_3` … `level_7_plus` (**lowercase**) |

Why it matters: UC-02 is the implementation behind everyone's `LearnerContextProvider`
port (brief §6). Four components consume that port and seven of them type the level as a
closed enum. An `int` of `7` reaching a field typed `NaricLevel` is a validation error, not
a rounding; an integer `8` has no enum member at all. Nothing fails at build time.

Additionally, UC-02 maps integers 3–9 and clamps anything outside that range
(`packages/uc02/uc02/domain/explanation_mapping.py:36-50`), while every enum user treats an
unmapped value as an **invalid response** — default applied, source marked — never a clamp.
Those two policies produce different learner-visible behaviour from the same upstream bug.

**Agreed target (brief §4.1): closed enum `level_3 | level_4 | level_5 | level_6 |
level_7 | level_7_plus`, lowercase, with a value outside the enum treated as an invalid
response.**

**Recommendation.** Adopt the UC-09/UC-10 lowercase form as canonical, since §4.1 already
requires every emitted enum value to be lowercase; convert UC-03 – UC-08 (six casing
changes); and make the integer-to-enum translation the job of the UC-02 adapter behind
`LearnerContextProvider`, not of any domain model. UC-01 and UC-02's internal integer stays
their business as long as nothing integer-typed crosses a port.

**Do not write the `LearnerContextProvider` adapter until this row closes.** It is the
first adapter the brief's suggested order reaches (§13 step 7) and four components depend
on it.

---

## 3. `naric_level_source` — OPEN (small, mechanical)

| Component | Field | Allowed values |
|---|---|---|
| UC-01 | `naric_level_source` | `naric`, `default`, `default_user_acknowledged` |
| UC-02 | `level_source` | `retrieved`, `default` |
| UC-03 – UC-10 | `naric_level_source` | `retrieved`, `default` |

Two divergences: UC-01 emits `naric` where everyone else emits `retrieved`, and UC-01
carries a third state (`default_user_acknowledged`) that no consumer models. UC-02 also
names the field `level_source` rather than `naric_level_source`.

**Agreed target (§4.1): field `naric_level_source`, values `retrieved | default`.**

UC-01's third state is real behaviour — a learner who was told the default applied and
continued — and it has nowhere to go in a two-value enum. **Decide whether it becomes a
separate boolean on the session record or is dropped.** Do not silently fold it into
`default`: that loses the acknowledgement, which is the only evidence the learner was told.

---

## 4. Explanation profiles — OPEN (three names for one three-way grouping)

| Component | Type | Values |
|---|---|---|
| UC-02 | `ExplanationTemplateId` | `basic`, `intermediate`, `advanced` |
| UC-03 | `ExplanationDepth` | `foundation`, `intermediate`, `advanced` |
| UC-04 – UC-10 | `ExplanationProfile` | `basic`, `intermediate`, `advanced` |

Seven components agree exactly. UC-02 agrees on values but not on the type name; UC-03
diverges on the type name and on one member — `foundation` where the platform says `basic`.

**Casing on this row is now closed.** UC-03's values were lowercased under the §4.2
instruction. The `foundation` vs `basic` naming was *not* part of that instruction and stays
open.

UC-02 additionally publishes a richer profile object — `depth`
(`a_level_equivalent | practitioner_foundation | masters_level`), `terminology_level`,
`assumed_prior_knowledge`, `detail_level` (1–3) — at
`packages/uc02/uc02/domain/models/enums.py`. No other component models these. They are not
in conflict, but the register must say which is canonical for the wire.

**Agreed target: `explanation_profile` with values `basic | intermediate | advanced`.**
UC-03's `foundation` still needs renaming to `basic` — one member, not yet instructed. UC-02's
extra profile attributes stay UC-02-internal unless a consumer asks for them.

### Level to profile mapping — AGREED

| Level | Profile |
|---|---|
| `level_3`, `level_4` | `basic` |
| `level_5`, `level_6` | `intermediate` |
| `level_7`, `level_7_plus` | `advanced` |

Verified identical in UC-04, UC-05, UC-06, UC-08, UC-09, UC-10, and equivalent in UC-02's
integer table (3,4 → basic; 5,6 → intermediate; 7,8,9 → advanced).

**Levels 4 and 6 are an assumption in every component that maps them** — UC-05 marks it
`A-PROFILE-4-6`, UC-09 marks it `A-002` and exposes `ASSUMED_PROFILE_LEVELS`. It is
Decision 6 in `docs/DECISIONS.md` and it is unanswered. The good news: all ten guessed
identically, so confirming the guess costs nothing and correcting it is one table per
component.

---

## 5. Source status vocabulary — OPEN (UC-01 is missing two members)

| Component | Type | Values |
|---|---|---|
| UC-01 | `DependencyState` | `available`, `empty`, `incomplete`, `unavailable` |
| UC-02, UC-04 – UC-10 | `SourceStatus` | `available`, `empty`, `partial`, `unavailable`, `invalid` |

UC-01 has no `partial` and no `invalid`, and adds `incomplete` (which is NARIC-assessment
state — a different axis, see `NaricAssessmentState`). Eight components agree on the
five-member set.

**Agreed target (§4.1): `available | empty | partial | unavailable | invalid`.**

§4.1 requires specifically that no component conflate `empty` with `unavailable`. **UC-01
and UC-02 both hold that distinction correctly** — UC-02's assumption A-24 records that
"NARIC is down" and "NARIC holds no qualification" both apply the Level 5 default but are
stored with different statuses (`unavailable` vs `empty`). This is the row most likely to
be broken by a careless adapter, because both cases produce the same learner-visible level.

UC-01's mapping onto the five-member set (`incomplete` → `partial`? → `empty`?) is a real
question, not a rename: it decides whether "NARIC is still calibrating" reads as a partial
answer or as no answer at all.

---

## 6. Course progress — AGREED on type, OPEN on name

| Property | Value |
|---|---|
| Type | integer, 0–100 inclusive |
| Companion | `course_progress_status: SourceStatus` |

| Component | Field name | Verified at |
|---|---|---|
| UC-08 | `course_progress_percent` | `packages/uc08/uc08/domain/models.py:68` (`CompletionPercent`) |
| UC-10 | `course_completion_percent` | `packages/uc10/uc10/domain/models.py:66` (`ge=0, le=100`) |

Type and range agree; the field name does not. Pick one — `course_progress_percent` matches
the `..._status` companion — and rename in the other.

---

## 7. Enum casing — INSTRUCTED, not yet done

§4.1 requires every emitted enum value to be lowercase, on API responses and stored records
alike. Current state:

| Component | Casing | Affected types |
|---|---|---|
| UC-01 | lowercase values, but hyphenated modes | see §8 |
| UC-02 | lowercase | — |
| UC-03 | lowercase, except `NaricLevel` | renamed under §4.2 — see below |
| UC-04 – UC-08 | mixed | `NaricLevel` uppercase; everything else lowercase |
| UC-09, UC-10 | lowercase throughout | — |

**UC-03's rename has landed.** The eight enum types the brief names — `Classification`,
`ClassificationKind`, `ResponseStatus`, `FollowUpAction`, `AuthorityStatus`,
`ExplanationDepth`, `FieldAvailability`, `LogStatus` — now emit lowercase, across the API, the
stored record and the LLM adapter's JSON schema (which derives its enum from the values, so it
followed automatically). 88 literals across 10 files; 272 tests still pass.

`NaricLevel` was deliberately left uppercase in UC-03. The brief's §4.2 list does not include
it, and it is the open platform-wide row in §2 above — changing it here would have picked an
answer to a question the company still owns. `tests/test_wire_naming.py` now asserts both
facts, so the exception is visible and that test fails loudly when §2 closes.

One upstream literal was protected during the rename: `docs/examples/company_authority_adapter.py`
compares `verification_state != "VERIFIED"`, which is the *upstream* vocabulary, not ours.

What remains on this row: UC-04's uppercase-throughout problem, also named in §4.2, **is already fixed in the
Python port** — every enum in `packages/uc04/uc04/domain/enums.py` is lowercase except
`NaricLevel`, which is the platform-wide problem in §2 rather than a UC-04 one.

- **A data migration is now owed.** UC-03's values are persisted in `QuestionLogRecord`
  (`status`, `classification`, `follow_up_action`). Rows written by an earlier build carry the
  old uppercase and will not parse against the renamed enums. The migration warning is in
  `packages/uc03/uc03/domain/enums.py`; nothing in the package performs one.
- **UC-04 – UC-08's `NaricLevel`** is the only casing divergence left, and it is §2's problem,
  not this row's.
- UC-09 and UC-10 enforce lowercase through base classes (`LowerStrEnum`,
  `LowercaseStrEnum`). Reuse one of those rather than re-deriving the behaviour.

---

## 8. Session mode and response mode — OPEN

| Component | Field | Values |
|---|---|---|
| UC-01 | `SessionMode` | `free-form`, `course-linked`, `case-linked` (**hyphens**) |
| UC-05 | `mode` on the log record | closed enum `Mode`: `free_form`, `course_linked`, `case_linked`, `socratic` |
| UC-06 | `mode` | `ResponseMode` |
| UC-10 | `session_mode` | free `str`, "vocabulary owned upstream" |

Three problems:

1. **UC-01 uses hyphens where the platform uses underscores.** `free-form` vs `free_form`
   is a silent mismatch: both are valid strings and no validator rejects either.
2. ~~UC-05's `mode` is a fixed literal.~~ **Closed.** It is now the closed enum
   `Mode` (`packages/uc05/uc05/domain/enums.py`). The wire value is unchanged — UC-05 still
   writes `socratic`, because nothing tells it the session type — but a value outside the set
   is now rejected rather than accepted, and the three session types the platform defines are
   admissible for a shared store. Pinned by three tests in `tests/test_domain_units.py`.
3. **The known limitation the brief asks you to decide on stands open.** One `mode` field
   conflates session type with response mode, so a Socratic turn cannot carry the session
   type underneath it. §4.2 asks whether to split into orthogonal fields now. Closing the
   enum did not resolve this and was not meant to; the limitation is recorded on the `Mode`
   docstring so it cannot be missed by anyone reading the type.

**Recommendation: split now.** Two fields — `session_mode`
(`free_form | course_linked | case_linked`, owned by UC-01) and `response_mode`
(`direct | socratic`, owned by the answering component) — cost one nullable column today and
are unrecoverable later, because a merged field cannot be un-merged from historical rows.
UC-10 already treats `session_mode` as its own field name.

Also **closed:** UC-05's `ResponseKind` has gained its sixth member,
`closing_acknowledgement` (§4.2). It relabels a path that already existed — transitions T04
and T11 sent `vocab.CLOSING_ACKNOWLEDGEMENT` plus a consolidating question while publishing
`acknowledgement_and_guiding_question`, so a reader of the log could not tell "the learner
reasoned their way there" from "still working". The text emitted is unchanged; only the label
is. No test had pinned that label, so one was added.

---

## 9. The interaction log — OPEN. The single largest integration risk (§4.3).

Four components write interaction records. Four read them. At integration they must resolve
to one store. **They currently do not agree on the name of a single field** — not the
primary key, not the timestamp, not the question text.

### 9.1 What each writer emits

| Field | UC-03 | UC-04 | UC-05 | UC-06 |
|---|---|---|---|---|
| primary key | `question_id` | `interaction_id` | `interaction_id` | `interaction_id` |
| session | `session_id` | `session_id` | `session_id` | `session_id` |
| user | `user_id` | `user_id` | `user_id` | `user_id` |
| when | `timestamp` | `asked_at` | `asked_at` | `asked_at` |
| question text | `question` (stored) | `question_text` (**redaction marker only**) | `question_text` (stored) | *absent by design* |
| topic | `topic_tag: TopicTag` enum + `topic_tag_accepted: bool` | `topic_tag: str` | `topic_tag: str` | `topic_tag: str` |
| question class | `classification: ClassificationKind\|None` | `question_class: QuestionClass` | *absent* | `question_class: str` |
| level | `naric_level\|None` + `naric_level_source\|None` | `naric_level` | `naric_level` | `naric_level` |
| response | `answer: AnswerParts\|None` | `response_id` | `response_id` | `response_id` |
| mode | *absent* | *absent* | `mode = "socratic"` | `mode: ResponseMode` |
| concept | `concept_key\|None` | `concept_tag` | *absent* | *absent* |
| framing | `framing\|None` | `framing_used\|None` | *absent* | *absent* |
| follow-up | `follow_up_of`, `follow_up_action` | `follow_up_of`, `explain_differently_count` | `follow_up_of` | *absent* |
| rating | `rating_state = pending` | `rating_state = pending` | `rating_state = pending` | `rating_state` |
| mode-specific | `status`, `error`, `degraded`, `elapsed_ms`, `citation_guard_violations` | `course_id`, `lesson_id`, `lesson_section_id`, `grounding`, `quiz_intent_detected`, `quiz_detection_confirmed` | `dialogue_id`, `exchange_number`, `response_kind`, `resolution` | `case_file_id`, `case_facts_referenced`, `guard_triggered`, `disclaimer_present` |

Sources: `packages/uc03/uc03/domain/models.py:231`,
`packages/uc04/uc04/domain/models.py:230`, `packages/uc05/uc05/domain/models.py:264`,
`packages/uc06/uc06/domain/models.py:167`.

### 9.2 What each reader requires

| Reader | Requires | Verified at |
|---|---|---|
| **UC-07** | `interaction_id`, `session_id`, `user_id`, `asked_at`, `topic_tag`, **`question_class` (mandatory, non-empty)**, `naric_level`, `response_id`, `follow_up_of`, **`explain_differently_count`**, `rating_state` | `packages/uc07/uc07/domain/models.py:61` |
| **UC-08** | `interaction_id`, `occurred_at` — nothing else | `packages/uc08/uc08/domain/models.py:48` |
| **UC-09** | `interaction_id`, `session_id`, **`occurred_at`**, `question_text`, **`topic_tags` (tuple)**, **`concept_tags` (tuple)** | `packages/uc09/uc09_summary/domain/models.py:63` |
| **UC-10** | `interaction_id`, `session_id`, `user_id`, `question_text`, **`response_text`**, `response_category`, `topic_tag`, `session_mode`, `naric_level`, `naric_level_source`, `explanation_profile`, `naric_source_status`, `course_completion_percent`, **`delivered_at`**, `source_status` | `packages/uc10/uc10/domain/models.py:48` |

### 9.3 Reader by writer: what actually fails

Every cell below is a today-fact, verified against the models above.

| | UC-03 writes | UC-04 writes | UC-05 writes | UC-06 writes |
|---|---|---|---|---|
| **UC-07 reads** | ✗ no `interaction_id`, no `asked_at`, no `question_class`, no `explain_differently_count` | ✓ | ✗ no `question_class` | ✗ no `explain_differently_count` (silently defaults to 0) |
| **UC-08 reads** | ✗ no `interaction_id`, no `occurred_at` | ✗ no `occurred_at` | ✗ no `occurred_at` | ✗ no `occurred_at` |
| **UC-09 reads** | ✗ key and timestamp names; topic is singular; no `concept_tags` | ✗ `occurred_at`; tags singular; `question_text` is a redaction marker | ✗ `occurred_at`; tags singular; no concept | ✗ no question text at all; tag singular; no concept |
| **UC-10 reads** | ✗ **no `response_text`**; no `delivered_at`; no profile/status fields | ✗ **no `response_text`**; question text redacted | ✗ **no `response_text`** | ✗ **no `response_text`**; no question text |

Two findings deserve to be read twice:

1. **No writer publishes `response_text`, and UC-10 requires it** as a mandatory field on
   every record it rates. UC-10 stores question and response text for the improvement
   pipeline (brief §9.2 permits storing it and forbids logging it). Either the writers start
   publishing response text — which puts case-linked response text into the shared store,
   and UC-06 deliberately writes no content at all — or UC-10's provider adapter fetches it
   from a second source. **This is a design decision, not a field mapping.**
2. **UC-06 by design writes no `question_text` and no fact text** ("a question about a live
   matter is itself sensitive", `packages/uc06/uc06/domain/models.py:170`). UC-09 renders
   `question_text` in its question-log fallback and UC-10 requires it. The privacy
   constraint must win; the superset schema must therefore make the field nullable **and**
   every reader must handle null without inventing a placeholder.

### 9.4 Agreed target: one superset record

Field names below are canonical. Mode-specific fields are nullable. A writer that does not
own a field writes null; a reader that needs a field it may not get must degrade honestly
rather than substitute.

```text
interaction_id        str, globally unique across ALL writers   (see 9.5)
session_id            str, opaque, from UC-01
user_id               str, resolved server-side
occurred_at           datetime, tz-aware UTC   <- one name; replaces asked_at/timestamp/delivered_at
session_mode          enum: free_form|course_linked|case_linked        (§8)
response_mode         enum: direct|socratic                           (§8)
topic_tags            tuple[str, ...] from the ONE vocabulary          (§10)
concept_tags          tuple[str, ...], nullable
question_class        enum, nullable          <- UC-05 has none; do NOT default it
naric_level           enum                                            (§2)
naric_level_source    enum: retrieved|default                         (§3)
explanation_profile   enum: basic|intermediate|advanced               (§4)
response_id           str
question_text         str | null   <- null for UC-06 by design; redaction marker for UC-04
response_text         str | null   <- OPEN, see 9.3 finding 1
follow_up_of          str | null, never equal to interaction_id
explain_differently_count   int >= 0, nullable   <- see caution below
rating_state          enum: pending|rated; written pending by the creator, changed only by UC-10
source_status         enum                                            (§5)

-- mode-specific, all nullable --
course_id, lesson_id, lesson_section_id, grounding, quiz_intent_detected, quiz_detection_confirmed
dialogue_id, exchange_number, response_kind, resolution
case_file_id, case_facts_referenced, guard_triggered, disclaimer_present
framing, classification, status, error, degraded, elapsed_ms, citation_guard_violations
```

**Caution on `explain_differently_count`.** UC-07 defaults it to 0 and uses it as a struggle
signal (`EXPLAIN_DIFFERENTLY_STRUGGLE_THRESHOLD=2`). Three of the four writers do not
populate it. A default of 0 therefore reads as "this learner never asked for a
re-explanation" — indistinguishable from "this writer does not track it". A learner
struggling in Socratic or case-linked mode is invisible to the struggle signal. **Make it
nullable rather than defaulted, and have UC-07 treat null as unknown rather than zero.**
This is exactly the §4.3 warning about gap reports being wrong in a way nobody notices.

**Caution on `question_class`.** UC-05 writes none and UC-07 requires it as non-empty. Do
not synthesise one in the adapter — that is the adapter inventing data, forbidden by §8.

### 9.5 `interaction_id` uniqueness — OPEN

§4.3 point 4 requires global uniqueness across all four writers, not per component. Each
writer mints its own ids today and no shared allocator exists. Until one is chosen, two
writers can produce the same id and the shared store will silently overwrite or reject.
Options: a UUID per writer (cheapest, verifiable), or a writer prefix (`uc03_…`), which also
makes provenance visible in the store — useful given 9.3.

### 9.6 The single-writer rule — AGREED, and already correct

`rating_state` is written `pending` by whichever component creates the interaction and
changed only by UC-10. Verified: UC-03, UC-04 and UC-05 all default it to `pending`, and no
component other than UC-10 writes any other value. Preserve this when the store becomes
shared — it is the one column where four writers and one mutator meet.

---

## 10. Topic and concept vocabularies — OPEN. Four disjoint vocabularies.

§4.1 requires one list, everywhere. There are four, and they barely intersect.

| Component | Vocabulary | Size | Verified at |
|---|---|---|---|
| **UC-03** | `contract_formation`, `contract_remedies`, `negligence`, `criminal_liability`, `criminal_procedure`, `civil_procedure`, `land_and_property`, `employment`, `company_and_insolvency`, `family`, `immigration`, `wills_and_probate`, `evidence`, `human_rights`, `legal_system`, `professional_conduct`, `unclassified` | 17 | `packages/uc03/uc03/domain/topics.py` |
| **UC-04** | `evidence`, `civil_procedure`, `professional_conduct`, `contract_law`, `data_protection` | 5 | `packages/uc04/uc04/domain/vocabularies.py:18` |
| **UC-05** | `contract`, `tort`, `employment`, `land`, `crime`, `equity`, `family`, `public`, `evidence`, `procedure` | 10 | `packages/uc05/uc05/domain/topics.py:21` |
| **UC-06** | `breach_of_duty`, `causation`, `dishonesty`, `duress`, `self_defence`, `general` | 6 | `packages/uc06/uc06/domain/legal_tests.py` |
| UC-07 – UC-10 | free strings; they aggregate whatever arrives | — | `NonEmptyStr` / `str` |

Overlap across all four writers: **`evidence` alone.**

Worked consequence. A learner asks about contract law in a free-form session, then in a
course-linked lesson, then in Socratic mode. Three records land in one store tagged
`contract_formation`, `contract_law` and `contract`. UC-07 groups by `topic_tag` and reports
**three weak topic areas instead of one**. The Definition of Done line "the gap report
identifies at least three concrete topic areas after ten interactions" then passes on an
artefact of vocabulary drift. The readers cannot detect this: they accept free strings.

UC-06's set is worse than a naming difference — `breach_of_duty` and `causation` are
doctrinal legal tests, a **different axis** from a subject area. They are not renamable onto
a topic list; UC-06 needs either a mapping onto the platform topics or a second field
(`legal_test_tag`) that UC-07 does not aggregate.

**This is Decision 7 and it is unanswered.** Changing the vocabulary after launch
invalidates every historical row, because the tag is what gap reports, content-review flags
and progress logging all group by.

**Recommendation.** Adopt UC-03's 17-member list as the platform vocabulary — it is the
largest, the only closed enum, and the only one with an explicit `unclassified` member for
the honest-failure case. Map UC-04 (`contract_law` → `contract_formation`, five terms) and
UC-05 (ten keyword groups) onto it. Give UC-06 a separate `legal_test_tag`. Then close the
readers: replace `str` with the enum in UC-07 – UC-10, so a drifted tag becomes a loud
validation error instead of a silent extra topic.

**The concept vocabulary is in the same state and is not yet registered:** UC-03 has
`concept_key: str|None`, UC-04 has `concept_tag: str` plus a `ConceptEntry` vocabulary,
UC-09 reads `concept_tags: tuple[str, ...]` and calls it "the *only* admissible source of
concepts in a summary". One list is needed here too.

---

## 11. Configuration namespace — OPEN (found during the merge; not in the brief)

Topology B puts all ten components in one process reading one environment. **Eight
configuration names are used by two to four components each, and their allowed values
differ.**

| Name | Components | Allowed values differ? |
|---|---|---|
| `COURSES_PROVIDER` | UC-02, UC-04, UC-07 | **Yes** — UC-02 `{mock, company}`; UC-04 `{mock, company, foreign_demo, company_courses}`; UC-07 `{mock, foreign, acme}` |
| `LEARNER_CONTEXT_PROVIDER` | UC-04, UC-05, UC-06 | to verify per registry |
| `INTERACTION_LOG_REPOSITORY` | UC-04, UC-05, UC-06 | writers only |
| `CURRENT_USER_PROVIDER` | UC-04, UC-05, UC-06, UC-07 | UC-07 adds `static` |
| `ALLOW_DEV_SESSION_IDS` | UC-02, UC-04, UC-05, UC-06 | boolean; one switch now flips four components |
| `GENERATION_TIMEOUT_MS`, `GENERATION_TARGET_P95_MS` | UC-04, UC-05, UC-06 | same meaning, so far |
| `LOG_LEVEL` | UC-02, UC-05, UC-08, UC-10 | same meaning |
| `NARIC_PROVIDER` | UC-01 (as `UC01_NARIC_ADAPTER`), UC-02 | UC-01 is namespaced, UC-02 is not |

Consequence: you cannot express "UC-02 uses the company Courses adapter while UC-04 stays on
mock" in one environment. Set `COURSES_PROVIDER=acme` for UC-07 and UC-02 fails at startup
on an unknown registry key. That loud failure is the architecture working as designed
(§2) — but it means the config namespace, not the code, is what blocks a staged rollout.

**Recommendation.** Adopt the convention UC-01 and UC-09 already use: prefix every setting
`UCnn_`. It is a composition-root and settings-layer change — `env_prefix` on each
component's pydantic-settings class — so it touches no domain or application code and no
existing test. Do it before the first real adapter, because a staged rollout is exactly how
real adapters get introduced.

---

## 12. Divergences named in brief §4.2 — status after the merge

| Divergence | Status |
|---|---|
| UC-04 runtime is TypeScript | **Resolved.** The Python port is the merged package and passes 381 tests of its own; the TypeScript tree is archived at `reference/uc04-typescript/`. The port landed mid-merge, complete with its three documents — see `docs/MERGE_NOTES.md`, "UC-04 arrived mid-merge". |
| UC-04 invented a three-point qualification scale (`BEGINNER`/`INTERMEDIATE`/`ADVANCED`) with no provenance field | **Fixed in the port.** `packages/uc04/uc04/domain/enums.py` uses the platform `NaricLevel` and has `NaricLevelSource`. |
| UC-04 uppercase enums | **Fixed in the port**, except `NaricLevel` — the platform-wide problem in §2, not a UC-04 one. |
| UC-03 lowercase rename across the named enum types | **Done.** Eight types, 88 literals, 10 files; 272 tests pass. `NaricLevel` deliberately excluded — see §7. **A data migration is owed** for persisted rows. |
| UC-05 `response_kind` gains `closing_acknowledgement` | **Done.** Six members; T04 and T11 relabelled; new test pins it. |
| UC-05 `mode` becomes a closed enum | **Done.** `Mode` with the four members; wire value unchanged; unknown values now rejected. The orthogonal-fields question remains open — see §8. |

---

## 13. What this register does not yet cover

Stated plainly so nobody mistakes silence for agreement:

- **Authority and citation records** (UC-03 and UC-06 into UC-09's `CitationProvider`).
  UC-09's `Resource` requires `cited_in_interaction_ids`; the shape UC-03 and UC-06 publish
  has not been reconciled against it.
- **Feedback and rating records** (UC-10 into UC-07's `FeedbackProvider`).
- **Gap report records** (UC-07 into UC-08 and UC-09).
- **Session records** (UC-01 into UC-05's `SessionModeRepository` and UC-09's
  `SessionProvider`).
- **Alert and notification sink payloads** (UC-06, UC-08).
- **UC-01 and UC-02 have no `docs/SHARED_CONTRACT.md`.** Their contracts were read out of the
  code for this register. UC-04's arrived late in the merge and has **not** yet been read back
  against §9 — its `InteractionRecord` shape was re-verified as unchanged, but the rest of its
  published contract has not been cross-checked.
- **Thresholds tuned against fake generators** (§4.4) are inventoried in
  `docs/DECISIONS.md` but not re-measured. Every one is still a fake-generator number.
