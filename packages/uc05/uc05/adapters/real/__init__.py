"""Real adapter family.

``_template.py`` is a copy-paste skeleton, not a working adapter; it is
deliberately NOT imported here.  Importing it would register a "company" key
whose ``_fetch`` raises ``NotImplementedError``, which is exactly the kind of
half-wired provider that should not be selectable.

Add a real adapter by listing its module in ``uc05.composition.ADAPTER_MODULES``.
"""

from .configured_generator import (  # noqa: F401
    ConfiguredAnswerGenerator,
    ConfiguredGuidingQuestionGenerator,
)
