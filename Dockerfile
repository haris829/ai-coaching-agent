# Courses Quiz Agent — one image, one service.
#
# WHY A DOCKERFILE RATHER THAN NIXPACKS AUTO-DETECTION
# ----------------------------------------------------
# This repository needs two toolchains: Node to build the React test UI, Python to run the API.
# A buildpack that auto-detects a language picks one — it sees the root `package.json`, decides
# "Node app", and produces an image with no Python in it. Saying so explicitly is shorter than
# fighting the detection, and it pins both runtimes so a deploy six months from now builds the same
# thing as a deploy today.
#
# THE ARCHITECTURE THIS PRODUCES
# ------------------------------
# One service. The API serves `/api/...` and the built UI for everything else, so:
#
#   * there is one URL to hand a reviewer;
#   * the browser only ever makes same-origin requests, so CORS is not part of the deployment at
#     all — not "configured", absent. See `backend/app/web.py`.
#
# The database is a separate managed service (Railway Postgres), reached through `DATABASE_URL`.
# That is the only stateful component, and it is deliberately not in this image: the container's
# filesystem is discarded on every redeploy, so a SQLite file inside it would silently lose every
# attempt, result and certificate a reviewer had created.

# ---------------------------------------------------------------------------
# Stage 1 — build the test UI
# ---------------------------------------------------------------------------
FROM node:20-alpine AS web

WORKDIR /build

# Manifests first, so a source-only change does not re-resolve the dependency tree.
COPY package.json package-lock.json ./
COPY frontend/package.json ./frontend/

# `npm ci` needs the lockfile to match the manifests exactly, which is the point: a deploy must not
# quietly resolve a different dependency tree than the one that was tested.
RUN npm ci --workspaces --include-workspace-root

COPY frontend ./frontend

# `tsc -b && vite build` — the typecheck is part of the build on purpose. An image that compiled
# broken TypeScript into working-looking JavaScript is worse than a build that failed.
RUN npm run build --workspace @quiz-agent/frontend

# ---------------------------------------------------------------------------
# Stage 2 — the API, serving the assets built above
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS api

# PYTHONUNBUFFERED so log lines reach the platform's collector as they happen rather than when a
# buffer fills — the difference between watching a start-up and reading about it afterwards.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# `psycopg[binary]` ships its own libpq, so there is nothing to compile and no build-essential or
# libpq-dev to install. That is the whole reason for choosing the binary extra here.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend ./backend

# The built UI, at the path `FRONTEND_DIST` names below.
COPY --from=web /build/frontend/dist ./frontend/dist

# Run as a non-root user. Nothing here needs to write to the image, and a process that cannot
# modify its own code is one fewer thing an exploited dependency can do.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

WORKDIR /app/backend

ENV FRONTEND_DIST=/app/frontend/dist \
    ENVIRONMENT=production

# Documentation only — the platform routes to whatever `PORT` it injects, and
# `scripts/start.py` binds that. This line does not publish anything.
EXPOSE 8000

# Migrate, optionally bootstrap, then serve on 0.0.0.0:$PORT. The ordering and the reasons for it
# are in `backend/scripts/start.py`; it is a Python entry point rather than a shell chain so that
# the host and port come from the same settings object the application uses.
CMD ["python", "-m", "scripts.start"]
