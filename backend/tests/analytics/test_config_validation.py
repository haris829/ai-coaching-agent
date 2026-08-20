"""Configuration and threshold validation tests (spec sections 8, 16, 25)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.analytics.config import (
    AnalyticsSettings,
    IssueSeverity,
    validate_settings_payload,
)
from app.modules.analytics.errors import ConfigurationError, InvalidThresholdError

from .conftest import make_settings


class TestRangeValidation:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("flag_wrong_answer_rate_threshold", 0),  # must be > 0
            ("flag_wrong_answer_rate_threshold", -5),
            ("flag_wrong_answer_rate_threshold", 100.1),
            ("flag_wrong_answer_rate_threshold", "not-a-number"),
            ("flag_min_responses", 0),
            ("flag_min_responses", -1),
            ("repository_page_size", 0),
            ("query_timeout_seconds", 0),
            ("query_timeout_seconds", -30),
            ("decimal_places", -1),
            ("decimal_places", 7),
            ("export_max_rows", 0),
            ("log_level", "CHATTY"),
        ],
    )
    def test_out_of_range_values_are_reported_as_errors(self, field, value):
        report = validate_settings_payload({field: value})

        assert report.valid is False
        assert report.requires_confirmation is False
        assert [issue.field for issue in report.errors] == [field]
        assert report.errors[0].severity is IssueSeverity.ERROR

    def test_unknown_setting_is_rejected(self):
        report = validate_settings_payload({"flag_threshold_typo": 50})

        assert report.valid is False
        assert report.errors

    def test_invalid_values_never_raise_from_the_validator(self):
        # The endpoint must be able to report a problem, not crash on it.
        assert validate_settings_payload({"flag_min_responses": "abc"}).valid is False

    def test_construction_rejects_out_of_range_values(self):
        with pytest.raises(ValidationError):
            make_settings(flag_wrong_answer_rate_threshold=150)


class TestWarningTier:
    @pytest.mark.parametrize("threshold", [6.0, 10.0, 14.9])
    def test_unusually_low_threshold_warns_but_stays_valid(self, threshold):
        report = validate_settings_payload(
            {"flag_wrong_answer_rate_threshold": threshold}
        )

        assert report.valid is True
        assert "THRESHOLD_EXTREME_LOW" in {issue.code for issue in report.warnings}

    @pytest.mark.parametrize("threshold", [90.1, 95.0, 98.9])
    def test_unusually_high_threshold_warns_but_stays_valid(self, threshold):
        report = validate_settings_payload(
            {"flag_wrong_answer_rate_threshold": threshold}
        )

        assert report.valid is True
        assert "THRESHOLD_EXTREME_HIGH" in {issue.code for issue in report.warnings}

    def test_ordinary_threshold_raises_no_issues(self):
        report = validate_settings_payload(
            {"flag_wrong_answer_rate_threshold": 45.0}
        )

        assert report.valid is True
        assert report.warnings == ()
        assert report.dangerous == ()

    def test_tiny_sample_requirement_warns(self):
        report = validate_settings_payload(
            {"flag_min_responses": 1}
        )

        assert report.valid is True
        assert "MIN_RESPONSES_LOW_CONFIDENCE" in {issue.code for issue in report.warnings}

    def test_the_settings_carry_no_authentication_switch(self):
        """The old test warned when authentication was on but no keys were configured.

        Both halves are gone. Authentication is the application's — every analytics endpoint sits
        behind the administrator guard UC-01, UC-02 and UC-08 use — and this settings object is
        something a *client* can validate candidate values against. A runtime-tunable
        authentication flag is a runtime-tunable way to disable authentication, so the strongest
        form of the old assertion is that neither field exists to be set.
        """
        fields = set(AnalyticsSettings.model_fields)
        assert "auth_enabled" not in fields
        assert "admin_api_keys" not in fields

        # And a payload that tries to set one is refused rather than ignored.
        report = validate_settings_payload({"auth_enabled": False})
        assert report.valid is False
        assert "auth_enabled" in {issue.field for issue in report.errors}

    def test_warnings_are_available_from_a_live_settings_object(self):
        settings = make_settings(flag_min_responses=1)

        codes = {issue.code for issue in settings.issues()}

        assert "MIN_RESPONSES_LOW_CONFIDENCE" in codes


class TestDangerousTier:
    @pytest.mark.parametrize(
        ("payload", "expected_code"),
        [
            ({"flag_wrong_answer_rate_threshold": 1.0}, "THRESHOLD_DANGEROUSLY_LOW"),
            ({"flag_wrong_answer_rate_threshold": 5.0}, "THRESHOLD_DANGEROUSLY_LOW"),
            ({"flag_wrong_answer_rate_threshold": 99.0}, "THRESHOLD_DANGEROUSLY_HIGH"),
            ({"flag_wrong_answer_rate_threshold": 100.0}, "THRESHOLD_DANGEROUSLY_HIGH"),
            ({"repository_page_size": 5000}, "PAGE_SIZE_DANGEROUS"),
            ({"query_timeout_seconds": 600.0}, "TIMEOUT_DANGEROUS"),
        ],
    )
    def test_dangerous_values_require_confirmation(self, payload, expected_code):
        report = validate_settings_payload({**payload})

        assert report.valid is False
        assert report.requires_confirmation is True
        assert expected_code in {issue.code for issue in report.dangerous}
        assert report.effective is None

    def test_confirmation_makes_a_dangerous_configuration_valid(self):
        report = validate_settings_payload(
            {"flag_wrong_answer_rate_threshold": 2.0},
            confirm_dangerous=True,
        )

        assert report.valid is True
        assert report.requires_confirmation is False
        assert report.effective is not None

    def test_construction_refuses_dangerous_thresholds_without_confirmation(self):
        with pytest.raises(InvalidThresholdError) as exc:
            AnalyticsSettings(_env_file=None, flag_wrong_answer_rate_threshold=2.0)

        assert exc.value.code == "INVALID_THRESHOLD"
        assert "allow_dangerous_configuration" in exc.value.message
        assert exc.value.details["requires_confirmation"] is True

    def test_explicit_confirmation_permits_a_dangerous_configuration(self):
        settings = AnalyticsSettings(
            _env_file=None,
            flag_wrong_answer_rate_threshold=2.0,
            allow_dangerous_configuration=True,
        )

        assert settings.flag_wrong_answer_rate_threshold == 2.0

    def test_nothing_dangerous_is_accepted_silently(self):
        """Every dangerous value either raises or is explicitly confirmed."""
        for payload in (
            {"flag_wrong_answer_rate_threshold": 1.0},
            {"repository_page_size": 5000},
            {"query_timeout_seconds": 600.0},
        ):
            with pytest.raises(ConfigurationError):
                AnalyticsSettings(_env_file=None, **payload)


class TestSecretHandling:
    """Nothing in these settings is a credential any more — and that is the point.

    UC-10 held an API-key map here and had to redact it from the ``/config`` payload and from every
    validation report. The map is gone with the merge, so the tests below assert the *reason* the
    redaction existed is no longer present, plus the one thing still worth guarding: a validation
    report must never echo an arbitrary value back at a caller.
    """

    def test_the_public_dump_contains_no_credential(self):
        settings = make_settings()

        dumped = settings.public_dump()

        assert "admin_api_keys" not in dumped
        assert "auth_enabled" not in dumped
        # Nothing whose name suggests a secret survives in these settings at all.
        for key in dumped:
            assert not any(
                fragment in key.lower()
                for fragment in ("key", "secret", "password", "token", "credential")
            ), key

    def test_a_validation_report_does_not_echo_an_arbitrary_value(self):
        """An unknown field is reported by *name*, and its value is not reflected verbatim.

        Still worth asserting: a report is rendered to an administrator's browser, and echoing
        whatever was posted is how a validation endpoint becomes a reflection sink.
        """
        report = validate_settings_payload({"unexpected_setting": "super-secret-string"})

        assert report.valid is False
        assert "unexpected_setting" in {issue.field for issue in report.errors}
        rendered = str(report.model_dump())
        assert "super-secret-string" not in rendered


class TestSettingsIntegrity:
    def test_settings_are_immutable(self):
        settings = make_settings()

        with pytest.raises(ValidationError):
            settings.flag_wrong_answer_rate_threshold = 99.0

    def test_candidate_validation_starts_from_the_running_configuration(self):
        running = make_settings(flag_min_responses=11, decimal_places=3)

        report = validate_settings_payload(
            {"flag_wrong_answer_rate_threshold": 50.0}, base=running
        )

        assert report.effective["flag_min_responses"] == 11
        assert report.effective["decimal_places"] == 3
        assert report.effective["flag_wrong_answer_rate_threshold"] == 50.0

    def test_validation_does_not_apply_the_candidate(self):
        running = make_settings(flag_wrong_answer_rate_threshold=40.0)

        validate_settings_payload({"flag_wrong_answer_rate_threshold": 90.0}, base=running)

        assert running.flag_wrong_answer_rate_threshold == 40.0


class TestNoHardcodedThresholds:
    def test_service_modules_contain_no_literal_threshold(self):
        """Thresholds must come from configuration, not from source literals."""
        import pathlib
        import re

        service_dir = pathlib.Path(__file__).resolve().parents[1] / "uc10_analytics" / "services"
        offenders: list[str] = []
        # Bare percentage-like literals in a comparison, e.g. "rate > 40".
        pattern = re.compile(r"[<>]=?\s*\d{2,3}(\.\d+)?\b")
        for path in service_dir.glob("*.py"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("#")[0]
                if pattern.search(code):
                    offenders.append(f"{path.name}:{number}: {line.strip()}")

        assert offenders == [], "threshold comparisons must read from settings"

    def test_flag_criteria_are_driven_entirely_by_arguments(self):
        from app.modules.analytics.services.aggregation import QuestionAccumulator

        from .factories import make_response

        accumulator = QuestionAccumulator(question_id="q")
        for index in range(10):
            accumulator.add(
                make_response(f"r{index}", selected_answer="B", is_correct=index >= 5)
            )

        assert accumulator.meets_flag_criteria(threshold=49.0, min_responses=1) is True
        assert accumulator.meets_flag_criteria(threshold=51.0, min_responses=1) is False
