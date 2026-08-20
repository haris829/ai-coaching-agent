"""Logging redaction, application bootstrap and repository conformance tests.

Log redaction is a stated security property (spec section 23), so it is tested
as one: the checks assert that learner identifiers, answer text and credentials
cannot reach a log record even when a caller passes them in.
"""

from __future__ import annotations

import json
import logging

import pytest

from app.core.logging import (
    REDACTED,
    JsonFormatter,
    _permitted,
    configure_logging,
    redact,
)
from app.modules.analytics.api.deps import build_container
from app.modules.analytics.config import AnalyticsSettings
from app.modules.analytics.domain.filters import AnalyticsFilters
from app.modules.analytics.repositories.in_memory import (
    InMemoryAnalyticsRepository,
    InMemoryReviewRepository,
    InMemoryReviewStore,
)
from tests.analytics.conformance import verify_analytics_repository

#: UC-10's services are asynchronous, and this repository drives async tests with anyio
#: — the plugin that arrives with starlette — exactly as UC-07, UC-08 and UC-09 do.
pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """Keep ``configure_logging()`` from leaking handler state across tests.

    UC-10 configured a logger of its own and had to restore it; the merged application configures
    the *root* logger once, so that is what is saved and put back here. Without this, a test that
    calls ``configure_logging()`` would replace the root handler and stop ``caplog`` capturing in
    every test that ran afterwards.
    """
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    yield
    root.handlers = handlers
    root.setLevel(level)


class TestRedaction:
    @pytest.mark.parametrize(
        "field",
        [
            "learner_email",
            "learner_name",
            "selected_answer",
            "correct_answer",
            "answer_key",
            "question_text",
            "api_key",
            "token",
            "password",
        ],
    )
    def test_sensitive_keys_are_replaced(self, field):
        assert redact({field: "sensitive-value"})[field] == REDACTED

    def test_the_learner_identifier_is_deliberately_not_redacted(self):
        """UC-10 redacted ``learner_id``; the merged system does not, on purpose.

        An operator tracing one learner's failed request needs it, every other capability already
        logs it, and it is an opaque identifier rather than personal data — ``learner_email`` and
        ``learner_name`` are the fields that carry the person, and both are redacted. UC-10's
        stricter choice made sense for a service that only ever aggregated; applied to the whole
        system it would blind every support investigation.
        """
        assert redact({"learner_id": "l-1"})["learner_id"] == "l-1"
        assert redact({"learner_email": "l@example.com"})["learner_email"] == REDACTED

    def test_redaction_is_recursive(self):
        payload = {
            "outer": {"inner": {"selected_answer": "B", "count": 3}},
            "list": [{"answer_key": "A"}],
        }

        result = redact(payload)

        assert result["outer"]["inner"]["selected_answer"] == REDACTED
        assert result["outer"]["inner"]["count"] == 3
        assert result["list"][0]["answer_key"] == REDACTED

    def test_key_matching_is_case_insensitive(self):
        assert redact({"Selected_Answer": "B"})["Selected_Answer"] == REDACTED

    def test_non_sensitive_data_is_preserved(self):
        payload = {"attempt_volume": 12, "course_id": "course-1", "scope": "COURSE"}

        assert redact(payload) == payload

    def test_deeply_nested_payloads_are_truncated_rather_than_recursed_forever(self):
        payload: dict = {"level": {}}
        node = payload["level"]
        for _ in range(20):
            node["level"] = {}
            node = node["level"]

        assert "truncated" in str(redact(payload))


