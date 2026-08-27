"""Read-only access to an interaction delivered by another component.

READ ONLY BY SHAPE: this Protocol declares no mutating method, and an architecture test
asserts that neither the port nor any adapter implementing it exposes one.  This
component never writes to, corrects, or annotates an interaction.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from uc10.domain.models import InteractionRecord


@runtime_checkable
class InteractionProvider(Protocol):
    def get(self, interaction_id: str) -> InteractionRecord:
        """Return the interaction, normalised onto the platform contract.

        Raises ProviderUnavailable / ProviderTimeout / ProviderInvalidResponse / RecordNotFound.
        """
        ...

    def delivered_at(self, interaction_id: str) -> datetime:
        """Server-side delivery time (UTC). The historical rating window is measured
        against this value and never against a client-supplied timestamp."""
        ...
