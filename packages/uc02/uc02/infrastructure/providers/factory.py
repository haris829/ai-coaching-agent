"""Provider factory: config value -> adapter instance.

Switching to a real integration is a config change plus one new class per
provider. This module is the only place that maps a config string to an
implementation.
"""

from __future__ import annotations

from dataclasses import dataclass

from uc02.domain.ports.providers import (
    CoursesProvider,
    LegalFootprintsProvider,
    NaricProvider,
    QuestionHistoryProvider,
)
from uc02.infrastructure.config.settings import Settings
from uc02.infrastructure.providers.company import (
    CompanyCoursesProvider,
    CompanyLegalFootprintsProvider,
    CompanyNaricProvider,
    CompanyQuestionHistoryProvider,
)
from uc02.infrastructure.providers.mocks import (
    MockCoursesProvider,
    MockLegalFootprintsProvider,
    MockNaricProvider,
    MockQuestionHistoryProvider,
)


@dataclass(frozen=True)
class ProviderBundle:
    """The four upstream adapters the assembly service needs."""

    naric: NaricProvider
    courses: CoursesProvider
    legal: LegalFootprintsProvider
    history: QuestionHistoryProvider


_NARIC = {"mock": MockNaricProvider, "company": CompanyNaricProvider}
_COURSES = {"mock": MockCoursesProvider, "company": CompanyCoursesProvider}
_LEGAL = {"mock": MockLegalFootprintsProvider, "company": CompanyLegalFootprintsProvider}
_HISTORY = {"mock": MockQuestionHistoryProvider, "company": CompanyQuestionHistoryProvider}


def build_providers(settings: Settings) -> ProviderBundle:
    """Instantiate the configured adapters.

    ``company`` selects the stub classes, which raise ``ProviderNotImplemented``
    on call. That is intentional: they mark where real adapters go and fail loudly
    rather than silently degrading to mock data.
    """
    return ProviderBundle(
        naric=_NARIC[settings.naric_provider](),
        courses=_COURSES[settings.courses_provider](),
        legal=_LEGAL[settings.legal_provider](),
        history=_HISTORY[settings.history_provider](),
    )
