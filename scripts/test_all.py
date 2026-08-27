#!/usr/bin/env python
"""Run every component suite in the platform.

Each package keeps its own pytest configuration and is executed with its own
directory as the working directory. That is deliberate, not a shortcut: three
components import their fixtures as a top-level ``tests`` package
(``from tests.fixtures.factories import ...``), so a single flat pytest run
would collide on the module name ``tests``. Running each suite in its own
context keeps all ten suites exactly as their authors wrote them - no test file
was edited to make the merge work.

    python scripts/test_all.py            # every package
    python scripts/test_all.py uc03 uc06  # named packages only
    python scripts/test_all.py -v         # stream pytest output as it runs
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages"

# Components with no suite of their own, and why. Empty today: UC-04's port
# arrived with its own 18 test files late in the merge (see docs/MERGE_NOTES.md,
# "UC-04 arrived mid-merge"). Kept because the platform will grow packages that
# legitimately have no suite of their own - the router, for one.
NO_SUITE: dict[str, str] = {}

SUMMARY = re.compile(r"(\d+) (passed|failed|error)")


def discover(names: list[str]) -> list[Path]:
    all_pkgs = sorted(p for p in PACKAGES.iterdir() if p.is_dir())
    if not names:
        return all_pkgs
    chosen = []
    for name in names:
        path = PACKAGES / name
        if not path.is_dir():
            sys.exit(f"no such package: {name}")
        chosen.append(path)
    return chosen


def run(pkg: Path, verbose: bool) -> tuple[str, int, str]:
    """Return (status, returncode, detail) for one package."""
    if pkg.name in NO_SUITE:
        return "skipped", 0, NO_SUITE[pkg.name]
    if not (pkg / "tests").is_dir():
        return "skipped", 0, "no tests/ directory"

    # No -q here: six packages already set -q in their own addopts, and -qq
    # suppresses the summary line this runner reports.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider"],
        cwd=pkg,
        env={**dict(__import__("os").environ), "PYTHONPATH": "."},
        capture_output=not verbose,
        text=True,
    )
    output = "" if verbose else (proc.stdout or "") + (proc.stderr or "")
    counts = dict.fromkeys(("passed", "failed", "error"), 0)
    for line in output.splitlines():
        if line.startswith("=") or "passed" in line or "failed" in line:
            for number, kind in SUMMARY.findall(line):
                counts[kind] = max(counts[kind], int(number))
    detail = ", ".join(f"{v} {k}" for k, v in counts.items() if v) or "see output"
    status = "ok" if proc.returncode == 0 else "FAILED"
    if status == "FAILED" and not verbose:
        print(output)
    return status, proc.returncode, detail


def main() -> int:
    argv = [a for a in sys.argv[1:] if a not in ("-v", "--verbose")]
    verbose = len(argv) != len(sys.argv[1:])

    results = []
    for pkg in discover(argv):
        print(f"--- {pkg.name} ---", flush=True)
        results.append((pkg.name, *run(pkg, verbose)))

    print()
    print(f"{'component':<12} {'status':<9} detail")
    print("-" * 78)
    failed = 0
    for name, status, code, detail in results:
        failed += bool(code)
        print(f"{name:<12} {status:<9} {detail}")
    print()
    print("all component suites passing" if not failed else f"{failed} component suite(s) failing")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
