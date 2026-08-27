"""JSON-file persistence: a lightweight local implementation, no production DB.

One file per record family under ``PERSISTENCE_DIR``. Writes are whole-file and
atomic-by-rename, which is enough for local development and keeps the failure
mode honest: either the new state is on disk or the previous state is.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any

from uc08.domain.errors import RepositoryReadFailed, RepositoryWriteFailed
from uc08.domain.models import Badge, FreezeOffer, StreakRecord, WeeklySummary
from uc08.ports.repositories import (
    BadgeRepository,
    FreezeOfferRepository,
    ProcessedInteractionStore,
    StreakRepository,
    WeeklySummaryRepository,
)


class _JsonTable:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = RLock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> dict[str, Any]:
        with self._lock:
            if not self._path.exists():
                return {}
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RepositoryReadFailed(f"could not read {self._path.name}") from exc

    def write(self, payload: dict[str, Any]) -> None:
        with self._lock:
            temporary = self._path.with_suffix(self._path.suffix + ".tmp")
            try:
                temporary.write_text(json.dumps(payload, default=str, indent=2), encoding="utf-8")
                os.replace(temporary, self._path)
            except OSError as exc:
                raise RepositoryWriteFailed(f"could not write {self._path.name}") from exc


class JsonFileStreakRepository(StreakRepository):
    def __init__(self, directory: str | Path) -> None:
        self._table = _JsonTable(Path(directory) / "streaks.json")

    def get(self, user_id: str) -> StreakRecord | None:
        row = self._table.read().get(user_id)
        return StreakRecord.model_validate(row) if row else None

    def save(self, streak: StreakRecord) -> None:
        rows = self._table.read()
        rows[streak.user_id] = json.loads(streak.model_dump_json())
        self._table.write(rows)


class JsonFileBadgeRepository(BadgeRepository):
    def __init__(self, directory: str | Path) -> None:
        self._table = _JsonTable(Path(directory) / "badges.json")

    def get_all(self, user_id: str) -> tuple[Badge, ...]:
        held = self._table.read().get(user_id, {})
        return tuple(Badge.model_validate(held[key]) for key in sorted(held, key=int))

    def award(self, badge: Badge) -> None:
        rows = self._table.read()
        held = rows.setdefault(badge.user_id, {})
        held.setdefault(str(badge.milestone), json.loads(badge.model_dump_json()))
        self._table.write(rows)


class JsonFileWeeklySummaryRepository(WeeklySummaryRepository):
    def __init__(self, directory: str | Path) -> None:
        self._table = _JsonTable(Path(directory) / "weekly_summaries.json")

    def save(self, summary: WeeklySummary) -> None:
        rows = self._table.read()
        rows.setdefault(summary.user_id, {})[summary.week] = json.loads(summary.model_dump_json())
        self._table.write(rows)

    def get(self, user_id: str, week: str) -> WeeklySummary | None:
        row = self._table.read().get(user_id, {}).get(week)
        return WeeklySummary.model_validate(row) if row else None

    def list_for_user(self, user_id: str) -> tuple[WeeklySummary, ...]:
        rows = self._table.read().get(user_id, {})
        return tuple(WeeklySummary.model_validate(rows[week]) for week in sorted(rows, reverse=True))


class JsonFileFreezeOfferRepository(FreezeOfferRepository):
    def __init__(self, directory: str | Path) -> None:
        self._table = _JsonTable(Path(directory) / "freeze_offers.json")

    def get_latest(self, user_id: str) -> FreezeOffer | None:
        rows = self._table.read().get(user_id, [])
        return FreezeOffer.model_validate(rows[-1]) if rows else None

    def save(self, offer: FreezeOffer) -> None:
        rows = self._table.read()
        offers = rows.setdefault(offer.user_id, [])
        payload = json.loads(offer.model_dump_json())
        for index, existing in enumerate(offers):
            if existing["offer_id"] == offer.offer_id:
                offers[index] = payload
                break
        else:
            offers.append(payload)
        self._table.write(rows)


class JsonFileProcessedInteractionStore(ProcessedInteractionStore):
    def __init__(self, directory: str | Path) -> None:
        self._table = _JsonTable(Path(directory) / "processed_interactions.json")

    def was_processed(self, user_id: str, interaction_id: str) -> bool:
        return interaction_id in self._table.read().get(user_id, [])

    def mark_processed(self, user_id: str, interaction_id: str) -> None:
        rows = self._table.read()
        seen = rows.setdefault(user_id, [])
        if interaction_id not in seen:
            seen.append(interaction_id)
            self._table.write(rows)
