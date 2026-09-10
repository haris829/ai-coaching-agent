"""The two ways generation fails, kept apart because the response to them differs.

An operator woken by a failure needs to know whether to wait or to read a prompt. Collapsing both
into one error sends them to debug a prompt that was working perfectly, which wastes an afternoon
and does not fix the outage.
"""

from __future__ import annotations


class QGenError(Exception):
    """Base for everything this package raises deliberately."""

    status_code = 500
    code = "QGEN_ERROR"
    retryable = False

    def __init__(self, message: str, *, reason: str = "") -> None:
        super().__init__(message)
        self.message = message
        #: A short machine-ish token for logs. Never the provider's response body: a provider
        #: error can echo the prompt back, and the prompt is not something to put in a response.
        self.reason = reason


class GenerationUnavailable(QGenError):
    """503 - the model could not be reached, or none is configured.

    Retryable, and nothing has been written, so repeating the request is safe.

    **Throttling belongs here, not in** :class:`GenerationFailed`. A 429 that survives its retries
    is a capacity problem that clears on its own; reporting it as "the model would not follow the
    output contract" points the investigation at the one thing that is not broken.
    """

    status_code = 503
    code = "GENERATION_UNAVAILABLE"
    retryable = True

    def __init__(self, *, reason: str) -> None:
        super().__init__(
            "Question generation is temporarily unavailable. Nothing was saved - "
            "please try again shortly.",
            reason=reason,
        )


class GenerationFailed(QGenError):
    """502 - the model answered, but with nothing usable.

    Distinct from unavailable because an outage clears itself and a model that will not follow the
    output contract needs a person to look at the prompt.
    """

    status_code = 502
    code = "GENERATION_FAILED"
    retryable = False

    def __init__(self, *, reason: str) -> None:
        super().__init__(
            "The model did not return usable questions. Nothing was saved.",
            reason=reason,
        )


class InvalidRequest(QGenError):
    """422 - the caller asked for something that cannot be done.

    An empty question, a question longer than the search can use, a count outside the allowed
    range. Separated from the 500 default because these are the caller's to fix and nothing is
    wrong with the service: reporting them as server errors sends somebody to read logs that say
    the system worked correctly.
    """

    status_code = 422
    code = "INVALID_REQUEST"


class ConfigurationError(QGenError):
    """Something the operator has to fix before anything can run - a missing database URL."""

    status_code = 500
    code = "QGEN_MISCONFIGURED"
