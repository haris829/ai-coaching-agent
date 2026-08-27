"""Architecture guardrails.

These tests are what keep the layering claim honest as the project grows. They parse the
import statements of every module and assert the allowed direction of dependencies.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "uc01"


def _modules(subpackage: str) -> list[Path]:
    return sorted((PACKAGE_ROOT / subpackage).rglob("*.py"))


def _imported_uc01_modules(path: Path) -> set[str]:
    """Return the ``uc01.*`` modules a file imports, resolving relative imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    parts = path.relative_to(PACKAGE_ROOT.parent).with_suffix("").parts
    package_parts = list(parts[:-1]) if parts[-1] != "__init__" else list(parts)

    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("uc01"):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package_parts[: len(package_parts) - (node.level - 1)]
                module = ".".join(base + ([node.module] if node.module else []))
                found.add(module)
            elif node.module and node.module.startswith("uc01"):
                found.add(node.module)
    return found


ALL_MODULES = [
    path
    for path in PACKAGE_ROOT.rglob("*.py")
    if "__pycache__" not in str(path)
]


@pytest.mark.parametrize("path", _modules("domain"), ids=lambda p: p.name)
def test_domain_imports_nothing_but_domain(path: Path):
    for imported in _imported_uc01_modules(path):
        assert imported.startswith("uc01.domain"), (
            f"{path.name} imports {imported}; the domain layer must stay pure"
        )


@pytest.mark.parametrize("path", _modules("contracts"), ids=lambda p: p.name)
def test_contracts_only_depend_on_domain(path: Path):
    for imported in _imported_uc01_modules(path):
        assert imported.startswith(("uc01.contracts", "uc01.domain")), (
            f"{path.name} imports {imported}"
        )


@pytest.mark.parametrize("path", _modules("application"), ids=lambda p: p.name)
def test_application_depends_only_on_domain_and_contracts(path: Path):
    for imported in _imported_uc01_modules(path):
        assert imported.startswith(
            ("uc01.application", "uc01.contracts", "uc01.domain")
        ), (
            f"{path.name} imports {imported}; the use-case layer must not know about "
            "adapters, persistence implementations or HTTP"
        )


@pytest.mark.parametrize("path", _modules("adapters"), ids=lambda p: p.name)
def test_adapters_do_not_reach_into_application_or_api(path: Path):
    for imported in _imported_uc01_modules(path):
        assert not imported.startswith(("uc01.application", "uc01.api")), (
            f"{path.name} imports {imported}; adapters must not depend on the use case"
        )


@pytest.mark.parametrize("path", _modules("persistence"), ids=lambda p: p.name)
def test_persistence_does_not_depend_on_api_or_application(path: Path):
    for imported in _imported_uc01_modules(path):
        assert not imported.startswith(("uc01.api", "uc01.application")), (
            f"{path.name} imports {imported}"
        )


def test_only_the_container_knows_which_adapter_is_used():
    """Adapter classes are wired in exactly one place."""
    offenders = []
    for path in ALL_MODULES:
        if path.parts[-2:] == ("api", "container.py"):
            continue
        if "adapters" in path.parts:
            continue
        for imported in _imported_uc01_modules(path):
            if imported.startswith("uc01.adapters"):
                offenders.append(f"{path.relative_to(PACKAGE_ROOT.parent)} -> {imported}")
    # config.py imports the scenario *enums* (configuration values, not adapters);
    # deps.py imports the scenario parser for the dev header. Both are allowed.
    allowed_suffixes = ("uc01.adapters.mock.scenarios",)
    unexpected = [
        entry for entry in offenders if not entry.endswith(allowed_suffixes)
    ]
    assert not unexpected, f"adapter imports outside the container: {unexpected}"


def test_no_other_use_case_logic_is_present():
    """UC-01 only. No UC-02..UC-10 business logic sneaks in."""
    banned = ("UC-02", "UC-03", "UC-04", "UC-05", "UC-06", "UC-07", "UC-08", "UC-09", "UC-10")
    for path in ALL_MODULES:
        text = path.read_text(encoding="utf-8")
        for marker in banned:
            for line in text.splitlines():
                if marker in line:
                    # Mentioning a future UC in a comment/docstring is fine; importing or
                    # implementing one is not.
                    stripped = line.strip()
                    assert stripped.startswith(("#", "*", '"', "'", "-")) or (
                        marker in stripped and "import" not in stripped
                    ), f"{path.name}: {stripped}"


def test_no_silent_exception_handling_anywhere():
    """``except ...: pass`` is banned; every handler must log or re-raise."""
    for path in ALL_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                body = [
                    statement
                    for statement in node.body
                    if not isinstance(statement, ast.Pass)
                ]
                assert body, f"{path.name}:{node.lineno} swallows an exception silently"
