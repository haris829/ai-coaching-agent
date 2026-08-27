"""The integration swap, executed and measured.

`docs/INTEGRATION.md` claims a real provider costs one new adapter file, one
registry line and one environment variable. This test performs that swap against
a brand-new adapter family that no existing file has ever heard of, and then
checks -- from the repository itself -- that nothing else changed.

The adapter below stands in for the "one new file". It is a third family, with a
third payload shape, written the way the template tells an integrator to write
one.
"""

from __future__ import annotations

import ast
import pathlib
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from uc08.adapters.clock.clocks import FixedClock
from uc08.api.app import create_app
from uc08.composition import build_container
from uc08.config import load_settings
from uc08.domain.enums import SourceStatus
from uc08.domain.errors import ProviderInvalidResponse, ProviderTimeout, ProviderUnavailable
from uc08.domain.models import (
    ActivityInteraction,
    ActivityWindowRead,
    QuestionCountRead,
    Topic,
    TopicMention,
    TopicsRead,
)
from uc08.domain.naric import normalise_completion_percent, normalise_naric_level
from uc08.domain.time_utils import ensure_utc
from uc08.ports.clock import Clock
from uc08.ports.conformance import CONFORMANCE_USER_ID, REQUIRED_CONFORMANCE_SCENARIOS
from uc08.ports.upstream import ActivityProvider, GapReportProvider
from uc08.registry import ACTIVITY_PROVIDERS, GAP_REPORT_PROVIDERS, ProviderEntry

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SUBJECT = "learner-7781"
HEADERS = {"X-UC08-Subject": SUBJECT}
NOW = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)


# ==========================================================================
# "One new adapter file". A third payload family:
#   * the account key is "profileHandle"
#   * time is an ISO-8601 string with a +05:30 offset, not UTC and not epoch
#   * interactions are a flat list under "log", keyed "ref"
#   * the count is nested at "counters.answered" as a float
#   * the NARIC level arrives as "NARIC 7+" and completion as 40 (an int)
# ==========================================================================
class _AcmeUpstream:
    """A stand-in for the third upstream. Nothing here reaches a network."""

    KOLKATA = timezone(timedelta(hours=5, minutes=30))

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.fault = "none"

    def record_for(self, handle: str) -> dict[str, Any]:
        return self.records.setdefault(handle, {"profileHandle": handle, "log": [], "counters": {}})

    def log_entry(self, handle: str, moment: datetime, ref: str, subject: str | None = None) -> None:
        entry = {"ref": ref, "when": ensure_utc(moment).astimezone(self.KOLKATA).isoformat()}
        if subject:
            entry["area"] = subject
        self.record_for(handle)["log"].append(entry)

    def set_answered(self, handle: str, count: float | None) -> None:
        counters = self.record_for(handle)["counters"]
        if count is None:
            counters.pop("answered", None)
        else:
            counters["answered"] = float(count)

    def set_next_topic(self, handle: str, payload: dict[str, Any] | None) -> None:
        self.record_for(handle)["nextTopic"] = payload

    def read(self, handle: str) -> dict[str, Any]:
        if self.fault == "down":
            raise AcmeUnreachable("acme-gw-3: no healthy upstream for shard 12")
        if self.fault == "slow":
            raise AcmeTimedOut("acme-gw-3: read exceeded 5000ms budget")
        if self.fault == "garbled":
            return {"profileHandle": handle, "log": "not-a-list"}
        return self.record_for(handle)


class AcmeUnreachable(RuntimeError):
    """Vendor-shaped. Must never escape the adapter."""


class AcmeTimedOut(RuntimeError):
    """Vendor-shaped. Must never escape the adapter."""


