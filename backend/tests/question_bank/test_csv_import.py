"""CSV bulk import (UC-02 §17–§20, §25).

Valid rows are imported, invalid rows are rejected with row-level reasons, and the counts add
up. A single bad row must never abort the run or roll back the good rows.
"""

from __future__ import annotations

import io

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.question_bank.csv_import.template import CSV_HEADERS, render_template_csv
from app.modules.question_bank.models import Question, QuestionImportError
from tests.factories import API

HEADER = ",".join(CSV_HEADERS)


def _row(**values: str) -> str:
    """Build one CSV line, quoting every cell so commas in text are safe."""
    cells = []
    for header in CSV_HEADERS:
        cell = values.get(header, "")
        cells.append('"' + cell.replace('"', '""') + '"')
    return ",".join(cells)


def _csv(*rows: str) -> str:
    return "\n".join([HEADER, *rows]) + "\n"


def _upload(client: TestClient, content: str, filename: str = "questions.csv") -> dict:
    response = client.post(
        f"{API}/imports",
        files={"file": (filename, io.BytesIO(content.encode("utf-8")), "text/csv")},
    )
    return {"status_code": response.status_code, "body": response.json()}


# ---------------------------------------------------------------------------
# Reusable rows, one per question type
# ---------------------------------------------------------------------------

SINGLE_CHOICE_ROW = _row(
    type="SINGLE_CHOICE",
    question_text="Which port does HTTPS use by default?",
    options="A:80|B:443|C:22|D:25",
    correct_answers="B",
    explanation="HTTPS runs over TLS on port 443 by default.",
    topics="Networking|Ports",
    points="1",
    difficulty="EASY",
)

TRUE_FALSE_ROW = _row(
    type="TRUE_FALSE",
    question_text="ICMP is a transport-layer protocol.",
    correct_answers="FALSE",
    explanation="ICMP is a network-layer protocol.",
    topics="Networking",
)

MULTI_SELECT_ROW = _row(
    type="MULTI_SELECT",
    question_text="Which of these are symmetric ciphers?",
    options="A:AES|B:ChaCha20|C:RSA|D:ECDSA",
    correct_answers="A|B",
    explanation="AES and ChaCha20 are symmetric; RSA and ECDSA are asymmetric.",
    topics="Cryptography",
    points="2",
    scoring_strategy="PARTIAL_CREDIT",
)

SCENARIO_ROW = _row(
    type="SCENARIO",
    question_text="What should the administrator check first?",
    scenario_text=(
        "Learners report that quiz submissions fail intermittently during the evening peak. "
        "Application logs show database connection timeouts, while CPU and memory on the "
        "application servers remain low throughout."
    ),
    options="A:The learners' browsers|B:The database connection pool size|C:The CSS bundle|D:The email server",
    correct_answers="B",
    explanation="Connection timeouts under load point at an exhausted connection pool.",
    topics="Troubleshooting|Databases",
    points="2",
)

DRAG_TO_ORDER_ROW = _row(
    type="DRAG_TO_ORDER",
    question_text="Order the stages of the TLS 1.3 handshake.",
    options="A:ClientHello|B:ServerHello|C:Finished|D:Application data",
    correct_order="A|B|C|D",
    explanation="ClientHello, ServerHello, Finished, then application data.",
    topics="Cryptography",
    points="4",
    scoring_strategy="PARTIAL_CREDIT",
)


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------


def test_template_download_covers_all_five_question_types(client: TestClient) -> None:
    response = client.get(f"{API}/imports/template")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]

    text = response.text
    assert text.splitlines()[0].split(",") == list(CSV_HEADERS)
    for question_type in (
        "SINGLE_CHOICE",
        "TRUE_FALSE",
        "MULTI_SELECT",
        "SCENARIO",
        "DRAG_TO_ORDER",
    ):
        assert question_type in text


def test_the_shipped_template_imports_cleanly(client: TestClient) -> None:
    """The documented template must itself be valid — otherwise the docs are wrong."""
    result = _upload(client, render_template_csv(), "template.csv")
    assert result["status_code"] == 201, result["body"]
    body = result["body"]
    assert body["totalRows"] == 5
    assert body["importedRows"] == 5
    assert body["rejectedRows"] == 0
    assert body["status"] == "COMPLETED"


