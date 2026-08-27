"""No configuration key can disable the disclaimer or alter the redirect.

The absence of a suppression path is the guarantee, so it is asserted directly:
the whole configuration surface is enumerated and checked, both by name and by
effect. A flag defaulted to false would still be a suppression path.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from uc06 import config
from uc06.application.emitter import ResponseEmitter
from uc06.composition import build_container
from uc06.config import ENV_KEYS, Settings
from uc06.domain.disclaimer import CANONICAL_DISCLAIMER
from uc06.domain.enums import GuardClass

from .conftest import make_settings

#: Any of these fragments in a configuration key name would be a suppression
#: path, whatever its default.
FORBIDDEN_FRAGMENTS = (
    "disclaimer",
    "disclosure",
    "legal_notice",
    "notice_enabled",
    "suppress",
    "redirect",
    "guard",
    "prediction",
    "outcome",
    "safety",
    "bypass",
    "override",
    "skip",
    "unsafe",
    "raw_mode",
    "test_mode",
    "debug_mode",
)

ALLOWED_GUARD_LIKE = {"guard_classifier"}  # selects WHICH classifier, never WHETHER


class TestNoSuppressionKeyExists:
    def test_no_settings_field_name_could_disable_a_safety_control(self):
        offenders = [
            name
            for name in Settings.field_names()
            if name not in ALLOWED_GUARD_LIKE
            and any(fragment in name.lower() for fragment in FORBIDDEN_FRAGMENTS)
        ]
        assert offenders == [], f"configuration keys that could disable a safety control: {offenders}"

    def test_no_environment_variable_could_disable_a_safety_control(self):
        offenders = [
            key
            for key in ENV_KEYS
            if key.lower() not in ALLOWED_GUARD_LIKE
            and any(fragment in key.lower() for fragment in FORBIDDEN_FRAGMENTS)
        ]
        assert offenders == [], f"environment variables that could disable a safety control: {offenders}"

    def test_guard_classifier_key_selects_an_implementation_not_a_switch(self):
        """The one guard-shaped key chooses which classifier runs. It cannot
        choose that none runs: an unregistered name fails at startup, and the
        in-domain rule set is the fallback when a classifier errors."""
        from uc06.domain.errors import ConfigurationError

        with pytest.raises(ConfigurationError):
            build_container(make_settings(guard_classifier="off"))
        with pytest.raises(ConfigurationError):
            build_container(make_settings(guard_classifier="none"))
        with pytest.raises(ConfigurationError):
            build_container(make_settings(guard_classifier="disabled"))

    def test_the_env_example_declares_no_suppression_key(self):
        text = Path(".env.example").read_text(encoding="utf-8")
        keys = re.findall(r"^#?\s*([A-Z0-9_]+)=", text, re.MULTILINE)
        offenders = [
            key
            for key in keys
            if key.lower() not in ALLOWED_GUARD_LIKE
            and any(fragment in key.lower() for fragment in FORBIDDEN_FRAGMENTS)
        ]
        assert offenders == []

    def test_config_module_never_mentions_the_disclaimer_as_a_setting(self):
        """config.py may DISCUSS the absence in prose; it must not import the
        constant or reference a disclaimer value."""
        source = Path("uc06/config.py").read_text(encoding="utf-8")
        assert "CANONICAL_DISCLAIMER" not in source
        assert "from .domain.disclaimer" not in source

    def test_no_settings_value_changes_the_emitted_disclaimer(self):
        """Effect, not just naming: sweep every boolean and provider setting and
        assert the emitted disclaimer is identical in every combination."""
        from uc06.domain.responses import SafeErrorResponse

        seen = set()
        for allow_dev in (True, False):
            for timeout in (1, 10_000, 600_000):
                settings = make_settings(allow_dev_session_ids=allow_dev, generation_timeout_ms=timeout)
                container = build_container(settings)
                payload, _ = container.emitter.emit(
                    SafeErrorResponse(code="x", message="y", request_id="r"),
                    session_id="s",
                    user_id="u",
                    case_file_id=None,
                    request_id="r",
                )
                seen.add(payload["disclaimer"])
        assert seen == {CANONICAL_DISCLAIMER}

    def test_the_emitter_exposes_no_switch(self):
        """ResponseEmitter takes ports and a serializer. There is no 'enabled',
        'strict' or 'check' parameter to turn the boundary check off."""
        import inspect

        parameters = set(inspect.signature(ResponseEmitter).parameters)
        assert parameters == {"halts", "admin_alerts", "security_incidents", "serializer"}

    def test_check_payload_takes_no_options(self):
        import inspect

        from uc06.application.boundary import check_payload

        assert list(inspect.signature(check_payload).parameters) == ["payload"]


class TestNoConfigurationAltersTheRedirect:
    def test_no_settings_combination_changes_guard_classification(self):
        from uc06.domain.guard_vocabulary import classify_question

        questions = ["Will my client win at trial?", "Should we plead to the lesser count?"]
        for allow_dev in (True, False):
            for timeout in (1, 10_000):
                settings = make_settings(allow_dev_session_ids=allow_dev, generation_timeout_ms=timeout)
                container = build_container(settings)
                for question in questions:
                    assert container.guard.classify(question).triggered
                    assert classify_question(question)[0] is not GuardClass.NONE

    def test_the_guard_vocabulary_reads_no_configuration(self):
        source = Path("uc06/domain/guard_vocabulary.py").read_text(encoding="utf-8")
        assert "os.environ" not in source
        assert "getenv" not in source
        assert "Settings" not in source

    def test_the_redirect_library_reads_no_configuration(self):
        source = Path("uc06/domain/legal_tests.py").read_text(encoding="utf-8")
        assert "os.environ" not in source
        assert "getenv" not in source
        assert "Settings" not in source

    def test_no_module_reads_an_undeclared_environment_variable(self):
        """Every environment read in the package goes through config.ENV_KEYS."""
        pattern = re.compile(r"""os\.environ(?:\.get)?\[?\s*["']([A-Z0-9_]+)["']""")
        found: set[str] = set()
        for path in Path("uc06").rglob("*.py"):
            found.update(pattern.findall(path.read_text(encoding="utf-8")))
        assert found <= set(ENV_KEYS), f"undeclared environment reads: {sorted(found - set(ENV_KEYS))}"

    def test_settings_reads_environment_only_through_from_env(self):
        source = Path("uc06/config.py").read_text(encoding="utf-8")
        assert source.count("os.environ") == 1
        others = [
            path
            for path in Path("uc06").rglob("*.py")
            if "os.environ" in path.read_text(encoding="utf-8") and path != Path("uc06/config.py")
        ]
        assert others == []
