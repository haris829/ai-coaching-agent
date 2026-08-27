"""Foreign ("Nexus LMS") adapters: proof that upstream shape is adapter-local.

Everything Nexus-specific - field names, nesting, epoch millis, ``EQF-6`` bands,
``POSITIVE``/``NEGATIVE`` sentiments, ``FULL``/``PARTIAL``/``ABSENT``
completeness - lives in this module. The service, domain models, API and
persistence never learn any of it.

These adapters are read-only, and they never invent missing data: a payload that
cannot satisfy the platform contract raises ``ProviderInvalidResponse``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from uc07.domain.enums import NaricLevel, SourceStatus
from uc07.domain.errors import (
    PortName,
    ProviderInvalidResponse,
    ProviderTimeout,
    ProviderUnavailable,
)
from uc07.domain.models import (
    CourseSummary,
    Enrolment,
    FeedbackRecord,
    InteractionRecord,
    LearnerProfile,
    LessonSummary,
    Recommendation,
)
from uc07.ports.read_only import (
    CoursesProvider,
    FeedbackProvider,
    InteractionLogProvider,
    LearnerProfileProvider,
)

# --- Nexus value vocabularies (adapter-local) ------------------------------

_EQF_TO_NARIC = {
    "EQF-3": NaricLevel.LEVEL_3,
    "EQF-4": NaricLevel.LEVEL_4,
    "EQF-5": NaricLevel.LEVEL_5,
    "EQF-6": NaricLevel.LEVEL_6,
    "EQF-7": NaricLevel.LEVEL_7,
    "EQF-7+": NaricLevel.LEVEL_7_PLUS,
}

_COMPLETENESS_TO_STATUS = {
    "FULL": SourceStatus.AVAILABLE,
    "PARTIAL": SourceStatus.PARTIAL,
    "NONE": SourceStatus.EMPTY,
    "EMPTY": SourceStatus.EMPTY,
    "ABSENT": SourceStatus.UNAVAILABLE,
    "BROKEN": SourceStatus.INVALID,
}

_SENTIMENT_TO_RATING = {"POSITIVE": "up", "NEGATIVE": "down"}

_LIFECYCLE_TO_RATING_STATE = {"COMPLETE": "rated", "AWAITING": "pending"}


def _epoch_ms_to_datetime(value: Any) -> datetime:
    if not isinstance(value, int):
        raise ValueError("expected epoch milliseconds as an integer")
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def _dossier(payload: dict[str, Any], external_id: str) -> dict[str, Any] | None:
    for dossier in payload.get("learnerDossiers", []):
        if dossier.get("learner", {}).get("externalId") == external_id:
            return dossier
    return None


def _status_of(section: dict[str, Any], key: str = "completeness") -> SourceStatus:
    raw = section.get(key)
    if raw not in _COMPLETENESS_TO_STATUS:
        raise ValueError(f"unmappable completeness value for key '{key}'")
    return _COMPLETENESS_TO_STATUS[raw]


class _NexusAdapterBase:
    """Shared plumbing: injected payload plus an optional simulated failure."""

    _port: PortName

    def __init__(self, payload: dict[str, Any], *, failure: str | None = None) -> None:
        self._payload = payload
        self._failure = failure

    def _guard(self) -> None:
        if self._failure == "unavailable":
            raise ProviderUnavailable(self._port)
        if self._failure == "timeout":
            raise ProviderTimeout(self._port)
        if self._failure == "invalid":
            raise ProviderInvalidResponse(self._port)

    def _invalid(self, exc: Exception) -> ProviderInvalidResponse:
        return ProviderInvalidResponse(self._port)


class ForeignInteractionLogProvider(_NexusAdapterBase, InteractionLogProvider):
    _port = PortName.INTERACTION_LOG

    def _ledger(self, user_id: str) -> dict[str, Any]:
        dossier = _dossier(self._payload, user_id)
        if dossier is None:
            return {"completeness": "NONE", "entries": [], "tally": 0}
        ledger = dossier.get("coachingLedger")
        if not isinstance(ledger, dict):
            raise ProviderInvalidResponse(self._port)
        return ledger

    def _map(self, entry: dict[str, Any], user_id: str) -> InteractionRecord:
        try:
            eqf = entry["eqfBand"]
            if eqf not in _EQF_TO_NARIC:
                raise ValueError("unmappable eqfBand")
            lifecycle = entry.get("verdictLifecycle", "AWAITING")
            if lifecycle not in _LIFECYCLE_TO_RATING_STATE:
                raise ValueError("unmappable verdictLifecycle")
            return InteractionRecord(
                interaction_id=entry["entryRef"],
                session_id=entry["conversation"]["ref"],
                user_id=user_id,
                asked_at=_epoch_ms_to_datetime(entry["occurredAtEpochMs"]),
                topic_tag=entry["taxonomy"]["primary"],
                question_class=str(entry["promptKind"]).lower(),
                naric_level=_EQF_TO_NARIC[eqf],
                response_id=entry["reply"]["ref"],
                follow_up_of=entry.get("parentEntryRef"),
                explain_differently_count=entry.get("reexplainTally", 0),
                rating_state=_LIFECYCLE_TO_RATING_STATE[lifecycle],
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise self._invalid(exc) from exc

    def for_user(self, user_id: str) -> Sequence[InteractionRecord]:
        self._guard()
        ledger = self._ledger(user_id)
        return tuple(self._map(entry, user_id) for entry in ledger.get("entries", []))

    def count_for_user(self, user_id: str) -> int:
        self._guard()
        ledger = self._ledger(user_id)
        tally = ledger.get("tally")
        if tally is None:
            return len(ledger.get("entries", []))
        if not isinstance(tally, int) or tally < 0:
            raise ProviderInvalidResponse(self._port)
        return tally

    def status_for_user(self, user_id: str) -> SourceStatus:
        self._guard()
        ledger = self._ledger(user_id)
        try:
            return _status_of(ledger)
        except ValueError as exc:
            raise self._invalid(exc) from exc


class ForeignFeedbackProvider(_NexusAdapterBase, FeedbackProvider):
    _port = PortName.FEEDBACK

    def __init__(
        self, payload: dict[str, Any], *, external_id: str, failure: str | None = None
    ) -> None:
        super().__init__(payload, failure=failure)
        self._external_id = external_id

    def _ledger(self) -> dict[str, Any]:
        dossier = _dossier(self._payload, self._external_id)
        if dossier is None:
            return {"completeness": "NONE", "entries": []}
        ledger = dossier.get("verdictLedger")
        if not isinstance(ledger, dict):
            raise ProviderInvalidResponse(self._port)
        return ledger

    def _map(self, entry: dict[str, Any]) -> FeedbackRecord:
        try:
            sentiment = entry["sentiment"]
            if sentiment not in _SENTIMENT_TO_RATING:
                raise ValueError("unmappable sentiment")
            return FeedbackRecord(
                rating_id=entry["verdictRef"],
                interaction_id=entry["entryRef"],
                user_id=self._external_id,
                rated_at=_epoch_ms_to_datetime(entry["atEpochMs"]),
                rating=_SENTIMENT_TO_RATING[sentiment],
                comment=entry.get("remark"),
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise self._invalid(exc) from exc

    def for_interactions(self, interaction_ids: Sequence[str]) -> Sequence[FeedbackRecord]:
        self._guard()
        wanted = set(interaction_ids)
        return tuple(
            self._map(entry)
            for entry in self._ledger().get("entries", [])
            if entry.get("entryRef") in wanted
        )

    def status_for_interactions(self, interaction_ids: Sequence[str]) -> SourceStatus:
        self._guard()
        try:
            return _status_of(self._ledger())
        except ValueError as exc:
            raise self._invalid(exc) from exc


class ForeignLearnerProfileProvider(_NexusAdapterBase, LearnerProfileProvider):
    _port = PortName.LEARNER_PROFILE

    _FOCUS_COMPLETENESS = {
        "FULL": SourceStatus.AVAILABLE,
        "PARTIAL": SourceStatus.PARTIAL,
        "NONE": SourceStatus.EMPTY,
        "ABSENT": SourceStatus.UNAVAILABLE,
        "BROKEN": SourceStatus.INVALID,
    }

    def get_profile(self, user_id: str) -> LearnerProfile:
        self._guard()
        dossier = _dossier(self._payload, user_id)
        if dossier is None:
            return LearnerProfile(user_id=user_id, speciality_status=SourceStatus.EMPTY)
        document = dossier.get("profileDoc")
        if not isinstance(document, dict):
            raise ProviderInvalidResponse(self._port)
        try:
            completeness = document.get("focusCompleteness", "NONE")
            if completeness not in self._FOCUS_COMPLETENESS:
                raise ValueError("unmappable focusCompleteness")
            status = self._FOCUS_COMPLETENESS[completeness]
            areas = tuple(
                area["tag"] for area in document.get("focusAreas", []) or ()
            )
            if status is SourceStatus.EMPTY:
                # Nexus can send focus areas alongside NONE; the contract says an
                # empty speciality carries no areas, so this is a contract breach
                # rather than something to paper over.
                if areas:
                    raise ValueError("focusCompleteness NONE with focus areas present")
            band = document.get("eqfBand")
            naric = _EQF_TO_NARIC[band] if band in _EQF_TO_NARIC else None
            if band is not None and naric is None:
                raise ValueError("unmappable eqfBand")
            origin = document.get("eqfOrigin")
            naric_source = (
                {"LOOKUP": "retrieved", "FALLBACK": "default"}[origin]
                if origin is not None
                else None
            )
            return LearnerProfile(
                user_id=user_id,
                speciality_areas=areas,
                speciality_status=status,
                naric_level=naric,
                naric_level_source=naric_source if naric is not None else None,
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise self._invalid(exc) from exc


class ForeignCoursesProvider(_NexusAdapterBase, CoursesProvider):
    _port = PortName.COURSES

    def _curriculum(self) -> dict[str, Any]:
        curriculum = self._payload.get("curriculum")
        if not isinstance(curriculum, dict):
            raise ProviderInvalidResponse(self._port)
        return curriculum

    def resolve_recommendations(
        self, topic_tags: Sequence[str]
    ) -> Sequence[Recommendation]:
        self._guard()
        wanted = set(topic_tags)
        out: list[Recommendation] = []
        try:
            for suggestion in self._payload.get("suggestionFeed", []):
                if suggestion.get("subjectTag") not in wanted:
                    continue
                grain = suggestion.get("grain")
                if grain == "PROGRAMME":
                    out.append(
                        Recommendation(
                            topic_tag=suggestion["subjectTag"],
                            recommendation_type="course",
                            course_id=suggestion["programmeRef"],
                            lesson_id=None,
                            title=suggestion.get("label"),
                        )
                    )
                elif grain == "MODULE":
                    out.append(
                        Recommendation(
                            topic_tag=suggestion["subjectTag"],
                            recommendation_type="lesson",
                            course_id=suggestion["programmeRef"],
                            lesson_id=suggestion["moduleRef"],
                            title=suggestion.get("label"),
                        )
                    )
                else:
                    raise ValueError("unmappable suggestion grain")
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise self._invalid(exc) from exc
        return tuple(out)

    def enrolments_for(self, user_id: str) -> Sequence[Enrolment]:
        self._guard()
        try:
            return tuple(
                Enrolment(
                    user_id=user_id,
                    course_id=registration["programmeRef"],
                    enrolled_at=_epoch_ms_to_datetime(registration["joinedAtEpochMs"]),
                    completion_percentage=registration.get("progressPercent"),
                )
                for registration in self._payload.get("registrations", [])
                if registration.get("learnerExternalId") == user_id
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise self._invalid(exc) from exc

    def catalogue(self) -> Sequence[CourseSummary]:
        self._guard()
        try:
            return tuple(
                CourseSummary(
                    course_id=programme["programmeRef"],
                    title=programme.get("label"),
                    topic_tags=tuple(programme.get("subjectTags", ())),
                    lessons=tuple(
                        LessonSummary(
                            lesson_id=module["moduleRef"],
                            title=module.get("label"),
                            topic_tags=tuple(module.get("subjectTags", ())),
                        )
                        for module in programme.get("modules", ())
                    ),
                )
                for programme in self._curriculum().get("programmes", ())
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise self._invalid(exc) from exc

    def status(self) -> SourceStatus:
        self._guard()
        try:
            return _status_of(self._curriculum())
        except ValueError as exc:
            raise self._invalid(exc) from exc
