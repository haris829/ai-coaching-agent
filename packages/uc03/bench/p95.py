"""P95 latency benchmark for UC-03 (requirement 12).

Measures real wall-clock latency of `QAService.answer` under a defined
"normal load" scenario. Nothing here is simulated arithmetic: every request
runs the full pipeline - classification, context, topic tagging, authority
lookup, generation, citation guard and logging - and the elapsed time is taken
with `time.perf_counter` around the real coroutine.

NORMAL LOAD (the scenario this benchmark defines)
-------------------------------------------------
  * 300 questions, 20 concurrently in flight
  * question mix: 60% legal-concept, 15% process, 10% definitional,
    10% ambiguous (clarification), 5% out-of-scope
  * dependency latency, drawn from a seeded uniform distribution:
        context provider    5-25 ms
        authority provider  20-120 ms
        answer generator    300-1200 ms
        question log        2-10 ms

The generator profile is the load-bearing assumption: it stands in for a real
LLM-backed generator. It must be re-measured against the production generator
before this number is quoted as a production SLO - see the note printed with
the results.

Usage:
    python -m bench.p95            # default scenario
    python -m bench.p95 --json     # machine-readable
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from dataclasses import dataclass

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from uc03.adapters.mocks import (  # noqa: E402
    InMemoryQuestionLogger,
    MockContextProvider,
    MockLegalAuthorityProvider,
    SlowAnswerGenerator,
    StaticSessionAuthorizer,
    SystemClock,
    full_context,
)
from uc03.adapters.rule_based import (  # noqa: E402
    RuleBasedClassifier,
    RuleBasedTopicTagger,
    TemplateAnswerGenerator,
)
from uc03.config import Settings  # noqa: E402
from uc03.domain.enums import ResponseStatus  # noqa: E402
from uc03.domain.models import Principal  # noqa: E402
from uc03.service import QAService  # noqa: E402

SESSION = "session-alice-1"
PRINCIPAL = Principal(user_id="user-alice")

QUESTION_MIX: tuple[tuple[str, int], ...] = (
    ("What is negligence in tort law?", 30),
    ("What is consideration in contract law?", 30),
    ("How do I file a claim in the small claims court?", 15),
    ("What does mens rea mean?", 10),
    ("Tell me about consideration", 10),
    ("What is the weather tomorrow?", 5),
)


@dataclass(frozen=True)
class Scenario:
    total_requests: int = 300
    concurrency: int = 20
    context_ms: tuple[int, int] = (5, 25)
    authority_ms: tuple[int, int] = (20, 120)
    generator_ms: tuple[int, int] = (300, 1200)
    logger_ms: tuple[int, int] = (2, 10)
    seed: int = 20260824


def _delay(rng: random.Random, bounds: tuple[int, int]):
    lo, hi = bounds
    return lambda: rng.uniform(lo / 1000, hi / 1000)


def build_service(scenario: Scenario) -> tuple[QAService, InMemoryQuestionLogger]:
    rng = random.Random(scenario.seed)
    logger = InMemoryQuestionLogger(delay=_delay(rng, scenario.logger_ms))
    service = QAService(
        classifier=RuleBasedClassifier(),
        generator=SlowAnswerGenerator(
            inner=TemplateAnswerGenerator(), delay=_delay(rng, scenario.generator_ms)
        ),
        context_provider=MockContextProvider(
            builder=full_context, delay=_delay(rng, scenario.context_ms)
        ),
        authority_provider=MockLegalAuthorityProvider(
            delay=_delay(rng, scenario.authority_ms)
        ),
        tagger=RuleBasedTopicTagger(),
        logger=logger,
        authorizer=StaticSessionAuthorizer(),
        clock=SystemClock(),
        settings=Settings(),
    )
    return service, logger


def _question_sequence(scenario: Scenario) -> list[str]:
    rng = random.Random(scenario.seed + 1)
    population = [q for q, weight in QUESTION_MIX for _ in range(weight)]
    return [rng.choice(population) for _ in range(scenario.total_requests)]


async def run(scenario: Scenario) -> dict:
    service, logger = build_service(scenario)
    questions = _question_sequence(scenario)
    semaphore = asyncio.Semaphore(scenario.concurrency)
    samples: list[float] = []
    statuses: dict[str, int] = {}

    async def one(question: str) -> None:
        async with semaphore:
            started = time.perf_counter()
            response = await service.answer(
                question=question, session_id=SESSION, principal=PRINCIPAL
            )
            samples.append((time.perf_counter() - started) * 1000)
            statuses[response.status.value] = statuses.get(response.status.value, 0) + 1

    wall_start = time.perf_counter()
    await asyncio.gather(*(one(q) for q in questions))
    wall = time.perf_counter() - wall_start

    ordered = sorted(samples)

    def pct(p: float) -> float:
        # Nearest-rank percentile - no interpolation, no flattery.
        index = max(0, min(len(ordered) - 1, int(round(p / 100 * len(ordered) + 0.5)) - 1))
        return ordered[index]

    return {
        "scenario": {
            "total_requests": scenario.total_requests,
            "concurrency": scenario.concurrency,
            "dependency_latency_ms": {
                "context_provider": list(scenario.context_ms),
                "authority_provider": list(scenario.authority_ms),
                "answer_generator": list(scenario.generator_ms),
                "question_log": list(scenario.logger_ms),
            },
            "seed": scenario.seed,
        },
        "results": {
            "count": len(ordered),
            "min_ms": round(ordered[0], 1),
            "p50_ms": round(pct(50), 1),
            "p90_ms": round(pct(90), 1),
            "p95_ms": round(pct(95), 1),
            "p99_ms": round(pct(99), 1),
            "max_ms": round(ordered[-1], 1),
            "mean_ms": round(statistics.fmean(ordered), 1),
            "wall_clock_s": round(wall, 2),
            "throughput_rps": round(len(ordered) / wall, 1),
        },
        "statuses": statuses,
        "target_p95_ms": Settings().p95_target_ms,
        "timeouts": statuses.get(ResponseStatus.TIMEOUT.value, 0),
        "logged_records": len(logger.records),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="UC-03 P95 latency benchmark")
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    parser.add_argument("--requests", type=int, default=Scenario.total_requests)
    parser.add_argument("--concurrency", type=int, default=Scenario.concurrency)
    args = parser.parse_args()

    scenario = Scenario(total_requests=args.requests, concurrency=args.concurrency)
    report = asyncio.run(run(scenario))
    target = report["target_p95_ms"]
    p95 = report["results"]["p95_ms"]
    passed = p95 <= target

    if args.json:
        print(json.dumps({**report, "pass": passed}, indent=2))
    else:
        r = report["results"]
        print("UC-03 P95 latency benchmark")
        print("-" * 58)
        print(f"  requests            {r['count']} @ concurrency {scenario.concurrency}")
        print(f"  wall clock          {r['wall_clock_s']}s ({r['throughput_rps']} req/s)")
        print(f"  min / p50           {r['min_ms']} ms / {r['p50_ms']} ms")
        print(f"  p90 / p95 / p99     {r['p90_ms']} ms / {r['p95_ms']} ms / {r['p99_ms']} ms")
        print(f"  max / mean          {r['max_ms']} ms / {r['mean_ms']} ms")
        print(f"  statuses            {report['statuses']}")
        print(f"  timeouts            {report['timeouts']}")
        print(f"  records logged      {report['logged_records']}")
        print("-" * 58)
        print(f"  P95 target          {target} ms")
        print(f"  RESULT              {'PASS' if passed else 'FAIL'} (p95 = {p95} ms)")
        print()
        print(
            "  Note: the answer-generator latency band is a stand-in for a real\n"
            "  LLM-backed generator. Re-measure against the production generator\n"
            "  before quoting this as a production SLO."
        )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
