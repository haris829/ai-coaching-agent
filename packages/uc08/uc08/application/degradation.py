"""Mapping upstream failures onto the source status vocabulary.

``empty`` and ``unavailable`` are different states and are never conflated:
``empty`` comes from a successful read with nothing in it, and only a raised
provider error produces ``unavailable``, ``invalid`` or ``partial``.
"""

from __future__ import annotations

from uc08.domain.enums import SourceStatus
from uc08.domain.errors import ProviderError, ProviderInvalidResponse, ProviderTimeout, ProviderUnavailable


def status_for_provider_error(error: ProviderError) -> SourceStatus:
    if isinstance(error, ProviderInvalidResponse):
        return SourceStatus.INVALID
    if isinstance(error, (ProviderUnavailable, ProviderTimeout)):
        return SourceStatus.UNAVAILABLE
    return SourceStatus.UNAVAILABLE
