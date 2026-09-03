"""Centralised, validated configuration.

Every tunable number used by UC-10 lives here: flag thresholds, pagination
size, query timeout, rounding precision, export limits, auth. No service
module is permitted to embed a literal threshold - they read it from
:class:`AnalyticsSettings`.

Validation has three tiers (spec section 16):

``ERROR``
    Structurally invalid (out of range, wrong type). Construction fails.

``WARNING``
    Accepted, but unusual enough to be worth an operator's attention. Logged at
    construction time and reported by the validation endpoint.

``DANGEROUS``
    Accepted only with explicit confirmation
    (``allow_dangerous_configuration=True``). Without it, construction fails
    rather than silently accepting a setting that would, say, flag every
    question in the catalogue or disable authentication.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.modules.analytics.errors import ConfigurationError, InvalidThresholdError

__all__ = [
    "AnalyticsSettings",
    "ConfigIssue",
    "ConfigurationReport",
    "IssueSeverity",
    "validate_settings_payload",
]

logger = logging.getLogger("uc10.config")


class IssueSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    DANGEROUS = "DANGEROUS"


class ConfigIssue(BaseModel):
    """A single configuration finding."""

    model_config = ConfigDict(frozen=True)

    field: str = Field(description="Setting the issue refers to, or '__root__'.")
    code: str = Field(description="Stable machine-readable issue code.")
    severity: IssueSeverity
    message: str
    value: Any = Field(default=None, description="Offending value, when safe to echo.")


class ConfigurationReport(BaseModel):
    """Result of validating a candidate configuration."""

    model_config = ConfigDict(frozen=True)

    valid: bool = Field(description="True when the configuration can be applied as-is.")
    requires_confirmation: bool = Field(
        description="True when dangerous values are present and not yet confirmed."
    )
    issues: tuple[ConfigIssue, ...] = ()
    effective: dict[str, Any] | None = Field(
        default=None,
        description="Normalised settings (secrets redacted) when the candidate is valid.",
    )

    @property
    def errors(self) -> tuple[ConfigIssue, ...]:
        return tuple(i for i in self.issues if i.severity is IssueSeverity.ERROR)

    @property
    def warnings(self) -> tuple[ConfigIssue, ...]:
        return tuple(i for i in self.issues if i.severity is IssueSeverity.WARNING)

    @property
    def dangerous(self) -> tuple[ConfigIssue, ...]:
        return tuple(i for i in self.issues if i.severity is IssueSeverity.DANGEROUS)


# Bounds that separate "unusual" from "dangerous". Kept as module constants so
# the numbers are declared once and referenced by both validation and tests.
EXTREME_THRESHOLD_LOW = 15.0
EXTREME_THRESHOLD_HIGH = 90.0
DANGEROUS_THRESHOLD_LOW = 5.0
DANGEROUS_THRESHOLD_HIGH = 99.0
DANGEROUS_PAGE_SIZE = 2000
DANGEROUS_TIMEOUT_SECONDS = 300.0
LOW_CONFIDENCE_MIN_RESPONSES = 3


class AnalyticsSettings(BaseSettings):
    """Immutable settings object, injected into every service.

    Read from the environment with the ``UC10_`` prefix, e.g.
    ``UC10_FLAG_WRONG_ANSWER_RATE_THRESHOLD=55``.
    """

    model_config = SettingsConfigDict(
        env_prefix="UC10_",
        # **No `env_file`.** This class must not read the shared `.env` itself.
        #
        # `extra="forbid"` is load-bearing: it is what makes `validate_settings_payload` reject a
        # misspelled setting instead of silently ignoring it, and what stops an
        # `auth_enabled` switch being introduced by the back door. But pydantic-settings feeds
        # *every* key in a dotenv file into the model, so a `.env` carrying the other capabilities'
        # settings — DATABASE_URL, COACHING_LLM_MODEL, SEED_ADMIN_TOKEN, forty more — collides with
        # `forbid` and the application cannot start at all. Which is exactly how anyone who copies
        # `.env.example` to `.env` will try to start it.
        #
        # Nothing is lost by dropping the file source: `analytics_settings_from` threads each value
        # through from the application's own `Settings`, which does read `.env`. Real `UC10_`
        # environment variables still work, which is how a deployment sets them.
        extra="forbid",
        frozen=True,
    )

    # ----------------------------------------------------------- flag thresholds
    flag_wrong_answer_rate_threshold: float = Field(
        default=40.0,
        gt=0.0,
        le=100.0,
        description=(
            "Percentage of graded responses answered incorrectly, above which a "
            "question is flagged for content review. Strictly greater-than."
        ),
    )
    flag_min_responses: int = Field(
        default=5,
        ge=1,
        le=100_000,
        description=(
            "Minimum graded responses a question needs before it can be flagged. "
            "Prevents flagging a question on a single wrong answer."
        ),
    )
    reflag_enabled: bool = Field(
        default=True,
        description=(
            "Whether a question whose flag was resolved may be flagged again "
            "after fresh evidence accumulates. Retired questions are never "
            "re-flagged."
        ),
    )
    reflag_min_new_responses: int = Field(
        default=5,
        ge=1,
        le=100_000,
        description=(
            "Graded responses recorded after the resolution timestamp before a "
            "resolved question becomes eligible for re-flagging."
        ),
    )

    # ------------------------------------------------------------- query budget
    repository_page_size: int = Field(
        default=500,
        ge=1,
        le=50_000,
        description="Page size requested from the repository during aggregation.",
    )
    query_timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        le=3600.0,
        description="Wall-clock budget for a single analytics query.",
    )
    max_scanned_records: int = Field(
        default=5_000_000,
        ge=1,
        description=(
            "Safety stop: abort aggregation once this many records have been "
            "scanned, so a mis-specified filter cannot exhaust memory."
        ),
    )

    # ----------------------------------------------------------------- reporting
    decimal_places: int = Field(
        default=2,
        ge=0,
        le=6,
        description="Rounding applied to reported percentages and averages.",
    )
    export_max_rows: int = Field(
        default=100_000,
        ge=1,
        description="Maximum data rows written to a single CSV export.",
    )
    export_sanitise_formulas: bool = Field(
        default=True,
        description=(
            "Prefix values starting with = + - @ with an apostrophe so "
            "spreadsheet software cannot execute exported text as a formula."
        ),
    )

    # ------------------------------------------------------------------ security
    #
    # UC-10 shipped with ``auth_enabled`` and its own ``admin_api_keys`` map, because standalone it
    # had to authenticate its own callers. Both are gone: the merged application has one identity
    # seam, every analytics endpoint sits behind the administrator guard UC-01, UC-02 and UC-08
    # already use, and there is no switch anywhere that could turn it off. ``log_level`` is the
    # application's too.
    #
    # This is a settings object that a *client* can validate candidate values against, so removing
    # a security switch from it is not only de-duplication: a runtime-tunable authentication flag
    # is a runtime-tunable way to disable authentication.

    # --------------------------------------------------------------- guard rail
    allow_dangerous_configuration: bool = Field(
        default=False,
        description="Explicit confirmation that DANGEROUS values are intended.",
    )

    # ------------------------------------------------------------------ helpers

    def model_post_init(self, __context: Any) -> None:
        """Enforce the DANGEROUS tier and log WARNING-tier findings."""
        issues = tuple(_semantic_issues(self))
        dangerous = [i for i in issues if i.severity is IssueSeverity.DANGEROUS]
        if dangerous and not self.allow_dangerous_configuration:
            first = dangerous[0]
            error_cls = (
                InvalidThresholdError if "threshold" in first.field else ConfigurationError
            )
            raise error_cls(
                (
                    f"Configuration value {first.field}={first.value!r} is dangerous: "
                    f"{first.message} Set allow_dangerous_configuration=true to confirm."
                ),
                details={
                    "requires_confirmation": True,
                    "issues": [i.model_dump(mode="json") for i in dangerous],
                },
            )
        for issue in issues:
            if issue.severity is IssueSeverity.WARNING:
                logger.warning(
                    "configuration warning: %s (%s=%r)",
                    issue.message,
                    issue.field,
                    issue.value,
                )
            elif issue.severity is IssueSeverity.DANGEROUS:
                logger.warning(
                    "dangerous configuration accepted by explicit confirmation: %s (%s=%r)",
                    issue.message,
                    issue.field,
                    issue.value,
                )

    def issues(self) -> tuple[ConfigIssue, ...]:
        """Semantic findings for the current values."""
        return tuple(_semantic_issues(self))

    def public_dump(self) -> dict[str, Any]:
        """Settings safe to expose over the API.

        Everything here is safe now, because nothing in this object is a credential — UC-10's own
        API-key map went when the merged identity seam took over authentication. The method stays
        as the single place a future secret-shaped setting would be filtered, rather than the
        ``/config`` endpoint dumping the model directly and being the wrong place to notice.
        """
        return self.model_dump(mode="json")


def _semantic_issues(settings: AnalyticsSettings) -> Iterable[ConfigIssue]:
    """Checks that go beyond per-field range validation."""
    threshold = settings.flag_wrong_answer_rate_threshold

    if threshold <= DANGEROUS_THRESHOLD_LOW:
        yield ConfigIssue(
            field="flag_wrong_answer_rate_threshold",
            code="THRESHOLD_DANGEROUSLY_LOW",
            severity=IssueSeverity.DANGEROUS,
            message=(
                f"A threshold of {threshold}% flags virtually every question, "
                "which makes the content review queue meaningless."
            ),
            value=threshold,
        )
    elif threshold >= DANGEROUS_THRESHOLD_HIGH:
        yield ConfigIssue(
            field="flag_wrong_answer_rate_threshold",
            code="THRESHOLD_DANGEROUSLY_HIGH",
            severity=IssueSeverity.DANGEROUS,
            message=(
                f"A threshold of {threshold}% flags almost nothing, so genuinely "
                "broken questions will never surface for review."
            ),
            value=threshold,
        )
    elif threshold < EXTREME_THRESHOLD_LOW:
        yield ConfigIssue(
            field="flag_wrong_answer_rate_threshold",
            code="THRESHOLD_EXTREME_LOW",
            severity=IssueSeverity.WARNING,
            message=(
                f"A threshold of {threshold}% is unusually low and will flag "
                "questions that are merely difficult."
            ),
            value=threshold,
        )
    elif threshold > EXTREME_THRESHOLD_HIGH:
        yield ConfigIssue(
            field="flag_wrong_answer_rate_threshold",
            code="THRESHOLD_EXTREME_HIGH",
            severity=IssueSeverity.WARNING,
            message=(
                f"A threshold of {threshold}% is unusually high; only near-total "
                "failure will be flagged."
            ),
            value=threshold,
        )

    if settings.flag_min_responses < LOW_CONFIDENCE_MIN_RESPONSES:
        yield ConfigIssue(
            field="flag_min_responses",
            code="MIN_RESPONSES_LOW_CONFIDENCE",
            severity=IssueSeverity.WARNING,
            message=(
                f"Flagging after {settings.flag_min_responses} response(s) draws "
                "conclusions from a sample too small to be reliable."
            ),
            value=settings.flag_min_responses,
        )

    if settings.repository_page_size > DANGEROUS_PAGE_SIZE:
        yield ConfigIssue(
            field="repository_page_size",
            code="PAGE_SIZE_DANGEROUS",
            severity=IssueSeverity.DANGEROUS,
            message=(
                f"A page size of {settings.repository_page_size} holds a very "
                "large batch in memory per query and defeats bounded-memory "
                "aggregation."
            ),
            value=settings.repository_page_size,
        )

    if settings.query_timeout_seconds > DANGEROUS_TIMEOUT_SECONDS:
        yield ConfigIssue(
            field="query_timeout_seconds",
            code="TIMEOUT_DANGEROUS",
            severity=IssueSeverity.DANGEROUS,
            message=(
                f"A {settings.query_timeout_seconds}s budget lets a single query "
                "occupy a worker long enough to starve other requests."
            ),
            value=settings.query_timeout_seconds,
        )

    if settings.reflag_enabled and settings.reflag_min_new_responses < LOW_CONFIDENCE_MIN_RESPONSES:
        yield ConfigIssue(
            field="reflag_min_new_responses",
            code="REFLAG_SENSITIVE",
            severity=IssueSeverity.WARNING,
            message=(
                "Resolved questions will be re-flagged after very little new "
                "evidence, which can re-open reviews immediately."
            ),
            value=settings.reflag_min_new_responses,
        )


def validate_settings_payload(
    payload: Mapping[str, Any],
    *,
    confirm_dangerous: bool = False,
    base: AnalyticsSettings | None = None,
) -> ConfigurationReport:
    """Validate a candidate configuration without applying it.

    ``base`` supplies the values the candidate does not mention - normally the
    settings the service is currently running with, so the report describes the
    configuration that would actually be in force rather than one built from bare
    defaults.

    Never raises for invalid input: the failure detail is the return value.
    """
    candidate: dict[str, Any] = {}
    if base is not None:
        candidate.update(base.model_dump())
    candidate.update(payload)
    candidate["allow_dangerous_configuration"] = True  # collect, do not raise

    try:
        settings = AnalyticsSettings(**candidate)
    except ValidationError as exc:
        issues = tuple(
            ConfigIssue(
                field=".".join(str(p) for p in err["loc"]) or "__root__",
                code=str(err["type"]).upper(),
                severity=IssueSeverity.ERROR,
                message=err["msg"],
                value=_safe_value(err.get("input"), err["loc"]),
            )
            for err in exc.errors()
        )
        return ConfigurationReport(valid=False, requires_confirmation=False, issues=issues)
    except (ConfigurationError, InvalidThresholdError) as exc:  # pragma: no cover
        return ConfigurationReport(
            valid=False,
            requires_confirmation=True,
            issues=(
                ConfigIssue(
                    field="__root__",
                    code=exc.code,
                    severity=IssueSeverity.DANGEROUS,
                    message=exc.message,
                ),
            ),
        )

    issues = tuple(_semantic_issues(settings))
    dangerous = [i for i in issues if i.severity is IssueSeverity.DANGEROUS]
    requires_confirmation = bool(dangerous) and not confirm_dangerous
    valid = not requires_confirmation

    return ConfigurationReport(
        valid=valid,
        requires_confirmation=requires_confirmation,
        issues=issues,
        effective=settings.public_dump() if valid else None,
    )


#: Stands in for the value of a field the schema does not recognise.
REDACTED_VALUE = "<redacted>"


def _safe_value(value: Any, loc: tuple[Any, ...]) -> Any:
    """Never echo a secret back in a validation report.

    UC-10 held an API-key map in these settings and redacted that one field. The map is gone with
    the merge, and the rule generalised rather than disappearing: **the value of an unrecognised
    field is never echoed.**

    The reasoning is that echoing a value is only useful diagnostically for a field the schema
    knows — "you sent 500 for a percentage" tells an administrator something. For a field the
    schema has never heard of, the *name* is the whole diagnosis, and the value could be anything
    the caller happened to paste, including a credential. A validation report is rendered straight
    into a browser, so reflecting arbitrary input back is how the endpoint becomes a reflection
    sink.
    """
    field = str(loc[0]) if loc else ""
    if field not in AnalyticsSettings.model_fields:
        return REDACTED_VALUE
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(type(value).__name__)
