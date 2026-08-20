"""UC-11's constraint on itself, made executable.

The specification is explicit that UC-11 is a validation layer: it must not implement quiz logic,
scoring, analytics or any new business rule. A docstring saying so is a promise; this file is the
enforcement, and it reads the package's own syntax tree rather than trusting the prose.

It exists because the failure mode is so easy and so quiet. A validator that computes an expected
percentage for itself no longer validates the system — it validates that two implementations of the
same arithmetic agree, and it will happily stay green while the real one is wrong. Every rule below
is one way that has happened to somebody.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent
BACKEND = PACKAGE.parents[1]
APP = BACKEND / "app"

#: Modules the package is allowed to import from ``app``. Everything here is either shared
#: vocabulary or a way of *reading* what the system stored — never a rule.
ALLOWED_APP_IMPORTS: dict[str, str] = {
    "app.core.question_types": "the shared question-type vocabulary; UC-11 must not define its own",
    "app.core.config": "read to assert the deployment guard, not to reimplement it",
    "app.modules.question_bank.models": (
        "read-only, and only in conftest: an answer is built from UC-02's own key so that "
        '"answered correctly" means correctly rather than "matched a fixture"'
    ),
    "app.modules.identity": (
        "the security module is monkeypatched to switch the real administrator guard on for the "
        "authorization sweep — the guard itself is untouched"
    ),
}

#: This module reads the package's own files, so its constants are file paths rather than
#: expectations about the system. It makes no assertion about behaviour and so cannot encode a rule.
PLUMBING = "test_no_new_domain_logic.py"

#: Only this module may read the question bank directly. Anywhere else, a test would be deriving an
#: answer key of its own instead of going through ``answer_payload``.
BANK_READER = "conftest.py"


def _modules() -> list[tuple[Path, ast.Module]]:
    return [
        (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in sorted(PACKAGE.glob("*.py"))
    ]


def _imported_modules(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


# ---------------------------------------------------------------------------
# 1. UC-11 ships no application code
# ---------------------------------------------------------------------------


def test_uc11_added_no_module_to_the_application() -> None:
    """A validation layer with a module of its own is a feature wearing a validator's name."""
    intruders = [
        path.relative_to(BACKEND).as_posix()
        for pattern in ("**/global_dod*", "**/uc11*", "**/global_definition_of_done*")
        for path in APP.glob(pattern)
    ]
    assert intruders == [], f"UC-11 must add no application module: {intruders}"


def test_the_application_does_not_import_uc11() -> None:
    """Nothing in production may depend on a test package — not even for a constant.

    If it did, the package would have become part of the system it is meant to judge, and deleting
    it would break the application rather than only reducing its coverage.
    """
    offenders: list[str] = []
    for path in APP.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "global_dod" in text or "tests.global_dod" in text:
            offenders.append(path.relative_to(BACKEND).as_posix())
    assert offenders == [], f"application code referencing UC-11: {offenders}"


# ---------------------------------------------------------------------------
# 2. UC-11 reaches the system the way a caller does
# ---------------------------------------------------------------------------


def test_no_module_imports_a_domain_or_service_module() -> None:
    """Rules live in ``domain`` and ``services``. Importing one is how a validator starts using it.

    A test that calls a scoring service directly stops testing the wiring — the routing, the
    guards, the transaction boundary, the presenter — which is precisely the part UC-11 exists to
    check. Everything here goes over HTTP, or reads committed rows with SQL.
    """
    offenders: list[str] = []
    for path, tree in _modules():
        for module in sorted(_imported_modules(tree)):
            if not module.startswith("app."):
                continue
            if module in ALLOWED_APP_IMPORTS:
                continue
            offenders.append(f"{path.name}: {module}")
    assert offenders == [], (
        "UC-11 must reach the system through its API. Add to ALLOWED_APP_IMPORTS with a reason "
        f"only if the import genuinely reads rather than decides: {offenders}"
    )


def test_only_the_conftest_reads_the_question_bank() -> None:
    """One place derives an answer from UC-02's key, and it is documented there.

    Spread across the package, that read would become a second answer-key implementation — the
    exact duplication that lets a broken key look correct because the test computed the same
    broken thing.
    """
    offenders = [
        path.name
        for path, tree in _modules()
        if path.name != BANK_READER
        and "app.modules.question_bank.models" in _imported_modules(tree)
    ]
    assert offenders == [], f"only {BANK_READER} may read the bank directly: {offenders}"


