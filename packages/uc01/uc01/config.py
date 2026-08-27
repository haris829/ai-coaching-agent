"""Configuration for the standalone UC-01 project.

Environment driven, with an optional ``.env`` file (parsed by hand — no extra
dependency). Every setting is documented in ``.env.example``.

The important switches for a future integration engineer are the four
``UC01_*_ADAPTER`` values: flipping one from ``mock`` to ``real`` is the whole
integration change on the UC-01 side.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .adapters.mock.scenarios import (
    CaseScenario,
    CoursesScenario,
    NaricScenario,
    ProfileScenario,
    ScenarioSet,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def load_env_file(path: Path = DEFAULT_ENV_FILE) -> Mapping[str, str]:
    """Parse a ``KEY=value`` file. Missing file is fine; existing env wins."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


class _Env:
    def __init__(self, file_values: Mapping[str, str]) -> None:
        self._file = dict(file_values)

    def get(self, key: str, default: str | None = None) -> str | None:
        return os.environ.get(key, self._file.get(key, default))

    def flag(self, key: str, default: bool) -> bool:
        raw = self.get(key)
        if raw is None:
            return default
        lowered = raw.strip().lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        return default

    def integer(self, key: str, default: int) -> int:
        raw = self.get(key)
        try:
            return int(raw) if raw is not None else default
        except ValueError:
            return default

    def enum(self, key: str, enum_cls, default):
        raw = self.get(key)
        if raw is None:
            return default
        for member in enum_cls:
            if member.value == raw.strip().lower():
                return member
        return default


@dataclass(frozen=True)
class Settings:
    """Resolved application settings."""

    environment: str = "development"
    dev_mode: bool = True
    host: str = "127.0.0.1"
    port: int = 8000

    persistence: str = "sqlite"
    database_path: str = str(PROJECT_ROOT / "data" / "uc01.sqlite3")
    auto_migrate: bool = True

    naric_adapter: str = "mock"
    courses_adapter: str = "mock"
    cases_adapter: str = "mock"
    profile_adapter: str = "mock"
    identity_provider: str = "dev"

    scenarios: ScenarioSet = field(default_factory=ScenarioSet)
    dev_scenario_header_enabled: bool = True

    log_level: str = "INFO"
    log_format: str = "json"
    expose_error_details: bool = False
    """Developer-only mode. When True, safe responses gain a ``debug`` block containing
    the technical detail. Never enable outside local development."""

    serve_frontend: bool = True

    @property
    def uses_only_mock_adapters(self) -> bool:
        return {
            self.naric_adapter,
            self.courses_adapter,
            self.cases_adapter,
            self.profile_adapter,
        } == {"mock"}

    def describe_adapters(self) -> Mapping[str, str]:
        return {
            "naric": self.naric_adapter,
            "courses": self.courses_adapter,
            "cases": self.cases_adapter,
            "profile": self.profile_adapter,
            "identity": self.identity_provider,
            "persistence": self.persistence,
        }


def load_settings(env_file: Path = DEFAULT_ENV_FILE) -> Settings:
    env = _Env(load_env_file(env_file))
    dev_mode = env.flag("UC01_DEV_MODE", True)
    return Settings(
        environment=env.get("UC01_ENV", "development") or "development",
        dev_mode=dev_mode,
        host=env.get("UC01_HOST", "127.0.0.1") or "127.0.0.1",
        port=env.integer("UC01_PORT", 8000),
        persistence=(env.get("UC01_PERSISTENCE", "sqlite") or "sqlite").lower(),
        database_path=env.get("UC01_DATABASE_PATH", str(PROJECT_ROOT / "data" / "uc01.sqlite3"))
        or str(PROJECT_ROOT / "data" / "uc01.sqlite3"),
        auto_migrate=env.flag("UC01_AUTO_MIGRATE", True),
        naric_adapter=(env.get("UC01_NARIC_ADAPTER", "mock") or "mock").lower(),
        courses_adapter=(env.get("UC01_COURSES_ADAPTER", "mock") or "mock").lower(),
        cases_adapter=(env.get("UC01_CASES_ADAPTER", "mock") or "mock").lower(),
        profile_adapter=(env.get("UC01_PROFILE_ADAPTER", "mock") or "mock").lower(),
        identity_provider=(env.get("UC01_IDENTITY_PROVIDER", "dev") or "dev").lower(),
        scenarios=ScenarioSet(
            naric=env.enum("UC01_MOCK_NARIC", NaricScenario, NaricScenario.PER_USER),
            courses=env.enum("UC01_MOCK_COURSES", CoursesScenario, CoursesScenario.AVAILABLE),
            cases=env.enum("UC01_MOCK_CASES", CaseScenario, CaseScenario.AVAILABLE),
            profile=env.enum("UC01_MOCK_PROFILE", ProfileScenario, ProfileScenario.AVAILABLE),
        ),
        # The dev scenario header can never be enabled unless dev mode is on.
        dev_scenario_header_enabled=dev_mode
        and env.flag("UC01_DEV_SCENARIO_HEADER", True),
        log_level=(env.get("UC01_LOG_LEVEL", "INFO") or "INFO").upper(),
        log_format=(env.get("UC01_LOG_FORMAT", "json") or "json").lower(),
        expose_error_details=dev_mode and env.flag("UC01_EXPOSE_ERROR_DETAILS", False),
        serve_frontend=env.flag("UC01_SERVE_FRONTEND", True),
    )


__all__ = ["PROJECT_ROOT", "Settings", "load_env_file", "load_settings"]
