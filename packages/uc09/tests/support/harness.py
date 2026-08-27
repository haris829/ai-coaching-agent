"""Test harness: a wired application with selected ports overridden.

Everything is built through the real composition root, so a test exercises the
same wiring the service runs with. Overrides install a specific scenario
adapter for one port and leave every other port exactly as configuration
selects it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient

from uc09_summary.adapters.mock.clock import FixedClock
from uc09_summary.adapters.real.identity import USER_HEADER
from uc09_summary.api.app import create_app
from uc09_summary.application.summary_service import SummaryService
from uc09_summary.composition import Container, build_container
from uc09_summary.config import load_settings


@dataclass
class Harness:
    """A wired application plus direct handles on the ports a test inspects."""

    container: Container
    client: TestClient

    @property
    def service(self) -> SummaryService:
        return self.container.service

    @property
    def clock(self) -> Any:
        return self.container.providers["clock"]

    @property
    def summaries(self) -> Any:
        return self.container.providers["summary_repository"]

    @property
    def downloads(self) -> Any:
        return self.container.providers["download_log_repository"]

    @property
    def renderer(self) -> Any:
        return self.container.providers["document_renderer"]

    def as_user(self, user_id: str) -> dict[str, str]:
        """Headers identifying a caller."""
        return {USER_HEADER: user_id}


def build_harness(
    *,
    overrides: dict[str, Any] | None = None,
    **settings_overrides: Any,
) -> Harness:
    """Build a harness.

    Args:
        overrides: port name -> already-built adapter instance.
        settings_overrides: settings fields, e.g. ``session_provider="foreign"``.
    """
    settings = load_settings(
        clock="fixed",
        document_renderer="fake",
        log_json=True,
        **settings_overrides,
    )
    supplied = dict(overrides or {})
    supplied.setdefault("clock", FixedClock())
    container = build_container(settings, overrides=supplied)
    app = create_app(container)
    return Harness(container=container, client=TestClient(app))
