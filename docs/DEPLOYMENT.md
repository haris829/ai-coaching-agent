# Deploying the Courses Quiz Agent

Written for a **review deployment**: a real hosted instance, with real guards and a real database,
whose purpose is for the company to try the system out and tell us what to change. Everything here
also applies to a production deployment; the two differences are called out where they matter.

---

## The architecture this deploys

**One application service, one managed database.**

```
                    ┌─────────────────────────────────────────┐
   browser ────────▶│  quiz-agent  (one container)            │
                    │                                         │
                    │   /api/...   FastAPI — all 10 UCs       │
                    │   /*         the built React test UI    │
                    └──────────────────┬──────────────────────┘
                                       │ DATABASE_URL
                    ┌──────────────────▼──────────────────────┐
                    │  PostgreSQL  (managed, separate service) │
                    └─────────────────────────────────────────┘
```

**Why one service rather than two.** The API serves `/api/...` and the built frontend for everything
else, so the browser only ever makes same-origin requests. CORS then stops being part of the
deployment *at all* — not "configured correctly", absent — which removes a class of
misconfiguration whose only symptom is a browser-side error that never appears in a server log. It
is also one URL to hand a reviewer. See [`backend/app/web.py`](../backend/app/web.py) for the
implementation and for what the SPA fallback deliberately refuses to swallow.

**Why the database is separate and must be PostgreSQL.** A container's filesystem is discarded on
every redeploy. A SQLite file inside it would silently lose every attempt, result and certificate
the company created while reviewing — silently being the operative word. There is no application
change involved: `DATABASE_URL` is the only thing that differs, every model uses portable
SQLAlchemy types, every constraint is explicitly named, and
[`tests/test_schema_migration.py`](../backend/tests/test_schema_migration.py) asserts the migrated
schema matches the models. See [DATABASE.md](DATABASE.md).

A bare `postgresql://…` or `postgres://…` URL — the form every managed provider hands out — is
normalised to `postgresql+psycopg://…` by `Settings`, so the provider's value can be pasted
unchanged. Without that it would fail at engine construction complaining about `psycopg2`, which is
not a dependency, and the error would look like a packaging problem rather than a URL one.

### PostgreSQL is verified, and it was not free

The schema did **not** work on PostgreSQL when first tried. The migration failed on revision 2 of 9,
which means a deployment would have failed on its first boot before serving a request. Three classes
of defect, none visible to any of the 2045 tests — because every test runs on SQLite, and SQLite is
permissive in exactly the places PostgreSQL is not:

* **eight identifiers over PostgreSQL's 63-character limit** (SQLite has none), up to 93 characters;
* **sixteen boolean predicates** of the form `answered = 1`, valid on SQLite because it stores
  booleans as integers, and `operator does not exist: boolean = integer` on PostgreSQL;
* **server defaults contradicting their column's type**, in both directions.

All are fixed, with the details in [UC11-FINDINGS.md](UC11-FINDINGS.md) (F-21 to F-23). Two gates now
guard against recurrence:

```bash
npm test                    # includes tests/test_database_portability.py — six static checks that
                            # run on SQLite and still catch PostgreSQL-only faults
npm run verify:postgres -- --database-url "$DATABASE_URL"
                            # 42 checks against a real server: all nine revisions apply, all eleven
                            # immutability triggers install, a trigger genuinely refuses an UPDATE,
                            # the six partial unique indexes are genuinely partial, 146 CHECK
                            # constraints migrated, all 27 foreign keys are enforced
```

Point `verify:postgres` at a **scratch** server, not the one holding a reviewer's work — it creates
and drops its own database, but the server should still be disposable. With no PostgreSQL to hand,
`--embedded` starts a temporary one (needs `pip install pgserver`).

---

## Environment variables

`backend/.env.example` is the annotated reference. This is the deployment subset.

### Required

| Variable | Value | Why it is required |
|---|---|---|
| `DATABASE_URL` | the managed database's URL | Paste it as the provider gives it. |
| `ENVIRONMENT` | `production` | Anything not in `{development, dev, test, local}` gets the strict behaviour. |
| `ADMIN_API_TOKEN` | a value you generate | **The application refuses to start without it.** Unset, the administrator guard admits everybody — and question-bank reads carry `isCorrect`, the answer key. |
| `SYSTEM_API_TOKEN` | a different value you generate | **Also refuses to start without it.** Unset, UC-09's platform-internal endpoints accept an administrator credential; a caller who can report a disconnect can auto-submit a formal assessment. |
| `FRONTEND_DIST` | `/app/frontend/dist` | Set by the Dockerfile. Serves the UI from the API, which is what removes CORS. |

