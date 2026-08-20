"""Production entry point: migrate, then serve.

    python -m scripts.start

Three things have to happen in one order, and getting the order wrong produces failures that look
like something else:

1. **Migrate.** A fresh managed database is empty. Starting the server first gives a reviewer a
   working health check and a 500 from every endpoint that touches a table.
2. **Bootstrap, if asked.** A deployment nobody can sign in to and with no quiz to sit is not
   reviewable. Off by default and skipped entirely when the database already holds a quiz.
3. **Serve**, bound to the interface and port the platform dictates.

WHY A PYTHON SCRIPT AND NOT A SHELL ONE-LINER
---------------------------------------------
``alembic upgrade head && uvicorn --host 0.0.0.0 --port $PORT`` looks equivalent and is not. It
duplicates the settings object's knowledge of which host and port to use, so a change to
``Settings`` and a change to the start command have to be made together and are not checked against
each other. It also cannot distinguish "the migration failed" from "the migration is already at
head" without parsing output, and on a platform that restarts a crashed container it will retry the
failing migration forever with no diagnosis in between.

Here, a migration failure is fatal and says so once, with the database's own error. That is the
correct behaviour: serving an application whose schema is unknown is worse than not starting.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from app.core.logging import configure_logging, get_logger  # noqa: E402

logger = get_logger("scripts.start")


def run_migrations() -> None:
    """Bring the database to ``head``.

    Called in-process rather than by shelling out, so the same ``DATABASE_URL`` the application
    will use is the one that gets migrated — there is no second place for it to be resolved
    differently.
    """
    from alembic.config import Config

    from alembic import command

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)

    logger.info(
        "start.migrating",
        extra={"database": "sqlite" if settings.is_sqlite else "server"},
    )
    command.upgrade(config, "head")
    logger.info("start.migrated")


def bootstrap_if_requested() -> None:
    """Seed the demo course, quiz, formal quiz and identities — once, and never over real work.

    Guarded three ways, because a bootstrap that runs at the wrong moment destroys exactly the data
    a reviewer was in the middle of producing:

    * ``AUTO_SEED`` is off unless set;
    * ``ENVIRONMENT=test`` ignores it outright, so no test can race it;
    * and the seed itself is idempotent and refuses to touch a database that already has a quiz.
    """
    if not settings.auto_seed:
        logger.info("start.seed_skipped", extra={"reason": "AUTO_SEED is off"})
        return
    if settings.is_test:
        logger.info("start.seed_skipped", extra={"reason": "ENVIRONMENT=test"})
        return

    from scripts.seed import seed

    try:
        summary = seed()
    except Exception as exc:  # pragma: no cover - a broken seed must not block the API
        # Deliberately not fatal. A failed bootstrap leaves an empty but *working* system, which an
        # operator can seed by hand; refusing to start would leave them with no API to do it
        # through. Logged at error so it is not mistaken for success.
        logger.error("start.seed_failed", exc_info=exc)
        return
    logger.info("start.seeded", extra={"summary": summary})


def main() -> int:
    configure_logging()

    try:
        run_migrations()
    except Exception as exc:
        # Fatal, and it must be. An application serving against an unknown schema fails later, in
        # scattered places, with errors that describe the symptom rather than this cause.
        logger.critical("start.migration_failed", exc_info=exc)
        return 1

    bootstrap_if_requested()

    import uvicorn

    host = settings.bind_host
    logger.info("start.serving", extra={"host": host, "port": settings.port})
    uvicorn.run(
        "app.main:app",
        host=host,
        port=settings.port,
        # No reload, one worker. The submission pipeline runs in the request that submitted the
        # attempt, and every invariant that matters is enforced by a database constraint rather
        # than by in-process state — so scaling out is a matter of running more containers, not
        # more workers inside one, and a single worker keeps the log stream readable.
        workers=1,
        log_config=None,  # keep this application's JSON logging rather than uvicorn's own
        access_log=True,
        proxy_headers=True,  # behind the platform's router, so X-Forwarded-* is authoritative
        forwarded_allow_ips="*",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
