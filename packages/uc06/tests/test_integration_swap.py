"""Proof that the swap is real.

The UNMODIFIED service runs against a deliberately foreign adapter family whose
fictional upstream ("Mattersphere") uses different field names, different
nesting, different value representations, a different marker syntax and its own
exception type. If the service produces correct results against both the mock
family and the foreign family without modification, replaceability is
demonstrated rather than asserted.

Nothing in this file touches the domain, the application service or the API. The
only difference between the two runs is three configuration values.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from uc06.adapters.foreign import _upstream
from uc06.adapters.identity.header_user import USER_HEADER
from uc06.adapters.mock import case_file as mock_cf
from uc06.api.app import create_app
from uc06.composition import build_container
from uc06.config import Settings
from uc06.domain.disclaimer import CANONICAL_DISCLAIMER
from uc06.domain.enums import GuardClass, NaricLevel, NaricLevelSource, ResponseMode, SourceStatus
from uc06.domain.errors import ProviderInvalidResponse, ProviderUnavailable

from . import support
from .conftest import DEFAULT_USER, make_settings

QUESTION = "How does the defence of duress apply to the account in this file?"

#: The ONLY difference between the two deployments. Three values.
MOCK_FAMILY = make_settings(
    case_file_provider="mock", learner_context_provider="mock", answer_generator="fake"
)
FOREIGN_FAMILY = make_settings(
    case_file_provider="foreign", learner_context_provider="foreign", answer_generator="foreign"
)

FAMILIES = [
    ("mock", MOCK_FAMILY, mock_cf.CASE_FULL, "sess-level-7-plus"),
    ("foreign", FOREIGN_FAMILY, _upstream.MATTER_STANDARD, "ms-session-1"),
]


def _client(settings: Settings):
    container = build_container(settings)
    return TestClient(create_app(container), raise_server_exceptions=False), container


def _ask(client, case_file_id, session_id, question=QUESTION, user=DEFAULT_USER):
    support.record_question(question)
    return client.post(
        "/api/v1/case-coaching/questions",
        headers={USER_HEADER: user},
        json={"question": question, "case_file_id": case_file_id, "session_id": session_id},
    )


@pytest.mark.parametrize("name,settings,case_id,session_id", FAMILIES, ids=[f[0] for f in FAMILIES])
class TestTheSameServiceAgainstBothFamilies:
    def test_it_produces_a_correct_case_linked_answer(self, name, settings, case_id, session_id):
        client, _ = _client(settings)
        response = _ask(client, case_id, session_id)

        assert response.status_code == 200, name
        body = response.json()
        assert body["mode"] == ResponseMode.CASE_LINKED.value
        assert body["case_file_id"] == case_id
        assert body["disclaimer"] == CANONICAL_DISCLAIMER
        assert len(body["content"].split()) > 60

    def test_facts_are_referenced_by_verified_identifier(self, name, settings, case_id, session_id):
        client, container = _client(settings)
        body = _ask(client, case_id, session_id).json()

        known = {fact.fact_id for fact in container.case_files.get_case_file(case_id).facts}
        assert body["case_facts_referenced"], name
        assert set(body["case_facts_referenced"]) <= known
        assert "[[fact:" not in body["content"]
        assert "<<ref:" not in body["content"], "foreign marker syntax escaped the adapter"

    def test_the_naric_level_arrives_as_the_platform_enum(self, name, settings, case_id, session_id):
        client, _ = _client(settings)
        body = _ask(client, case_id, session_id).json()

        assert body["naric_level"] in {level.value for level in NaricLevel}
        assert body["naric_level_source"] in {source.value for source in NaricLevelSource}
        assert body["explanation_profile"] in {"basic", "intermediate", "advanced"}

    def test_the_guard_behaves_identically(self, name, settings, case_id, session_id):
        client, _ = _client(settings)
        body = _ask(client, case_id, session_id, question="Will my client win at trial?").json()

        assert body["guard_triggered"] == GuardClass.OUTCOME_PREDICTION.value
        assert len(body["content"].split()) >= 150
        assert body["disclaimer"] == CANONICAL_DISCLAIMER

    def test_access_denial_behaves_identically(self, name, settings, case_id, session_id):
        client, _ = _client(settings)
        denied_id = mock_cf.CASE_ACCESS_DENIED if name == "mock" else _upstream.MATTER_BLOCKED
        response = _ask(client, denied_id, session_id)

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "case_access_denied"

    def test_origin_rejection_behaves_identically(self, name, settings, case_id, session_id):
        client, _ = _client(settings)
        foreign_origin = mock_cf.CASE_FOREIGN_ORIGIN if name == "mock" else _upstream.MATTER_OTHER_ORIGIN
        response = _ask(client, foreign_origin, session_id)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "case_origin_rejected"

    def test_an_unreachable_upstream_degrades_identically(self, name, settings, case_id, session_id):
        client, _ = _client(settings)
        gone = mock_cf.CASE_UNAVAILABLE if name == "mock" else _upstream.MATTER_GONE
        response = _ask(client, gone, session_id)
        body = response.json()

        assert response.status_code == 200
        assert body["mode"] == ResponseMode.GENERAL_FALLBACK.value
        assert body["case_file_status"] == SourceStatus.UNAVAILABLE.value
        assert body["notice"]
        assert body["disclaimer"] == CANONICAL_DISCLAIMER

    def test_a_partial_case_file_is_reported_identically(self, name, settings, case_id, session_id):
        client, _ = _client(settings)
        partial = mock_cf.CASE_NO_LEGISLATION if name == "mock" else _upstream.MATTER_NO_AUTHORITIES
        body = _ask(client, partial, session_id).json()

        assert body["case_file_status"] == SourceStatus.PARTIAL.value
        assert body["mode"] == ResponseMode.CASE_LINKED.value

    def test_an_unmappable_payload_is_reported_identically(self, name, settings, case_id, session_id):
        client, _ = _client(settings)
        garbled = mock_cf.CASE_INVALID_SHAPE if name == "mock" else _upstream.MATTER_GARBLED
        body = _ask(client, garbled, session_id).json()

        assert body["case_file_status"] == SourceStatus.INVALID.value
        assert body["mode"] == ResponseMode.GENERAL_FALLBACK.value


class TestTheAdapterIsTheOnlyPlaceUpstreamShapesAreKnown:
    def test_no_upstream_field_name_escapes_the_adapter(self):
        client, _ = _client(FOREIGN_FAMILY)
        text = _ask(client, _upstream.MATTER_STANDARD, "ms-session-2").text

        for upstream_name in (
            "matterRef",
            "particulars",
            "narrative",
            "countRef",
            "descriptor",
            "provenance",
            "producedBy",
            "eqfBand",
            "practiceGroup",
            "envelope",
            "sourceRef",
            "mattersphere",
        ):
            assert upstream_name not in text, f"{upstream_name} escaped the adapter"

    def test_no_upstream_error_text_escapes_the_adapter(self):
        client, _ = _client(FOREIGN_FAMILY)
        text = _ask(client, _upstream.MATTER_GONE, "ms-session-3").text

        assert "node pool draining" not in text
        assert "UPSTREAM_503" not in text
        assert "MatterSphereError" not in text

    def test_the_upstream_exception_type_never_leaves_the_adapter(self):
        from uc06.adapters.foreign.case_file import ForeignCaseFileAdapter

        adapter = ForeignCaseFileAdapter()
        with pytest.raises(ProviderUnavailable):
            adapter.get_case_file(_upstream.MATTER_GONE)
        with pytest.raises(ProviderInvalidResponse):
            adapter.get_case_file(_upstream.MATTER_GARBLED)

    def test_an_unmappable_band_is_invalid_never_a_nearest_neighbour(self):
        from uc06.adapters.foreign.learner_context import ForeignLearnerContextAdapter

        with pytest.raises(ProviderInvalidResponse):
            ForeignLearnerContextAdapter().get_context("ms-unknown-band", "u")

    def test_the_service_still_answers_when_the_foreign_context_is_invalid(self):
        """Normalisation failure is handled by the platform default, and the
        answer still happens with the disclaimer intact."""
        client, _ = _client(FOREIGN_FAMILY)
        body = _ask(client, _upstream.MATTER_STANDARD, "ms-unknown-band").json()

        assert body["naric_level"] == NaricLevel.LEVEL_5.value
        assert body["naric_level_source"] == NaricLevelSource.DEFAULT.value
        assert body["disclaimer"] == CANONICAL_DISCLAIMER

    def test_the_foreign_value_representation_is_normalised(self):
        from uc06.adapters.foreign.learner_context import ForeignLearnerContextAdapter

        adapter = ForeignLearnerContextAdapter()
        assert adapter.get_context("ms-session", "u").naric_level is NaricLevel.LEVEL_7_PLUS
        assert adapter.get_context("ms-junior", "u").naric_level is NaricLevel.LEVEL_3


class TestSwapCost:
    def test_the_service_layer_never_names_a_provider(self):
        """Nothing outside the composition root knows an adapter exists."""
        from pathlib import Path

        registry_file = Path("uc06/composition.py")
        offenders = []
        for path in Path("uc06").rglob("*.py"):
            if path == registry_file or "adapters" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            for token in ("MockCaseFileProvider", "FakeAnswerGenerator", "ForeignCaseFileAdapter", "Mattersphere"):
                if token in source:
                    offenders.append(f"{path}: {token}")
        assert offenders == []

    def test_switching_families_requires_only_configuration(self):
        """Same code, same container builder, same app factory: three strings."""
        differences = {
            field
            for field in Settings.field_names()
            if getattr(MOCK_FAMILY, field) != getattr(FOREIGN_FAMILY, field)
        }
        assert differences == {"case_file_provider", "learner_context_provider", "answer_generator"}

    def test_the_domain_and_application_layers_import_no_adapter(self):
        from pathlib import Path

        for folder in ("uc06/domain", "uc06/application", "uc06/api"):
            for path in Path(folder).rglob("*.py"):
                source = path.read_text(encoding="utf-8")
                assert "adapters" not in source, f"{path} imports an adapter"