The two token requirements are enforced by
`Settings._require_credentials_outside_development`, not documented and hoped for. A requirement
nobody enforces is eventually not met, and the failure mode here is silent.

`PORT` is injected by the platform and read automatically. Do not set `HOST`: outside development
the server binds `0.0.0.0` by itself, and a process bound to loopback in a container starts
happily, passes its own health check from inside, and answers nothing.

### For a review deployment

| Variable | Value | Effect |
|---|---|---|
| `AUTO_SEED` | `true` | Creates the demo course, three quizzes and four identities on first start. Idempotent; never reconfigures a quiz that already has an active configuration version, so a redeploy cannot disturb a reviewer's work. |
| `DEMO_IDENTITIES` | `true` | `GET /api/session` lists the directory's identities **and their tokens**, which is how the test UI switches role without a login screen. |
| `SEED_ADMIN_TOKEN` | a value you generate | The seeded administrator's bearer token. |
| `SEED_LEARNER_TOKEN` | a value you generate | |
| `SEED_LEARNER2_TOKEN` | a value you generate | A second learner, so cross-learner isolation is demonstrable. |
| `SEED_ASSESSOR_TOKEN` | a value you generate | **Required to demonstrate UC-09 at all** — an administrator credential is refused on the assessor endpoints by design. |

`DEMO_IDENTITIES` exists because two correct requirements were in direct conflict: the guards are
only real when `ENVIRONMENT` is not development, and the reviewer can only sign in when the identity
list is exposed. One of the two had to become explicit, and it should be the one whose entire
meaning is *"this deployment hands out its own credentials"*. **A production deployment leaves it
unset**, the list disappears, and the only way in is a credential the operator issued.

The seed **refuses** to write its built-in fallback tokens (`admin-token`, `learner-token`, …) into
a non-development environment, because those are published in this repository. Generate real ones.

### Optional

| Variable | Default | Notes |
|---|---|---|
| `COACHING_LLM_PROVIDER` + `COACHING_LLM_API_KEY` | unset | The only genuinely external credential in the system. **Leave them unset and UC-07 reports coaching unavailable**, honestly, while the other nine capabilities work normally. It never degrades into invented teaching text. |
| `LOG_LEVEL` | `INFO` | Structured JSON to stdout. Values are redacted recursively; `token`, `secret`, `password` and `api_key` can never appear in log context. |
| `RETAKE_CONFIGURATION_POLICY` | `ACTIVE_AT_RETAKE` | An unrecognised value refuses to start rather than falling back silently. |
| `ANALYTICS_FLAG_THRESHOLD` | `40` | Wrong-answer percentage above which a question is flagged for content review. |

---

## Deploying to Railway

The repository carries [`Dockerfile`](../Dockerfile) and [`railway.json`](../railway.json), so
Railway needs no build configuration beyond pointing at the repo.

A Dockerfile rather than buildpack auto-detection because this repository needs **two** toolchains:
Node to build the React UI, Python to run the API. A buildpack that detects a language sees the root
`package.json`, decides "Node app", and produces an image with no Python in it.

1. **Create the database first.** Add a PostgreSQL service. Railway exposes `DATABASE_URL`; the
   application service references it. Do this first so the app's first boot has a database to
   migrate.
2. **Create the application service** from this repository. `railway.json` selects the Dockerfile
   and sets the health check to `/api/health` with a 120-second timeout — enough for a migration on
   a cold database.
3. **Set the variables** from the tables above.
4. **Deploy.** On start, `backend/scripts/start.py` migrates to `head`, optionally seeds, then binds
   `0.0.0.0:$PORT`. A migration failure is **fatal and loud**: serving an application whose schema
   is unknown produces scattered errors that describe symptoms rather than the cause.

### What "deployed successfully" has to mean

A completed build is not a working application. Check, in this order:

```bash
BASE=https://<your-service>.up.railway.app

# 1. Readiness — and that it checked the database rather than just answering.
curl -s $BASE/api/health | jq '{status, database, environment, modules: (.modules | length)}'
#   expect: status "ok", database "ok", environment "production", modules 10

# 2. The UI is served, not a 404.
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' $BASE/
#   expect: 200 text/html

# 3. A client-side route resolves to the app shell, not a 404.
curl -s -o /dev/null -w '%{http_code}\n' $BASE/analytics
#   expect: 200

# 4. An unknown API path is JSON, not the HTML shell. (A fallback that answered everything with
#    index.html would make a mistyped path look like a parse error somewhere unrelated.)
curl -s $BASE/api/nope | jq .error.code
#   expect: "NOT_FOUND"

# 5. The guards are real. This is the check that matters most: it carries the answer key.
curl -s -o /dev/null -w '%{http_code}\n' $BASE/api/question-bank/questions
#   expect: 401 — NOT 200

# 6. The API documentation a reviewer will read.
curl -s -o /dev/null -w '%{http_code}\n' $BASE/api/docs
#   expect: 200
```

