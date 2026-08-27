"""Architecture tests.

These assert structural properties the specification requires: a read-only interaction
port, no threshold constant in business logic, a registry rather than a conditional
chain, layering, and the absence of a frontend, a production database or an agent
framework.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from uc10.adapters.foreign.interaction_provider import ForeignInteractionProvider
from uc10.adapters.mock.interaction_provider import MockInteractionProvider
from uc10.adapters.real._template import TemplateInteractionProvider
from uc10.adapters.registry import INTERACTION_PROVIDERS, build_interaction_provider
from uc10.ports.interaction_provider import InteractionProvider

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "uc10"
BUSINESS_LOGIC = [PACKAGE / "domain", PACKAGE / "application"]

WRITE_VERBS = (
    "save",
    "write",
    "update",
    "delete",
    "remove",
    "create",
    "put",
    "post",
    "patch",
    "set",
    "insert",
    "upsert",
    "store",
    "persist",
    "annotate",
    "correct",
    "mutate",
    "supersede",
    "record",
    "publish",
    "send",
)


def _python_files(*roots: pathlib.Path):
    for root in roots:
        yield from sorted(root.rglob("*.py"))


def _public_methods(obj) -> list[str]:
    return [
        name
        for name, _ in inspect.getmembers(obj, callable)
        if not name.startswith("_")
    ]


# ------------------------------------------------- read-only interaction port


def test_the_interaction_port_declares_only_read_methods():
    assert sorted(_public_methods(InteractionProvider)) == ["delivered_at", "get"]


@pytest.mark.parametrize(
    "adapter",
    [MockInteractionProvider, ForeignInteractionProvider, TemplateInteractionProvider],
    ids=lambda a: a.__name__,
)
def test_no_interaction_adapter_exposes_a_mutating_method(adapter):
    offenders = [
        name
        for name in _public_methods(adapter)
        if any(name.lower().startswith(verb) for verb in WRITE_VERBS)
    ]
    assert offenders == [], f"{adapter.__name__} must not be able to write: {offenders}"


def test_every_registered_interaction_adapter_is_read_only(clock, settings):
    from uc10.adapters.registry import ProviderContext

    for key in INTERACTION_PROVIDERS:
        provider = build_interaction_provider(
            ProviderContext(
                settings=settings.model_copy(update={"interaction_provider": key}), clock=clock
            )
        )
        offenders = [
            name
            for name in _public_methods(provider)
            if any(name.lower().startswith(verb) for verb in WRITE_VERBS)
        ]
        # 'register' seeds a mock's own fixture table; it writes to no upstream system.
        offenders = [name for name in offenders if name != "register"]
        assert offenders == [], f"{key} exposes {offenders}"


def test_nothing_in_the_component_calls_a_write_on_the_interaction_provider():
    """No call site anywhere treats the interaction port as writable."""
    offenders = []
    for path in _python_files(PACKAGE):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            target = node.func.value
            is_interaction_port = isinstance(target, ast.Attribute) and target.attr in (
                "_interactions",
                "interactions",
            )
            if is_interaction_port and node.func.attr not in ("get", "delivered_at"):
                offenders.append((path.name, node.func.attr))
    assert offenders == []


# -------------------------------------------- no threshold in business logic

#: Values that express flagging or window policy. None may be a literal in business logic.
FORBIDDEN_POLICY_LITERALS = {0.3, 30, 0.5, 50, 7, 24, 168}  # 0.30 == 0.3


def test_no_policy_threshold_literal_exists_in_domain_or_application_code():
    offenders = []
    for path in _python_files(*BUSINESS_LOGIC):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, (int, float))
                and not isinstance(node.value, bool)
                and node.value in FORBIDDEN_POLICY_LITERALS
            ):
                offenders.append((path.relative_to(ROOT).as_posix(), node.lineno, node.value))
    assert offenders == [], f"policy values must come from configuration: {offenders}"


def test_the_threshold_default_lives_only_in_configuration():
    """The literal 0.30 may appear in exactly one module, and it is not business logic.

    Docstrings are exempt -- documenting the specified default is not hardcoding it.
    """
    holders = []
    for path in _python_files(PACKAGE):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == 0.30 and isinstance(
                node.value, float
            ):
                holders.append(path.relative_to(ROOT).as_posix())
    assert sorted(set(holders)) == ["uc10/config.py"], holders


def test_the_flagging_rule_reads_every_number_it_uses_from_a_policy_object():
    source = (PACKAGE / "domain" / "flagging.py").read_text(encoding="utf-8")
    assert "down_rate_threshold" in source
    assert "minimum_sample_size" in source
    assert "FlaggingPolicy" in source


# --------------------------------------------------------- registry, not ifs


def test_provider_selection_contains_no_conditional_chain():
    source = inspect.getsource(build_interaction_provider)
    assert "elif" not in source
    assert source.count("if ") == 1, "one guard for the unknown key, no provider branching"
    assert "INTERACTION_PROVIDERS.get(" in source


def test_no_module_outside_the_registry_names_a_provider_implementation():
    """Adding a provider touches one file. Nothing else may name adapters."""
    adapter_names = ("MockInteractionProvider", "ForeignInteractionProvider")
    offenders = []
    for path in _python_files(PACKAGE):
        if path.name in ("registry.py", "interaction_provider.py"):
            continue
        text = path.read_text(encoding="utf-8")
        for name in adapter_names:
            if name in text:
                offenders.append((path.relative_to(ROOT).as_posix(), name))
    assert offenders == []


# ------------------------------------------------------------------ layering


def test_the_domain_depends_on_nothing_but_itself():
    for path in _python_files(PACKAGE / "domain"):
        text = path.read_text(encoding="utf-8")
        forbidden_imports = (
            "from uc10.adapters",
            "from uc10.api",
            "from uc10.application",
            "fastapi",
        )
        for forbidden in forbidden_imports:
            assert forbidden not in text, f"{path.name} imports {forbidden}"


def test_the_application_layer_knows_no_adapter_and_no_web_framework():
    for path in _python_files(PACKAGE / "application"):
        text = path.read_text(encoding="utf-8")
        for forbidden in ("from uc10.adapters", "from uc10.api", "fastapi", "starlette"):
            assert forbidden not in text, f"{path.name} imports {forbidden}"


def test_ports_depend_only_on_the_domain():
    for path in _python_files(PACKAGE / "ports"):
        text = path.read_text(encoding="utf-8")
        for forbidden in ("from uc10.adapters", "from uc10.application", "from uc10.api"):
            assert forbidden not in text


def test_only_the_composition_root_builds_the_container():
    builders = [
        path.relative_to(ROOT).as_posix()
        for path in _python_files(PACKAGE)
        if "def build_container" in path.read_text(encoding="utf-8")
    ]
    assert builders == ["uc10/api/deps.py"]


# --------------------------------------------- nothing outside this use case


FORBIDDEN_DEPENDENCIES = (
    "langchain",
    "langgraph",
    "llama_index",
    "chromadb",
    "faiss",
    "pinecone",
    "openai",
    "anthropic",
    "sentence_transformers",
    "sqlalchemy",
    "alembic",
    "psycopg",
    "django",
    "flask",
    "jinja2",
    "react",
)


def test_no_agent_framework_vector_store_or_production_database_is_used():
    offenders = []
    for path in _python_files(PACKAGE):
        text = path.read_text(encoding="utf-8").lower()
        for module in FORBIDDEN_DEPENDENCIES:
            if f"import {module}" in text or f"from {module}" in text:
                offenders.append((path.name, module))
    assert offenders == []


def test_no_frontend_asset_exists_in_the_repository():
    assets = [
        path.relative_to(ROOT).as_posix()
        for suffix in ("*.html", "*.css", "*.jsx", "*.tsx", "*.js", "*.vue")
        for path in ROOT.rglob(suffix)
        if ".venv" not in path.parts and "node_modules" not in path.parts
    ]
    assert assets == []


def test_no_other_use_case_leaks_into_this_component():
    """UC-10 captures ratings and raises flags. Nothing else."""
    forbidden_concepts = (
        "def create_session",
        "class CoachingService",
        "def answer_question",
        "class GapAnalysis",
        "def compute_streak",
        "class SummaryService",
    )
    for path in _python_files(PACKAGE):
        text = path.read_text(encoding="utf-8")
        for concept in forbidden_concepts:
            assert concept not in text, f"{path.name} implements {concept}"


def test_the_component_exposes_exactly_the_specified_endpoints(client):
    schema = client.get("/openapi.json").json()["paths"]
    exposed = {
        (path, method.upper())
        for path, operations in schema.items()
        for method in operations
        if path.startswith("/api/")
    }
    assert exposed == {
        ("/api/v1/interactions/{interaction_id}/rating", "POST"),
        ("/api/v1/interactions/{interaction_id}/rating", "GET"),
        ("/api/v1/admin/flags", "GET"),
        ("/api/v1/admin/flags/{flag_id}", "PATCH"),
        ("/api/v1/healthz", "GET"),
    }


# ------------------------------------------------------------ adapter template


def test_the_adapter_template_marks_every_point_needing_a_real_value():
    source = (PACKAGE / "adapters" / "real" / "_template.py").read_text(encoding="utf-8")
    for marker in ("TODO(endpoint)", "TODO(auth)", "TODO(mapping)", "TODO(transport)"):
        assert marker in source, f"the template must mark {marker}"
    assert "TODO(timeout)" in source
    assert "uc10/adapters/registry.py" in source, "the template names the registry line"
    assert "pytest tests/conformance" in source, "the template names the conformance command"


def test_the_template_implements_the_port_signature():
    assert sorted(_public_methods(TemplateInteractionProvider)) == ["delivered_at", "get"]
