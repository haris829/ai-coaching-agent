"""Context assembly: the one place UC-02's business logic lives.

Responsibilities:

* Call the four upstream ports **concurrently**, each under its own timeout, with
  a hard ceiling on total assembly time.
* Isolate failure per source: no single failure, and no combination of failures,
  prevents a valid ``SessionContext`` from being returned.
* Apply the documented per-field defaults and record what was defaulted.
* Resolve the explanation profile from the NARIC level via the mapping table.
* Bind the context to the session id it was handed and the server-resolved user id.
* Build once at session start; never re-query providers for a session that
  already has stored context.

This class depends only on ports and pure functions. Swapping a mock adapter for
a real one requires no change here -- that property is asserted by
``tests/unit/test_adapter_independence.py``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from logging import Logger

from pydantic import BaseModel, ConfigDict

from uc02.application.normalisers import (
    Normalised,
    normalise_courses,
    normalise_history,
    normalise_legal,
    normalise_naric,
)
from uc02.domain.errors import (
    ContextAccessDenied,
    ContextNotFound,
    ProviderBudgetExceeded,
    ProviderError,
    ProviderTimeout,
    ProviderUnexpectedError,
)
from uc02.domain.explanation_mapping import profile_for_level
from uc02.domain.models.context import (
    PERSONALIZATION_UNAVAILABLE_NOTICE,
    PersonalizationStatus,
    SessionContext,
    SourceOutcome,
)
from uc02.domain.models.enums import ContextStatus, SourceName, SourceStatus
from uc02.domain.models.session import SessionIdentity
from uc02.domain.ports.providers import (
    CoursesProvider,
    LegalFootprintsProvider,
    NaricProvider,
    QuestionHistoryProvider,
)
from uc02.domain.ports.repository import SessionContextRepository
from uc02.infrastructure.config.settings import Settings
from uc02.infrastructure.logging.setup import get_logger, user_reference

#: Shown when every source responded but the learner has nothing recorded yet.
#: Deliberately different wording from the outage notice: ``empty`` is not
#: ``unavailable`` (scope section 8).
PERSONALIZATION_EMPTY_NOTICE = (
    "No personalisation data is recorded for your account yet. You can continue your session."
)

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class SourceCallResult:
    """Raw outcome of one provider call, before normalisation."""

    name: SourceName
    record: object | None = None
    error: ProviderError | None = None
    duration_ms: int = 0


class InitializeOutcome(BaseModel):
    """What ``initialize`` returns: the context plus how it was obtained."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    context: SessionContext
    status: ContextStatus