def test_every_suite_actually_drives_the_running_system() -> None:
    """A file of assertions that never issues a request proves nothing about the system.

    Cheap, and it catches the case where a suite is refactored until its HTTP calls are gone and
    only its self-consistent fixtures remain.
    """
    exempt = {"__init__.py", "conftest.py", Path(__file__).name}
    for path, _tree in _modules():
        if path.name in exempt:
            continue
        source = path.read_text(encoding="utf-8")
        assert "ctx.client" in source or "ctx." in source, (
            f"{path.name} never calls the application"
        )


# ---------------------------------------------------------------------------
# 3. UC-11 introduces no vocabulary and no rule of its own
# ---------------------------------------------------------------------------


def test_no_module_defines_an_enum_a_dataclass_or_a_model() -> None:
    """A new type is how a new rule gets somewhere to live.

    UC-11 asserts against the system's vocabulary — ``PASS``, ``SCORED``, ``PARTIALLY_CORRECT`` —
    as the strings the system actually returns. Redeclaring them as an enum here would create a
    second definition that can drift from the first without either failing.
    """
    offenders: list[str] = []
    for path, tree in _modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = {ast.unparse(base) for base in node.bases}
                decorators = {ast.unparse(item) for item in node.decorator_list}
                modelling = (
                    any("Enum" in base for base in bases)
                    or any("BaseModel" in base for base in bases)
                    or any("dataclass" in item for item in decorators)
                )
                if modelling:
                    offenders.append(f"{path.name}: {node.name}")
    assert offenders == [], f"UC-11 must define no domain type: {offenders}"


def test_no_module_level_constant_is_computed() -> None:
    """Every constant in the package is written down, not derived.

    A computed constant is a rule: the moment an expected pass mark is calculated rather than
    stated, the suite has an opinion about the arithmetic and can agree with a wrong answer. The
    configuration dictionaries here are *inputs* the system is asked to honour, which is the
    opposite relationship.
    """
    offenders: list[str] = []
    for path, tree in _modules():
        if path.name == PLUMBING:
            continue
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            for inner in ast.walk(node.value):
                if isinstance(inner, ast.Call):
                    rendered = ast.unparse(inner)
                    # ``pytest.mark.*`` is collection metadata, not a value.
                    if rendered.startswith("pytest.mark"):
                        continue
                    offenders.append(f"{path.name}: {ast.unparse(node)[:60]}")
                    break
    assert offenders == [], f"a computed constant is a rule in disguise: {offenders}"


def test_no_module_reimplements_scoring_or_analytics() -> None:
    """No helper in the package is named for a calculation it should be reading instead.

    Named as a guard against intent rather than syntax: something called ``_expected_percentage``
    is a scoring implementation whatever its body does, and reviewing the name is enough to know
    it should not be here.

    Helpers only. A *test* name describes the system's behaviour, so
    ``test_a_confirmed_score_is_not_recomputed`` is exactly the right name for an assertion that
    the system does not recompute — the opposite of a violation.
    """
    forbidden = (
        "compute",
        "calculate",
        "recompute",
        "expected_percentage",
        "expected_score",
        "expected_marks",
        "grade",
        "mark_answer",
        "apply_penalty",
        "pass_mark_for",
    )
    offenders: list[str] = []
    for path, tree in _modules():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    continue
                lowered = node.name.lower()
                if any(word in lowered for word in forbidden):
                    offenders.append(f"{path.name}: {node.name}")
    assert offenders == [], (
        f"UC-11 must read the system's figures rather than produce its own: {offenders}"
    )


# ---------------------------------------------------------------------------
# 4. The package is honest about what it is
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", sorted(PACKAGE.glob("test_*.py")), ids=lambda p: p.name)
def test_every_suite_names_the_requirement_it_covers(path: Path) -> None:
    """Each module's docstring says which section it validates.

    So a failure points at a requirement, not only at a line number — and so a module that covers
    nothing in particular is visible as such when it is written rather than a year later.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstring = ast.get_docstring(tree)
    assert docstring, f"{path.name} has no module docstring"
    assert "UC-11" in docstring, f"{path.name} does not say it belongs to UC-11"
    names_a_section = "§" in docstring or "constraint on itself" in docstring
    assert names_a_section, f"{path.name} does not name the requirement it covers"