def test_template_guide_describes_the_format(client: TestClient) -> None:
    body = client.get(f"{API}/imports/template/guide").json()
    assert body["listDelimiter"] == "|"
    assert set(body["requiredHeaders"]) <= set(body["headers"])
    assert len(body["fields"]) == len(CSV_HEADERS)


# ---------------------------------------------------------------------------
# Fully valid import
# ---------------------------------------------------------------------------


def test_valid_csv_imports_every_row_and_persists_them(
    client: TestClient, db: Session
) -> None:
    result = _upload(
        client,
        _csv(SINGLE_CHOICE_ROW, TRUE_FALSE_ROW, MULTI_SELECT_ROW, SCENARIO_ROW, DRAG_TO_ORDER_ROW),
    )
    assert result["status_code"] == 201, result["body"]
    body = result["body"]

    assert body["totalRows"] == 5
    assert body["importedRows"] == 5
    assert body["rejectedRows"] == 0
    assert body["rejected"] == []
    assert len(body["imported"]) == 5
    # Row numbers are spreadsheet rows: the header is row 1.
    assert [row["rowNumber"] for row in body["imported"]] == [2, 3, 4, 5, 6]

    assert int(db.execute(select(func.count(Question.id))).scalar_one()) == 5
    stored = db.execute(select(Question)).scalars().all()
    assert {q.type for q in stored} == {
        "SINGLE_CHOICE",
        "TRUE_FALSE",
        "MULTI_SELECT",
        "SCENARIO",
        "DRAG_TO_ORDER",
    }
    # Provenance is recorded on each imported question.
    assert all(q.import_id == body["id"] for q in stored)
    assert sorted(q.import_row_number or 0 for q in stored) == [2, 3, 4, 5, 6]


def test_imported_questions_are_complete_and_correct(client: TestClient) -> None:
    _upload(client, _csv(DRAG_TO_ORDER_ROW, SCENARIO_ROW, TRUE_FALSE_ROW))

    listing = client.get(f"{API}/questions", params={"pageSize": 50}).json()["items"]
    by_type = {item["type"]: item["id"] for item in listing}

    order_q = client.get(f"{API}/questions/{by_type['DRAG_TO_ORDER']}").json()
    assert order_q["correctOrder"] == ["A", "B", "C", "D"]
    assert order_q["correctLabels"] == []
    assert order_q["scoring"]["scoringStrategy"] == "PARTIAL_CREDIT"

    scenario_q = client.get(f"{API}/questions/{by_type['SCENARIO']}").json()
    assert scenario_q["scenarioText"].startswith("Learners report that quiz submissions fail")
    assert scenario_q["primaryLabel"] == "B"

    tf_q = client.get(f"{API}/questions/{by_type['TRUE_FALSE']}").json()
    # options was left blank, so the fixed TRUE/FALSE pair was generated.
    assert [o["label"] for o in tf_q["options"]] == ["TRUE", "FALSE"]
    assert tf_q["correctLabels"] == ["FALSE"]


def test_import_creates_topics_automatically(client: TestClient) -> None:
    _upload(client, _csv(SINGLE_CHOICE_ROW))
    topics = client.get(f"{API}/topics").json()
    assert {topic["name"] for topic in topics} == {"Networking", "Ports"}
    assert all(topic["questionCount"] == 1 for topic in topics)


