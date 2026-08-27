"""A file-backed `SessionModeRepository`.

This adapter exists as **evidence for the integration swap rule**. It was
written after the rest of the component was finished and every test was
passing, specifically to demonstrate that adding a real implementation of a
port costs exactly three things:

1.  this one new file;
2.  one line in ``uc05.composition.ADAPTER_MODULES``;
3.  one environment variable, ``SESSION_MODE_REPOSITORY=jsonfile``.

Nothing else changed. See the "Integration Swap Proof" section of the report
for the literal diff and the list of files that were *not* touched.

It also happens to be the useful shape for a standalone run: mode state
survives a process restart, so a page refresh that lands on a fresh worker
still finds the learner's setting. It is a lightweight local store, not a
production database: no ORM, no schema migration, no company assumptions.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from ...config import Settings
from ...domain.enums import ModeSource
from ...domain.errors import ProviderUnavailable
from ...domain.models import ModeState, utcnow
from ...registry import SESSION_MODE_REPOSITORY_REGISTRY

PORT = "session_mode_repository"
DEFAULT_PATH = Path(".uc05-session-modes.json")


@SESSION_MODE_REPOSITORY_REGISTRY.register("jsonfile")
class JsonFileSessionModeRepository:
    def __init__(self, settings: Settings | None = None, **_: object) -> None:
        raw = os.environ.get("SESSION_MODE_FILE")
        self._path = Path(raw) if raw else DEFAULT_PATH
        self._lock = asyncio.Lock()

    # -- port ------------------------------------------------------------

    async def get_mode(self, session_id: str) -> ModeState | None:
        """Returns ``None`` for an unseen session.

        It deliberately does not invent a default: the application owns that,
        so two implementations of this port cannot disagree about what "unset"
        means.
        """
        async with self._lock:
            return self._read().get(session_id)

    async def set_mode(
        self, session_id: str, enabled: bool, owner_user_id: str
    ) -> ModeState:
        async with self._lock:
            stored = self._read()
            existing = stored.get(session_id)
            state = ModeState(
                session_id=session_id,
                enabled=enabled,
                source=ModeSource.PERSISTED,
                owner_user_id=(
                    existing.owner_user_id
                    if existing and existing.owner_user_id
                    else owner_user_id
                ),
                updated_at=utcnow(),
            )
            stored[session_id] = state
            self._write(stored)
            return state

    # -- storage ---------------------------------------------------------

    def _read(self) -> dict[str, ModeState]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # An unreadable store is an infrastructure failure, and it must
            # leave this adapter as a typed error rather than as whatever the
            # filesystem or the JSON parser raised.
            raise ProviderUnavailable(PORT, "mode store unreadable") from exc
        if not isinstance(raw, dict):
            raise ProviderUnavailable(PORT, "mode store corrupt")
        return {
            session_id: ModeState.model_validate(payload)
            for session_id, payload in raw.items()
        }

    def _write(self, states: dict[str, ModeState]) -> None:
        payload = {
            session_id: json.loads(state.model_dump_json())
            for session_id, state in states.items()
        }
        try:
            # Write to a temporary file and replace, so a crash mid-write
            # cannot leave a truncated store behind.
            self._path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                dir=str(self._path.parent), suffix=".tmp"
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as out:
                json.dump(payload, out, indent=1, sort_keys=True)
            os.replace(temporary, self._path)
        except OSError as exc:
            raise ProviderUnavailable(PORT, "mode store not writable") from exc
