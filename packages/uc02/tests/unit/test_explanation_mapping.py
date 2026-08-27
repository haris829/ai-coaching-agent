"""NARIC level -> explanation template mapping (scope section 6)."""

from __future__ import annotations

import pytest

from uc02.domain.explanation_mapping import (
    LEVEL_TO_TEMPLATE,
    TEMPLATE_PROFILES,
    profile_for_level,
    template_for_level,
)
from uc02.domain.models.enums import (
    AssumedPriorKnowledge,
    ExplanationDepth,
    ExplanationTemplateId,
    TerminologyLevel,
)


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (3, ExplanationTemplateId.BASIC),
        (4, ExplanationTemplateId.BASIC),
        (5, ExplanationTemplateId.INTERMEDIATE),
        (6, ExplanationTemplateId.INTERMEDIATE),
        (7, ExplanationTemplateId.ADVANCED),
        (8, ExplanationTemplateId.ADVANCED),
    ],
)
def test_each_level_maps_to_the_specified_template(level, expected):
    assert template_for_level(level) is expected


def test_level_6_is_never_advanced():
    """A Level 6 qualification is an undergraduate degree, not Masters level."""
    assert template_for_level(6) is ExplanationTemplateId.INTERMEDIATE
    assert template_for_level(6) is not ExplanationTemplateId.ADVANCED


def test_only_three_templates_exist():
    assert set(TEMPLATE_PROFILES) == {
        ExplanationTemplateId.BASIC,
        ExplanationTemplateId.INTERMEDIATE,
        ExplanationTemplateId.ADVANCED,
    }
    assert set(LEVEL_TO_TEMPLATE.values()) == set(TEMPLATE_PROFILES)


def test_profile_fields_differ_by_template():
    basic = profile_for_level(3)
    intermediate = profile_for_level(5)
    advanced = profile_for_level(7)

    assert basic.depth is ExplanationDepth.A_LEVEL_EQUIVALENT
    assert basic.terminology_level is TerminologyLevel.PLAIN_LANGUAGE
    assert basic.assumed_prior_knowledge is AssumedPriorKnowledge.MINIMAL

    assert intermediate.depth is ExplanationDepth.PRACTITIONER_FOUNDATION

    assert advanced.depth is ExplanationDepth.MASTERS_LEVEL
    assert advanced.terminology_level is TerminologyLevel.TECHNICAL
    assert advanced.assumed_prior_knowledge is AssumedPriorKnowledge.SUBSTANTIAL

    assert basic.detail_level < intermediate.detail_level < advanced.detail_level


@pytest.mark.parametrize(("level", "expected"), [(1, "basic"), (2, "basic"), (12, "advanced")])
def test_levels_outside_the_table_clamp_rather_than_crash(level, expected):
    """Assumption A-04: an unexpected level must never break assembly."""
    assert template_for_level(level).value == expected


def test_mapping_is_a_single_configuration_structure():
    """No scattered conditionals: the mapping lives in one module, in two tables.

    This is a structural guard -- if someone reintroduces ``if level == 3``
    elsewhere, it shows up here as a second place the level is branched on.
    """
    import pathlib
    import re

    offenders = []
    pattern = re.compile(r"(?:level|naric_level)\s*(?:==|>=|<=|>|<)\s*\d")
    for path in pathlib.Path("uc02").rglob("*.py"):
        if path.as_posix().endswith("uc02/domain/explanation_mapping.py"):
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(path.as_posix())
    assert offenders == []


def test_mapping_is_pure_and_deterministic():
    assert profile_for_level(7) == profile_for_level(7)
    assert profile_for_level(3) != profile_for_level(7)
