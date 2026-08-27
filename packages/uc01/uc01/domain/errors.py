"""UC-01 business errors.

Every error carries:

* ``code``          — stable machine code, safe to send to a client.
* ``user_message``  — safe, non-technical text for the end user.
* ``failure_code``  — what gets written to the session record for diagnosis.

Technical detail (upstream messages, stack traces, connection strings) is passed
separately in ``technical_detail`` and is only ever logged server-side.
"""

from __future__ import annotations

from collections.abc import Mapping


class Uc01Error(Exception):
    """Base class for all expected UC-01 failures."""

    code: str = "uc01_error"
    user_message: str = "We could not complete that request. Please try again."

    def __init__(
        self,
        user_message: str | None = None,
        *,
        technical_detail: str | None = None,
        context: Mapping[str, object] | None = None,
    ) -> None:
        self.user_message = user_message or type(self).user_message
        self.technical_detail = technical_detail
        self.context: Mapping[str, object] = dict(context or {})
        super().__init__(self.user_message)

    @property
    def failure_code(self) -> str:
        return self.code


class AuthenticationRequiredError(Uc01Error):
    code = "authentication_required"
    user_message = "Please sign in to start a coaching session."


class ModeUnavailableError(Uc01Error):
    """The requested session mode is not available for this user right now.

    Raised by the server even if the client managed to bypass the disabled control in
    the UI.
    """

    code = "session_mode_unavailable"
    user_message = "That session mode is not available right now."


class SelectionRequiredError(Uc01Error):
    """The mode needs a course/lesson/case selection that was not supplied."""

    code = "selection_required"
    user_message = "Please choose what this session should be linked to."


class SelectionNotAllowedError(Uc01Error):
    """A selection was supplied that does not belong to the chosen mode."""

    code = "selection_not_allowed"
    user_message = "That selection cannot be used with the chosen session mode."


class SelectionNotAccessibleError(Uc01Error):
    """The client sent an id that does not exist for this user or is not accessible.

    Deliberately does not distinguish 'missing' from 'not yours' in the user-facing
    message, so the API cannot be used to enumerate other users' resources.
    """

    code = "selection_not_accessible"
    user_message = "That item is not available for your account."


class SessionNotFoundError(Uc01Error):
    code = "session_not_found"
    user_message = "That coaching session could not be found."


class DependencyDegradedError(Uc01Error):
    """A dependency required by the requested mode failed during session opening.

    The session record still exists and is marked ``failed`` with this failure code.
    """

    code = "dependency_unavailable"
    user_message = (
        "That part of the platform is temporarily unavailable. "
        "You can still start a free-form session."
    )


class SessionInitializationError(Uc01Error):
    """Unexpected internal failure during initialisation.

    The session record is still persisted and marked ``failed``.
    """

    code = "session_initialization_failed"
    user_message = (
        "We could not fully open your coaching session. The attempt has been logged; "
        "please try again."
    )
