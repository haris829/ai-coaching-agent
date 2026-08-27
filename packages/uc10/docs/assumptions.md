# Assumptions register — UC-10 Feedback & Improvement

Everything this component invented because the specification did not settle it. Written as
the code was written, not afterwards.

**Read A-01 first.** It is a policy decision about when the platform accuses its own legal
content of being wrong, and it needs the company's confirmation before this component is
trusted in production.

Legend: **Confidence** is how likely we think the assumption is to survive contact with the
company. **Risk if wrong** is what actually breaks.

---

## A-01 — Minimum sample size before any flag is raised · **REQUIRES COMPANY CONFIRMATION**

| Field | Value |
|---|---|
| Area | Content review flagging |
| Assumption | A topic must have **at least 10 current ratings** in the rolling 7-day window before a flag can be raised, regardless of the down rate. Configurable via `FLAG_MINIMUM_SAMPLE_SIZE`; shipped default **10**. |
| Why | The specification fixes the threshold at 30% but names no minimum sample. Without one, the first thumbs down on a brand-new topic sits at 100% and raises a content review flag against a legal topic on a sample of one. The dashboard fills with noise, the team stops reading it, and a genuine problem walks through the resulting indifference. Ten is small enough to detect a bad topic within roughly a day of normal traffic, and large enough that ordinary dissatisfaction does not fire it: with a true down rate of 10%, the chance of a spurious flag per topic-window is ≈7% at n=10, against ≈27% at n=3 and ≈1% at n=20. |
| Risk if wrong | **Too high** → a genuinely wrong topic with light traffic stays unflagged; wrong legal coaching reaches practising solicitors for longer. **Too low** → false flags train the platform team to ignore the dashboard, which produces the same outcome by a different route. Either way the failure lands on the same people. |
| Where in code | `uc10/config.py` (`flag_minimum_sample_size`), `uc10/ports/threshold_config_provider.py` (`minimum_sample_size()`), `uc10/domain/flagging.py` (`FlaggingPolicy`, `evaluate_topic`) |
| Evidence | `tests/unit/test_flagging_rule.py::test_a_single_thumbs_down_does_not_raise_a_flag_on_a_sample_of_one` shows the same single rating flagging at minimum 1 and not flagging at 10. |
| Confidence | Medium. The number is defensible, not derived from the company's traffic. |
| What we need from the company | The real number, chosen against real per-topic rating volumes, and whether it should vary by topic maturity. Until then this is our number, not theirs. |

---

## Flagging policy

