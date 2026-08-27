"""The three required documents exist and carry the required content.

Documentation drift is how a contract stops being a contract. These assertions
are deliberately about substance - the disclaimer discrepancy being flagged, every
required assumption row being present, every port being documented - not about
word counts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ASSUMPTIONS = Path("docs/assumptions.md")
CONTRACT = Path("docs/SHARED_CONTRACT.md")
INTEGRATION = Path("docs/INTEGRATION.md")


@pytest.fixture(scope="module")
def assumptions() -> str:
    return ASSUMPTIONS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def contract() -> str:
    return CONTRACT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def integration() -> str:
    return INTEGRATION.read_text(encoding="utf-8")


class TestAllThreeExist:
    @pytest.mark.parametrize("path", [ASSUMPTIONS, CONTRACT, INTEGRATION])
    def test_the_document_exists_and_is_substantial(self, path):
        assert path.exists(), f"{path} is a required deliverable"
        assert len(path.read_text(encoding="utf-8")) > 2000


class TestAssumptionsRegister:
    def test_the_disclaimer_discrepancy_is_flagged_prominently(self, assumptions):
        """Not buried in a table row: it has its own section near the top."""
        head = assumptions[:3000]
        assert "DISCLAIMER TEXT DISCREPANCY" in head
        assert "different wording" in head
        assert "A-01" in head
        assert "Overview" in head and "step 5" in head

    def test_it_says_the_discrepancy_is_unresolved(self, assumptions):
        assert "unresolved" in assumptions.lower()
        assert "requires the company" in assumptions.lower() or "must confirm" in assumptions.lower()

    @pytest.mark.parametrize(
        "required",
        [
            "disclaimer",
            "case file shape",
            "fact identifier scheme",
            "phrase sets",
            "Case Prep Agent",
            "Halt clearing",
            "levels 4 and 6",
        ],
    )
    def test_every_required_row_is_present(self, assumptions, required):
        assert required.lower() in assumptions.lower(), f"missing required assumption: {required}"

    def test_every_row_has_the_required_columns(self, assumptions):
        for heading in ("Assumption", "Why", "Risk if wrong", "Where in code"):
            assert heading in assumptions

    def test_the_ids_are_contiguous(self, assumptions):
        import re

        ids = sorted({int(m) for m in re.findall(r"\bA-(\d{2})\b", assumptions)})
        assert ids == list(range(1, max(ids) + 1)), f"gaps in assumption ids: {ids}"

    def test_every_referenced_code_path_exists(self, assumptions):
        import re

        paths = set(re.findall(r"`(uc06/[\w/]+\.py)`", assumptions))
        missing = [p for p in paths if not Path(p).exists()]
        assert missing == [], f"assumptions reference files that do not exist: {missing}"


class TestSharedContract:
    def test_the_canonical_disclaimer_is_reproduced_exactly(self, contract):
        from uc06.domain.disclaimer import CANONICAL_DISCLAIMER

        assert CANONICAL_DISCLAIMER in contract

    def test_the_discrepancy_is_flagged_here_too(self, contract):
        assert "UNRESOLVED" in contract
        assert "A-01" in contract

    def test_the_three_layers_are_documented(self, contract):
        assert "three layers" in contract.lower() or "The three layers" in contract
        for layer in ("Type", "Serialisation boundary", "Output scan"):
            assert layer in contract

    def test_every_interaction_record_field_is_documented(self, contract):
        import dataclasses

        from uc06.domain.models import InteractionRecord

        for field in dataclasses.fields(InteractionRecord):
            assert field.name in contract, f"{field.name} is written but not documented"

    def test_it_states_there_is_no_question_text_field(self, contract):
        assert "no `question_text` field" in contract

    def test_every_audit_record_field_is_documented(self, contract):
        import dataclasses

        from uc06.domain.models import AuditRecord

        for field in dataclasses.fields(AuditRecord):
            assert field.name in contract

    def test_the_case_file_shape_and_identifier_scheme_are_documented(self, contract):
        import dataclasses

        from uc06.domain.models import CaseFile

        for field in dataclasses.fields(CaseFile):
            assert field.name in contract
        assert "fact identifier scheme" in contract.lower()
        assert "stable" in contract.lower()

    def test_the_learner_context_shape_is_documented(self, contract):
        import dataclasses

        from uc06.domain.models import LearnerContext

        for field in dataclasses.fields(LearnerContext):
            assert field.name in contract

    def test_session_identity_is_documented(self, contract):
        assert "never creates one" in contract

    def test_every_vocabulary_is_documented(self, contract):
        from uc06.domain.enums import GuardClass, NaricLevel, RatingState, SourceStatus

        for enum in (NaricLevel, SourceStatus, GuardClass, RatingState):
            for member in enum:
                assert member.value in contract, f"{member.value} is not documented"

    def test_the_profile_mapping_is_documented(self, contract):
        from uc06.domain.enums import PROFILE_BY_LEVEL

        for level, profile in PROFILE_BY_LEVEL.items():
            assert level.value in contract
            assert profile.value in contract

    def test_halt_semantics_are_documented(self, contract):
        for heading in ("What halts", "What it blocks", "How it clears"):
            assert heading in contract

    def test_every_port_is_documented_with_its_signature(self, contract):
        for port in (
            "CaseFileProvider",
            "LearnerContextProvider",
            "AnswerGenerator",
            "GuardClassifier",
            "InteractionLogRepository",
            "SessionHaltRepository",
            "AdminAlertSink",
            "SecurityIncidentSink",
            "CurrentUserProvider",
        ):
            assert port in contract

    def test_extension_points_are_documented(self, contract):
        assert "Extension points" in contract
        assert "Not extension points" in contract

    def test_every_field_is_marked_company_or_assumed(self, contract):
        assert "[COMPANY]" in contract
        assert "[ASSUMED]" in contract

    def test_every_error_code_is_documented(self, contract):
        for code in (
            "invalid_request",
            "identity_unavailable",
            "session_id_required",
            "case_access_denied",
            "session_not_visible",
            "case_origin_rejected",
            "session_not_case_linked",
            "session_halted",
            "generation_invalid",
            "generation_unavailable",
            "response_withheld",
            "generation_timeout",
            "internal_error",
        ):
            assert code in contract


class TestIntegrationRunbook:
    def test_it_names_the_three_costs(self, integration):
        assert "One new adapter file" in integration
        assert "One line" in integration
        assert "One environment variable" in integration

    def test_every_dependency_has_file_port_registry_env_and_command(self, integration):
        for section in (
            "Case file system",
            "Learner context",
            "Answer generator",
            "Guard classifier",
            "Interaction log",
            "Alerting",
            "Authentication",
        ):
            assert section in integration

    def test_every_environment_variable_is_named(self, integration):
        from uc06.config import PROVIDER_KEYS

        for attribute, _ in PROVIDER_KEYS:
            assert attribute.upper() in integration, f"{attribute.upper()} is not in the runbook"

    def test_every_conformance_command_is_literal_and_runnable(self, integration):
        import re

        commands = re.findall(r"python -m pytest (tests/conformance/[\w/.]+)", integration)
        assert commands, "no conformance commands given"
        for path in set(commands):
            assert Path(path).exists(), f"{path} does not exist"

    def test_it_points_at_the_assumptions_to_check_first(self, integration):
        assert "Assumptions to check first" in integration
        assert "A-03" in integration and "A-05" in integration

    def test_there_is_a_worked_example_with_real_file_contents(self, integration):
        assert "Worked example" in integration
        assert "class LexOsCaseFileAdapter" in integration
        assert "def get_case_file" in integration
        assert "ProviderInvalidResponse" in integration

    def test_the_worked_example_shows_the_registry_diff_and_the_config_change(self, integration):
        assert '+        "lexos"' in integration
        assert "+CASE_FILE_PROVIDER=lexos" in integration

    def test_it_states_what_was_touched(self, integration):
        assert "What was touched" in integration
        assert "Zero changes to domain models" in integration

    def test_the_confidentiality_section_is_present_and_specific(self, integration):
        assert "Confidentiality" in integration
        assert "sign-off" in integration
        assert "privilege" in integration.lower()
        assert "fact_digest" in integration

    def test_it_finishes_with_the_non_negotiables(self, integration):
        tail = integration[-3000:]
        assert "non-negotiables" in tail.lower()
        for rule in (
            "only place upstream payload shapes are known",
            "never invents data",
            "Authorisation stays server-side",
            "contract conversation, not an adapter workaround",
        ):
            assert rule in tail

    def test_the_template_it_tells_you_to_copy_exists(self, integration):
        assert "uc06/adapters/real/_template.py" in integration
        assert Path("uc06/adapters/real/_template.py").exists()


class TestTheTemplateHasTodosWhereRealValuesGo:
    def test_it_marks_every_point_needing_a_real_value(self):
        source = Path("uc06/adapters/real/_template.py").read_text(encoding="utf-8")
        for marker in (
            "TODO 1 - ENDPOINT",
            "TODO 2 - AUTH",
            "TODO 3 - PAYLOAD MAPPING",
            "TODO 4 - TRANSPORT AND ERROR TRANSLATION",
        ):
            assert marker in source, f"the template is missing {marker}"

    def test_it_states_the_failure_contract(self):
        source = Path("uc06/adapters/real/_template.py").read_text(encoding="utf-8")
        for error in ("ProviderUnavailable", "ProviderTimeout", "ProviderInvalidResponse"):
            assert error in source

    def test_it_states_the_four_rules(self):
        source = Path("uc06/adapters/real/_template.py").read_text(encoding="utf-8")
        assert "NEVER invents data" in source
        assert "Authorisation stays server-side" in source
        assert "contract conversation, not an adapter workaround" in source

    def test_it_is_importable_so_a_copy_starts_from_working_code(self):
        from uc06.adapters.real import _template

        assert hasattr(_template, "TemplateCaseFileAdapter")
        assert hasattr(_template, "TemplateLearnerContextAdapter")
