"""CSV export tests (spec sections 10, 25).

The important properties are that the file parses as CSV, that it says exactly
what the API says, and that the same inputs always produce the same bytes.
"""

from __future__ import annotations

import csv
import io

import pytest

from app.modules.analytics.domain.enums import DataState, QuestionSortField, SortDirection
from app.modules.analytics.domain.filters import AnalyticsFilters
from app.modules.analytics.errors import DatasetTooLargeError
from app.modules.analytics.repositories.in_memory import InMemoryAnalyticsRepository
from app.modules.analytics.services import AnalyticsService, CsvExportService
from app.modules.analytics.services.export_service import OVERALL_COLUMNS, QUESTION_COLUMNS

from .conftest import make_settings
from .factories import make_attempt, make_flag, make_question, make_response

#: UC-10's services are asynchronous, and this repository drives async tests with anyio
#: — the plugin that arrives with starlette — exactly as UC-07, UC-08 and UC-09 do.
pytestmark = pytest.mark.anyio


def parse(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


class TestFormatValidity:
    async def test_questions_export_parses_as_csv(self, export_service, context):
        export = await export_service.export_questions(AnalyticsFilters(), context)

        rows = parse(export.render())

        assert len(rows) == 2
        assert [row["question_id"] for row in rows] == ["question-1", "question-2"]

    async def test_uses_crlf_line_endings(self, export_service, context):
        text = (await export_service.export_questions(AnalyticsFilters(), context)).render()

        assert text.count("\r\n") == 3  # header plus two rows
        assert "\n\n" not in text

    async def test_column_order_is_stable(self, export_service, context):
        overall = await export_service.export_overall(AnalyticsFilters(), context)
        questions = await export_service.export_questions(AnalyticsFilters(), context)

        assert overall.header == OVERALL_COLUMNS
        assert questions.header == QUESTION_COLUMNS
        assert parse(questions.render())[0].keys() == dict.fromkeys(QUESTION_COLUMNS).keys()

    async def test_flagged_export_reuses_the_question_layout(
        self, export_service, review_store, context
    ):
        await review_store.put_flag(make_flag("question-1"))

        flagged = await export_service.export_flagged_questions(AnalyticsFilters(), context)

        assert flagged.header == QUESTION_COLUMNS

    async def test_filename_is_stamped_with_the_calculation_time(
        self, export_service, context
    ):
        export = await export_service.export_questions(AnalyticsFilters(), context)

        assert export.filename == "uc10-questions-20260301T120000Z.csv"

    async def test_streamed_chunks_reproduce_the_document(self, export_service, context):
        export = await export_service.export_questions(AnalyticsFilters(), context)

        streamed = "".join([chunk async for chunk in export.iter_chunks(rows_per_chunk=1)])

        assert streamed == export.render()


class TestEscaping:
    async def _export_with_answer(self, answer, review_store, settings, clock, sanitise=True):
        repository = InMemoryAnalyticsRepository(
            [make_attempt("a1")],
            [
                make_response("r1", attempt_id="a1", question_id="q1", selected_answer=answer, is_correct=False),
                make_response("r2", attempt_id="a1", question_id="q1", selected_answer=answer, is_correct=False),
            ],
            [make_question("q1")],
            review_store=review_store,
        )
        applied = settings if sanitise else make_settings(export_sanitise_formulas=False)
        service = CsvExportService(AnalyticsService(repository, applied, clock), applied, clock)
        return service, repository

    @pytest.mark.parametrize(
        "answer",
        [
            'He said "yes", then no',
            "line one\nline two",
            "comma, separated, values",
            "naive, unicode: café naïve über",
            "tab\tseparated",
            "semi;colon",
        ],
    )
    async def test_awkward_answers_round_trip_intact(
        self, review_store, settings, clock, context, answer
    ):
        service, _ = await self._export_with_answer(answer, review_store, settings, clock)

        export = await service.export_questions(AnalyticsFilters(), context)
        rows = parse(export.render())

        expected = answer if not answer.startswith("\t") else "'" + answer
        assert rows[0]["most_frequent_wrong_answer"] == expected

    @pytest.mark.parametrize("payload", ["=cmd|' /c calc'!A1", "+1+1", "-2+3", "@SUM(A1)"])
    async def test_formula_payloads_are_neutralised(
        self, review_store, settings, clock, context, payload
    ):
        service, _ = await self._export_with_answer(payload, review_store, settings, clock)

        rows = parse((await service.export_questions(AnalyticsFilters(), context)).render())

        assert rows[0]["most_frequent_wrong_answer"] == "'" + payload

    async def test_sanitisation_can_be_switched_off(
        self, review_store, settings, clock, context
    ):
        service, _ = await self._export_with_answer(
            "=1+1", review_store, settings, clock, sanitise=False
        )

        rows = parse((await service.export_questions(AnalyticsFilters(), context)).render())

        assert rows[0]["most_frequent_wrong_answer"] == "=1+1"


class TestNullAndZeroHandling:
    async def test_absent_metrics_are_empty_fields_not_zeros(
        self, settings, clock, review_store, context
    ):
        repository = InMemoryAnalyticsRepository(
            [make_attempt("a1")],
            [make_response("r1", attempt_id="a1", question_id="q1", selected_answer="A", is_correct=None, time_spent_seconds=None)],
            [make_question("q1")],
            review_store=review_store,
        )
        service = CsvExportService(AnalyticsService(repository, settings, clock), settings, clock)

        rows = parse((await service.export_questions(AnalyticsFilters(), context)).render())

        assert rows[0]["accuracy_percentage"] == ""
        assert rows[0]["average_time_seconds"] == ""
        assert rows[0]["most_frequent_wrong_answer"] == ""
        assert rows[0]["attempt_count"] == "1"  # a real count is still reported

    async def test_never_writes_the_literal_none(self, export_service, context):
        text = (await export_service.export_questions(AnalyticsFilters(), context)).render()

        assert "None" not in text

    async def test_real_zero_is_written_as_zero(self, export_service, context):
        rows = parse((await export_service.export_questions(AnalyticsFilters(), context)).render())

        question_2 = next(row for row in rows if row["question_id"] == "question-2")
        assert question_2["wrong_answer_rate"] == "0.00"

    async def test_booleans_use_a_stable_lowercase_form(self, export_service, context):
        rows = parse((await export_service.export_questions(AnalyticsFilters(), context)).render())

        assert rows[0]["is_flagged"] in {"true", "false"}
        assert rows[0]["meets_flag_criteria"] in {"true", "false"}


class TestDeterminism:
    async def test_repeated_exports_are_byte_identical(self, export_service, context):
        first = await export_service.export_questions(AnalyticsFilters(), context)
        second = await export_service.export_questions(AnalyticsFilters(), context)

        assert first.render() == second.render()
        assert first.filename == second.filename

    async def test_row_order_is_independent_of_provider_ordering(
        self, dataset, review_store, settings, clock, context
    ):
        forward = InMemoryAnalyticsRepository(
            dataset["attempts"], dataset["responses"], dataset["questions"], review_store=review_store
        )
        reversed_provider = InMemoryAnalyticsRepository(
            list(reversed(dataset["attempts"])),
            list(reversed(dataset["responses"])),
            list(reversed(dataset["questions"])),
            review_store=review_store,
        )

        outputs = []
        for repository in (forward, reversed_provider):
            service = CsvExportService(
                AnalyticsService(repository, settings, clock), settings, clock
            )
            outputs.append((await service.export_questions(AnalyticsFilters(), context)).render())

        assert outputs[0] == outputs[1]

    async def test_floats_are_formatted_to_a_fixed_precision(self, export_service, context):
        rows = parse((await export_service.export_questions(AnalyticsFilters(), context)).render())

        assert rows[0]["accuracy_percentage"] == "25.00"
        assert rows[0]["average_time_seconds"] == "20.00"

    async def test_precision_follows_configuration(self, repository, clock, context):
        settings = make_settings(decimal_places=4)
        service = CsvExportService(
            AnalyticsService(repository, settings, clock), settings, clock
        )

        rows = parse((await service.export_questions(AnalyticsFilters(), context)).render())

        assert rows[0]["accuracy_percentage"] == "25.0000"


class TestConsistencyWithTheApi:
    async def test_question_figures_match_the_api_exactly(
        self, analytics_service, export_service, context
    ):
        page = await analytics_service.list_question_analytics(
            AnalyticsFilters(), context, limit=100
        )
        rows = parse((await export_service.export_questions(AnalyticsFilters(), context)).render())

        by_id = {row["question_id"]: row for row in rows}
        for question in page.items:
            row = by_id[question.question_id]
            assert row["attempt_count"] == str(question.attempt_count)
            assert row["correct_count"] == str(question.correct_count)
            assert row["incorrect_count"] == str(question.incorrect_count)
            assert row["accuracy_percentage"] == f"{question.accuracy_percentage:.2f}"
            assert row["graded_count"] == str(question.graded_count)
            assert row["question_type"] == question.question_type_label

    async def test_overall_figures_match_the_api_exactly(
        self, analytics_service, export_service, context
    ):
        api = await analytics_service.get_overall_analytics(AnalyticsFilters(), context)
        row = parse((await export_service.export_overall(AnalyticsFilters(), context)).render())[0]

        assert row["attempt_volume"] == str(api.attempt_volume)
        assert row["average_score"] == f"{api.average_score:.2f}"
        assert row["pass_rate"] == f"{api.pass_rate:.2f}"
        assert row["completion_rate"] == f"{api.completion_rate:.2f}"
        assert row["unique_learners"] == str(api.unique_learners)
        assert row["data_state"] == api.data_state.value
        assert row["calculated_at"] == api.calculated_at.isoformat().replace("+00:00", "Z")

    async def test_sorting_matches_the_api(self, analytics_service, export_service, context):
        page = await analytics_service.list_question_analytics(
            AnalyticsFilters(),
            context,
            limit=100,
            sort_by=QuestionSortField.ACCURACY,
            direction=SortDirection.DESC,
        )
        rows = parse(
            (
                await export_service.export_questions(
                    AnalyticsFilters(),
                    context,
                    sort_by=QuestionSortField.ACCURACY,
                    direction=SortDirection.DESC,
                )
            ).render()
        )

        assert [row["question_id"] for row in rows] == [q.question_id for q in page.items]

    async def test_flag_state_is_carried_into_the_export(
        self, export_service, review_store, context
    ):
        await review_store.put_flag(make_flag("question-1", wrong_answer_rate=90.0))

        rows = parse((await export_service.export_flagged_questions(AnalyticsFilters(), context)).render())

        assert rows[0]["question_id"] == "question-1"
        assert rows[0]["is_flagged"] == "true"
        assert rows[0]["flag_status"] == "FLAGGED"
        assert rows[0]["flag_reason"] == "WRONG_ANSWER_RATE_EXCEEDED"


class TestEmptyStates:
    async def test_empty_question_export_has_a_header_and_no_rows(
        self, export_service, context
    ):
        export = await export_service.export_questions(
            AnalyticsFilters(course_id="nope"), context
        )

        assert export.row_count == 0
        assert export.data_state is DataState.NO_ATTEMPTS
        assert export.render().strip() == ",".join(QUESTION_COLUMNS)

    async def test_empty_overall_export_states_the_absence_explicitly(
        self, export_service, context
    ):
        export = await export_service.export_overall(AnalyticsFilters(course_id="nope"), context)
        row = parse(export.render())[0]

        assert export.data_state is DataState.NO_ATTEMPTS
        assert row["data_state"] == "NO_ATTEMPTS"
        assert row["average_score"] == ""
        assert row["attempt_volume"] == "0"


class TestLimits:
    async def test_export_beyond_the_row_limit_is_refused_not_truncated(
        self, repository, clock, context
    ):
        settings = make_settings(export_max_rows=1)
        service = CsvExportService(
            AnalyticsService(repository, settings, clock), settings, clock
        )

        with pytest.raises(DatasetTooLargeError) as exc:
            await service.export_questions(AnalyticsFilters(), context)

        assert exc.value.details["export_max_rows"] == 1
        assert "Narrow the filters" in exc.value.message