| ID | Area | Assumption | Why | Risk if wrong | Where in code |
|---|---|---|---|---|---|
| A-02 | Threshold comparison | A topic flags when the down rate is **greater than or equal to** the threshold. Exactly 30% flags. | "Above the threshold" is ambiguous at the boundary; inclusive is the safer reading for a safety control, and the specification's own boundary tests name 30% as a flagging case. Compared in `Decimal` so 3/10 flags despite binary float representation. | Exclusive comparison would silently let every exactly-at-threshold topic through. | `uc10/domain/flagging.py::_meets_threshold` |
| A-03 | Duplicate scope | "The same topic and window" means: an **open** flag for that topic whose stored window **overlaps** the current rolling window. Such a flag is updated in place (counts, rate, rule, interaction ids, `updated_at`); no second flag is opened. | A rolling window never repeats exactly, so exact-window matching would raise a new flag on every evaluation. Overlap is the only reading that satisfies "do not create duplicate open flags". | Too loose → one topic's flags never separate across genuinely distinct incidents. Too strict → duplicate flags, the failure the specification names. | `uc10/adapters/memory/repositories.py::InMemoryFlagRepository.open_flag_for`, `uc10/application/flagging_service.py::_merge` |
| A-04 | Explanation profiles | NARIC levels 4 and 6 band with the level below them: 3/4 → `basic`, 5/6 → `intermediate`, 7/7+ → `advanced`. | The specification states this mapping and explicitly flags 4 and 6 as an assumption to record. | A learner at level 6 receives intermediate rather than advanced explanations. This component only carries the value; it does not act on it. | `uc10/domain/enums.py::_PROFILE_BY_LEVEL` |
| A-22 | Retry semantics | A flag whose write failed is retried with **fresh counts if the topic still qualifies**, and with the **recorded candidate if it no longer does**. It is written either way. | "Never silently drop a flag" outranks "only flag what is currently true". The rule fired; the team must see it. | A topic that recovered between decision and retry still produces a flag the team must dismiss. We judged a spurious dismissal cheaper than a lost flag. | `uc10/application/flagging_service.py::_retry` |
| A-23 | No auto-retraction | A topic that falls back below the threshold does **not** close its open flag. Only an administrator moves a flag out of `open`. | A flag is a request for human review of legal content, not a live gauge. | Flags accumulate if the team does not triage them. | `uc10/application/flagging_service.py` (no retraction path) |
| A-24 | Evaluation trigger | Evaluation runs (a) after every successful rating capture, for that rating's topic, and (b) on `FlaggingService.run_cycle()`, which retries deferred flags and re-evaluates every topic in the window. **No scheduler is shipped**; the host process decides when to call `run_cycle()`. | Scheduling is deployment policy this component cannot see, and inventing a background thread would be a decision made on the company's behalf. | If nobody calls `run_cycle()`, deferred flags are retried only when the next rating on that topic arrives. Documented in `docs/SHARED_CONTRACT.md` as an extension point. | `uc10/api/deps.py` (wiring), `uc10/application/flagging_service.py::run_cycle` |
| A-30 | Rate precision | `down_rate` is stored rounded to 6 decimal places; the flagging comparison itself uses exact `Decimal` arithmetic on the counts. | A stored float should be readable; a decision should be exact. | None material — the stored rate is a display value, not the decision. | `uc10/domain/flagging.py::evaluate_topic` |

---

## Records and vocabularies

| ID | Area | Assumption | Why | Risk if wrong | Where in code |
|---|---|---|---|---|---|
| A-05 | Response category | `answer`, `redirect`, `refusal`, `clarifying_question`, `degraded_fallback`, plus `unknown` for anything this component has never seen. | The five names come from the specification's mock table; the vocabulary itself is owned upstream. `unknown` exists so a category we do not recognise is still **rateable** — rateability must never depend on category. | If the company's vocabulary differs, adapters map into ours; nothing downstream changes. A missing `unknown` would make a new upstream category unrateable, breaking "no responses are unrateable". | `uc10/domain/enums.py::ResponseCategory` |
| A-20 | Topic and mode vocabularies | `topic_tag` and `session_mode` are **opaque lowercase slugs**, not closed enums. Validated against `^[a-z0-9][a-z0-9_-]{0,127}$`. | These vocabularies belong to components we cannot see. A closed enum here would reject legitimate upstream values and turn a normal topic into an invalid response. | A malformed upstream value is rejected at the adapter boundary rather than being silently normalised into the wrong topic. | `uc10/domain/models.py` |
| A-06 | Comment length | A learner comment is at most **500 characters**; blank/whitespace-only becomes `null`. | The specification calls it "a one-line comment" without a bound. An unbounded free-text field that we store is a privacy and storage risk. | Too short → a learner's complaint is truncated by a validation error (they are told which field, never shown their own text back). | `uc10/domain/models.py::MAX_COMMENT_LENGTH` |
| A-07 | `naric_source_status` | The interaction record carries the status of the **NARIC value specifically** (`available` / `empty` / `invalid`) separately from the record-level `source_status`. | The platform contract requires an invalid level to be recorded as status `invalid` while the interaction as a whole may be perfectly available. Conflating them would lose one of the two facts. | Without it, "the level was defaulted" and "the interaction was degraded" become indistinguishable. | `uc10/domain/models.py::InteractionRecord` |
| A-08 | `minimum_sample_size_applied` on the flag | Flags carry the minimum sample in force as well as the threshold. | The specification requires a flag to record the rule that produced it. The sample-size half is part of that rule (A-01). | A reader could not tell whether an old flag was raised under a different sampling policy. | `uc10/domain/models.py::ContentReviewFlag` |
| A-09 | `updated_at` on the flag | Set when an open flag is updated rather than re-raised, and on a status change. | Without it, "updated, not re-raised" is invisible to the reader of a flag. | None material. | `uc10/domain/models.py::ContentReviewFlag` |
| A-19 | Rating response shape | API responses carry `rating_id`, `interaction_id`, `session_id`, rating, the learner's own comment, topic, mode, level and timestamps — **never** question or response text. | The caller already has the response it rendered; re-emitting it widens the blast radius of the content this component holds. | A frontend that expected the text back must read it from wherever it rendered it. | `uc10/api/schemas.py::RatingView` |
| A-25 | Integer NARIC values | A bare integer (`7`) is an **invalid response**, not level 7. Default applied, source `default`, status `invalid`, logged. | The platform contract says the level is never an integer scale. Mapping `7 → LEVEL_7` would quietly re-introduce one. | An upstream that legitimately sends integers has every interaction defaulted to level 5 until its adapter maps them — visible immediately in the `naric_level_defaulted` log, by design. | `uc10/domain/naric.py` |
| A-26 | Missing vs unmappable | A missing/blank level is `empty` (the upstream answered and had nothing); an unrecognised value is `invalid`. Both default to `LEVEL_5` with source `default`. | `empty` and `unavailable` must never be conflated, and the same discipline is worth applying to `empty` vs `invalid`. | Losing the distinction hides whether the upstream is silent or wrong. | `uc10/domain/naric.py` |
| A-29 | Identifier format | `rat_<uuid4hex>`, `flg_<uuid4hex>`, `fwk_<uuid4hex>`; opaque to every consumer. | Prefixes make identifiers self-describing in logs; nothing parses them. | None, provided nothing downstream parses identifiers. | `uc10/domain/ids.py` |