class AcmeActivityAdapter(ActivityProvider):
    def __init__(self, clock: Clock, *, timeout_seconds: float = 5.0, upstream: _AcmeUpstream | None = None) -> None:
        self._clock = clock
        self._timeout_seconds = timeout_seconds
        self._upstream = upstream if upstream is not None else _AcmeUpstream()

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    def last_activity_at(self, user_id: str) -> datetime | None:
        entries = self._entries(user_id)
        return max((moment for moment, _ref, _area in entries), default=None)

    def interactions_in_window(self, user_id: str, since: datetime) -> ActivityWindowRead:
        boundary = ensure_utc(since)
        found = tuple(
            ActivityInteraction(interaction_id=ref, occurred_at=moment)
            for moment, ref, _area in sorted(self._entries(user_id), key=lambda row: row[0])
            if moment >= boundary
        )
        return ActivityWindowRead(
            interactions=found, status=SourceStatus.AVAILABLE if found else SourceStatus.EMPTY
        )

    def question_count(self, user_id: str) -> QuestionCountRead:
        body = self._read(user_id)
        counters = body.get("counters")
        if not isinstance(counters, dict):
            raise ProviderInvalidResponse(self.port_name, "activity response shape is not usable")
        if "answered" not in counters:
            return QuestionCountRead(count=0, status=SourceStatus.EMPTY)
        raw = counters["answered"]
        try:
            count = int(float(raw))
        except (TypeError, ValueError) as exc:
            raise ProviderInvalidResponse(self.port_name, "question count is not an integer") from exc
        return QuestionCountRead(count=count, status=SourceStatus.AVAILABLE)

    def topics_in_window(self, user_id: str, since: datetime) -> TopicsRead:
        boundary = ensure_utc(since)
        first_seen: dict[str, datetime] = {}
        for moment, _ref, area in sorted(self._entries(user_id), key=lambda row: row[0]):
            if moment >= boundary and area:
                first_seen.setdefault(area, moment)
        mentions = tuple(
            TopicMention(name=name, first_mentioned_at=moment) for name, moment in first_seen.items()
        )
        return TopicsRead(
            topics=mentions, status=SourceStatus.AVAILABLE if mentions else SourceStatus.EMPTY
        )

    def _read(self, user_id: str) -> dict[str, Any]:
        try:
            return self._upstream.read(user_id)
        except AcmeTimedOut as exc:
            raise ProviderTimeout(self.port_name, f"deadline of {self._timeout_seconds}s exceeded") from exc
        except AcmeUnreachable as exc:
            raise ProviderUnavailable(self.port_name, "activity read model did not answer") from exc

    def _entries(self, user_id: str) -> list[tuple[datetime, str, str | None]]:
        body = self._read(user_id)
        rows = body.get("log")
        if not isinstance(rows, list):
            raise ProviderInvalidResponse(self.port_name, "activity collection shape is not usable")
        translated = []
        for row in rows:
            ref, when = row.get("ref"), row.get("when")
            if not ref or not when:
                raise ProviderInvalidResponse(
                    self.port_name, "activity entry is missing an identifier or a timestamp"
                )
            parsed = datetime.fromisoformat(when)
            if parsed.tzinfo is None:
                raise ProviderInvalidResponse(self.port_name, "timestamp carries no timezone")
            translated.append((ensure_utc(parsed), str(ref), row.get("area")))
        return translated

    @classmethod
    def conformance_scenarios(cls) -> Mapping[str, Callable[[Clock], ActivityProvider]]:
        return _ACME_ACTIVITY_SCENARIOS


class AcmeGapReportAdapter(GapReportProvider):
    def __init__(self, clock: Clock, *, timeout_seconds: float = 5.0, upstream: _AcmeUpstream | None = None) -> None:
        self._clock = clock
        self._timeout_seconds = timeout_seconds
        self._upstream = upstream if upstream is not None else _AcmeUpstream()

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    def suggested_topic(self, user_id: str) -> Topic | None:
        try:
            body = self._upstream.read(user_id)
        except AcmeTimedOut as exc:
            raise ProviderTimeout(self.port_name, f"deadline of {self._timeout_seconds}s exceeded") from exc
        except AcmeUnreachable as exc:
            raise ProviderUnavailable(self.port_name, "gap report did not answer") from exc

        payload = body.get("nextTopic")
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise ProviderInvalidResponse(self.port_name, "gap report response shape is not usable")
        code, title = payload.get("code"), payload.get("title")
        if not code or not title:
            raise ProviderInvalidResponse(
                self.port_name, "gap report suggestion is missing an identifier or a name"
            )
        level = normalise_naric_level(payload.get("naricBand"), port=self.port_name)
        progress = normalise_completion_percent(payload.get("progressPct"), port=self.port_name)
        return Topic(
            topic_id=str(code),
            name=str(title),
            naric_level=level.level,
            naric_level_source=level.source,
            naric_level_status=level.status,
            explanation_profile=level.explanation_profile,
            course_progress_percent=progress.percent,
            course_progress_status=progress.status,
        )

    @classmethod
    def conformance_scenarios(cls) -> Mapping[str, Callable[[Clock], GapReportProvider]]:
        return _ACME_GAP_SCENARIOS


