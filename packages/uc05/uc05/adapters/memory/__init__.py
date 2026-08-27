"""In-memory adapter family.  Importing the package performs registration."""

from .repositories import (  # noqa: F401
    InMemoryDialogueRepository,
    InMemoryInteractionLogRepository,
    InMemorySessionModeRepository,
)
