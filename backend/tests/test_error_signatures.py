"""Every ``raise SomeError(...)`` must actually be constructible.

WHY THIS EXISTS
---------------
Three separate defects of exactly this shape have been found by running the system, never by a test:

* **F-17** — UC-09's seven provider-outage classes inherited a base that had gained a required
  ``provider`` argument. Eighteen call sites raised ``TypeError``, so every dependency outage was
  reported as an opaque 500 with the real cause discarded.
* **F-19** — eight sites in UC-09's persistence layer passed arguments their classes did not
  declare: the compare-and-set guard, the single-device lock, the one-formal-attempt-per-quiz rule
  and the one-review-per-attempt rule. Each conflict a service was written to catch and handle
  arrived instead as an unhandled ``TypeError``.
* and the device-lock case that surfaced F-19, which a reviewer saw as "the app crashed when I
  opened a second tab".

The pattern is always the same, and it is why a normal test suite cannot see it. These are **error
paths**, reached only when a database constraint fires or a dependency is down. The unit suites use
in-memory doubles that have no constraints, so the path is never taken; the integration suites take
the happy path. The code looks right, reviews as right, and fails the first time it matters — in
production, on the exact request whose failure was supposed to be handled gracefully.

WHAT THIS CHECKS
----------------
Statically, across the whole of ``app/``: for every ``raise`` of a name that resolves to an
exception class, bind the call's arguments against that class's ``__init__``. A mismatch is a
guaranteed ``TypeError`` on a path somebody wrote deliberately.

It cannot check argument *types*, and it skips call sites using ``*args``/``**kwargs`` or a class
name defined in more than one module — it would rather stay silent than guess. Even so, it would
have caught all three defects above on the commit that introduced them.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"


def _exception_classes() -> dict[str, list[type]]:
    """Every exception class reachable by name from any module under ``app/``.

    Keyed by the *bare* name, because that is how a raise site refers to it. A name defined in two
    modules is kept as a list and skipped later: resolving which one a given file meant would mean
    reimplementing import resolution, and a wrong guess here would produce a false failure that
    teaches people to distrust this test.
    """
    found: dict[str, list[type]] = {}
    for path in sorted(APP.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module_name = path.relative_to(BACKEND).with_suffix("").as_posix().replace("/", ".")
        try:
            module = importlib.import_module(module_name)
        except Exception:  # pragma: no cover - an unimportable module is another test's problem
            continue
        for name, obj in vars(module).items():
            if inspect.isclass(obj) and issubclass(obj, BaseException):
                bucket = found.setdefault(name, [])
                if obj not in bucket:
                    bucket.append(obj)
    return found


def _raise_sites() -> list[tuple[Path, ast.Raise, ast.Call]]:
    sites: list[tuple[Path, ast.Raise, ast.Call]] = []
    for path in sorted(APP.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                sites.append((path, node, node.exc))
    return sites


def test_every_raise_site_matches_its_exception_signature() -> None:
    classes = _exception_classes()
    problems: list[str] = []
    checked = 0

    for path, raise_node, call in _raise_sites():
        func = call.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if not name or name not in classes:
            continue
        candidates = classes[name]
        if len(candidates) != 1:
            continue  # ambiguous across modules — see the docstring on _exception_classes
        cls = candidates[0]

        # ``*args`` / ``**kwargs`` at a call site means the arguments are not statically known.
        if any(isinstance(arg, ast.Starred) for arg in call.args):
            continue
        if any(keyword.arg is None for keyword in call.keywords):
            continue

        try:
            signature = inspect.signature(cls.__init__)
        except (TypeError, ValueError):  # pragma: no cover - builtins without introspection
            continue

        positional = [None] * len(call.args)
        keywords = {keyword.arg: None for keyword in call.keywords if keyword.arg}
        checked += 1
        try:
            # `None` stands in for `self`; only arity and names are being checked.
            signature.bind(None, *positional, **keywords)
        except TypeError as exc:
            problems.append(
                f"{path.relative_to(BACKEND).as_posix()}:{raise_node.lineno} "
                f"raise {name}({len(call.args)} positional, "
                f"keywords={sorted(keywords)}) — {exc}"
            )

    assert checked > 100, (
        f"only {checked} raise sites were checked, which suggests the discovery above stopped "
        "working rather than that the application stopped raising errors"
    )
    assert problems == [], (
        "these raise sites would fail with TypeError the moment they are reached, turning a "
        "handled failure into an opaque 500:\n  " + "\n  ".join(problems)
    )


def test_the_check_would_catch_a_regression() -> None:
    """Guard the guard: prove the binding check rejects what it is supposed to reject.

    Without this, a refactor that made ``signature.bind`` always succeed would leave the test above
    passing on a codebase full of the exact defect it exists to find.
    """
    from app.core.errors import ConflictError

    class Narrow(ConflictError):
        def __init__(self, *, only_keyword: str) -> None:
            super().__init__("x", code="X")

    signature = inspect.signature(Narrow.__init__)

    # What the real defects looked like: a positional argument the class does not accept.
    try:
        signature.bind(None, "positional")
    except TypeError:
        rejected = True
    else:
        rejected = False
    assert rejected, "the binding check must reject a positional argument on a keyword-only class"

    # And the correct call still binds.
    signature.bind(None, only_keyword="value")