def _acme_activity(fault: str = "none", *, offset_hours: int = 23, offset_minutes: int = 59, count: float = 1.0):
    def build(clock: Clock) -> ActivityProvider:
        upstream = _AcmeUpstream()
        if fault == "none":
            upstream.log_entry(
                CONFORMANCE_USER_ID,
                clock.now() - timedelta(hours=offset_hours, minutes=offset_minutes),
                f"prior-{offset_hours}h{offset_minutes:02d}m",
                "professional-conduct",
            )
            upstream.set_answered(CONFORMANCE_USER_ID, count)
        elif fault == "empty":
            upstream.record_for(CONFORMANCE_USER_ID)
        else:
            upstream.fault = fault
        return AcmeActivityAdapter(clock, upstream=upstream)

    return build


def _acme_gap(fault: str = "none", *, topic: dict[str, Any] | None = None):
    def build(clock: Clock) -> GapReportProvider:
        upstream = _AcmeUpstream()
        if fault == "none":
            upstream.set_next_topic(CONFORMANCE_USER_ID, topic)
        elif fault == "empty":
            upstream.set_next_topic(CONFORMANCE_USER_ID, None)
        else:
            upstream.fault = fault
        return AcmeGapReportAdapter(clock, upstream=upstream)

    return build


_ACME_TOPIC = {
    "code": "topic-solicitors-accounts",
    "title": "Solicitors Accounts Rules",
    "naricBand": "NARIC 7+",
    "progressPct": 40,
}

_ACME_ACTIVITY_SCENARIOS: Mapping[str, Callable[[Clock], ActivityProvider]] = {
    "available": _acme_activity(),
    "empty": _acme_activity("empty"),
    "unavailable": _acme_activity("down"),
    "timeout": _acme_activity("slow"),
    "invalid": _acme_activity("garbled"),
}

_ACME_GAP_SCENARIOS: Mapping[str, Callable[[Clock], GapReportProvider]] = {
    "available": _acme_gap(topic=dict(_ACME_TOPIC)),
    "empty": _acme_gap("empty"),
    "unavailable": _acme_gap("down"),
    "timeout": _acme_gap("slow"),
    "invalid": _acme_gap(topic={"code": "", "title": ""}),
}


# ==========================================================================
# The swap
# ==========================================================================
@pytest.fixture
def swapped(monkeypatch):
    """One registry line per port, one config value per port. Nothing else."""
    # --- the registry lines ---
    monkeypatch.setitem(
        ACTIVITY_PROVIDERS,
        "acme",
        ProviderEntry(f"{__name__}:AcmeActivityAdapter", "acme activity read model"),
    )
    monkeypatch.setitem(
        GAP_REPORT_PROVIDERS,
        "acme",
        ProviderEntry(f"{__name__}:AcmeGapReportAdapter", "acme gap report"),
    )
    # --- the config values ---
    monkeypatch.setenv("ACTIVITY_PROVIDER", "acme")
    monkeypatch.setenv("GAP_REPORT_PROVIDER", "acme")
    return None


def test_the_registry_resolves_the_new_family_from_config_alone(swapped):
    from uc08.registry import resolve_provider_class

    settings = load_settings()
    assert settings.activity_provider == "acme"
    assert resolve_provider_class("activity", settings.activity_provider) is AcmeActivityAdapter
    assert resolve_provider_class("gap_report", settings.gap_report_provider) is AcmeGapReportAdapter


def test_the_conformance_suite_covers_the_new_family_without_a_new_test(swapped):
    """The kit discovers adapters from the registry, so this family is in it."""
    from tests.conformance.conftest import adapters_for, scenarios_of

    activity = dict(adapters_for("activity"))
    gap = dict(adapters_for("gap_report"))
    assert activity["acme"] is AcmeActivityAdapter
    assert gap["acme"] is AcmeGapReportAdapter

    for adapter_class in (AcmeActivityAdapter, AcmeGapReportAdapter):
        builders = scenarios_of(adapter_class)
        assert set(REQUIRED_CONFORMANCE_SCENARIOS) <= set(builders)


