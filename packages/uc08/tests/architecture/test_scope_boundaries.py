"""Scope boundaries: no frontend, no production database, no AI, no other use case.

Also: no module in this repository imports from a sibling component, because no
sibling component is assumed to exist.
"""

from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
UC08_ROOT = REPO_ROOT / "uc08"

FORBIDDEN_IMPORTS = {
    # frontend
    "react",
    "jinja2",
    "flask",
    "starlette.templating",
    # ORM / production database drivers
    "sqlalchemy",
    "django",
    "peewee",
    "tortoise",
    "psycopg",
    "psycopg2",
    "asyncpg",
    "pymongo",
    "redis",
    "sqlite3",
    "alembic",
    # AI, agents, retrieval, embeddings
    "openai",
    "anthropic",
    "langchain",
    "langgraph",
    "llama_index",
    "transformers",
    "sentence_transformers",
    "torch",
    "chromadb",
    "faiss",
    "pinecone",
    "weaviate",
    "qdrant_client",
    "tiktoken",
}

#: Vocabulary belonging to other use cases. UC-08 reads activity and writes
#: streaks, badges and weekly summaries; it does not coach, answer, analyse gaps
#: or generate feedback.
OUT_OF_SCOPE_SYMBOLS = (
    "answer_question",
    "generate_feedback",
    "analyse_gap",
    "analyze_gap",
    "run_coaching",
    "start_session",
    "create_session",
    "new_session",
    "llm",
    "prompt_template",
    "embedding",
    "vector_store",
    "retriever",
)


def _python_files() -> list[pathlib.Path]:
    files = sorted(UC08_ROOT.rglob("*.py"))
    assert files
    return files


def test_no_forbidden_import_anywhere():
    offenders = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            for module in modules:
                root = module.split(".")[0]
                if root in FORBIDDEN_IMPORTS or module in FORBIDDEN_IMPORTS:
                    offenders.append(f"{path.name}:{node.lineno} {module}")
    assert not offenders, offenders


def test_no_frontend_asset_is_shipped():
    patterns = ("*.html", "*.css", "*.js", "*.jsx", "*.ts", "*.tsx", "*.vue", "*.svelte")
    found = []
    for pattern in patterns:
        found.extend(
            path for path in REPO_ROOT.rglob(pattern) if ".venv" not in path.parts and ".git" not in path.parts
        )
    assert not found, found


def test_no_out_of_scope_symbol_is_defined():
    offenders = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                lowered = node.name.lower()
                for symbol in OUT_OF_SCOPE_SYMBOLS:
                    assert symbol not in lowered, f"{path.name}:{node.lineno} {node.name}"
    assert not offenders


def test_session_ids_are_received_and_only_minted_behind_a_flag():
    """The one place a session id can be created, and its default."""
    from uc08.application import session
    from uc08.config import load_settings

    assert load_settings().allow_dev_session_minting is False

    source = (UC08_ROOT / "application" / "session.py").read_text(encoding="utf-8")
    assert "allow_dev_minting" in source
    assert "DEV_SESSION_PREFIX" in source
    # Nothing else in the component builds a session id.
    minting_files = [
        path.name
        for path in _python_files()
        if "session_id" in path.read_text(encoding="utf-8")
        and "f\"{DEV_SESSION_PREFIX}" in path.read_text(encoding="utf-8")
    ]
    assert minting_files == ["session.py"]
    assert session.DEV_SESSION_PREFIX == "dev-minted-session"


def test_no_module_imports_a_sibling_component():
    """Nothing outside ``uc08`` is imported except the declared stack."""
    allowed_third_party = {
        "fastapi",
        "pydantic",
        "pydantic_settings",
        "starlette",
        "uvicorn",
        "typing",
        "typing_extensions",
    }
    standard_library_like = {
        "abc",
        "ast",
        "collections",
        "dataclasses",
        "datetime",
        "enum",
        "functools",
        "importlib",
        "json",
        "logging",
        "os",
        "pathlib",
        "re",
        "sys",
        "threading",
        "__future__",
    }
    offenders = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    offenders.append(f"{path.name}:{node.lineno} relative import")
                    continue
                modules = [node.module or ""]
            for module in modules:
                root = module.split(".")[0]
                if root in {"uc08"} | allowed_third_party | standard_library_like:
                    continue
                offenders.append(f"{path.name}:{node.lineno} {module}")
    assert not offenders, offenders


def test_the_api_surface_is_exactly_the_specified_routes():
    from uc08.adapters.clock.clocks import FixedClock
    from uc08.api.app import create_app
    from uc08.composition import build_container
    from uc08.config import load_settings
    from datetime import datetime, timezone

    container = build_container(load_settings(), clock=FixedClock(datetime(2026, 3, 10, tzinfo=timezone.utc)))
    app = create_app(container)
    routes = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if path.startswith("/api")
    }
    assert routes == {
        ("POST", "/api/v1/streaks/record-activity"),
        ("GET", "/api/v1/streaks"),
        ("GET", "/api/v1/badges"),
        ("POST", "/api/v1/streaks/freeze"),
        ("POST", "/api/v1/weekly-summaries/generate"),
        ("GET", "/api/v1/weekly-summaries"),
        ("GET", "/api/v1/healthz"),
    }


def test_no_route_accepts_a_user_identifier():
    from uc08.adapters.clock.clocks import FixedClock
    from uc08.api.app import create_app
    from uc08.composition import build_container
    from uc08.config import load_settings
    from datetime import datetime, timezone

    container = build_container(load_settings(), clock=FixedClock(datetime(2026, 3, 10, tzinfo=timezone.utc)))
    app = create_app(container)
    schema = app.openapi()

    forbidden = {"user_id", "userid", "learner_id", "account_id", "subject", "user"}
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            for parameter in operation.get("parameters", []):
                assert parameter["name"].lower() not in forbidden, (path, method, parameter)
            assert "{" not in path, f"{path} has a path parameter"

    for name, component in schema.get("components", {}).get("schemas", {}).items():
        if not name.endswith("Request"):
            continue
        for field in component.get("properties", {}):
            assert field.lower() not in forbidden, (name, field)
