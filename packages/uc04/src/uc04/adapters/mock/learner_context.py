"""Mock LearnerContextProvider covering every NARIC level and every source state."""

from __future__ import annotations

from ...core.calibration import coerce_level, invalid_level_context
from ...domain.enums import NaricLevel, NaricLevelSource, SourceStatus
from ...domain.errors import ProviderUnavailable
from ...domain.models import LearnerContext, default_learner_context
from . import fixtures as fx

PORT = "learner_context"

#: An upstream value that maps to no enum member - an invalid response, not a level.
RAW_INVALID_LEVEL = "postgraduate-ish"


class MockLearnerContextProvider:
    name = "mock"

    def get_context(self, session_id: str, user_id: str) -> LearnerContext:
        if user_id == fx.USER_CONTEXT_DOWN:
            raise ProviderUnavailable(PORT, "context service unavailable")

        if user_id == fx.USER_LEVEL_INVALID:
            # The adapter is where an unmappable upstream value is caught. It never guesses.
            if coerce_level(RAW_INVALID_LEVEL) is None:
                return invalid_level_context(user_id)

        if user_id == fx.USER_NO_CONTEXT:
            # Reached the service, but it holds nothing for this learner: empty, not unavailable.
            return default_learner_context(user_id, SourceStatus.EMPTY)

        known = fx.LEARNER_CONTEXTS.get(user_id)
        if known is not None:
            return known

        return LearnerContext(
            user_id=user_id,
            naric_level=NaricLevel.LEVEL_5,
            naric_level_source=NaricLevelSource.RETRIEVED,
            practice_area=None,
            source_status=SourceStatus.AVAILABLE,
        )