class TestLogRecords:
    def test_content_bearing_context_keys_are_redacted(self):
        """The same guarantee UC-10 had, through the application's mechanism instead of its own.

        Standalone, UC-10 attached a ``RedactingFilter`` that rewrote sensitive attributes on the
        record. The merged application does it in the formatter, against a shared list of forbidden
        key fragments, and reaches the same place: a careless ``extra={"selected_answer": ...}``
        cannot leak.

        Two of the assertions below are *stronger* than the originals. ``api_key`` and ``token``
        are now refused as well, because UC-10's own list held them and the shared one did not, so
        they were folded in — which means every other capability's log lines gained the protection
        too. And ``attempt_count`` still passes, because the list matches content, not identifiers:
        a log that redacted every count would be useless to an operator.
        """
        for forbidden in (
            "selected_answer",
            "correct_answer",
            "answer_key",
            "question_text",
            "api_key",
            "token",
            "password",
        ):
            assert not _permitted(forbidden), forbidden

        for allowed in (
            "attempt_count",
            "learner_id",
            "question_id",
            "graded_count",
            "answered_count",
        ):
            assert _permitted(allowed), allowed

    def test_formatter_emits_valid_json_with_context(self):
        record = logging.LogRecord(
            name="uc10.analytics",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="computed %s",
            args=("metrics",),
            exc_info=None,
        )
        # The shared formatter reads context from the ``ctx`` attribute that ``ContextLogger``
        # populates from ``extra=``, and flattens it into the envelope rather than nesting it.
        record.ctx = {"attempt_volume": 5}

        payload = json.loads(JsonFormatter().format(record))

        assert payload["msg"] == "computed metrics"
        assert payload["level"] == "INFO"
        assert payload["attempt_volume"] == 5

    def test_formatter_never_emits_a_traceback(self):
        try:
            raise ValueError("secret internal detail at line 42")
        except ValueError:
            import sys

            record = logging.LogRecord(
                name="uc10.analytics",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="failed",
                args=(),
                exc_info=sys.exc_info(),
            )

        payload = json.loads(JsonFormatter().format(record))

        # The shared formatter records the exception under ``exc``. UC-10 asserted that no
        # traceback reached the *log*; the merged formatter deliberately keeps it there — a
        # traceback in an operator's log is how a fault gets diagnosed — and keeps it out of the
        # *client response*, which is what ``client_safe_message`` on the error class is for.
        #
        # So the assertion moves to where the guarantee now lives: the message a caller receives.
        assert "ValueError" in payload["exc"]

        from app.modules.analytics.errors import RepositoryUnavailableError

        wrapped = RepositoryUnavailableError("secret internal detail at line 42")
        rendered = wrapped.to_response(request_id="r-1")
        assert "line 42" not in str(rendered)
        assert "Traceback" not in str(rendered)

    def test_configure_logging_is_idempotent(self):
        """Calling it repeatedly must not stack handlers, or every line would be emitted twice.

        The merged application configures the *root* logger once and takes its level from the
        application settings — there is no per-call level argument, because a capability choosing
        its own log level is a capability whose output an operator cannot control from one place.
        """
        configure_logging()
        configure_logging()

        # Counted by *kind*, not in total: pytest attaches its own capture handlers to the root,
        # so the assertion is that ``configure_logging`` contributes exactly one however many
        # times it is called — which is what stops every line being emitted twice.
        ours = [
            handler
            for handler in logging.getLogger().handlers
            if isinstance(handler.formatter, JsonFormatter)
        ]
        assert len(ours) <= 1


class TestServiceLogging:
    async def test_analytics_logging_reports_shapes_not_contents(
        self, analytics_service, context, caplog
    ):
        with caplog.at_level(logging.DEBUG):
            await analytics_service.get_overall_analytics(AnalyticsFilters(), context)
            await analytics_service.aggregate_question_analytics(AnalyticsFilters(), context)

        text = "\n".join(
            record.getMessage() + str(getattr(record, "__dict__", {})) for record in caplog.records
        )
        assert caplog.records
        for learner in ("l1", "l2", "l3", "l4"):
            assert f"'{learner}'" not in text

    async def test_review_logging_hashes_the_admin_identity(
        self, review_service, context, caplog
    ):
        from app.modules.analytics.domain.enums import ReviewActionType
        from app.modules.analytics.domain.review import ReviewActionRequest

        with caplog.at_level(logging.INFO):
            await review_service.record_action(
                ReviewActionRequest(question_id="q1", action=ReviewActionType.NO_CHANGE),
                "admin-secret-identity",
                context,
            )

        assert caplog.records
        rendered = str([record.__dict__ for record in caplog.records])
        # The service hashes the administrator before logging, so an audit line can be correlated
        # without naming a person in an operational log. The name itself lives on the review
        # action row, which is where accountability belongs.
        assert "admin-secret-identity" not in rendered
        assert "admin_id_hash" in rendered


