"""Request schemas.

`extra="forbid"` everywhere: an attempt to send `disclaimer`, `naric_level`,
`guard_triggered`, `system_prompt` or `user_id` produces a visible validation
error, not a silent ignore. Fields UC-06 owns are not negotiable by a client, and
a client that tries is told so.

There is no response model here. Responses are built by the domain response types
and serialised through the boundary check, so no schema definition can be edited
in a way that drops the disclaimer.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


class AskCaseQuestionRequest(BaseModel):
    """Ask a case-linked question.

    Note what is absent: no user_id (resolved server-side from request metadata,
    never read from the body), no naric_level (retrieved server-side; a
    client-supplied level is rejected), no prompt of any kind, and no disclaimer
    field of any kind.
    """

    model_config = _STRICT

    question: str = Field(min_length=1, max_length=4000)
    case_file_id: str = Field(min_length=1, max_length=128)
    #: UC-06 receives an opaque session_id and never creates one on a production
    #: path. It may be omitted only when ALLOW_DEV_SESSION_IDS is enabled, which
    #: defaults to off.
    session_id: str | None = Field(default=None, max_length=128)