class ContextAssemblyService:
    def __init__(
        self,
        *,
        naric: NaricProvider,
        courses: CoursesProvider,
        legal: LegalFootprintsProvider,
        history: QuestionHistoryProvider,
        repository: SessionContextRepository,
        settings: Settings,
        clock: Clock = utc_now,
        logger: Logger | None = None,
    ) -> None:
        self._naric = naric
        self._courses = courses
        self._legal = legal
        self._history = history
        self._repository = repository
        self._settings = settings
        self._clock = clock
        self._log = logger or get_logger()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def initialize(
        self, identity: SessionIdentity, *, force_refresh: bool = False
    ) -> InitializeOutcome:
        """Return the context for ``identity.session_id``, building it if needed.

        Idempotency rule (scope section 9):

        * no stored context -> build, store, ``created``
        * stored context    -> return unchanged, ``existing``, zero provider calls
        * stored context and ``force_refresh`` -> rebuild, ``refreshed``
          (only reachable from the config-gated internal path)
        """
        ref = self._user_ref(identity.user_id)
        self._log.info(
            "context.initialize.start",
            extra={
                "session_id": identity.session_id,
                "user_reference": ref,
                "session_id_origin": identity.session_id_origin,
                "force_refresh": force_refresh,
            },
        )

        stored = await self._repository.get(identity.session_id)
        if stored is not None:
            if stored.user_id != identity.user_id:
                # A session id alone is never sufficient. Do not leak existence.
                self._log.warning(
                    "context.access.denied",
                    extra={
                        "session_id": identity.session_id,
                        "user_reference": ref,
                        "reason": "session_owned_by_another_user",
                    },
                )
                raise ContextAccessDenied(identity.session_id)
            if not force_refresh:
                self._log.info(
                    "context.initialize.reused",
                    extra={
                        "session_id": identity.session_id,
                        "user_reference": ref,
                        "context_status": ContextStatus.EXISTING.value,
                        "provider_calls": 0,
                    },
                )
                return InitializeOutcome(context=stored, status=ContextStatus.EXISTING)

        context = await self._build(identity)
        await self._repository.save(context)
        status = ContextStatus.REFRESHED if stored is not None else ContextStatus.CREATED
        return InitializeOutcome(context=context, status=status)

    async def get_for_user(self, session_id: str, user_id: str) -> SessionContext:
        """Fetch stored context, enforcing ownership.

        Raises ``ContextNotFound`` when absent/expired and ``ContextAccessDenied``
        when the context belongs to someone else. The API maps both to 404 so a
        session id cannot be used to probe for another learner's context.
        """
        stored = await self._repository.get(session_id)
        if stored is None:
            raise ContextNotFound(session_id)
        if stored.user_id != user_id:
            self._log.warning(
                "context.access.denied",
                extra={
                    "session_id": session_id,
                    "user_reference": self._user_ref(user_id),
                    "reason": "session_owned_by_another_user",
                },
            )
            raise ContextAccessDenied(session_id)
        return stored

    # ------------------------------------------------------------------
    # Assembly
    # ------------------------------------------------------------------
    async def _build(self, identity: SessionIdentity) -> SessionContext:
        started = time.perf_counter()
        ref = self._user_ref(identity.user_id)

        results = await self._gather_sources(identity.user_id)

        naric = normalise_naric(
            self._record(results[SourceName.NARIC]), results[SourceName.NARIC].error
        )
        courses = normalise_courses(
            self._record(results[SourceName.COURSES]), results[SourceName.COURSES].error
        )
        legal = normalise_legal(
            self._record(results[SourceName.LEGAL_PROFILE]),
            results[SourceName.LEGAL_PROFILE].error,
        )
        history = normalise_history(
            self._record(results[SourceName.QUESTION_HISTORY]),  # type: ignore[arg-type]
            results[SourceName.QUESTION_HISTORY].error,
            limit=self._settings.question_history_limit,
        )

        normalised: Mapping[SourceName, Normalised] = {
            SourceName.NARIC: naric,
            SourceName.COURSES: courses,
            SourceName.LEGAL_PROFILE: legal,
            SourceName.QUESTION_HISTORY: history,
        }

        source_status: dict[SourceName, SourceOutcome] = {}
        for name, outcome in normalised.items():
            source_status[name] = SourceOutcome(
                status=outcome.status,
                error_category=outcome.error_category,
                duration_ms=results[name].duration_ms,
                fallback_applied=outcome.fallback_applied,
            )
            self._log.info(
                "context.provider.result",
                extra={
                    "session_id": identity.session_id,
                    "user_reference": ref,
                    "source": name.value,
                    "status": outcome.status.value,
                    "error_category": outcome.error_category.value,
                    "duration_ms": results[name].duration_ms,
                },
            )
            for fallback in outcome.fallbacks:
                self._log.warning(
                    "context.fallback.applied",
                    extra={
                        "session_id": identity.session_id,
                        "user_reference": ref,
                        "source": name.value,
                        "fallback": fallback,
                    },
                )

        # The explanation profile is derived only from the resolved level. A
        # client cannot influence it: nothing here reads the request body.
        explanation_profile = profile_for_level(naric.value.level)
        personalization = self._personalization(source_status)

        context = SessionContext(
            session_id=identity.session_id,
            user_id=identity.user_id,
            naric=naric.value,
            courses=courses.value,
            legal_profile=legal.value,
            question_history=history.value,
            explanation_profile=explanation_profile,
            personalization=personalization,
            source_status=source_status,
            built_at=self._clock(),
        )

        self._log.info(
            "context.assembly.complete",
            extra={
                "session_id": context.session_id,
                "user_reference": ref,
                "context_version": context.context_version,
                "template_id": explanation_profile.template_id.value,
                "naric_level": context.naric.level,
                "naric_level_source": context.naric.level_source.value,
                "enrolment_count": len(context.courses.enrolments),
                "history_count": context.question_history.count,
                "history_truncated": context.question_history.truncated,
                "explanation_domain": context.legal_profile.explanation_domain.value,
                "personalization_available": personalization.available,
                "statuses": {k.value: v.status.value for k, v in source_status.items()},
                "duration_ms": int((time.perf_counter() - started) * 1000),
            },
        )
        return context

    async def _gather_sources(self, user_id: str) -> dict[SourceName, SourceCallResult]:
        """Call all four providers concurrently, per-provider timeout plus a total budget.

        Each wrapped call records its own result as soon as it finishes, so if the
        overall budget elapses first, whatever already completed is still used and
        only the stragglers are marked ``budget_exceeded``.
        """
        results: dict[SourceName, SourceCallResult] = {}
        timeout = self._settings.provider_timeout_seconds
        limit = self._settings.question_history_limit

        async def call(name: SourceName, factory: Callable[[], Awaitable[object]]) -> None:
            started = time.perf_counter()
            try:
                record = await asyncio.wait_for(factory(), timeout=timeout)
                results[name] = SourceCallResult(
                    name=name, record=record, duration_ms=_ms_since(started)
                )
            except TimeoutError:
                results[name] = SourceCallResult(
                    name=name,
                    error=_timeout_error(name, self._settings.provider_timeout_ms),
                    duration_ms=_ms_since(started),
                )
            except ProviderError as error:
                results[name] = SourceCallResult(
                    name=name, error=error, duration_ms=_ms_since(started)
                )
            except Exception as error:  # adapter broke its own contract
                results[name] = SourceCallResult(
                    name=name,
                    error=ProviderUnexpectedError(name, type(error).__name__),
                    duration_ms=_ms_since(started),
                )

        gathered = asyncio.gather(
            call(SourceName.NARIC, lambda: self._naric.get_qualification_level(user_id)),
            call(SourceName.COURSES, lambda: self._courses.get_learning_context(user_id)),
            call(SourceName.LEGAL_PROFILE, lambda: self._legal.get_profile(user_id)),
            call(
                SourceName.QUESTION_HISTORY,
                lambda: self._history.get_recent_questions(user_id, limit),
            ),
            return_exceptions=True,
        )
        try:
            await asyncio.wait_for(gathered, timeout=self._settings.assembly_budget_seconds)
        except TimeoutError:
            # wait_for has already cancelled the gather and awaited its teardown.
            self._log.warning(
                "context.assembly.budget_exceeded",
                extra={
                    "budget_ms": self._settings.context_assembly_budget_ms,
                    "resolved_sources": sorted(name.value for name in results),
                },
            )

        for name in SourceName:
            results.setdefault(
                name,
                SourceCallResult(
                    name=name,
                    error=ProviderBudgetExceeded(
                        name,
                        f"assembly budget of {self._settings.context_assembly_budget_ms}ms elapsed",
                    ),
                    duration_ms=self._settings.context_assembly_budget_ms,
                ),
            )
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _record(result: SourceCallResult):
        return result.record if result.error is None else None

    @staticmethod
    def _personalization(
        source_status: Mapping[SourceName, SourceOutcome],
    ) -> PersonalizationStatus:
        contributing = tuple(
            name
            for name, outcome in source_status.items()
            if outcome.status in (SourceStatus.AVAILABLE, SourceStatus.PARTIAL)
        )
        responded = any(
            outcome.status
            in (SourceStatus.AVAILABLE, SourceStatus.PARTIAL, SourceStatus.EMPTY)
            for outcome in source_status.values()
        )
        if contributing:
            return PersonalizationStatus(
                available=True, notice=None, contributing_sources=contributing
            )
        notice = PERSONALIZATION_EMPTY_NOTICE if responded else PERSONALIZATION_UNAVAILABLE_NOTICE
        return PersonalizationStatus(available=False, notice=notice, contributing_sources=())

    def _user_ref(self, user_id: str) -> str:
        return user_reference(user_id, self._settings.user_id_log_salt)


def _ms_since(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _timeout_error(name: SourceName, timeout_ms: int) -> ProviderTimeout:
    return ProviderTimeout(name, f"exceeded per-provider timeout of {timeout_ms}ms")
