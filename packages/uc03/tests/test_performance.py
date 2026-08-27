"""Requirement 12 - P95 <= 3 seconds under normal load.

This runs the real benchmark from `bench.p95` at a reduced request count so it
fits in a test suite. The latency figures are measured with `perf_counter`
around real `QAService.answer` calls; nothing is estimated.
"""

from __future__ import annotations

import pytest

from bench.p95 import Scenario, run
from uc03.config import Settings


@pytest.mark.slow
async def test_p95_under_normal_load_meets_the_target():
    scenario = Scenario(total_requests=120, concurrency=20)
    report = await run(scenario)

    results = report["results"]
    target = Settings().p95_target_ms

    assert results["count"] == 120
    assert report["logged_records"] == 120, "every request must be logged"
    assert report["timeouts"] == 0
    assert results["p95_ms"] <= target, (
        f"p95 {results['p95_ms']}ms exceeded the {target}ms target; "
        f"full results: {results}"
    )
    # Sanity: the benchmark must actually be exercising the slow path, not
    # accidentally measuring a no-op.
    assert results["p50_ms"] > 100, "dependency latency was not applied"


@pytest.mark.slow
async def test_no_request_exceeds_the_hard_timeout_under_normal_load():
    report = await run(Scenario(total_requests=120, concurrency=20))
    assert report["results"]["max_ms"] < Settings().timeout_ms
    assert report["statuses"].get("timeout", 0) == 0
