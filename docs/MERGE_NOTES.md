# Merge notes

What moved, what changed, what deliberately did not. Read this before assuming anything about
the layout.

## Scope of this merge

Ten independently built repositories became one repository with ten packages — brief §5
topology **B**, "one application, ten packages". Contract reconciliation (brief §4) was done
on paper and is in `../PLATFORM_CONTRACT.md`.

**Nothing else was done here.** No coaching router, no shared interaction store, no scheduler,
no admin surfaces, no journey tests. Those are Phases 3–7 and are listed under "Not in this
merge" below. One real adapter does exist, but it arrived with UC-04 rather than being written
for the merge — see "UC-04 arrived mid-merge".

## Source mapping

| Source repository | Merged as | Package | Tests moved | Result |
|---|---|---|---|---|
| `Documents/tas11` | `packages/uc01` | `uc01` | 14 files | 249 passed |
| `Documents/tas22` | `packages/uc02` | `uc02` (**renamed from `app`**) | 16 files | 186 passed |
| `Documents/tas33` | `packages/uc03` | `uc03` | 15 files | 272 passed |
| `Documents/tas44/python` | `packages/uc04` | `uc04` (kept its `src/` layout) | 18 files | 381 passed |
| `Documents/tas55` | `packages/uc05` | `uc05` | 20 files | 508 passed |
| `Documents/tas66` | `packages/uc06` | `uc06` | 22 files | 843 passed |
| `Documents/tas77` | `packages/uc07` | `uc07` | 21 files | 421 passed |
| `Documents/tas88` | `packages/uc08` | `uc08` | 22 files | 310 passed |
| `Documents/tas99` | `packages/uc09` | `uc09_summary` (name kept) | 21 files | 569 passed |
| `Documents/tas1010` | `packages/uc10` | `uc10` | 15 files | 381 passed |

**4,120 tests pass at this point, the same count as before the merge, component by
component.** (The repository total is now 4,123: three tests were added later, closing the
§4.2 divergences.) The source repositories were left untouched and remain available for
diffing.

Each package kept its own `README.md`, `docs/`, `pyproject.toml`, `.env.example` and tests.
UC-01 also brought `data/` and `scripts/`; UC-03 brought `bench/`; UC-07 brought `evidence/`.

Not copied: `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `node_modules/`, `.coverage`,
and each repository's `.claude/settings.json` (per-repo tool settings, not project code).

## The one change the merge itself made to component code

Exhaustive for the merge. Work done *after* it is in "Closing the §4.2 divergences" below.

**1. UC-02's top-level package `app` was renamed to `uc02`.** A package called `app` cannot
be the top-level name of one component inside a platform of ten — it is the obvious name for
a future composition root, and it says nothing about which component it is. The rename was
mechanical:

- `from app.` → `from uc02.` (139 sites)
- `app.main:app` → `uc02.main:app` (2 sites, docs and a docstring)
- `"app.domain.ports.providers"` → `"uc02.domain.ports.providers"` (1 site, an architecture
  assertion in `tests/unit/test_adapter_independence.py`)
- `pathlib.Path("app")` → `pathlib.Path("uc02")` (2 sites, source-scanning tests in
  `tests/unit/test_config_and_factory.py` and `tests/unit/test_explanation_mapping.py`)
- `app/…` → `uc02/…` in prose paths across `README.md`, `docs/`, `.env.example`

All 186 UC-02 tests pass after the rename, with the same count as before. No assertion was
changed — only the paths and module names the assertions point at.

**2. Nothing in any `domain/` or `application/` directory was touched,** in any component.
Brief §2 says that if integration requires it, you have found a contract conflict rather than
a patch. Several conflicts were found; all are recorded in `../PLATFORM_CONTRACT.md` and none
were patched.

### What was NOT renamed, and why

`uc09_summary` keeps its module name inside `packages/uc09/`. Renaming it would touch 406
occurrences across 157 files including its `UC09_`-prefixed environment variables, and it
would change a configuration surface for no functional gain. The directory says `uc09`; the
module says `uc09_summary`. Recorded here so it reads as a decision rather than an oversight.

## UC-04 arrived mid-merge

UC-04 existed twice in `tas44`:

| | Location | State |
|---|---|---|
| TypeScript | `tas44/src` + `tas44/tests` | complete, 11 vitest test files |
| Python port | `tas44/python/src/uc04` | the merged package |

**The port was still being finished while this merge ran.** At first copy (21:16) it was code
only, with an empty `tests/` directory. By 21:19 it had gained 18 test files with fixtures,
its three documents (`docs/SHARED_CONTRACT.md`, `docs/assumptions.md`, `docs/INTEGRATION.md`),
a README, and changes to five source files. `packages/uc04` was re-synced from that 21:19
state and its suite passes unmodified: **381 tests.** A check of the other nine repositories
confirmed none of them changed during the merge — only `tas44` did.

Two consequences worth knowing:

- **UC-04 keeps its own `src/` layout** (`packages/uc04/src/uc04`), unlike the other nine
  packages. `tests/test_registry_and_swap.py:208` resolves the package as
  `parents[1] / "src" / "uc04"`, so flattening would have meant editing a test. Its own
  `pythonpath = ["src"]` handles imports and the runner does not care.
- **UC-04 now carries the platform's first real adapter.**
  `src/uc04/adapters/real/company_courses.py` is `_template.py` filled in for the company
  Courses Agent, registered as `company` on the `CoursesProvider` port. It reads recorded
  staging responses when `COMPANY_COURSES_BASE_URL` starts with `file://`, so the payload
  mapping is exercised before the endpoint is reachable — one line to switch to HTTP.
  `tests/test_company_courses_swap.py` and its staging fixtures cover it.

