"""The complete schema.

Importing this module registers every table on ``Base.metadata``. Alembic's environment, the test
harness and the local bootstrap all go through here, so a new module's tables can never be
half-registered — which on SQLite would show up as a confusing "no such table" rather than an
import error.
"""

from __future__ import annotations

from app.db.base import Base
from app.modules.analytics import models as analytics_models  # noqa: F401
from app.modules.attempt_delivery import models as attempt_delivery_models  # noqa: F401
from app.modules.certification import models as certification_models  # noqa: F401
from app.modules.coaching import models as coaching_models  # noqa: F401
from app.modules.feedback import models as feedback_models  # noqa: F401
from app.modules.formal_assessment import models as formal_assessment_models  # noqa: F401
from app.modules.identity import models as identity_models  # noqa: F401
from app.modules.question_bank import models as question_bank_models  # noqa: F401
from app.modules.quiz_configuration import models as quiz_configuration_models  # noqa: F401
from app.modules.retakes import models as retake_models  # noqa: F401
from app.modules.scoring import models as scoring_models  # noqa: F401

#: What Alembic compares the database against.
target_metadata = Base.metadata

__all__ = ["Base", "target_metadata"]
