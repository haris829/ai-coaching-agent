"""Filter contract, applied uniformly to every analytics output (spec 9).

Date-range semantics are fixed here, once, so that the API, the CSV export and
any repository implementation cannot drift apart:

* The anchor timestamp is ``AttemptRecord.started_at``.
* The interval is **half-open**: ``start_date <= started_at < end_date``. A
  half-open interval makes consecutive periods tile exactly - a report for
  January and one for February can never double-count the same attempt.
* Bounds are optional and independent; either may be supplied alone.
* A response is in scope if and only if its parent attempt is in scope. Question
  analytics never filters responses on their own timestamps, because a response
  belongs to the attempt that produced it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.time import ensure_utc
from app.modules.analytics.domain.enums import AnalyticsScope, AssessmentType
from app.modules.analytics.domain.records import AttemptRecord
from app.modules.analytics.errors import InvalidFilterError

__all__ = ["AnalyticsFilters"]


class AnalyticsFilters(BaseModel):
    """Filters shared by dashboard metrics, question analytics and exports."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    course_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Restrict to one course. Omit for platform-level analytics.",
    )
    cohort_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Restrict to one learner group.",
    )
    assessment_type: AssessmentType | None = Field(
        default=None,
        description="Restrict to Standard Quiz or Formal Assessment.",
    )
    start_date: datetime | None = Field(
        default=None,
        description="Inclusive lower bound on attempt start time.",
    )
    end_date: datetime | None = Field(
        default=None,
        description="Exclusive upper bound on attempt start time.",
    )

    @field_validator("start_date", "end_date")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else ensure_utc(value)

    @field_validator("course_id", "cohort_id")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @model_validator(mode="after")
    def _validate_range(self) -> AnalyticsFilters:
        if self.start_date is not None and self.end_date is not None:
            if self.start_date > self.end_date:
                raise InvalidFilterError(
                    "start_date must be earlier than or equal to end_date.",
                    details={
                        "start_date": self.start_date.isoformat(),
                        "end_date": self.end_date.isoformat(),
                    },
                )
            if self.start_date == self.end_date:
                raise InvalidFilterError(
                    "start_date and end_date are identical, so the half-open range "
                    "[start_date, end_date) selects no attempts. Widen the range.",
                    details={
                        "start_date": self.start_date.isoformat(),
                        "end_date": self.end_date.isoformat(),
                    },
                )
        return self

    # ------------------------------------------------------------------- scope

    @property
    def scope(self) -> AnalyticsScope:
        return AnalyticsScope.COURSE if self.course_id else AnalyticsScope.PLATFORM

    def applied_fields(self) -> tuple[str, ...]:
        """Names of the filters actually supplied, for logging and diagnostics."""
        return tuple(
            name for name, value in self.model_dump().items() if value is not None
        )

    def with_course(self, course_id: str) -> AnalyticsFilters:
        return self.model_copy(update={"course_id": course_id})

    # ------------------------------------------------------- canonical matching

    def matches_attempt(self, attempt: AttemptRecord) -> bool:
        """Reference implementation of the filter semantics.

        A repository backed by a real datastore should translate these filters
        into its own query language, but must reproduce exactly this behaviour.
        """
        if self.course_id is not None and attempt.course_id != self.course_id:
            return False
        if self.cohort_id is not None and attempt.cohort_id != self.cohort_id:
            return False
        if self.assessment_type is not None and attempt.assessment_type is not self.assessment_type:
            return False
        if self.start_date is not None and attempt.started_at < self.start_date:
            return False
        # SIM103 would have this final clause collapsed into `return not (...)`. Declined
        # deliberately: this method is a list of five independent rejections, and the last one is
        # not special. Rewriting only the last would make it read differently from the four
        # identical checks above it, which is exactly how a filter clause gets misread later.
        if self.end_date is not None and attempt.started_at >= self.end_date:  # noqa: SIM103
            return False
        return True

    # ------------------------------------------------------------- diagnostics

    def cache_key(self) -> str:
        """Stable identity of this filter set, for caching or de-duplication."""
        parts = []
        for name, value in sorted(self.model_dump(mode="json").items()):
            parts.append(f"{name}={value if value is not None else ''}")
        return "|".join(parts)

    def describe(self) -> dict[str, Any]:
        """Log-safe description: identifiers only, no learner data."""
        return {
            "scope": self.scope.value,
            "filters_applied": list(self.applied_fields()),
            "course_id": self.course_id,
            "cohort_id": self.cohort_id,
            "assessment_type": self.assessment_type.value if self.assessment_type else None,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
        }
