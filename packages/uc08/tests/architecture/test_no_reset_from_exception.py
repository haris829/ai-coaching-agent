"""No exception handler can reach the streak reset path.

This is asserted exhaustively rather than sampled. Every ``.py`` file under
``uc08/`` is parsed; a call graph is built by function name; every function that
can transitively reach the reset builder is marked as a *reset producer*; and
then every single ``except`` handler body in the package is checked for a
reference to any of them.

Marking by name over-approximates -- two unrelated functions sharing a name are
both marked -- which makes the assertion stricter, not weaker.
"""

from __future__ import annotations

import ast
import pathlib

UC08_ROOT = pathlib.Path(__file__).resolve().parents[2] / "uc08"

#: Anything that can lower or re-baseline a streak count. ``InactivityEvidence``
#: is the argument the reset builder cannot be called without, so it is a seed
#: too: if a handler could construct one, it could reach a reset.
SEED_RESET_PRODUCERS = frozenset(
    {
        "apply_reset",
        "InactivityEvidence",
        "decide",
        "_next_record",
        "_empty_record",
    }
)


def _modules() -> list[tuple[pathlib.Path, ast.Module]]:
    found = []
    for path in sorted(UC08_ROOT.rglob("*.py")):
        found.append((path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))))
    assert found, "no modules were parsed; the scan is not doing anything"
    return found


def _referenced_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
    return names


def _function_bodies(modules) -> dict[str, set[str]]:
    """Map every function name to the names its body references."""
    bodies: dict[str, set[str]] = {}
    for _path, tree in modules:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                bodies.setdefault(node.name, set()).update(_referenced_names(node))
    return bodies


def _reset_producers(bodies: dict[str, set[str]]) -> set[str]:
    producers = set(SEED_RESET_PRODUCERS)
    changed = True
    while changed:
        changed = False
        for name, referenced in bodies.items():
            if name not in producers and referenced & producers:
                producers.add(name)
                changed = True
    return producers


def test_the_reset_builder_lives_only_in_the_rules_module():
    definitions = []
    for path, tree in _modules():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "apply_reset":
                definitions.append(path)
    assert [path.name for path in definitions] == ["streak_rules.py"]


def test_no_except_handler_references_a_reset_producer():
    modules = _modules()
    producers = _reset_producers(_function_bodies(modules))
    # Sanity: the transitive walk actually found the real chain.
    assert {"apply_reset", "decide", "_next_record", "record_activity"} <= producers

    handlers_checked = 0
    for path, tree in modules:
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            handlers_checked += 1
            referenced = set()
            for statement in node.body:
                referenced |= _referenced_names(statement)
            offending = referenced & producers
            assert not offending, (
                f"{path}:{node.lineno} an exception handler references {sorted(offending)}, "
                "which can reach the streak reset path"
            )
    assert handlers_checked > 0, "no exception handlers were found; the scan is not doing anything"


def test_no_except_handler_writes_a_streak_count():
    """Belt and braces: no handler assigns a count at all, literal or otherwise."""
    for path, tree in _modules():
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            for statement in node.body:
                for child in ast.walk(statement):
                    if isinstance(child, ast.keyword) and child.arg in {
                        "current_streak_days",
                        "longest_streak_days",
                        "streak_started_at",
                    }:
                        raise AssertionError(f"{path}:{child.value.lineno} handler sets {child.arg}")
                    if isinstance(child, ast.Constant) and isinstance(child.value, str) and child.value in {
                        "current_streak_days",
                        "longest_streak_days",
                    }:
                        # e.g. a model_copy(update={"current_streak_days": ...})
                        raise AssertionError(f"{path}:{child.lineno} handler names {child.value}")


def test_only_the_rules_module_baselines_a_streak_count():
    """Where a count is set from a literal, and nowhere else."""
    allowed = {
        # apply_start and apply_reset: the two legitimate baselines.
        "uc08/domain/streak_rules.py",
        # _empty_record: a zero-valued read model for an account with no record.
        # Never persisted.
        "uc08/application/streak_service.py",
    }
    offenders = []
    checked = 0
    for path, tree in _modules():
        relative = path.relative_to(UC08_ROOT.parent).as_posix()
        for node in ast.walk(tree):
            # A constructor keyword: StreakRecord(current_streak_days=1, ...)
            if (
                isinstance(node, ast.keyword)
                and node.arg == "current_streak_days"
                and isinstance(node.value, ast.Constant)
            ):
                checked += 1
                if relative not in allowed:
                    offenders.append(f"{relative}:{node.value.lineno} (constructor keyword)")
            # A copy-with-update: streak.model_copy(update={"current_streak_days": ...})
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "model_copy":
                for keyword in node.keywords:
                    if keyword.arg != "update" or not isinstance(keyword.value, ast.Dict):
                        continue
                    for key, value in zip(keyword.value.keys, keyword.value.values):
                        if (
                            isinstance(key, ast.Constant)
                            and key.value == "current_streak_days"
                            and isinstance(value, ast.Constant)
                        ):
                            checked += 1
                            if relative not in allowed:
                                offenders.append(f"{relative}:{key.lineno} (model_copy update)")
    assert not offenders, offenders
    assert checked > 0, "the scan found no streak-count writes at all"


def test_the_persistence_module_cannot_see_the_rules_module():
    """The only code that catches a streak write failure has no reset in scope."""
    path = UC08_ROOT / "application" / "streak_persistence.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(alias.name for alias in node.names)
    assert "streak_rules" not in " ".join(imported)
    assert "uc08.domain.streak_rules" not in imported
