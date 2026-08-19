"""Identifier generation.

UC-03 uses UUID4 strings for all primary keys: opaque, safe in URLs, and generable
by a client without coordinating with the server (which is what lets a client mint
its own idempotency key).
"""

from __future__ import annotations

import uuid


def new_id() -> str:
    return str(uuid.uuid4())


def is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True