def test_raw_text_csv_body_is_also_accepted(client: TestClient) -> None:
    response = client.post(
        f"{API}/imports",
        content=_csv(SINGLE_CHOICE_ROW).encode("utf-8"),
        headers={"Content-Type": "text/csv", "X-Filename": "scripted.csv"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["importedRows"] == 1
    assert response.json()["filename"] == "scripted.csv"


# ---------------------------------------------------------------------------
# Mixed valid / invalid — the core requirement
# ---------------------------------------------------------------------------


def test_mixed_csv_imports_valid_rows_and_reports_rejected_rows(
    client: TestClient, db: Session
) -> None:
    bad_type = _row(
        type="multiplechoicee",
        question_text="What is 2 + 2?",
        options="A:3|B:4",
        correct_answers="B",
        explanation="Basic arithmetic.",
        topics="Maths",
    )
    two_correct = _row(
        type="SINGLE_CHOICE",
        question_text="Which protocol resolves domain names?",
        options="A:DNS|B:DHCP|C:ARP|D:NTP",
        correct_answers="A|B",
        explanation="DNS resolves names to addresses.",
        topics="Networking",
    )
    unknown_option = _row(
        type="SINGLE_CHOICE",
        question_text="Which layer is the presentation layer?",
        options="A:Layer 5|B:Layer 6|C:Layer 7|D:Layer 4",
        correct_answers="Z",
        explanation="Layer 6 is the presentation layer.",
        topics="Networking",
    )

    result = _upload(
        client, _csv(SINGLE_CHOICE_ROW, bad_type, TRUE_FALSE_ROW, two_correct, unknown_option)
    )
    assert result["status_code"] == 201, result["body"]
    body = result["body"]

    assert body["totalRows"] == 5
    assert body["importedRows"] == 2
    assert body["rejectedRows"] == 3
    assert body["importedRows"] + body["rejectedRows"] == body["totalRows"]

    # The valid rows really were persisted.
    assert int(db.execute(select(func.count(Question.id))).scalar_one()) == 2

    rejected = {row["rowNumber"]: row for row in body["rejected"]}
    assert set(rejected) == {3, 5, 6}

    codes = {row["rowNumber"]: {e["code"] for e in row["errors"]} for row in body["rejected"]}
    assert "INVALID_QUESTION_TYPE" in codes[3]
    assert "SINGLE_CHOICE_REQUIRES_ONE_CORRECT" in codes[5]
    assert "CORRECT_ANSWER_REFERENCES_UNKNOWN_OPTION" in codes[6]


def test_rejection_messages_are_actionable(client: TestClient) -> None:
    """UC-02 §19 asks for row-level errors an admin can act on."""
    bad_type = _row(
        type="multiplechoicee",
        question_text="Q",
        options="A:1|B:2",
        correct_answers="A",
        explanation="E",
        topics="T",
    )
    unknown_option = _row(
        type="SINGLE_CHOICE",
        question_text="Which layer is the presentation layer?",
        options="A:Layer 5|B:Layer 6|C:Layer 7|D:Layer 4",
        correct_answers="Z",
        explanation="Layer 6.",
        topics="Networking",
    )
    body = _upload(client, _csv(bad_type, unknown_option))["body"]

    messages = {
        row["rowNumber"]: [error["message"] for error in row["errors"]]
        for row in body["rejected"]
    }
    assert any("multiplechoicee" in message for message in messages[2])
    assert any(
        'references an option that does not exist' in message for message in messages[3]
    )
    # The offending field is named, and the raw row is preserved for the admin.
    fields = {error["field"] for row in body["rejected"] for error in row["errors"]}
    assert "type" in fields or "correct_answers" in fields
    assert all(row["rawRow"] for row in body["rejected"])


def test_all_invalid_csv_imports_nothing_but_still_reports(
    client: TestClient, db: Session
) -> None:
    missing_text = _row(
        type="SINGLE_CHOICE",
        options="A:1|B:2|C:3|D:4",
        correct_answers="A",
        explanation="E",
        topics="T",
    )
    missing_answer = _row(
        type="SINGLE_CHOICE",
        question_text="Pick one",
        options="A:1|B:2|C:3|D:4",
        explanation="E",
        topics="T",
    )
    result = _upload(client, _csv(missing_text, missing_answer))

    assert result["status_code"] == 201
    body = result["body"]
    assert body["status"] == "COMPLETED"
    assert body["totalRows"] == 2
    assert body["importedRows"] == 0
    assert body["rejectedRows"] == 2
    assert int(db.execute(select(func.count(Question.id))).scalar_one()) == 0

    codes = {row["rowNumber"]: {e["code"] for e in row["errors"]} for row in body["rejected"]}
    assert "QUESTION_TEXT_REQUIRED" in codes[2]
    assert "CORRECT_ANSWER_REQUIRED" in codes[3]


# ---------------------------------------------------------------------------
# Per-type CSV validation
# ---------------------------------------------------------------------------


def test_scenario_row_without_scenario_text_is_rejected(client: TestClient) -> None:
    row = _row(
        type="SCENARIO",
        question_text="What now?",
        options="A:One|B:Two",
        correct_answers="A",
        explanation="Because.",
        topics="Troubleshooting",
    )
    body = _upload(client, _csv(row))["body"]
    assert body["rejectedRows"] == 1
    codes = {error["code"] for error in body["rejected"][0]["errors"]}
    assert "SCENARIO_TEXT_REQUIRED" in codes


def test_scenario_row_with_two_correct_answers_is_rejected(client: TestClient) -> None:
    row = _row(
        type="SCENARIO",
        question_text="What should be checked first?",
        scenario_text="A long enough vignette describing an intermittent failure at peak time.",
        options="A:One|B:Two|C:Three",
        correct_answers="A|B",
        explanation="Because.",
        topics="Troubleshooting",
    )
    body = _upload(client, _csv(row))["body"]
    codes = {error["code"] for error in body["rejected"][0]["errors"]}
    assert "SCENARIO_REQUIRES_SINGLE_PRIMARY_ANSWER" in codes


def test_drag_to_order_row_with_incomplete_order_is_rejected(client: TestClient) -> None:
    row = _row(
        type="DRAG_TO_ORDER",
        question_text="Order the stages.",
        options="A:First|B:Second|C:Third",
        correct_order="A|B",
        explanation="Sequential.",
        topics="Process",
    )
    body = _upload(client, _csv(row))["body"]
    codes = {error["code"] for error in body["rejected"][0]["errors"]}
    assert "DRAG_TO_ORDER_MISSING_POSITIONS" in codes


def test_drag_to_order_row_referencing_an_unknown_item_is_rejected(client: TestClient) -> None:
    row = _row(
        type="DRAG_TO_ORDER",
        question_text="Order the stages.",
        options="A:First|B:Second",
        correct_order="A|B|Z",
        explanation="Sequential.",
        topics="Process",
    )
    body = _upload(client, _csv(row))["body"]
    codes = {error["code"] for error in body["rejected"][0]["errors"]}
    assert "CORRECT_ORDER_REFERENCES_UNKNOWN_OPTION" in codes


def test_drag_to_order_row_using_correct_answers_is_rejected(client: TestClient) -> None:
    row = _row(
        type="DRAG_TO_ORDER",
        question_text="Order the stages.",
        options="A:First|B:Second",
        correct_answers="A",
        correct_order="A|B",
        explanation="Sequential.",
        topics="Process",
    )
    body = _upload(client, _csv(row))["body"]
    codes = {error["code"] for error in body["rejected"][0]["errors"]}
    assert "CORRECT_ANSWERS_NOT_ALLOWED" in codes


def test_correct_order_on_a_choice_type_is_rejected(client: TestClient) -> None:
    row = _row(
        type="SINGLE_CHOICE",
        question_text="Pick one.",
        options="A:1|B:2|C:3|D:4",
        correct_answers="A",
        correct_order="A|B|C|D",
        explanation="E",
        topics="T",
    )
    body = _upload(client, _csv(row))["body"]
    codes = {error["code"] for error in body["rejected"][0]["errors"]}
    assert "CORRECT_ORDER_NOT_ALLOWED" in codes


def test_malformed_option_cell_is_rejected(client: TestClient) -> None:
    row = _row(
        type="SINGLE_CHOICE",
        question_text="Pick one.",
        options="A:1|no-colon-here|C:3|D:4",
        correct_answers="A",
        explanation="E",
        topics="T",
    )
    body = _upload(client, _csv(row))["body"]
    codes = {error["code"] for error in body["rejected"][0]["errors"]}
    assert "OPTION_FORMAT_INVALID" in codes


def test_invalid_multi_select_scoring_is_rejected(client: TestClient) -> None:
    row = _row(
        type="MULTI_SELECT",
        question_text="Which is symmetric?",
        options="A:AES|B:RSA|C:ECDSA",
        correct_answers="A",
        explanation="AES only.",
        topics="Cryptography",
        points="3",
        scoring_strategy="PARTIAL_CREDIT_WITH_PENALTY",
    )
    body = _upload(client, _csv(row))["body"]
    codes = {error["code"] for error in body["rejected"][0]["errors"]}
    assert {"PENALTY_REQUIRED_FOR_STRATEGY", "PARTIAL_CREDIT_REQUIRES_MULTIPLE_CORRECT"} & codes


def test_invalid_metadata_is_rejected(client: TestClient) -> None:
    row = _row(
        type="SINGLE_CHOICE",
        question_text="Pick one.",
        options="A:1|B:2|C:3|D:4",
        correct_answers="A",
        explanation="E",
        topics="T",
        points="not-a-number",
        difficulty="IMPOSSIBLE",
    )
    body = _upload(client, _csv(row))["body"]
    codes = {error["code"] for error in body["rejected"][0]["errors"]}
    assert "INVALID_POINTS" in codes
    assert "INVALID_DIFFICULTY" in codes


# ---------------------------------------------------------------------------
# File-level failures
# ---------------------------------------------------------------------------


def test_missing_required_headers_fails_the_whole_file(client: TestClient, db: Session) -> None:
    content = "type,question_text\nSINGLE_CHOICE,Hello\n"
    result = _upload(client, content)

    assert result["status_code"] == 400
    error = result["body"]["error"]
    assert error["code"] in {"BAD_REQUEST", "MISSING_HEADERS"}
    assert "options" in error["message"]
    assert int(db.execute(select(func.count(Question.id))).scalar_one()) == 0


def test_a_failed_file_is_recorded_as_failed(client: TestClient) -> None:
    _upload(client, "nothing,useful\n1,2\n")

    runs = client.get(f"{API}/imports").json()["items"]
    assert len(runs) == 1
    assert runs[0]["status"] == "FAILED"
    assert runs[0]["importedRows"] == 0
    assert runs[0]["errorMessage"]


def test_header_only_file_is_rejected(client: TestClient) -> None:
    result = _upload(client, HEADER + "\n")
    assert result["status_code"] == 400
    assert "no data rows" in result["body"]["error"]["message"].lower()


def test_empty_file_is_rejected(client: TestClient) -> None:
    result = _upload(client, "")
    assert result["status_code"] == 400


def test_upload_without_a_file_is_rejected(client: TestClient) -> None:
    response = client.post(f"{API}/imports")
    assert response.status_code == 400
    assert response.json()["error"]["code"] in {"BAD_REQUEST", "FILE_REQUIRED"}


def test_duplicate_headers_are_rejected(client: TestClient) -> None:
    content = HEADER + ",type\n" + SINGLE_CHOICE_ROW + ',"SINGLE_CHOICE"\n'
    result = _upload(client, content)
    assert result["status_code"] == 400
    assert "duplicate" in result["body"]["error"]["message"].lower()


# ---------------------------------------------------------------------------
# Data integrity
# ---------------------------------------------------------------------------


def test_duplicate_rows_within_one_file_are_rejected_once(client: TestClient) -> None:
    body = _upload(client, _csv(SINGLE_CHOICE_ROW, SINGLE_CHOICE_ROW))["body"]
    assert body["importedRows"] == 1
    assert body["rejectedRows"] == 1
    codes = {error["code"] for error in body["rejected"][0]["errors"]}
    assert "DUPLICATE_ROW_IN_FILE" in codes


def test_reimporting_an_existing_question_is_rejected(client: TestClient) -> None:
    first = _upload(client, _csv(SINGLE_CHOICE_ROW))["body"]
    assert first["importedRows"] == 1

    second = _upload(client, _csv(SINGLE_CHOICE_ROW))["body"]
    assert second["importedRows"] == 0
    assert second["rejectedRows"] == 1
    codes = {error["code"] for error in second["rejected"][0]["errors"]}
    assert "DUPLICATE_QUESTION" in codes


def test_duplicate_external_ref_within_a_file_is_rejected(client: TestClient) -> None:
    a = _row(
        type="SINGLE_CHOICE",
        question_text="First question?",
        options="A:1|B:2|C:3|D:4",
        correct_answers="A",
        explanation="E",
        topics="T",
        external_ref="SRC-1",
    )
    b = _row(
        type="SINGLE_CHOICE",
        question_text="Second, different question?",
        options="A:1|B:2|C:3|D:4",
        correct_answers="B",
        explanation="E",
        topics="T",
        external_ref="SRC-1",
    )
    body = _upload(client, _csv(a, b))["body"]
    assert body["importedRows"] == 1
    codes = {error["code"] for error in body["rejected"][0]["errors"]}
    assert "DUPLICATE_EXTERNAL_REF_IN_FILE" in codes


def test_quoted_fields_with_commas_and_newlines_survive(client: TestClient) -> None:
    content = (
        HEADER
        + "\n"
        + 'SCENARIO,"What is the cause?","A learner reports failures at peak, and the logs show\n'
        'timeouts, retries, and connection resets across the evening window.",'
        '"A:Browser cache|B:Connection pool exhaustion|C:CSS bundle",B,,'
        '"Timeouts under load, not a client problem.",Troubleshooting,2,,,,\n'
    )
    body = _upload(client, content)["body"]
    assert body["importedRows"] == 1, body

    question_id = body["imported"][0]["questionId"]
    stored = client.get(f"{API}/questions/{question_id}").json()
    assert "peak, and the logs show" in stored["scenarioText"]
    assert "\n" in stored["scenarioText"]
    assert stored["explanation"] == "Timeouts under load, not a client problem."


def test_blank_lines_are_skipped_not_reported(client: TestClient) -> None:
    content = HEADER + "\n\n" + SINGLE_CHOICE_ROW + "\n\n" + TRUE_FALSE_ROW + "\n\n"
    body = _upload(client, content)["body"]
    assert body["totalRows"] == 2
    assert body["importedRows"] == 2
    assert body["rejectedRows"] == 0


def test_unknown_columns_are_ignored(client: TestClient) -> None:
    content = (
        HEADER + ",author_notes\n" + SINGLE_CHOICE_ROW + ',"ignore me"\n'
    )
    body = _upload(client, content)["body"]
    assert body["importedRows"] == 1


def test_header_aliases_are_accepted(client: TestClient) -> None:
    content = (
        "Question Type,Question,Choices,Correct Answer,Rationale,Tags\n"
        'SINGLE_CHOICE,"Which port does SSH use?","A:21|B:22|C:23|D:25",B,'
        '"SSH listens on port 22.",Networking\n'
    )
    body = _upload(client, content)["body"]
    assert body["importedRows"] == 1, body


def test_utf8_bom_is_tolerated(client: TestClient) -> None:
    content = "﻿" + _csv(SINGLE_CHOICE_ROW)
    body = _upload(client, content)["body"]
    assert body["importedRows"] == 1, body


def test_semicolon_delimited_export_is_tolerated(client: TestClient) -> None:
    content = (
        ";".join(CSV_HEADERS)
        + "\n"
        + ";".join(
            [
                "SINGLE_CHOICE",
                "Which port does SSH use?",
                "",
                "A:21|B:22|C:23|D:25",
                "B",
                "",
                "SSH listens on port 22.",
                "Networking",
                "1",
                "",
                "",
                "",
                "",
            ]
        )
        + "\n"
    )
    body = _upload(client, content)["body"]
    assert body["importedRows"] == 1, body


# ---------------------------------------------------------------------------
# Import reporting
# ---------------------------------------------------------------------------


def test_row_errors_are_persisted_and_re_readable(client: TestClient, db: Session) -> None:
    bad = _row(
        type="SINGLE_CHOICE",
        question_text="Missing answer",
        options="A:1|B:2|C:3|D:4",
        explanation="E",
        topics="T",
    )
    body = _upload(client, _csv(SINGLE_CHOICE_ROW, bad))["body"]
    import_id = body["id"]

    stored_errors = db.execute(
        select(QuestionImportError).where(QuestionImportError.import_id == import_id)
    ).scalars().all()
    assert stored_errors
    assert all(error.row_number == 3 for error in stored_errors)

    # The same report can be fetched again later.
    reread = client.get(f"{API}/imports/{import_id}").json()
    assert reread["totalRows"] == 2
    assert reread["importedRows"] == 1
    assert reread["rejectedRows"] == 1
    assert [row["rowNumber"] for row in reread["rejected"]] == [3]
    assert [row["rowNumber"] for row in reread["imported"]] == [2]
    assert reread["rejected"][0]["errors"]


def test_import_history_is_listed_most_recent_first(client: TestClient) -> None:
    _upload(client, _csv(SINGLE_CHOICE_ROW), "first.csv")
    _upload(client, _csv(TRUE_FALSE_ROW), "second.csv")

    body = client.get(f"{API}/imports").json()
    assert body["total"] == 2
    assert [run["filename"] for run in body["items"]] == ["second.csv", "first.csv"]


def test_unknown_import_returns_404(client: TestClient) -> None:
    response = client.get(f"{API}/imports/nope")
    assert response.status_code == 404


def test_imported_questions_are_immediately_deliverable(client: TestClient) -> None:
    _upload(client, _csv(SINGLE_CHOICE_ROW, TRUE_FALSE_ROW))
    pool = client.get(f"{API}/delivery/pool", params={"limit": 50}).json()
    assert pool["totalAvailable"] == 2
