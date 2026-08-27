"""Architecture guarantees: read-only upstream, one write seam, no banned tech."""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
from pathlib import Path

import pytest

import uc07
import uc07.adapters
from uc07.ports import READ_ONLY_PORTS, GapReportRepository
from uc07.ports.read_only import ReadOnlyPort

#: Any method whose name starts with one of these is a write operation.
MUTATING_PREFIXES = (
    "create",
    "update",
    "delete",
    "patch",
    "save",
    "write",
    "put",
    "post",
    "insert",
    "upsert",
    "store",
    "persist",
    "push",
    "send",
    "mutate",
    "set_",
    "add_",
    "remove_",
    "modify",
    "edit",
    "log_interaction",
    "record_",
    "submit",
)

BANNED_IMPORTS = (
    "langgraph",
    "langchain",
    "llama_index",
    "openai",
    "anthropic",
    "cohere",
    "transformers",
    "sentence_transformers",
    "torch",
    "chromadb",
    "faiss",
    "pinecone",
    "weaviate",
    "qdrant",
    "milvus",
    "sqlalchemy",
    "psycopg",
    "pymongo",
    "asyncpg",
    "redis",
    "boto3",
    "celery",
    "jinja2",
    "flask",
    "django",
)


def _iter_modules(package) -> list:
    modules = [package]
    for info in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        modules.append(importlib.import_module(info.name))
    return modules


def _all_uc07_modules() -> list:
    return _iter_modules(uc07)


def _source_files() -> list[Path]:
    return sorted(Path("uc07").rglob("*.py"))


def _own_members(cls: type) -> set[str]:
    """Members declared by UC-07 itself, ignoring third-party base classes.

    Pydantic's ``BaseModel`` and the built-in ``Exception`` contribute names like
    ``update_forward_refs`` and ``add_note``; those are not UC-07 write surfaces.
    """
    names: set[str] = set()
    for klass in cls.__mro__:
        if not klass.__module__.startswith("uc07"):
            continue
        names.update(klass.__dict__)
    return names


def _mutating_members(cls: type) -> list[str]:
    offenders = [
        name
        for name in sorted(_own_members(cls))
        if not name.startswith("__") and name.lower().startswith(MUTATING_PREFIXES)
    ]
    return offenders


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("port", READ_ONLY_PORTS, ids=lambda port: port.__name__)
def test_read_only_ports_expose_no_write_operation(port):
    assert _mutating_members(port) == []


def test_read_only_ports_are_marked_as_such():
    for port in READ_ONLY_PORTS:
        assert issubclass(port, ReadOnlyPort)


def test_gap_report_repository_is_the_only_write_seam():
    writers = []
    for module in _all_uc07_modules():
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if not cls.__module__.startswith("uc07"):
                continue
            if issubclass(cls, ReadOnlyPort):
                continue
            if _mutating_members(cls):
                writers.append(cls)
    unexpected = {
        cls for cls in writers if not issubclass(cls, GapReportRepository)
    }
    assert unexpected == set(), sorted(cls.__name__ for cls in unexpected)


def test_repository_write_surface_is_exactly_save():
    assert _mutating_members(GapReportRepository) == ["save"]


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


def _read_only_adapters() -> list[type]:
    adapters: list[type] = []
    for module in _iter_modules(uc07.adapters):
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if not cls.__module__.startswith("uc07.adapters"):
                continue
            if issubclass(cls, ReadOnlyPort) and cls not in READ_ONLY_PORTS:
                adapters.append(cls)
    return sorted(set(adapters), key=lambda cls: cls.__name__)


def test_every_read_only_port_has_at_least_two_independent_adapters():
    adapters = _read_only_adapters()
    for port in READ_ONLY_PORTS:
        implementations = [cls for cls in adapters if issubclass(cls, port)]
        assert len(implementations) >= 2, port.__name__


@pytest.mark.parametrize(
    "adapter", _read_only_adapters(), ids=lambda cls: cls.__name__
)
def test_read_only_adapters_expose_no_write_operation(adapter):
    assert _mutating_members(adapter) == []


def test_the_real_adapter_template_is_also_read_only():
    module = importlib.import_module("uc07.adapters.real._template")
    for _, cls in inspect.getmembers(module, inspect.isclass):
        if cls.__module__ != module.__name__:
            continue
        assert _mutating_members(cls) == []


# ---------------------------------------------------------------------------
# Banned technology and layering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("banned", BANNED_IMPORTS)
def test_no_banned_dependency_is_imported(banned):
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                assert banned not in stripped, f"{path}: {stripped}"


def test_no_llm_rag_or_vector_machinery_anywhere():
    needles = ("embedding", "vector_store", "vectorstore", "rag_", "prompt_template")
    for path in _source_files():
        text = path.read_text(encoding="utf-8").lower()
        for needle in needles:
            assert needle not in text, f"{path} mentions {needle}"


def test_no_frontend_assets_exist_in_the_repository():
    for pattern in ("*.html", "*.css", "*.jsx", "*.tsx", "*.vue", "*.svelte"):
        assert list(Path(".").rglob(pattern)) == []


def test_domain_layer_does_not_depend_on_adapters_api_or_frameworks():
    for path in sorted(Path("uc07/domain").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for banned in ("uc07.adapters", "uc07.api", "uc07.application", "fastapi"):
            assert banned not in text, f"{path} imports {banned}"


def test_application_layer_does_not_depend_on_adapters_or_http():
    for path in sorted(Path("uc07/application").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for banned in ("uc07.adapters", "uc07.api", "fastapi", "starlette"):
            assert banned not in text, f"{path} imports {banned}"


def test_ports_layer_depends_only_on_the_domain():
    for path in sorted(Path("uc07/ports").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for banned in ("uc07.adapters", "uc07.api", "uc07.application", "fastapi"):
            assert banned not in text, f"{path} imports {banned}"


def test_provider_selection_uses_a_registry_not_a_conditional_chain():
    """No code path may branch on a provider name (docstrings are not code)."""
    provider_names = {"mock", "foreign", "company", "acme", "real"}
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            for comparator in node.comparators:
                if isinstance(comparator, ast.Constant) and comparator.value in provider_names:
                    raise AssertionError(
                        f"{path}:{node.lineno} branches on a provider name"
                    )

    composition = Path("uc07/composition.py").read_text(encoding="utf-8")
    for registry in (
        "INTERACTION_LOG_PROVIDERS",
        "FEEDBACK_PROVIDERS",
        "PROFILE_PROVIDERS",
        "COURSES_PROVIDERS",
    ):
        assert f"{registry}: dict[str," in composition