The port also fixed two of the three UC-04 divergences the brief lists in §4.2 — the invented
three-point qualification scale and the uppercase enums — see `../PLATFORM_CONTRACT.md` §12.
The TypeScript tree is archived verbatim at `reference/uc04-typescript/`; nothing runs it.

## Closing the §4.2 divergences

Step 3 of the brief's suggested order, done after the merge. All three were instructed by the
brief, so they are sanctioned exceptions to "do not modify component business logic" — and
each is a vocabulary change, not a logic change.

**1. UC-03's lowercase enum rename.** The eight enum types §4.2 names — `Classification`,
`ClassificationKind`, `ResponseStatus`, `FollowUpAction`, `AuthorityStatus`,
`ExplanationDepth`, `FieldAvailability`, `LogStatus` — now emit lowercase. 88 quoted literals
across 10 files, including the API surface, the stored `QuestionLogRecord` and the docs. The
LLM adapter needed no edit: its JSON-schema enum is derived from `[k.value for k in
ClassificationKind]`, so it followed.

Two things were deliberately *not* renamed:

- **`NaricLevel`**, which stays uppercase in UC-03. It is absent from §4.2's list and is the
  open platform row in `../PLATFORM_CONTRACT.md` §2, where six components emit `LEVEL_5` and
  two emit `level_5`. Renaming it would have picked an answer the company owns.
- **`docs/examples/company_authority_adapter.py`'s `verification_state != "VERIFIED"`**, which
  is the *upstream* vocabulary. A blanket search-and-replace would have silently broken the
  worked example that teaches integrators to keep upstream strings inside the adapter.

One test changed: `tests/test_wire_naming.py` asserted `payload["status"].isupper()`. It
encoded the pre-rename contract, so it now asserts lowercase — and additionally asserts that
`naric_level` is still uppercase, so the deliberate exception is visible in a test rather than
only in prose, and that test fails loudly when §2 closes.

**A data migration is owed.** UC-03's values are persisted. Rows written by an earlier build
carry the old uppercase and will not parse. The warning is on the module docstring in
`packages/uc03/uc03/domain/enums.py`; nothing performs the migration.

**2. UC-05's sixth `response_kind`.** `closing_acknowledgement` added, and transitions T04
(`learner_reasoned`) and T11 (`conclusion_while_confirming`) now publish it. This relabels a
path that already existed: both sent `vocab.CLOSING_ACKNOWLEDGEMENT` plus
`vocab.CONSOLIDATING_QUESTION` while publishing `acknowledgement_and_guiding_question`, so a
reader of the interaction log could not distinguish "the learner reasoned their way there"
from "still working". The text emitted is unchanged. No test had pinned that label, so an
assertion was added to the existing learner-reasoned test.

