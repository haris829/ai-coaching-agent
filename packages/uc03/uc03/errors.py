"""Service-level errors. The API layer maps these to HTTP status codes."""

from __future__ import annotations


class UC03Error(Exception):
    """Base class for UC-03 service errors."""


class AuthenticationError(UC03Error):
    """The credential did not identify a caller. -> 401"""


class AuthorizationError(UC03Error):
    """The caller does not own the session they addressed. -> 403"""


class InputValidationError(UC03Error):
    """The question failed server-side validation. -> 422"""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class InteractionNotFoundError(UC03Error):
    """No such interaction, or it belongs to another caller. -> 404"""
