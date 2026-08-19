"""FastAPI dependency aliases used by more than one router.

``DbSession`` was previously redeclared identically in six modules. One declaration means the
request-session contract changes in one place if it ever has to.

Only transport-level, capability-neutral dependencies belong here. Anything that resolves an
*identity* lives in ``app.modules.identity.security``; anything that assembles a *capability's*
collaborators lives in that capability (see ``quiz_configuration.context``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_db

#: A request-scoped database session. The route or service owns the transaction; this only
#: guarantees rollback-on-error and close.
DbSession = Annotated[Session, Depends(get_db)]
