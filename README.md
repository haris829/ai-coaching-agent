# AI Coaching Agent

Ten backend components — UC-01 to UC-10 — built as ten independent repositories and merged
into one. Coaching for legal professionals: session initiation, learner context, four kinds of
question answering, gap reporting, streaks, CPD export and feedback capture.

```
packages/
  uc01/   Coaching session initiation      - creates the session record
  uc02/   Contextual awareness setup       - assembles the learner context
  uc03/   Legal concept Q&A                - the four-part answer
  uc04/   Course content coaching          - lesson-grounded answers, quiz protection
  uc05/   Socratic method coaching         - guiding-question dialogue
  uc06/   Case-linked coaching             - case-file answers, mandatory disclaimer
  uc07/   Knowledge gap report             - read-only aggregation
  uc08/   Streaks and milestones           - no AI; arithmetic over timestamps
  uc09/   Session summary and CPD export
  uc10/   Feedback capture and content review flagging
reference/
  uc04-typescript/                         - UC-04's original TypeScript implementation
```

## Start here

| You want to | Read |
|---|---|
| Know what data the components share, and where they disagree | **[PLATFORM_CONTRACT.md](PLATFORM_CONTRACT.md)** |
| Know what the company still has to decide | [docs/DECISIONS.md](docs/DECISIONS.md) |
| Know what the merge moved and changed | [docs/MERGE_NOTES.md](docs/MERGE_NOTES.md) |
| Know what integration work remains | [docs/INTEGRATION_BRIEF.md](docs/INTEGRATION_BRIEF.md) |
| Work on one component | that package's own `README.md` and `docs/` |

## Running the tests

```bash
python -m pip install -r requirements-dev.txt

python scripts/test_all.py             # all ten components
python scripts/test_all.py uc03 uc06   # named components
python scripts/test_all.py -v          # stream pytest output

cd packages/uc03 && python -m pytest    # one suite, exactly as its author ran it
```

**4,123 tests pass across all ten components.** `pytest` at the repository root is deliberately
refused — ten packages on one `sys.path` would shadow the `tests` package of three of them.
The reason, and the alternative, are in
[docs/MERGE_NOTES.md](docs/MERGE_NOTES.md#import-model-and-why-there-is-no-single-pytest-at-the-root).

UC-04's port arrived late in the merge with its own 18 test files, three documents, and the
platform's first real adapter. Details in
[docs/MERGE_NOTES.md](docs/MERGE_NOTES.md#uc-04-arrived-mid-merge).

## Live test deployment

A deployment for the company to test against:

**https://ai-coaching-agent-production.up.railway.app**

All ten components are mounted under their own prefixes (`/uc01` … `/uc10`), each with its own
API docs. The landing page carries the per-component authentication table and two worked curl
calls — read it before testing, because the ten components resolve identity four different ways
and there is no single sign-in.

What it is not, and the page says so too:

- **It is not the coaching router.** Nothing decides which component handles a turn (§7.1 is
  unbuilt). A case-linked question sent to `/uc03` is answered without a disclaimer, because no
  routing rule exists yet to stop it.
- **No component is wired to another.** Every port resolves to its own mock, so UC-03's learner
  context is a fixture, not UC-02.
- **All data is fabricated, identity is a development header, state is in-memory** and resets
  on redeploy. Do not point it at real data.
- **The legal content is illustrative** — Decision 2, unresolved.

Redeploy with `railway up --service ai-coaching-agent` from the repository root
(`railway.json` holds the start command and the `/healthz` check).

## Running a component

Each component is a FastAPI application with its own composition root:

```bash
cp .env.example .env
cd packages/uc03 && python -m uvicorn uc03.api:app --reload --port 8003
```

Out of the box every port resolves to a mock or in-memory adapter. Nothing calls a URL.

## The architecture, in one screen

Every component follows the same shape, and integration depends on it holding:

- **Ports and adapters.** Every external dependency is a typed interface with a deterministic
  mock behind it. No component calls a URL directly.
- **A provider registry.** Adapter selection is a config value resolved through a registry.
  An unknown value fails loudly at startup and never falls back to a mock.
- **A conformance kit** per port — adapter-agnostic tests you point at your adapter. You do
  not write these.
- **A foreign-adapter proof** — a deliberately alien adapter family the unmodified service
  passes against, so replaceability is demonstrated rather than claimed.

The swap guarantee each component was built to hold: **one new adapter file, one registry
line, one config value.** If integration makes you edit a `domain/` or `application/`
directory, you have found a contract conflict — take it to
[PLATFORM_CONTRACT.md](PLATFORM_CONTRACT.md), not to a patch.

## Where this repository actually stands

The merge is done and the contracts are reconciled on paper. The wiring is not built.

**Done**

- Ten repositories merged into one; every component's tests, docs and configuration preserved
- 4,123 tests passing (4,120 pre-existing, counts identical to pre-merge, plus 3 new)
- The three §4.2 divergences closed: UC-03's enum casing, UC-05's sixth `response_kind`,
  UC-05's `mode` as a closed enum — see [docs/MERGE_NOTES.md](docs/MERGE_NOTES.md#closing-the-42-divergences)
- `PLATFORM_CONTRACT.md` written from source, with every divergence found and recorded
- The eight company decisions tracked, with their real code status
- UC-04 on one runtime with the platform enum, TypeScript archived

**Not started** (brief §§7–11)

- The coaching router — nothing yet decides which component handles a learner's turn
- The shared interaction store — four writers still write to four separate stores
- Real adapters — one exists (UC-04's `company` Courses adapter, on recorded staging
  responses); every other port on every component is still on a mock
- The weekly scheduler and the admin surfaces
- Journeys J1–J9, latency measurement, and the Definition of Done

**Blocked on the company**

- Decision 1, disclaimer wording, and Decision 2, legal content authorship. Both block
  release. Decision 2 additionally has no enforced gate in code — see
  [docs/DECISIONS.md](docs/DECISIONS.md#2-legal-content-authorship-and-jurisdiction--blocking).

**The three things most likely to bite, all in [PLATFORM_CONTRACT.md](PLATFORM_CONTRACT.md)**

1. **§2** — UC-01 and UC-02 carry the NARIC level as an `int`; UC-03 to UC-10 carry it as a
   closed enum, and those eight disagree on casing. UC-02 sits behind everyone's learner
   context port.
2. **§9** — the four interaction-log writers agree on the name of *no* field. No writer
   publishes the `response_text` UC-10 requires.
3. **§10** — four topic vocabularies with one term in common. Gap reports group by topic, so
   one learner's contract-law questions become three weak topics and nothing detects it.

## Repository layout

```
PLATFORM_CONTRACT.md     the single authority for shared data shapes
README.md                this file
conftest.py              refuses a root-level pytest run, and says what to run instead
pyproject.toml           platform metadata, dependency union, one ruff config
requirements*.txt        the same dependency union, for pip
.env.example             all ten components' settings, with the shared names marked
docs/
  INTEGRATION_BRIEF.md   the brief this merge was done against
  DECISIONS.md           the eight company decisions, and the fake-generator thresholds
  MERGE_NOTES.md         what moved, what changed, what deliberately did not
packages/uc01 .. uc10/   the components, each self-contained
reference/               UC-04's TypeScript implementation and its 11 test files
scripts/test_all.py      runs every component suite
```
