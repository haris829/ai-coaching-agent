"""Review action tests (spec sections 11, 25)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.modules.analytics.cancellation import QueryContext
from app.modules.analytics.domain.enums import FlagReason, FlagStatus, ReviewActionType
from app.modules.analytics.domain.review import ReviewActionRequest
from app.modules.analytics.errors import AuthorizationError, ReviewConflictError
from app.modules.analytics.services import ReviewService

from .conftest import ADMIN_ID
from .factories import NOW, make_flag

#: UC-10's services are asynchronous, and this repository drives async tests with anyio
#: — the plugin that arrives with starlette — exactly as UC-07, UC-08 and UC-09 do.
pytestmark = pytest.mark.anyio


class TestAllActionTypes:
    @pytest.mark.parametrize(
        ("action", "expected_status"),
        [
            (ReviewActionType.NO_CHANGE, FlagStatus.RESOLVED),
            (ReviewActionType.QUESTION_UPDATED, FlagStatus.RESOLVED),
            (ReviewActionType.QUESTION_RETIRED, FlagStatus.RETIRED),
        ],
    )
    async def test_action_transitions_an_active_flag(
        self, review_service, review_store, context, action, expected_status
    ):
        await review_store.put_flag(make_flag("q1"))

        result = await review_service.record_action(
            ReviewActionRequest(question_id="q1", action=action), ADMIN_ID, context
        )

        assert result.flag.status is expected_status
        assert review_store.flags_snapshot()["q1"].status is expected_status

    @pytest.mark.parametrize("action", list(ReviewActionType))
    async def test_every_action_is_audited(self, review_service, context, action):
        result = await review_service.record_action(
            ReviewActionRequest(question_id="q1", action=action), ADMIN_ID, context
        )

        assert result.action.action == action
        assert result.action.question_id == "q1"
        assert result.action.admin_id == ADMIN_ID
        assert result.action.created_at == NOW


class TestAuditRecordContents:
    async def test_record_captures_question_decision_admin_and_timestamp(
        self, review_service, review_store, context
    ):
        await review_store.put_flag(make_flag("q1"))

        result = await review_service.record_action(
            ReviewActionRequest(
                question_id="q1",
                action=ReviewActionType.QUESTION_UPDATED,
                note="Reworded the stem; the distractor was ambiguous.",
            ),
            ADMIN_ID,
            context,
        )

        action = result.action
        assert action.action_id
        assert action.question_id == "q1"
        assert action.action is ReviewActionType.QUESTION_UPDATED
        assert action.admin_id == ADMIN_ID
        assert action.created_at == NOW
        assert action.note == "Reworded the stem; the distractor was ambiguous."
        assert action.previous_flag_status is FlagStatus.FLAGGED
        assert action.resulting_flag_status is FlagStatus.RESOLVED

    async def test_resolution_is_attributed_on_the_flag(
        self, review_service, review_store, context
    ):
        await review_store.put_flag(make_flag("q1"))

        result = await review_service.record_action(
            ReviewActionRequest(question_id="q1", action=ReviewActionType.NO_CHANGE),
            "admin-42",
            context,
        )

        assert result.flag.resolved_by == "admin-42"
        assert result.flag.resolved_at == NOW
        assert result.flag.resolution_action is ReviewActionType.NO_CHANGE

    async def test_original_measurement_survives_resolution(
        self, review_service, review_store, context
    ):
        await review_store.put_flag(make_flag("q1", wrong_answer_rate=88.5, graded_responses_at_flag=17))

        result = await review_service.record_action(
            ReviewActionRequest(question_id="q1", action=ReviewActionType.NO_CHANGE),
            ADMIN_ID,
            context,
        )

        assert result.flag.wrong_answer_rate_at_flag == 88.5
        assert result.flag.graded_responses_at_flag == 17

    async def test_audit_log_is_append_only(self, review_service, review_store, context):
        for action in (ReviewActionType.NO_CHANGE, ReviewActionType.QUESTION_UPDATED):
            await review_service.record_action(
                ReviewActionRequest(question_id="q1", action=action), ADMIN_ID, context
            )

        assert len(review_store.actions_snapshot()) == 2

    async def test_duplicate_action_id_is_rejected(
        self, review_repository, settings, clock, context
    ):
        service = ReviewService(
            review_repository, settings, clock, id_factory=lambda: "fixed-id"
        )
        await service.record_action(
            ReviewActionRequest(question_id="q1", action=ReviewActionType.NO_CHANGE),
            ADMIN_ID,
            context,
        )

        with pytest.raises(ReviewConflictError):
            await service.record_action(
                ReviewActionRequest(question_id="q1", action=ReviewActionType.NO_CHANGE),
                ADMIN_ID,
                context,
            )


class TestUnflaggedQuestions:
    async def test_no_change_on_an_unflagged_question_invents_no_flag(
        self, review_service, review_store, context
    ):
        result = await review_service.record_action(
            ReviewActionRequest(question_id="q1", action=ReviewActionType.NO_CHANGE),
            ADMIN_ID,
            context,
        )

        assert result.flag is None
        assert review_store.flags_snapshot() == {}
        assert len(review_store.actions_snapshot()) == 1  # still audited

    async def test_retiring_an_unflagged_question_persists_the_retirement(
        self, review_service, review_store, context
    ):
        result = await review_service.record_action(
            ReviewActionRequest(question_id="q1", action=ReviewActionType.QUESTION_RETIRED),
            ADMIN_ID,
            context,
        )

        record = review_store.flags_snapshot()["q1"]
        assert record.status is FlagStatus.RETIRED
        assert record.reason is FlagReason.ADMINISTRATIVE_ACTION
        assert result.flag.status is FlagStatus.RETIRED

    async def test_administrative_record_reports_no_measurement_rather_than_zero(
        self, review_service, review_store, context
    ):
        await review_service.record_action(
            ReviewActionRequest(question_id="q1", action=ReviewActionType.QUESTION_RETIRED),
            ADMIN_ID,
            context,
        )

        record = review_store.flags_snapshot()["q1"]
        assert record.wrong_answer_rate is None
        assert record.threshold_used is None
        assert record.graded_responses_at_flag is None


class TestRetirementIsTerminal:
    @pytest.mark.parametrize("action", list(ReviewActionType))
    async def test_no_action_can_follow_retirement(
        self, review_service, review_store, context, action
    ):
        await review_store.put_flag(make_flag("q1", status=FlagStatus.RETIRED))

        with pytest.raises(ReviewConflictError) as exc:
            await review_service.record_action(
                ReviewActionRequest(question_id="q1", action=action), ADMIN_ID, context
            )

        assert exc.value.http_status == 409

    async def test_rejected_action_is_not_written_to_the_audit_log(
        self, review_service, review_store, context
    ):
        await review_store.put_flag(make_flag("q1", status=FlagStatus.RETIRED))

        with pytest.raises(ReviewConflictError):
            await review_service.record_action(
                ReviewActionRequest(question_id="q1", action=ReviewActionType.NO_CHANGE),
                ADMIN_ID,
                context,
            )

        assert review_store.actions_snapshot() == ()


class TestIdentityHandling:
    async def test_authenticated_identity_is_stored(self, review_service, context):
        result = await review_service.record_action(
            ReviewActionRequest(question_id="q1", action=ReviewActionType.NO_CHANGE),
            "admin-from-token",
            context,
        )

        assert result.action.admin_id == "admin-from-token"

    async def test_matching_body_identity_is_accepted(self, review_service, context):
        result = await review_service.record_action(
            ReviewActionRequest(
                question_id="q1", action=ReviewActionType.NO_CHANGE, admin_id="admin-9"
            ),
            "admin-9",
            context,
        )

        assert result.action.admin_id == "admin-9"

    async def test_mismatched_body_identity_is_rejected(
        self, review_service, review_store, context
    ):
        with pytest.raises(AuthorizationError) as exc:
            await review_service.record_action(
                ReviewActionRequest(
                    question_id="q1",
                    action=ReviewActionType.NO_CHANGE,
                    admin_id="someone-else",
                ),
                ADMIN_ID,
                context,
            )

        assert exc.value.http_status == 403
        assert review_store.actions_snapshot() == ()


class TestHistoryAndAudit:
    async def test_history_returns_actions_and_current_flag(
        self, review_service, review_store, context
    ):
        await review_store.put_flag(make_flag("q1"))
        await review_service.record_action(
            ReviewActionRequest(question_id="q1", action=ReviewActionType.QUESTION_UPDATED),
            ADMIN_ID,
            context,
        )

        history = await review_service.get_history("q1", context)

        assert history.question_id == "q1"
        assert history.total == 1
        assert history.actions[0].action is ReviewActionType.QUESTION_UPDATED
        assert history.current_flag.status is FlagStatus.RESOLVED
        assert history.calculated_at == NOW

    async def test_history_of_an_unreviewed_question_is_empty_not_an_error(
        self, review_service, context
    ):
        history = await review_service.get_history("never-touched", context)

        assert history.actions == ()
        assert history.total == 0
        assert history.current_flag is None

    async def test_actions_are_returned_newest_first(
        self, review_service, review_repository, clock, settings, context
    ):
        ids = iter([f"action-{i}" for i in range(10)])
        service = ReviewService(review_repository, settings, clock, id_factory=lambda: next(ids))

        await service.record_action(
            ReviewActionRequest(question_id="q1", action=ReviewActionType.NO_CHANGE),
            ADMIN_ID,
            context,
        )
        clock.set(NOW + timedelta(hours=1))
        later = QueryContext.create(timeout_seconds=30.0, clock=clock)
        await service.record_action(
            ReviewActionRequest(question_id="q1", action=ReviewActionType.QUESTION_UPDATED),
            ADMIN_ID,
            later,
        )

        page = await service.list_actions(later)

        assert [a.action for a in page.items] == [
            ReviewActionType.QUESTION_UPDATED,
            ReviewActionType.NO_CHANGE,
        ]

    async def test_audit_log_filters_by_question_and_admin(self, review_service, context):
        await review_service.record_action(
            ReviewActionRequest(question_id="q1", action=ReviewActionType.NO_CHANGE),
            "admin-a",
            context,
        )
        await review_service.record_action(
            ReviewActionRequest(question_id="q2", action=ReviewActionType.NO_CHANGE),
            "admin-b",
            context,
        )

        by_question = await review_service.list_actions(context, question_id="q1")
        by_admin = await review_service.list_actions(context, admin_id="admin-b")

        assert by_question.total == 1
        assert by_question.items[0].question_id == "q1"
        assert by_admin.total == 1
        assert by_admin.items[0].admin_id == "admin-b"

    async def test_audit_log_paginates(self, review_service, context):
        for index in range(5):
            await review_service.record_action(
                ReviewActionRequest(question_id=f"q{index}", action=ReviewActionType.NO_CHANGE),
                ADMIN_ID,
                context,
            )

        page = await review_service.list_actions(context, limit=2, offset=2)

        assert page.total == 5
        assert len(page.items) == 2
        assert page.limit == 2
        assert page.offset == 2
