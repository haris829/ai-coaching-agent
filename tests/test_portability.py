"""Gates on the SQL in this package, reading the source rather than running it.

Both of these shipped as real bugs in the sibling project. They pass on SQLite and fail on
PostgreSQL, which is the worst shape a bug can have: the local suite is green and the failure
waits until the deployment that matters.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SOURCE = sorted((Path(__file__).resolve().parent.parent / "qgen").rglob("*.py"))


def sql_literals(path: Path) -> list[str]:
    """Every string literal in the file that is not a docstring.

    Reading the raw source instead would flag the comments in ``db.py`` and ``storage.py`` that
    quote the bad forms in order to warn about them - a gate that fails on its own documentation
    teaches people to delete the documentation.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]

#: ``AND is_correct = 1``. SQLite stores booleans as integers and accepts the comparison
#: silently; PostgreSQL fails with ``operator does not exist: boolean = integer``.
BOOLEAN_COMPARISON = re.compile(r"is_correct\s*(=|!=|<>)\s*[01]\b")

#: Functions that do not exist under both names on both engines. JSON is assembled in Python
#: instead, which is longer and works everywhere.
NOT_PORTABLE = (
    "json_object(",
    "json_agg(",
    "json_group_array(",
    "group_concat(",
    "string_agg(",
    ") FILTER (",
)


@pytest.mark.parametrize("path", SOURCE, ids=lambda path: path.name)
def test_no_boolean_column_is_compared_against_an_integer(path: Path):
    for literal in sql_literals(path):
        assert not BOOLEAN_COMPARISON.search(literal), (
            f"{path.name} compares a boolean to an integer. Write `AND is_correct`."
        )


@pytest.mark.parametrize("path", SOURCE, ids=lambda path: path.name)
def test_no_engine_specific_json_or_aggregate_function_is_used(path: Path):
    for literal in sql_literals(path):
        lowered = literal.lower()
        for function in NOT_PORTABLE:
            assert function.lower() not in lowered, f"{path.name} uses {function}"


def test_there_is_source_to_check():
    """A gate that silently checks nothing is worse than no gate."""
    assert len(SOURCE) >= 8


def test_the_gate_would_catch_the_bug_it_exists_for(tmp_path):
    """The gate itself is checked, because one that never fires proves nothing."""
    offender = tmp_path / "bad.py"
    offender.write_text('SQL = "SELECT 1 FROM t WHERE is_correct = 1"', encoding="utf-8")

    assert any(BOOLEAN_COMPARISON.search(text) for text in sql_literals(offender))


def test_the_gate_does_not_fire_on_a_comment_warning_about_the_bug(tmp_path):
    offender = tmp_path / "documented.py"
    offender.write_text('# never write is_correct = 1\nSQL = "SELECT 1"', encoding="utf-8")

    assert not any(BOOLEAN_COMPARISON.search(text) for text in sql_literals(offender))