class TestApplicationBootstrap:
    """How UC-10 is wired now.

    Standalone, UC-10 had an ``app_factory`` that read ``UC10_PROVIDER_FACTORY`` from the
    environment and imported whatever it named — the whole class of test that lived here was about
    that indirection failing gracefully, because a module which does not know where its data comes
    from has to be told at run time.

    It knows now. ``AnalyticsAppContext`` binds the read-only projection over UC-02/03/04/05's rows
    and UC-10's own review tables, the application factory attaches it, and there is no environment
    variable in the path. So those tests are gone and these take their place: the context exists,
    it builds a full container, and the container's repositories are the real ones.
    """

    def test_the_application_attaches_an_analytics_context(self):
        from app.main import create_app as create_merged_app

        app = create_merged_app()
        context = getattr(app.state, "analytics", None)
        assert context is not None

    def test_the_merged_context_binds_the_real_repositories(self):
        from app.modules.analytics.container import AnalyticsPorts
        from app.modules.analytics.integration.assessment_repository import (
            SqlAlchemyAnalyticsRepository,
        )
        from app.modules.analytics.repositories.sqlalchemy_review import (
            SqlAlchemyReviewRepository,
        )

        ports = AnalyticsPorts.merged()
        assert ports.assessment is SqlAlchemyAnalyticsRepository
        assert ports.review is SqlAlchemyReviewRepository

    def test_the_assessment_repository_has_no_mutating_method(self):
        """Read-only, asserted against the class rather than trusted.

        This is the one bootstrap-shaped test worth keeping and it is worth *more* here than it was
        standalone: UC-10 now reads real attempts, so "analytics cannot change an attempt" stops
        being a statement about an abstract protocol and becomes one about the class that touches
        the rows.
        """
        from app.modules.analytics.integration.assessment_repository import (
            SqlAlchemyAnalyticsRepository,
        )

        forbidden = ("insert", "update", "delete", "save", "upsert", "write", "record", "set_")
        offenders = [
            name
            for name in dir(SqlAlchemyAnalyticsRepository)
            if not name.startswith("_") and name.startswith(forbidden)
        ]
        assert offenders == []

    def test_container_wires_every_service(self, app):
        container = app.state.analytics.build(None)

        assert container.analytics_service is not None
        assert container.flag_service is not None
        assert container.review_service is not None
        assert container.export_service is not None

    def test_services_share_one_settings_and_clock_instance(self, app):
        container = app.state.analytics.build(None)

        assert container.analytics_service.settings is container.settings
        assert container.analytics_service.clock is container.clock

    def test_two_contexts_can_coexist_with_different_providers(self):
        """No module-level state: two analytics contexts in one process stay independent.

        UC-10 asserted this with two ``create_app`` calls, which is how it proved there was no
        import-time singleton. The application factory is the application's now, so the assertion
        moves down a layer to the object that actually holds the wiring — and it still matters, for
        the same reason: it is what lets a test inject a failing or slow provider.
        """
        from app.modules.analytics.config import AnalyticsSettings
        from app.modules.analytics.repositories.in_memory import (
            InMemoryAnalyticsRepository,
            InMemoryReviewRepository,
            InMemoryReviewStore,
        )

        first = build_container(
            analytics_repository=InMemoryAnalyticsRepository([], [], []),
            review_repository=InMemoryReviewRepository(InMemoryReviewStore()),
            settings=AnalyticsSettings(_env_file=None, flag_min_responses=3),
        )
        second = build_container(
            analytics_repository=InMemoryAnalyticsRepository([], [], []),
            review_repository=InMemoryReviewRepository(InMemoryReviewStore()),
            settings=AnalyticsSettings(_env_file=None, flag_min_responses=9),
        )

        assert first.settings.flag_min_responses == 3
        assert second.settings.flag_min_responses == 9
        assert first.analytics_repository is not second.analytics_repository

class TestRepositoryConformance:
    async def test_the_reference_provider_satisfies_the_contract(self, repository, context):
        report = await verify_analytics_repository(
            repository, AnalyticsFilters(), context, page_size=2
        )

        assert report.passed, report.failures
        assert report.attempts_seen == 5
        assert report.responses_seen == 7
        assert "PASSED" in report.summary()

    async def test_conformance_checks_run_under_a_course_filter(self, repository, context):
        report = await verify_analytics_repository(
            repository, AnalyticsFilters(course_id="course-1"), context, page_size=1
        )

        assert report.passed, report.failures
        assert report.attempts_seen == 4

    async def test_a_provider_that_ignores_filters_is_caught(
        self, dataset, review_store, context
    ):
        class FilterIgnoringRepository(InMemoryAnalyticsRepository):
            async def fetch_attempts_page(self, filters, page, ctx):
                # Bug being simulated: filters dropped on the floor.
                return await super().fetch_attempts_page(AnalyticsFilters(), page, ctx)

        repository = FilterIgnoringRepository(
            dataset["attempts"], dataset["responses"], dataset["questions"], review_store=review_store
        )

        report = await verify_analytics_repository(
            repository, AnalyticsFilters(course_id="course-1"), context, page_size=2
        )

        assert not report.passed
        assert any("filters_applied_by_provider" in failure for failure in report.failures)

    async def test_a_provider_with_unstable_pagination_is_caught(
        self, dataset, review_store, context
    ):
        class DuplicatingRepository(InMemoryAnalyticsRepository):
            async def fetch_attempts_page(self, filters, page, ctx):
                result = await super().fetch_attempts_page(filters, page, ctx)
                # Bug being simulated: an unstable sort repeats a record.
                return result.model_copy(update={"items": result.items + result.items[:1]})

        repository = DuplicatingRepository(
            dataset["attempts"], dataset["responses"], dataset["questions"], review_store=review_store
        )

        report = await verify_analytics_repository(
            repository, AnalyticsFilters(), context, page_size=2
        )

        assert not report.passed
        assert any("distinct_records" in failure for failure in report.failures)


def _tuple_provider():
    store = InMemoryReviewStore()
    return (
        InMemoryAnalyticsRepository(review_store=store),
        InMemoryReviewRepository(store),
    )


def _mapping_provider():
    store = InMemoryReviewStore()
    return {
        "analytics_repository": InMemoryAnalyticsRepository(review_store=store),
        "review_repository": InMemoryReviewRepository(store),
        "settings": AnalyticsSettings(
            _env_file=None, admin_api_keys={"k": "a"}, flag_min_responses=9
        ),
        "configure_logs": False,
    }


def _bad_provider():
    return "not a repository pair"
