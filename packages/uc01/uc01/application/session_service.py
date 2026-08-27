"""UC-01 use-case service: Coaching Session Initiation.

This is the whole of UC-01's business logic. It depends only on:

* ``uc01.domain``   — enums, models, policy, messages
* ``uc01.contracts`` — service Protocols, repository Protocol, contract exceptions

It does **not** import any adapter, any HTTP framework, or any persistence
implementation. That is what makes a mock/real adapter swap a configuration change.

Two invariants are enforced here and covered by tests:

1. **A session record exists for every open attempt.** The record is created before any
   external dependency is contacted, and is updated to ``active`` / ``degraded`` /
   ``failed`` afterwards. No dependency failure can lose it.
2. **Nothing the client sends is trusted.** Mode, course, lesson and case are re-validated
   server-side on every open; the NARIC level and the system prompt are never client
   inputs at all.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace

from ..contracts.clock import Clock, IdGenerator, SystemClock, UuidIdGenerator
from ..contracts.exceptions import (
    ContractError,
    DependencyUnavailableError,
    InvalidUpstreamResponseError,
    ResourceNotAccessibleError,
)
from ..contracts.repository import SessionRepository
from ..contracts.services import (
    CaseFileService,
    CoursesService,
    GreetingGenerator,
    NaricService,
    ProfileService,
)
from ..domain import messages
from ..domain.enums import (
    DependencyFailurePolicy,
    DependencyName,
    DependencyState,
    NaricLevelSource,
    SessionEventType,
    SessionMode,
    SessionStatus,
)
from ..domain.errors import (
    DependencyDegradedError,
    ModeUnavailableError,
    SelectionNotAccessibleError,
    SelectionNotAllowedError,
    SelectionRequiredError,
    SessionInitializationError,
    SessionNotFoundError,
    Uc01Error,
)
from ..domain.models import (
    DEFAULT_EXPLANATION_LEVEL,
    CaseFile,
    Course,
    DependencyStatus,
    Greeting,
    Lesson,
    NaricAssessment,
    NaricResolution,
    SessionContext,
    SessionEvent,
    SessionRecord,
    UserContext,
    UserProfile,
)
from ..domain.policy import (
    available_modes,
    evaluate_mode_availability,
    find_mode_availability,
    resolve_naric_level,
)
from ..domain.prompts import GREETING_SYSTEM_PROMPT_ID, SystemPromptRegistry
from .dto import (
    BootstrapResult,
    CatalogueResult,
    Notice,
    OpenSessionCommand,
    OpenSessionResult,
)

logger = logging.getLogger(__name__)


class SessionInitiationService:
    """Opens coaching sessions and reports what the coaching interface may offer."""

    def __init__(
        self,
        *,
        naric_service: NaricService,
        courses_service: CoursesService,
        case_service: CaseFileService,
        profile_service: ProfileService,
        greeting_generator: GreetingGenerator,
        repository: SessionRepository,
        clock: Clock | None = None,
        id_generator: IdGenerator | None = None,
        prompts: SystemPromptRegistry | None = None,
    ) -> None:
        self._naric = naric_service
        self._courses = courses_service
        self._cases = case_service
        self._profile = profile_service
        self._greeting = greeting_generator
        self._repository = repository
        self._clock = clock or SystemClock()
        self._ids = id_generator or UuidIdGenerator()
        self._prompts = prompts or SystemPromptRegistry()

    # ------------------------------------------------------------------ #
    # Session-opening data
    # ------------------------------------------------------------------ #

    def load_bootstrap(
        self, user: UserContext, *, continue_without_calibration: bool = False
    ) -> BootstrapResult:
        """Everything the coaching interface needs before a session is opened.

        Never raises for a dependency failure: an outage becomes availability metadata so
        the UI can disable one mode and keep the rest of the interface usable.
        """
        dependencies: dict[DependencyName, DependencyStatus] = {}

        profile, profile_status = self._load_profile(user)
        dependencies[DependencyName.PROFILE] = profile_status

        naric, naric_status = self._load_naric(
            user, continue_without_calibration=continue_without_calibration
        )
        dependencies[DependencyName.NARIC] = naric_status

        courses, courses_status = self._load_courses(user)
        dependencies[DependencyName.COURSES] = courses_status

        case_files, cases_status = self._load_case_files(user)
        dependencies[DependencyName.CASES] = cases_status

        modes = evaluate_mode_availability(dependencies)

        preview_context = SessionContext(
            user=user,
            session_mode=SessionMode.FREE_FORM,
            profile=profile,
            naric=naric,
            dependencies=dependencies,
        )
        greeting_preview = self._compose_greeting(preview_context)

        logger.info(
            "session.bootstrap",
            extra={
                "uc01": {
                    "user_id": user.user_id,
                    "modes_available": [
                        mode.value for mode in available_modes(modes)
                    ],
                    "dependencies": {
                        name.value: status.state.value
                        for name, status in dependencies.items()
                    },
                    "naric_level_source": naric.source.value,
                }
            },
        )

        return BootstrapResult(
            user=user,
            display_name=(
                profile.display_name if preview_context.personalisation_available else None
            ),
            modes=modes,
            courses=courses,
            case_files=case_files,
            naric=naric,
            dependencies=dependencies,
            notices=self._build_notices(
                dependencies=dependencies, naric=naric, downgraded_from=None
            ),
            greeting_preview=greeting_preview,
            personalisation_available=preview_context.personalisation_available,
        )

    def list_courses(self, user: UserContext) -> CatalogueResult:
        courses, status = self._load_courses(user)
        return CatalogueResult(
            available=status.is_available,
            reason=status.user_message,
            courses=courses,
        )

    def list_case_files(self, user: UserContext) -> CatalogueResult:
        case_files, status = self._load_case_files(user)
        return CatalogueResult(
            available=status.is_available,
            reason=status.user_message,
            case_files=case_files,
        )

    # ------------------------------------------------------------------ #
    # Opening a session
    # ------------------------------------------------------------------ #

    def open_session(
        self, user: UserContext, command: OpenSessionCommand
    ) -> OpenSessionResult:
        """Open a coaching session.

        A record is written before anything else happens, so every attempt — including
        one that is rejected or crashes — is persisted and diagnosable.
        """
        record = self._create_initial_record(user, command)

        try:
            return self._initialise(user, command, record)
        except Uc01Error as error:
            self._mark_failed(record, error)
            raise
        except Exception as exc:  # noqa: BLE001 - converted, logged, never swallowed
            logger.exception(
                "session.open.unexpected_error",
                extra={
                    "uc01": {
                        "session_id": record.session_id,
                        "user_id": user.user_id,
                        "requested_mode": command.mode.value,
                    }
                },
            )
            wrapped = SessionInitializationError(technical_detail=repr(exc))
            self._mark_failed(record, wrapped)
            raise wrapped from exc

    def get_session(self, user: UserContext, session_id: str) -> SessionRecord:
        """Fetch one session, enforcing ownership.

        A session belonging to another user is reported as *not found*, so the endpoint
        cannot be used to discover that a session id exists.
        """
        record = self._repository.get(session_id)
        if record is None or not user.owns(record.user_id):
            logger.info(
                "session.access_denied",
                extra={
                    "uc01": {
                        "session_id": session_id[:64],
                        "caller": user.user_id,
                        "existed": record is not None,
                    }
                },
            )
            raise SessionNotFoundError()
        return record

    # ------------------------------------------------------------------ #
    # Initialisation steps
    # ------------------------------------------------------------------ #

    def _create_initial_record(
        self, user: UserContext, command: OpenSessionCommand
    ) -> SessionRecord:
        now = self._clock.now()
        record = SessionRecord(
            session_id=self._ids.new_session_id(),
            user_id=user.user_id,
            session_type=command.mode,
            status=SessionStatus.INITIALIZING,
            created_at=now,
            updated_at=now,
            requested_mode=command.mode,
            naric_level=None,
            naric_level_source=NaricLevelSource.DEFAULT,
            explanation_level=DEFAULT_EXPLANATION_LEVEL,
            dependency_failure_policy=command.dependency_failure_policy,
            diagnostics={
                "requested": {
                    "mode": command.mode.value,
                    "course_id": command.course_id,
                    "lesson_id": command.lesson_id,
                    "case_id": command.case_id,
                    "continue_without_calibration": command.continue_without_calibration,
                },
                "dependencies": {},
            },
        )
        try:
            self._repository.create(record)
            self._append_event(
                record,
                SessionEventType.SESSION_INITIALIZING,
                {"requested_mode": command.mode.value},
            )
        except Exception as exc:  # noqa: BLE001 - persistence failure, logged not hidden
            logger.exception(
                "session.record.create_failed",
                extra={"uc01": {"session_id": record.session_id, "user_id": user.user_id}},
            )
            raise SessionInitializationError(technical_detail=repr(exc)) from exc
        logger.info(
            "session.initializing",
            extra={
                "uc01": {
                    "session_id": record.session_id,
                    "user_id": user.user_id,
                    "requested_mode": command.mode.value,
                }
            },
        )
        return record

    def _initialise(
        self,
        user: UserContext,
        command: OpenSessionCommand,
        record: SessionRecord,
    ) -> OpenSessionResult:
        dependencies: dict[DependencyName, DependencyStatus] = {}

        # 1. Personalisation. Failure here must never prevent the session opening.
        profile, profile_status = self._load_profile(user)
        dependencies[DependencyName.PROFILE] = profile_status
        self._note_dependency(record, profile_status)

        # 2. NARIC. Resolved before any validation can fail, so that even a rejected
        #    attempt records the level that would have applied.
        naric, naric_status = self._load_naric(
            user, continue_without_calibration=command.continue_without_calibration
        )
        dependencies[DependencyName.NARIC] = naric_status
        self._note_dependency(record, naric_status)
        record.naric_level = naric.level
        record.naric_level_source = naric.source
        record.explanation_level = naric.level

        # 3. Server-side shape validation of the client's selection.
        self._validate_selection_shape(command)

        # 4. Mode-specific dependency load, authorization and selection resolution.
        effective_mode = command.mode
        downgraded_from: SessionMode | None = None
        course: Course | None = None
        lesson: Lesson | None = None
        case_file: CaseFile | None = None

        if command.mode is SessionMode.COURSE_LINKED:
            _, courses_status = self._load_courses(user)
            dependencies[DependencyName.COURSES] = courses_status
            self._note_dependency(record, courses_status)
            availability = find_mode_availability(
                evaluate_mode_availability(dependencies), SessionMode.COURSE_LINKED
            )
            if availability.available:
                course, lesson = self._resolve_course_selection(user, command)
            else:
                effective_mode, downgraded_from = self._handle_unavailable_mode(
                    user, command, record, dependencies, availability.reason
                )

        elif command.mode is SessionMode.CASE_LINKED:
            _, cases_status = self._load_case_files(user)
            dependencies[DependencyName.CASES] = cases_status
            self._note_dependency(record, cases_status)
            availability = find_mode_availability(
                evaluate_mode_availability(dependencies), SessionMode.CASE_LINKED
            )
            if availability.available:
                case_file = self._resolve_case_selection(user, command)
            else:
                effective_mode, downgraded_from = self._handle_unavailable_mode(
                    user, command, record, dependencies, availability.reason
                )

        # 5. Build the internal context and compose the greeting server-side.
        context = SessionContext(
            user=user,
            session_mode=effective_mode,
            profile=profile,
            course=course,
            lesson=lesson,
            case_file=case_file,
            naric=naric,
            dependencies=dependencies,
            downgraded_from=downgraded_from,
        )
        greeting = self._compose_greeting(context)

        # 6. Finalise the record.
        status = (
            SessionStatus.DEGRADED
            if context.degraded_dependencies or downgraded_from is not None
            else SessionStatus.ACTIVE
        )
        record.session_type = effective_mode
        record.downgraded_from = downgraded_from
        record.linked_resource = context.linked_resource()
        record.greeting_variant = greeting.variant
        record.system_prompt_id = greeting.system_prompt_id
        record.system_prompt_version = greeting.system_prompt_version
        record.status = status
        record.updated_at = self._clock.now()
        self._repository.update(record)
        self._append_event(
            record,
            SessionEventType.SESSION_OPENED,
            {
                "session_type": effective_mode.value,
                "status": status.value,
                "naric_level": record.naric_level,
                "naric_level_source": record.naric_level_source.value,
                "linked_resource_type": (
                    record.linked_resource.resource_type.value
                    if record.linked_resource
                    else None
                ),
                "linked_resource_id": (
                    record.linked_resource.resource_id if record.linked_resource else None
                ),
            },
        )

        logger.info(
            "session.opened",
            extra={
                "uc01": {
                    "session_id": record.session_id,
                    "user_id": user.user_id,
                    "session_type": effective_mode.value,
                    "status": status.value,
                    "degraded": [dep.value for dep in context.degraded_dependencies],
                    "naric_level_source": record.naric_level_source.value,
                    "greeting_variant": greeting.variant,
                }
            },
        )

        return OpenSessionResult(
            record=record,
            context=context,
            greeting=greeting,
            notices=self._build_notices(
                dependencies=dependencies, naric=naric, downgraded_from=downgraded_from
            ),
        )

    # ------------------------------------------------------------------ #
    # Validation / authorization
    # ------------------------------------------------------------------ #

    @staticmethod
    def _validate_selection_shape(command: OpenSessionCommand) -> None:
        """Reject selections that do not belong to the requested mode.

        Server-side only: the UI hides these controls, but hiding is not validation.
        """
        if command.mode is SessionMode.FREE_FORM and (
            command.course_id or command.lesson_id or command.case_id
        ):
            raise SelectionNotAllowedError(
                "A free-form session cannot be linked to a course or case file.",
                technical_detail="free-form request carried a linked-resource id",
            )
        if command.mode is SessionMode.COURSE_LINKED and command.case_id:
            raise SelectionNotAllowedError(
                "A course-linked session cannot also be linked to a case file.",
                technical_detail="course-linked request carried case_id",
            )
        if command.mode is SessionMode.CASE_LINKED and (
            command.course_id or command.lesson_id
        ):
            raise SelectionNotAllowedError(
                "A case-linked session cannot also be linked to a course or lesson.",
                technical_detail="case-linked request carried course_id/lesson_id",
            )

    def _resolve_course_selection(
        self, user: UserContext, command: OpenSessionCommand
    ) -> tuple[Course, Lesson]:
        if not command.course_id:
            raise SelectionRequiredError(messages.COURSE_SELECTION_REQUIRED)
        if not command.lesson_id:
            raise SelectionRequiredError(messages.LESSON_SELECTION_REQUIRED)

        try:
            course = self._courses.get_accessible_course(user, command.course_id)
        except ResourceNotAccessibleError as exc:
            logger.info(
                "session.course.not_accessible",
                extra={
                    "uc01": {
                        "user_id": user.user_id,
                        "course_id": command.course_id[:64],
                        "detail": exc.technical_detail,
                    }
                },
            )
            raise SelectionNotAccessibleError(
                messages.COURSE_NOT_ACCESSIBLE,
                technical_detail=exc.technical_detail,
                context={"course_id": command.course_id},
            ) from exc
        except (DependencyUnavailableError, InvalidUpstreamResponseError) as exc:
            logger.warning(
                "session.course.dependency_failed",
                extra={"uc01": {"detail": exc.technical_detail}},
            )
            raise DependencyDegradedError(
                messages.COURSES_UNAVAILABLE, technical_detail=exc.technical_detail
            ) from exc

        lesson = course.lesson(command.lesson_id)
        if lesson is None:
            logger.info(
                "session.lesson.not_accessible",
                extra={
                    "uc01": {
                        "user_id": user.user_id,
                        "course_id": command.course_id[:64],
                        "lesson_id": command.lesson_id[:64],
                    }
                },
            )
            raise SelectionNotAccessibleError(
                messages.LESSON_NOT_ACCESSIBLE,
                technical_detail="lesson id not present in the accessible course",
                context={"course_id": command.course_id, "lesson_id": command.lesson_id},
            )
        return course, lesson

    def _resolve_case_selection(
        self, user: UserContext, command: OpenSessionCommand
    ) -> CaseFile:
        if not command.case_id:
            raise SelectionRequiredError(messages.CASE_SELECTION_REQUIRED)
        try:
            return self._cases.get_accessible_case_file(user, command.case_id)
        except ResourceNotAccessibleError as exc:
            logger.info(
                "session.case.not_accessible",
                extra={
                    "uc01": {
                        "user_id": user.user_id,
                        "case_id": command.case_id[:64],
                        "detail": exc.technical_detail,
                    }
                },
            )
            raise SelectionNotAccessibleError(
                messages.CASE_NOT_ACCESSIBLE,
                technical_detail=exc.technical_detail,
                context={"case_id": command.case_id},
            ) from exc
        except (DependencyUnavailableError, InvalidUpstreamResponseError) as exc:
            logger.warning(
                "session.case.dependency_failed",
                extra={"uc01": {"detail": exc.technical_detail}},
            )
            raise DependencyDegradedError(
                messages.CASES_UNAVAILABLE, technical_detail=exc.technical_detail
            ) from exc

    def _handle_unavailable_mode(
        self,
        user: UserContext,
        command: OpenSessionCommand,
        record: SessionRecord,
        dependencies: dict[DependencyName, DependencyStatus],
        reason: str | None,
    ) -> tuple[SessionMode, SessionMode | None]:
        """The requested mode cannot be opened right now.

        Either downgrade to free-form (client asked for that) or reject the attempt. In
        both cases the session record survives and records why.
        """
        if command.dependency_failure_policy is DependencyFailurePolicy.FALLBACK_FREE_FORM:
            logger.info(
                "session.mode_downgraded",
                extra={
                    "uc01": {
                        "session_id": record.session_id,
                        "requested_mode": command.mode.value,
                        "effective_mode": SessionMode.FREE_FORM.value,
                        "reason": reason,
                    }
                },
            )
            self._append_event(
                record,
                SessionEventType.MODE_DOWNGRADED,
                {"from": command.mode.value, "to": SessionMode.FREE_FORM.value},
            )
            return SessionMode.FREE_FORM, command.mode

        # Give the user an accurate list of what they *can* still open.
        self._ensure_catalogue_statuses(user, dependencies, record)
        modes = evaluate_mode_availability(dependencies)
        raise ModeUnavailableError(
            reason or messages.GENERIC_DEGRADED_SESSION,
            technical_detail=f"requested mode {command.mode.value} unavailable",
            context={
                "session_id": record.session_id,
                "requested_mode": command.mode.value,
                "available_modes": [mode.value for mode in available_modes(modes)],
                "suggested_mode": SessionMode.FREE_FORM.value,
            },
        )

    def _ensure_catalogue_statuses(
        self,
        user: UserContext,
        dependencies: dict[DependencyName, DependencyStatus],
        record: SessionRecord,
    ) -> None:
        """Load any catalogue status not yet known, so a recovery hint is accurate.

        Only used on the rejection path — a free-form open never pays for this.
        """
        if DependencyName.COURSES not in dependencies:
            _, status = self._load_courses(user)
            dependencies[DependencyName.COURSES] = status
            self._note_dependency(record, status)
        if DependencyName.CASES not in dependencies:
            _, status = self._load_case_files(user)
            dependencies[DependencyName.CASES] = status
            self._note_dependency(record, status)

    # ------------------------------------------------------------------ #
    # Dependency loading (every external call is funnelled through here)
    # ------------------------------------------------------------------ #

    def _load_profile(
        self, user: UserContext
    ) -> tuple[UserProfile | None, DependencyStatus]:
        try:
            profile = self._profile.get_profile(user)
        except ContractError as exc:
            logger.warning(
                "dependency.profile.failed",
                extra={
                    "uc01": {
                        "dependency": DependencyName.PROFILE.value,
                        "error": type(exc).__name__,
                        "detail": exc.technical_detail,
                        "user_id": user.user_id,
                    }
                },
            )
            return None, DependencyStatus(
                dependency=DependencyName.PROFILE,
                state=DependencyState.UNAVAILABLE,
                user_message=messages.PROFILE_UNAVAILABLE_NOTICE,
                technical_detail=exc.technical_detail,
            )

        if not profile.is_complete:
            return profile, DependencyStatus(
                dependency=DependencyName.PROFILE,
                state=DependencyState.INCOMPLETE,
                user_message=messages.PROFILE_INCOMPLETE_NOTICE,
                technical_detail="profile returned without a display name",
            )
        return profile, DependencyStatus(
            dependency=DependencyName.PROFILE, state=DependencyState.AVAILABLE
        )

    def _load_naric(
        self, user: UserContext, *, continue_without_calibration: bool
    ) -> tuple[NaricResolution, DependencyStatus]:
        assessment: NaricAssessment | None = None
        status = DependencyStatus(
            dependency=DependencyName.NARIC, state=DependencyState.AVAILABLE
        )
        try:
            assessment = self._naric.get_assessment(user)
        except ContractError as exc:
            logger.warning(
                "dependency.naric.failed",
                extra={
                    "uc01": {
                        "dependency": DependencyName.NARIC.value,
                        "error": type(exc).__name__,
                        "detail": exc.technical_detail,
                        "user_id": user.user_id,
                    }
                },
            )
            status = replace(
                status,
                state=DependencyState.UNAVAILABLE,
                technical_detail=exc.technical_detail,
            )
        else:
            if not assessment.usable:
                status = replace(
                    status,
                    state=DependencyState.INCOMPLETE,
                    technical_detail=(
                        f"assessment state={assessment.state.value} "
                        f"detail={assessment.detail_code}"
                    ),
                )

        resolution = resolve_naric_level(
            assessment,
            status,
            continue_without_calibration=continue_without_calibration,
        )
        return resolution, replace(status, user_message=resolution.notice)

    def _load_courses(
        self, user: UserContext
    ) -> tuple[Sequence[Course], DependencyStatus]:
        try:
            courses = tuple(self._courses.list_accessible_courses(user))
        except ContractError as exc:
            logger.warning(
                "dependency.courses.failed",
                extra={
                    "uc01": {
                        "dependency": DependencyName.COURSES.value,
                        "error": type(exc).__name__,
                        "detail": exc.technical_detail,
                        "user_id": user.user_id,
                    }
                },
            )
            return (), DependencyStatus(
                dependency=DependencyName.COURSES,
                state=DependencyState.UNAVAILABLE,
                user_message=messages.COURSES_UNAVAILABLE,
                technical_detail=exc.technical_detail,
            )

        if not courses:
            return (), DependencyStatus(
                dependency=DependencyName.COURSES,
                state=DependencyState.EMPTY,
                user_message=messages.COURSES_EMPTY,
            )
        return courses, DependencyStatus(
            dependency=DependencyName.COURSES, state=DependencyState.AVAILABLE
        )

    def _load_case_files(
        self, user: UserContext
    ) -> tuple[Sequence[CaseFile], DependencyStatus]:
        try:
            case_files = tuple(self._cases.list_accessible_case_files(user))
        except ContractError as exc:
            logger.warning(
                "dependency.cases.failed",
                extra={
                    "uc01": {
                        "dependency": DependencyName.CASES.value,
                        "error": type(exc).__name__,
                        "detail": exc.technical_detail,
                        "user_id": user.user_id,
                    }
                },
            )
            return (), DependencyStatus(
                dependency=DependencyName.CASES,
                state=DependencyState.UNAVAILABLE,
                user_message=messages.CASES_UNAVAILABLE,
                technical_detail=exc.technical_detail,
            )

        if not case_files:
            # Not an error: this user simply has no accessible case files.
            return (), DependencyStatus(
                dependency=DependencyName.CASES,
                state=DependencyState.EMPTY,
                user_message=messages.CASES_EMPTY,
            )
        return case_files, DependencyStatus(
            dependency=DependencyName.CASES, state=DependencyState.AVAILABLE
        )

    # ------------------------------------------------------------------ #
    # Greeting
    # ------------------------------------------------------------------ #

    def _compose_greeting(self, context: SessionContext) -> Greeting:
        """Compose the greeting, never letting the greeting layer break a session."""
        try:
            return self._greeting.generate(context)
        except Exception:  # noqa: BLE001 - logged with traceback, degraded not fatal
            logger.exception(
                "greeting.generation_failed",
                extra={
                    "uc01": {
                        "user_id": context.user.user_id,
                        "session_mode": context.session_mode.value,
                    }
                },
            )
            prompt = self._prompts.get(GREETING_SYSTEM_PROMPT_ID)
            return Greeting(
                text="Hi! Welcome back to your coaching session.",
                variant="generic.fallback",
                system_prompt_id=prompt.prompt_id,
                system_prompt_version=prompt.version,
                personalised=False,
            )

    # ------------------------------------------------------------------ #
    # Notices
    # ------------------------------------------------------------------ #

    def _build_notices(
        self,
        *,
        dependencies: Mapping[DependencyName, DependencyStatus],
        naric: NaricResolution,
        downgraded_from: SessionMode | None,
    ) -> tuple[Notice, ...]:
        notices: list[Notice] = []

        if downgraded_from is not None:
            notices.append(
                Notice(
                    code="session_mode_downgraded",
                    message=(
                        "We opened a free-form session because the "
                        f"{downgraded_from.value} option is not available right now."
                    ),
                    severity="warning",
                )
            )

        naric_status = dependencies.get(DependencyName.NARIC)
        if naric.notice:
            notices.append(
                Notice(
                    code="naric_calibration_unavailable",
                    message=naric.notice,
                    severity="warning",
                    action="continue_without_calibration",
                )
            )
        elif naric.is_fallback:
            notices.append(
                Notice(
                    code="naric_default_level_applied",
                    message=messages.NARIC_DEFAULT_APPLIED_NOTICE,
                    severity="info",
                )
            )
        elif naric_status is not None and naric_status.is_degraded:  # pragma: no cover
            notices.append(
                Notice(
                    code="naric_degraded",
                    message=messages.NARIC_UNAVAILABLE_NOTICE,
                    severity="warning",
                    action="continue_without_calibration",
                )
            )

        profile_status = dependencies.get(DependencyName.PROFILE)
        if profile_status is not None and profile_status.is_degraded:
            code = (
                "personalisation_unavailable"
                if profile_status.state is DependencyState.UNAVAILABLE
                else "personalisation_incomplete"
            )
            notices.append(
                Notice(
                    code=code,
                    message=profile_status.user_message or messages.PROFILE_UNAVAILABLE_NOTICE,
                    severity="warning" if code == "personalisation_unavailable" else "info",
                    action="retry" if code == "personalisation_unavailable" else None,
                )
            )

        courses_status = dependencies.get(DependencyName.COURSES)
        if courses_status is not None and courses_status.state is DependencyState.UNAVAILABLE:
            notices.append(
                Notice(
                    code="courses_unavailable",
                    message=messages.COURSES_UNAVAILABLE,
                    severity="warning",
                    action="retry",
                )
            )
        elif courses_status is not None and courses_status.state is DependencyState.EMPTY:
            notices.append(
                Notice(code="courses_empty", message=messages.COURSES_EMPTY, severity="info")
            )

        cases_status = dependencies.get(DependencyName.CASES)
        if cases_status is not None and cases_status.state is DependencyState.UNAVAILABLE:
            notices.append(
                Notice(
                    code="cases_unavailable",
                    message=messages.CASES_UNAVAILABLE,
                    severity="warning",
                    action="retry",
                )
            )
        elif cases_status is not None and cases_status.state is DependencyState.EMPTY:
            notices.append(
                Notice(code="cases_empty", message=messages.CASES_EMPTY, severity="info")
            )

        return tuple(notices)

    # ------------------------------------------------------------------ #
    # Record bookkeeping
    # ------------------------------------------------------------------ #

    def _note_dependency(self, record: SessionRecord, status: DependencyStatus) -> None:
        """Record a dependency outcome on the session for later diagnosis."""
        diagnostics = dict(record.diagnostics)
        observed = dict(diagnostics.get("dependencies") or {})
        observed[status.dependency.value] = {
            "state": status.state.value,
            "technical_detail": status.technical_detail,
        }
        diagnostics["dependencies"] = observed
        record.diagnostics = diagnostics

        if status.is_degraded and status.dependency not in record.degraded_dependencies:
            record.degraded_dependencies = record.degraded_dependencies + (
                status.dependency,
            )
            self._append_event(
                record,
                SessionEventType.DEPENDENCY_DEGRADED,
                {"dependency": status.dependency.value, "state": status.state.value},
            )

    def _mark_failed(self, record: SessionRecord, error: Uc01Error) -> None:
        """Persist a failed open attempt. The record is never deleted or skipped."""
        record.status = SessionStatus.FAILED
        record.failure_code = error.failure_code
        record.updated_at = self._clock.now()
        diagnostics = dict(record.diagnostics)
        diagnostics["failure"] = {
            "code": error.failure_code,
            "technical_detail": error.technical_detail,
            "context": dict(error.context),
        }
        record.diagnostics = diagnostics
        try:
            self._repository.update(record)
            self._append_event(
                record,
                SessionEventType.SESSION_FAILED,
                {"failure_code": error.failure_code},
            )
        except Exception:  # noqa: BLE001 - never mask the original error
            logger.exception(
                "session.record.fail_update_failed",
                extra={"uc01": {"session_id": record.session_id}},
            )
        logger.warning(
            "session.open.failed",
            extra={
                "uc01": {
                    "session_id": record.session_id,
                    "user_id": record.user_id,
                    "requested_mode": (
                        record.requested_mode.value if record.requested_mode else None
                    ),
                    "failure_code": error.failure_code,
                    "detail": error.technical_detail,
                }
            },
        )

    def _append_event(
        self, record: SessionRecord, event_type: SessionEventType, payload: Mapping
    ) -> None:
        self._repository.append_event(
            SessionEvent(
                session_id=record.session_id,
                event_type=event_type.value,
                occurred_at=self._clock.now(),
                payload=dict(payload),
            )
        )


__all__ = ["SessionInitiationService"]
