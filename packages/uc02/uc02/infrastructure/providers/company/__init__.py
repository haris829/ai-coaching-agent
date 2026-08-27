"""Where the real adapters go. Deliberately unimplemented.

The company has not delivered NARIC, the Courses Agent or Legal Foot Prints:
there are no endpoints, no specs and no sample payloads. Writing
``CompanyNaricProvider`` today would mean inventing an external API, which is
explicitly out of scope. So each class below is a marked, failing stub.

For each one, the integration engineer:

1. Reads the corresponding rows of docs/assumptions.md and diffs them against the
   delivered spec.
2. Implements the port (``uc02/domain/ports/providers.py``), translating the real
   payload into the record type and translating transport failures into
   ``ProviderUnavailable`` / ``ProviderTimeout`` / ``ProviderInvalidResponse``.
   Do not apply defaults here -- the assembly service owns defaulting so the
   fallback is recorded in ``source_status``.
3. Registers the class in ``uc02/infrastructure/providers/factory.py`` under the
   ``company`` choice.
4. Flips the matching config value (e.g. ``NARIC_PROVIDER=company``).

``ContextAssemblyService`` does not change.
"""

from __future__ import annotations

from uc02.domain.errors import ProviderNotImplemented
from uc02.domain.models.provider_records import (
    CoursesRecord,
    LegalProfileRecord,
    NaricRecord,
    QuestionRecord,
)
from uc02.domain.ports.providers import (
    CoursesProvider,
    LegalFootprintsProvider,
    NaricProvider,
    QuestionHistoryProvider,
)

_MESSAGE = (
    "{name} is a stub: the company system has not been delivered, so there is no "
    "contract to implement against. See uc02/infrastructure/providers/company/__init__.py "
    "and docs/integration.md."
)


class CompanyNaricProvider(NaricProvider):
    """TODO(integration): NARIC qualification lookup.

    Verify first: assumptions A-01 (numeric level), A-02 (label field),
    A-03 (levels 4 and 6 exist), A-04 (levels outside 3-8),
    A-05 (missing qualification is a success, not a 404).
    """

    async def get_qualification_level(self, user_id: str) -> NaricRecord:
        raise ProviderNotImplemented(_MESSAGE.format(name="CompanyNaricProvider"))


class CompanyCoursesProvider(CoursesProvider):
    """TODO(integration): Courses Agent enrolment/progress lookup.

    Verify first: assumptions A-06 (completion scale 0-100 vs 0-1),
    A-07 (one call returns all enrolments), A-08 (last-accessed lesson is per
    course and optional), A-09 (identifiers are opaque).
    """

    async def get_learning_context(self, user_id: str) -> CoursesRecord:
        raise ProviderNotImplemented(_MESSAGE.format(name="CompanyCoursesProvider"))


class CompanyLegalFootprintsProvider(LegalFootprintsProvider):
    """TODO(integration): Legal Foot Prints profile lookup.

    Verify first: assumptions A-10 (speciality is a list), A-11 (single practice
    area), A-12 (case-type preferences are free text), A-13 (speciality drives
    the explanation domain).
    """

    async def get_profile(self, user_id: str) -> LegalProfileRecord:
        raise ProviderNotImplemented(_MESSAGE.format(name="CompanyLegalFootprintsProvider"))


class CompanyQuestionHistoryProvider(QuestionHistoryProvider):
    """TODO(integration): cross-session question history lookup.

    Verify first: assumptions A-15 (cross-session query in one call, no
    pagination -- resolve this one before writing any code), A-16 (newest-first
    ordering), A-17 (topic tag), A-18 (question text handling).
    """

    async def get_recent_questions(self, user_id: str, limit: int) -> list[QuestionRecord]:
        raise ProviderNotImplemented(_MESSAGE.format(name="CompanyQuestionHistoryProvider"))


__all__ = [
    "CompanyCoursesProvider",
    "CompanyLegalFootprintsProvider",
    "CompanyNaricProvider",
    "CompanyQuestionHistoryProvider",
]