If step 5 returns `200`, `ADMIN_API_TOKEN` is not set — and the application should not have started
at all, so also check `ENVIRONMENT`.

---

## Verifying a deployment the way a user would

The five gates run against a local server and prove the system works. They do not prove *this*
deployment works. After deploying, walk the journeys:

| Journey | Steps | What proves it |
|---|---|---|
| **A — standard quiz** | Admin: configure the unconfigured quiz → version 1 appears. Learner: read rules, start, answer, reload mid-attempt, submit. | Answers survive the reload; the result, outcome and feedback agree on one percentage. |
| **B — failed attempt** | Answer everything wrongly, submit. | Fail, no certificate, attempts remaining decremented, feedback still generated. |
| **C — passed attempt** | Pass the practice quiz. | Certificate `ISSUED` with a number; a second pass mints no second certificate. |
| **D — formal assessment** | Supervised Final Examination: conditions → identity → start → save → submit. Then switch to the assessor. | The certificate does **not** exist until the assessor approves. A second browser is refused the device lock. |
| **E — retake** | Fail twice, be refused, grant an extra attempt as admin, pass. | The quiz's configured maximum is unchanged; earlier attempts unchanged; one certificate. |
| **F — analytics** | Admin → Analytics, with and without filters; export a CSV. | Figures match what you just did; a course with no attempts says `NO_ATTEMPTS` with null rates, not 0%. |

Sections 30–34 of `npm run verify:e2e` automate the same journeys against a live server, which is
where they are checked continuously. Doing them by hand once on the deployed instance is what
confirms the deployment, not the code.

`npm run verify:deployment -- --base-url <url> --yes` walks all six over HTTP only — 88 checks — and
is the tool to point at the Railway URL. It reads its credentials from `GET /api/session` when
`DEMO_IDENTITIES` is on, so pointing it at the deployment is the whole invocation.

---

## Operational notes

**Health checks.** `/api/health/live` touches nothing — a liveness probe that queried the database
would restart healthy processes during a database blip and make an outage worse. `/api/health`
checks the database and returns **503** when it is unreachable, because a readiness probe that
always answers 200 cannot take a broken instance out of rotation.

**Scaling.** One worker per container. Every invariant that matters is enforced by a database
constraint rather than by in-process state — one open attempt per learner and quiz, at most one
successful submission, idempotent retries, one issued certificate per learner and quiz — so scaling
out means more containers, not more workers, and a loser in a race gets a clean `409`.

**Background work.** None required. The result chain runs inside the request that submitted the
attempt. UC-09's review-queue recovery and disconnect detection are *endpoints* the platform's own
scheduler may call (`/api/system/formal-assessments/...`, `SYSTEM_API_TOKEN`), deliberately not an
in-process timer — so nothing is lost when a container restarts.

**Logging.** Structured JSON to stdout, one line per event, with an `X-Request-Id` echoed in every
error body so a reviewer's screenshot ties to an exact log line. Redaction recurses into nested
context and is depth-capped against cyclic structures.

**Secrets.** Nothing is committed: `git ls-files` shows only `backend/.env.example`. No endpoint
returns a secret, and the only endpoint that returns *any* credential is `GET /api/session` under
`DEMO_IDENTITIES`, which is off unless deliberately switched on.

**Migrations.** Run at start-up, in-process, against the same `DATABASE_URL` the application will
use — so there is no second place for it to resolve differently. Forward-only in practice: several
downgrades deliberately fail if data exists that the older schema cannot represent, because
downgrading past the release that produced a value is a data question, not a schema one.

---

## What is deliberately not production-ready

Stated plainly, because a review is the moment to disagree with it.

1. **Identity is a placeholder.** `backend/app/modules/identity/` resolves a bearer token against a
   local table. Replacing `resolve_principal` is the whole of the integration; no business rule
   reads a user row directly. Until then there is no login, no password, no session expiry, and no
   token rotation.
2. **The frontend is a test UI.** It exists so the workflows can be exercised in a browser. It is
   not designed, not responsive, and not accessibility-audited. The backend is the product.
3. **Certificate and CPD delivery are in-process adapters.** Both sit behind retryable ports with
   real failure handling; neither talks to a real certificate service yet, because there is not one
   to talk to.
4. **No rate limiting.** Appropriate at a platform edge rather than here, but worth naming.
5. **One database, no read replica, no caching layer.** None of the ten capabilities needs one at
   review scale. UC-10's analytics reads through a projection whose implementation is a single line
   to change (`AnalyticsPorts.merged()`) if it ever does.
