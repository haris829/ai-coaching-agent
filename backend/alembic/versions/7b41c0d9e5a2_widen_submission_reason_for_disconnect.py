"""Allow DISCONNECT_AUTO_SUBMIT, and restore UC-01's configuration immutability trigger

UC-09 added ``DISCONNECT_AUTO_SUBMIT`` as a third submission reason and widened the CHECK
constraint on ``qd_attempts`` (see ``c156bd33962a``) — but not the matching one on
``qd_attempt_submissions``, which UC-03 writes in the same transaction.

The result was that the entire disconnect path failed at the flush. A learner whose device dropped
out of a supervised sitting got a 500 from ``POST /formal-attempts/{id}/disconnect``, no submission
row, and no committed work: the exact loss the auto-submit rule exists to prevent. It went
unnoticed because UC-09's own suite drives UC-03 through port fakes, which have no constraints, and
because no chain test disconnected. UC-11's scenario E does.

Revision ID: 7b41c0d9e5a2
Revises: 050128fbddef
Create Date: 2026-08-20

"""

from collections.abc import Sequence

from alembic import op

revision: str = "7b41c0d9e5a2"
down_revision: str | Sequence[str] | None = "050128fbddef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The three reasons an attempt can be committed for, as of UC-09.
_REASONS = "'LEARNER_CONFIRMED', 'TIME_EXPIRED', 'DISCONNECT_AUTO_SUBMIT'"

#: The two that existed before it.
_REASONS_BEFORE = "'LEARNER_CONFIRMED', 'TIME_EXPIRED'"

#: UC-01's central invariant, dropped by UC-09's batch rebuild of ``qc_configuration_versions``.
#: ``108e83e56e69`` now reinstates it, which fixes any database migrated from scratch — but a
#: database that already ran that revision will never run it again, so the repair has to happen
#: here as well. Idempotent, so a fresh migration simply recreates what it just created.
_VERSION_TRIGGER = "trg_qc_config_version_no_update"

_IMMUTABLE_MESSAGE = (
    "IMMUTABLE_CONFIGURATION_VERSION: configuration versions cannot be modified; "
    "create a new version instead"
)


def _ensure_version_immutability_trigger() -> None:
    """Recreate the trigger unless the backend keeps its own through an ALTER.

    PostgreSQL alters in place and never lost it; SQLite rebuilds and did.
    """
    if op.get_bind().dialect.name != "sqlite":
        return
    op.execute(f"DROP TRIGGER IF EXISTS {_VERSION_TRIGGER}")
    op.execute(
        f"""
CREATE TRIGGER {_VERSION_TRIGGER}
BEFORE UPDATE ON qc_configuration_versions
BEGIN
  SELECT RAISE(ABORT, '{_IMMUTABLE_MESSAGE}');
END;
"""
    )


def upgrade() -> None:
    # Dropped and recreated rather than altered: no backend widens a CHECK constraint in place.
    with op.batch_alter_table("qd_attempt_submissions", schema=None) as batch_op:
        batch_op.drop_constraint("ck_submission_reason", type_="check")
        batch_op.create_check_constraint(
            "ck_submission_reason", f"submission_reason IN ({_REASONS})"
        )

    _ensure_version_immutability_trigger()


def downgrade() -> None:
    # A submission already committed with DISCONNECT_AUTO_SUBMIT fails the rebuild, and should:
    # downgrading past the release that produces a value is a data question, not a schema one, and
    # it must surface rather than be silently rewritten. Same reasoning as ``c156bd33962a``.
    with op.batch_alter_table("qd_attempt_submissions", schema=None) as batch_op:
        batch_op.drop_constraint("ck_submission_reason", type_="check")
        batch_op.create_check_constraint(
            "ck_submission_reason", f"submission_reason IN ({_REASONS_BEFORE})"
        )
