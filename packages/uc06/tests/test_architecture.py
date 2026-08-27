"""Architecture rules that keep the safety properties from eroding.

Each of these encodes a decision that is cheap to make once and expensive to
rediscover after someone has quietly undone it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SOURCE_FILES = [
    path
    for path in Path("uc06").rglob("*.py")
    if "__pycache__" not in path.parts
]


class TestFailureIsHandledByCategory:
    def test_no_bare_exception_handler_in_shipped_code(self):
        """Typed errors per port exist so failure is handled by category. A bare
        `except Exception` in business logic defeats that.

        Two exemptions, both deliberate: the API's last-resort handler, which is
        a framework hook that must catch everything to guarantee a safe envelope,
        and the adapter template, where a TODO tells the engineer to narrow it to
        their client's transport errors.
        """
        exempt = {Path("uc06/adapters/real/_template.py")}
        pattern = re.compile(r"except\s+(Exception|BaseException)\b|except\s*:")
        offenders = []
        for path in SOURCE_FILES:
            if path in exempt:
                continue
            source = path.read_text(encoding="utf-8")
            if pattern.search(source):
                offenders.append(str(path))
        assert offenders == [], f"bare exception handlers: {offenders}"

    def test_the_api_last_resort_handler_logs_the_type_only(self):
        source = Path("uc06/api/app.py").read_text(encoding="utf-8")
        assert "kind=type(exc).__name__" in source
        assert "str(exc)" not in source, "internal exception text must never be logged or returned"

    def test_every_provider_error_carries_a_port_name_not_a_provider_name(self):
        from uc06.domain.errors import ProviderInvalidResponse, ProviderTimeout, ProviderUnavailable

        for error_type in (ProviderUnavailable, ProviderTimeout, ProviderInvalidResponse):
            error = error_type("some_port", "some_detail")
            assert error.port == "some_port"
            assert error.code


class TestLayeringIsIntact:
    def test_the_domain_imports_nothing_outside_the_domain(self):
        for path in Path("uc06/domain").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for forbidden in ("from ..application", "from ..api", "from ..adapters", "import fastapi", "from ..config"):
                assert forbidden not in source, f"{path} imports {forbidden}"

    def test_the_application_layer_imports_no_adapter_and_no_web_framework(self):
        for path in Path("uc06/application").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert "adapters" not in source
            assert "fastapi" not in source

    def test_only_the_composition_root_and_the_api_know_the_container(self):
        offenders = [
            str(path)
            for path in SOURCE_FILES
            if "build_container" in path.read_text(encoding="utf-8")
            and path not in {Path("uc06/composition.py"), Path("uc06/api/app.py")}
        ]
        assert offenders == []

    def test_no_adapter_imports_another_adapter_family(self):
        for family in ("mock", "memory", "identity", "real", "foreign"):
            for path in Path(f"uc06/adapters/{family}").rglob("*.py"):
                source = path.read_text(encoding="utf-8")
                for other in {"mock", "memory", "real", "foreign"} - {family}:
                    assert f"adapters.{other}" not in source, f"{path} reaches into {other}"
                    assert f"from ..{other}" not in source, f"{path} reaches into {other}"


class TestTheDisclaimerIsStructurallyProtected:
    def test_only_the_disclaimer_module_defines_the_text(self):
        needle = "It does not constitute legal advice. Always consult"
        hits = [str(p) for p in SOURCE_FILES if needle in p.read_text(encoding="utf-8")]
        assert hits == ["uc06\\domain\\disclaimer.py"] or hits == ["uc06/domain/disclaimer.py"]

    def test_nothing_writes_to_the_disclaimer_field_except_the_response_base(self):
        pattern = re.compile(r"""\.disclaimer\s*=|\[["']disclaimer["']\]\s*=""")
        offenders = []
        for path in SOURCE_FILES:
            if path == Path("uc06/domain/responses.py"):
                continue
            if pattern.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path))
        assert offenders == []

    def test_the_response_base_sets_it_from_the_constant_only(self):
        source = Path("uc06/domain/responses.py").read_text(encoding="utf-8")
        assert "object.__setattr__(self, DISCLAIMER_FIELD, CANONICAL_DISCLAIMER)" in source

    def test_no_module_can_reach_the_boundary_check_conditionally(self):
        """check_payload is called unconditionally in emit(). There is no
        `if ...: check_payload(...)` anywhere."""
        source = Path("uc06/application/emitter.py").read_text(encoding="utf-8")
        assert "check_payload(payload)" in source
        assert re.search(r"if[^\n]*:\s*\n\s*check_payload", source) is None