---

## Ports and errors

| ID | Area | Assumption | Why | Risk if wrong | Where in code |
|---|---|---|---|---|---|
| A-10 | `RecordNotFound` | A fourth typed contract error alongside `ProviderUnavailable` / `ProviderTimeout` / `ProviderInvalidResponse`. | "This identifier does not exist" is neither an outage nor an unmappable payload; folding it into either would make a 404 look like a retryable incident. | Without it, an unknown interaction is reported to the learner as a service failure. | `uc10/ports/errors.py` |
| A-11 | `RatingRepository.current_in_window(start, end)` | Added to the specified port. Returns non-superseded ratings across all users in the window. | Rolling cross-user flagging is impossible with only `save` / `for_interaction` / `supersede`. | A real repository must implement it efficiently (index on `rated_at`, filter `superseded_by IS NULL`). Called out in `docs/INTEGRATION.md`. | `uc10/ports/rating_repository.py` |
| A-12 | `FlagRepository.open_flag_for(topic, window)` semantics | Returns the open flag for the topic whose window overlaps (A-03); the most recent if several. | The port signature was specified; the matching rule was not. | See A-03. | `uc10/ports/flag_repository.py` |
| A-13 | `FlagRepository.get(flag_id)` | Added to the specified port. | The admin status endpoint must load a flag that is no longer open; `list_open()` cannot see it. | Without it, a flag could never be moved from `reviewed` to `confirmed`. | `uc10/ports/flag_repository.py` |
| A-14 | Policy config port scope | `ThresholdConfigProvider` also serves `window_days()` and `historical_rating_window_hours()`. | The specification forbids a hardcoded threshold. The same reasoning applies to 7 days and 24 hours: a rule with a literal in it is a rule nobody can change. | If the company wants these on a different port, it is a rename. | `uc10/ports/threshold_config_provider.py` |
| A-15 | Admin identity as a separate port | `AdminIdentityProvider.resolve_admin(request)` is a distinct port from `CurrentUserProvider.resolve(request)`. | "Admin endpoints reachable by no learner path" should be structural. A role flag on the learner principal is an escalation waiting to happen; a separate port cannot be escalated into. | If the company's real auth issues a single principal with roles, the admin adapter reads the role — the separation of ports still holds. | `uc10/ports/current_user_provider.py` |
| A-16 | `FlagWorkQueue` port | A durable intent-to-flag queue: enqueue before the write, resolve only after the repository confirms it. | "Persist enough state that the retry is possible" and "make dropping structurally impossible" require somewhere to persist it. The specification names no port for it. | If the company already has an outbox, this port maps onto it. Losing the queue's durability would reduce the guarantee to "retried while the process lives". | `uc10/ports/flag_work_queue.py` |
| A-27 | Anonymous ratings | Refused with `401` and **not stored anywhere** — not in the pipeline, not in a side table. | "Anonymous ratings are not logged to the improvement pipeline. Authentication is a pre-condition." Storing them elsewhere would be the same data under another name. | If the company wants anonymous volume counted, that is a new, deliberate decision. | `uc10/application/rating_service.py` |
| A-28 | Dev identity mechanism | `X-User-Id` for learners; `X-Admin-Token` (matched against `DEV_ADMIN_TOKEN`) plus optional `X-Admin-Id` for administrators. With no token configured, **every** admin request is refused. | A minimal replaceable stand-in. Refusing by default is the only safe behaviour for a stand-in that will one day run somewhere it should not. | This is not authentication and must be replaced before production; see `docs/INTEGRATION.md`. | `uc10/adapters/mock/identity.py` |
| A-18 | `DEV_ADMIN_TOKEN` setting | One extra configuration key beyond the specified list, for A-28. | Needed to make the admin path testable without inventing an auth service. | None — it is inert once a real adapter replaces the dev one. | `uc10/config.py` |