def test_the_unmodified_service_works_end_to_end_on_the_new_family(swapped):
    """No domain, application, API, persistence or test file was touched."""
    clock = FixedClock(NOW)
    upstream = _AcmeUpstream()
    upstream.set_answered(SUBJECT, 50.0)
    upstream.set_next_topic(SUBJECT, dict(_ACME_TOPIC))

    container = build_container(
        load_settings(),
        clock=clock,
        activity=AcmeActivityAdapter(clock, upstream=upstream),
        gap_report=AcmeGapReportAdapter(clock, upstream=upstream),
    )
    client = TestClient(create_app(container))

    upstream.log_entry(SUBJECT, clock.now(), "i-1", "conduct")
    first = client.post(
        "/api/v1/streaks/record-activity",
        json={"interaction_id": "i-1", "session_id": "sess-1"},
        headers=HEADERS,
    )
    assert first.status_code == 200
    assert first.json()["outcome"] == "started"
    assert [badge["milestone"] for badge in first.json()["awarded_badges"]] == [10, 50]

    # The boundary rule holds against a third payload family, unchanged.
    clock.advance(hours=23, minutes=59)
    upstream.log_entry(SUBJECT, clock.now(), "i-2", "conduct")
    inside = client.post(
        "/api/v1/streaks/record-activity",
        json={"interaction_id": "i-2", "session_id": "sess-1"},
        headers=HEADERS,
    )
    assert inside.json()["outcome"] == "incremented"
    assert inside.json()["streak"]["current_streak_days"] == 2

    clock.advance(hours=24, minutes=1)
    upstream.log_entry(SUBJECT, clock.now(), "i-3", "conduct")
    outside = client.post(
        "/api/v1/streaks/record-activity",
        json={"interaction_id": "i-3", "session_id": "sess-1"},
        headers=HEADERS,
    )
    assert outside.json()["outcome"] == "reset"
    assert outside.json()["streak"]["current_streak_days"] == 1
    assert outside.json()["streak"]["longest_streak_days"] == 2


def test_a_third_time_representation_is_normalised_at_the_boundary(swapped):
    """The upstream sends +05:30 ISO strings; nothing above the port sees that."""
    clock = FixedClock(NOW)
    upstream = _AcmeUpstream()
    upstream.log_entry(SUBJECT, NOW - timedelta(hours=23, minutes=59), "prior")
    adapter = AcmeActivityAdapter(clock, upstream=upstream)

    last = adapter.last_activity_at(SUBJECT)
    assert last == NOW - timedelta(hours=23, minutes=59)
    assert last.utcoffset() == timedelta(0)

    raw = upstream.record_for(SUBJECT)["log"][0]["when"]
    assert "+05:30" in raw  # the upstream really did send an offset


def test_a_third_level_spelling_normalises_to_the_platform_enum(swapped):
    clock = FixedClock(NOW)
    upstream = _AcmeUpstream()
    upstream.set_next_topic(SUBJECT, dict(_ACME_TOPIC))

    topic = AcmeGapReportAdapter(clock, upstream=upstream).suggested_topic(SUBJECT)

    assert topic is not None
    assert topic.naric_level.value == "level_7_plus"
    assert topic.explanation_profile.value == "advanced"
    assert topic.course_progress_percent == 40


def test_no_vendor_detail_escapes_the_new_adapter(swapped):
    clock = FixedClock(NOW)
    down = _AcmeUpstream()
    down.fault = "down"
    adapter = AcmeActivityAdapter(clock, upstream=down)

    with pytest.raises(ProviderUnavailable) as caught:
        adapter.question_count(SUBJECT)

    message = str(caught.value).lower()
    for token in ("acme", "gw-3", "shard", "healthy upstream"):
        assert token not in message


def test_the_swap_touched_only_the_registry_and_the_config():
    """Measured against the repository, not asserted in prose.

    The registry is the only file outside ``uc08/adapters/`` that names an
    adapter class, and the layers the swap rule protects contain no provider
    name at all.
    """
    protected = ["uc08/domain", "uc08/application", "uc08/api", "uc08/adapters/persistence"]
    provider_names = set(ACTIVITY_PROVIDERS) | set(GAP_REPORT_PROVIDERS) | {"acme", "company"}

    for layer in protected:
        for path in (REPO_ROOT / layer).rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    assert node.value not in provider_names, f"{path}:{node.lineno} names a provider"
            assert "adapters." not in source.replace("uc08.adapters.persistence", ""), path

    # And the composition root selects providers only through the registry.
    composition = (REPO_ROOT / "uc08" / "composition.py").read_text(encoding="utf-8")
    assert "build_provider(" in composition
    assert "Adapter" not in composition
    assert "Mock" not in composition