class TestPersistenceIsBehindAnInterface:
    def test_no_production_database_driver_is_imported(self):
        for path in SOURCE_FILES:
            source = path.read_text(encoding="utf-8")
            for driver in ("psycopg", "sqlalchemy", "pymongo", "sqlite3", "redis", "asyncpg"):
                assert driver not in source, f"{path} imports {driver}"

    def test_no_agent_framework_or_vector_store_is_imported(self):
        for path in SOURCE_FILES:
            source = path.read_text(encoding="utf-8")
            for forbidden in (
                "langchain",
                "langgraph",
                "llama_index",
                "chromadb",
                "pinecone",
                "faiss",
                "weaviate",
                "qdrant",
                "embedding",
                "openai",
                "anthropic",
            ):
                assert forbidden not in source.lower(), f"{path} references {forbidden}"

    def test_there_is_no_frontend(self):
        for pattern in ("*.jsx", "*.tsx", "*.css", "*.html", "*.vue", "*.svelte"):
            assert list(Path(".").rglob(pattern)) == [], f"frontend file found: {pattern}"
        assert not Path("package.json").exists()

    def test_no_other_use_case_is_implemented(self):
        """Scope boundary: UC-06 owns case-linked coaching only."""
        forbidden = (
            "def create_session",
            "class QuizProtection",
            "class SocraticDialogue",
            "def build_gap_report",
            "class StreakTracker",
            "def rate_feedback",
            "def summarise_session",
        )
        for path in SOURCE_FILES:
            source = path.read_text(encoding="utf-8")
            for symbol in forbidden:
                assert symbol not in source, f"{path} implements out-of-scope behaviour: {symbol}"


class TestNoHardCodedInfrastructureInBusinessLogic:
    def test_no_url_appears_outside_an_adapter_or_config(self):
        pattern = re.compile(r"https?://(?!placeholder|TODO)")
        offenders = []
        for path in SOURCE_FILES:
            if "adapters" in path.parts or path == Path("uc06/config.py"):
                continue
            if pattern.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path))
        assert offenders == []

    def test_no_timeout_literal_appears_in_the_application_layer(self):
        """Timeouts come from Settings, never from a literal in business logic."""
        for path in Path("uc06/application").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert not re.search(r"timeout[_a-z]*\s*=\s*\d+", source), f"{path} hard-codes a timeout"

    def test_the_service_reads_its_budget_from_settings(self):
        source = Path("uc06/application/case_coaching_service.py").read_text(encoding="utf-8")
        assert "self._settings.generation_timeout_ms" in source


class TestInteractionLogPortSurface:
    def test_append_get_and_list_for_session_all_work(self, container, service_ask):
        service_ask("How does duress apply here?", session_id="sess-log-1")
        service_ask("What is the test for dishonesty?", session_id="sess-log-1")
        service_ask("How does causation work?", session_id="sess-log-2")

        for_session = container.interactions.list_for_session("sess-log-1")
        assert len(for_session) == 2
        assert all(record.session_id == "sess-log-1" for record in for_session)

        fetched = container.interactions.get(for_session[0].interaction_id)
        assert fetched is not None
        assert fetched.interaction_id == for_session[0].interaction_id

        assert container.interactions.get("no-such-id") is None
        assert container.interactions.list_for_session("no-such-session") == ()

    def test_the_log_is_append_only(self):
        from uc06.adapters.memory.storage import InMemoryInteractionLogRepository
        from uc06.ports import FORBIDDEN_MUTATION_PREFIXES

        methods = [m for m in dir(InMemoryInteractionLogRepository) if not m.startswith("_")]
        mutating = [m for m in methods if m.lower().startswith(FORBIDDEN_MUTATION_PREFIXES)]
        assert mutating == [], f"the interaction log exposes mutation: {mutating}"


class TestSessionHaltPortSurface:
    def test_halt_is_halted_clear_and_get_all_work(self, container):
        halts = container.halts
        assert halts.is_halted("s1") is False
        assert halts.get("s1").halted is False

        halts.halt("s1", "reason_code")
        assert halts.is_halted("s1") is True
        record = halts.get("s1")
        assert record.reason_code == "reason_code"
        assert record.halted_at is not None

        halts.clear("s1")
        assert halts.is_halted("s1") is False

    def test_clearing_an_unhalted_session_is_harmless(self, container):
        container.halts.clear("never-halted")
        assert container.halts.is_halted("never-halted") is False