---

## API behaviour

| ID | Area | Assumption | Why | Risk if wrong | Where in code |
|---|---|---|---|---|---|
| A-17 | Status transitions | `open → reviewed / confirmed / corrected`; `reviewed → confirmed / corrected`; `confirmed → corrected`; `corrected` is terminal. Re-applying the current status is idempotent; going backwards is `409`. | The specification names the four states but no ordering. This ordering treats the states as a review progressing towards a resolution. | Too strict → the team cannot record a correction they later reversed. Cheap to change: one dictionary. | `uc10/application/flagging_service.py::ALLOWED_TRANSITIONS` |
| A-21 | HTTP status mapping | `201` created · `200` replaced · `401` anonymous · `404` unknown **or another learner's** interaction · `409` outside the 24-hour window · `503` retryable failure · `502` unmappable upstream response · `403` non-admin on an admin route. | `404` for another learner's interaction avoids confirming that it exists. `409` distinguishes "closed for feedback" from a malformed request. | A frontend keying on codes needs this table; it is in `docs/SHARED_CONTRACT.md`. | `uc10/api/routes.py` |
| A-31 | Own-rating read | `GET .../rating` returns `200` with `rating: null` when the caller has not rated, rather than `404`. | A frontend rendering a thumbs control asks "has this learner rated?", which is a normal answer, not an error. | None material. | `uc10/api/routes.py::read_own_rating` |
| A-32 | Flag listing scope | `GET /api/v1/admin/flags` returns **open** flags only. | The specification calls it "open content review flags". | The team cannot browse historical flags through the API; they remain in the repository. Extension point noted in `docs/SHARED_CONTRACT.md`. | `uc10/api/routes.py::list_open_flags` |

---

## Assumptions that must be verified before a real adapter is written

These are the ones an integration engineer should check against the real upstream **first**;
`docs/INTEGRATION.md` repeats them at the point of use.

1. **A-25 / A-26** — how the real system represents a NARIC level, and what it sends when it has none.
2. **A-05** — the real response-category vocabulary, including whatever it calls a degraded fallback.
3. **A-20** — the real topic vocabulary, and whether topic tags are stable enough to aggregate a 7-day rate over.
4. **A-11** — whether the real rating store can serve a windowed cross-user read efficiently.
5. **A-01** — the minimum sample size, against real per-topic rating volume.