**3. UC-05's `mode` as a closed enum.** `Literal["socratic"]` became `Mode`
(`free_form | course_linked | case_linked | socratic`). The wire value is unchanged — UC-05
still writes `socratic`, because nothing tells it the session type — but an unknown value is
now rejected and the three session types are admissible, which is what a shared store needs.
Three tests were added, because a closed enum earns its keep by rejecting what is not in it
and nothing tested that.

**The limitation §4.2 asks about is still open.** One `mode` field conflates session type with
response mode. Closing the enum did not resolve it and was not meant to. The recommendation
(split into two orthogonal fields now) is in `../PLATFORM_CONTRACT.md` §8, and the limitation
is written on the `Mode` docstring so nobody meets the type without meeting the caveat.

**Result: 4,123 tests passing** — the three new UC-05 tests, everything else unchanged.

## Import model, and why there is no single `pytest` at the root

Each package is imported from its own directory: `packages/uc03` is on `sys.path` when UC-03
runs, so `import uc03` resolves. `scripts/test_all.py` runs each suite with that package as
the working directory.

**A single flat `pytest` run at the root is not possible without editing test files.** Three
components import their own fixtures as a top-level `tests` package:

- `packages/uc02/tests/conftest.py`: `from tests.fixtures.factories import ...`
- `packages/uc07/tests/api/test_endpoints.py`: `from tests.conftest import ...`
- `packages/uc07/tests/conformance/…`: `from tests.conformance.shared import ...`

Several packages also ship `tests/__init__.py`, making `tests` a real package. Put two of
those on one `sys.path` and one silently shadows the other; the tests that lose either fail
to import or, worse, import the wrong fixtures.

The options were to rename each `tests` package (editing test imports in three components) or
to run each suite in its own context. Brief §12 says not to weaken a test to make a suite
green, so suites run in their own context and **no test file's imports were touched**.

A root `conftest.py` refuses a root-level `pytest` invocation with a message pointing at
`scripts/test_all.py`, so nobody gets a misleading half-run.

```bash
python scripts/test_all.py             # all ten
python scripts/test_all.py uc03 uc06   # named packages
python scripts/test_all.py -v          # stream pytest output

cd packages/uc03 && python -m pytest   # a single suite, exactly as its author ran it
```

## Configuration

`.env.example` at the root is the union of all ten, **annotated with the collisions**: eight
setting names are shared by two to four components with different allowed values. In ten
separate repositories this was invisible. In one process reading one environment it decides
whether a staged rollout is expressible. See `../PLATFORM_CONTRACT.md` §11 — this was found
during the merge and is not in the brief.

The root `pyproject.toml` carries the dependency union at the highest floor any component
asked for, plus one ruff configuration (the loosest line length, the intersection of the
selected rule sets) so root lint never contradicts a package's own config. It deliberately
defines **no** pytest configuration; each package's own config stays authoritative.

## Not in this merge

Named so the gap is explicit, in brief order:

- **§4.3 shared interaction store.** Reconciled on paper (§9 of the contract), not built. The
  four writers still write to four separate in-memory stores.
- **§4.4 threshold re-measurement.** Every threshold tuned against a fake generator is still
  a fake-generator number. Inventory in `DECISIONS.md`.
- **§7.1 the coaching router.** Nothing yet decides which component handles a turn.
- **§7.2 the weekly summary scheduler.** UC-08 still exposes generation as a callable only.
- **§7.3 admin surfaces.** No content-review dashboard; no halt-clearing mechanism (blocked
  on Decision 4).
- **§8 real adapters.** One exists: UC-04's `company` Courses adapter (above), on recorded
  staging responses rather than a live endpoint. Every other port on every component still
  resolves to a mock or in-memory adapter.
- **§9 cross-cutting verification** against a centralised logger, and **§10 journeys J1–J9.**
- **§11 Definition of Done.** Not started; two decisions block it.
