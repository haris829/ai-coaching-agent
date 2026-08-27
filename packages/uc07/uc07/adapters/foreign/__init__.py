"""Foreign ("Nexus LMS") adapters plus their fictional payload."""

from uc07.adapters.foreign.adapters import (
    ForeignCoursesProvider,
    ForeignFeedbackProvider,
    ForeignInteractionLogProvider,
    ForeignLearnerProfileProvider,
)
from uc07.adapters.foreign.payload import EXTERNAL_LEARNER_ID, NEXUS_PAYLOAD

__all__ = [
    "EXTERNAL_LEARNER_ID",
    "NEXUS_PAYLOAD",
    "ForeignCoursesProvider",
    "ForeignFeedbackProvider",
    "ForeignInteractionLogProvider",
    "ForeignLearnerProfileProvider",
]
