"""Identity — a deliberately thin placeholder for the company's identity provider.

Neither UC-01 nor UC-02 owns authentication, but both need a trustworthy answer to "who is
acting, and in what role":

* UC-01 attributes configuration versions to an administrator and scopes attempts to a learner;
* UC-02 records ``created_by`` / ``updated_by`` / ``retired_by`` on questions.

Rather than let each capability invent its own mechanism, both resolve a :class:`Principal`
through this module. Replacing it with the company's real identity provider means reimplementing
:func:`app.modules.identity.security.resolve_principal` — nothing else changes, because no
business rule reads the ``qa_users`` table directly.
"""

from __future__ import annotations

from app.modules.identity.models import User
from app.modules.identity.principal import Principal, Role

__all__ = ["Principal", "Role", "User"]
