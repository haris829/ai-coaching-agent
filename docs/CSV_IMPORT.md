# CSV bulk import format

One format covers all five question types. Type-specific fields have their own columns and are
required or forbidden per type, so a row is never ambiguous.

Download a template with one worked example per type:

```
GET /api/question-bank/imports/template
```

or from the **CSV import → Download template** button in the admin UI.

---

## Import semantics

```
Select CSV → Upload → Parse → Validate every row → Import valid rows → Report rejected rows
```

**Row-level, not atomic.** Valid rows are persisted even when other rows fail — this is what UC-02
§17 requires. One bad row never aborts the run and never rolls back the good rows.

A **whole-file** problem is different: nothing is imported and the run is recorded as `FAILED`.
Whole-file problems are an unreadable file, no header row, a missing required column, duplicate
column names, no data rows, or exceeding the size/row ceiling.

Every rejection is stored in `qb_question_import_errors` with its row number, field, code and
message, so the report can be re-read later via `GET /api/question-bank/imports/{id}`.

> **Row numbers are spreadsheet row numbers.** The header is row 1, so the first data row is row 2 —
> the number in the report matches what you see in Excel.

---

## Columns

| Column                  | Required                | Meaning |
| ----------------------- | ----------------------- | ------- |
| `type`                  | always                  | `SINGLE_CHOICE` \| `TRUE_FALSE` \| `MULTI_SELECT` \| `SCENARIO` \| `DRAG_TO_ORDER` |
| `question_text`         | always                  | The question asked |
| `scenario_text`         | `SCENARIO` only         | The vignette shown before the question. **Must be empty for every other type.** Minimum 20 characters |
| `options`               | all except `TRUE_FALSE` | Pipe-separated `LABEL:Text` pairs — `A:Paris\|B:London\|C:Rome\|D:Madrid` |
| `correct_answers`       | all except `DRAG_TO_ORDER` | Pipe-separated correct option **labels**. **Must be empty for `DRAG_TO_ORDER`** |
| `correct_order`         | `DRAG_TO_ORDER` only    | Pipe-separated labels in the **correct sequence** — `A\|B\|C\|D`. **Must be empty for every other type** |
| `explanation`           | always                  | Why the answer is right |
| `topics`                | always                  | Pipe-separated topic names — `Networking\|OSI Model`. Unknown topics are created automatically |
| `points`                | optional                | Marks for the question. Default `1` |
| `scoring_strategy`      | optional                | `ALL_OR_NOTHING` (default) \| `PARTIAL_CREDIT` \| `PARTIAL_CREDIT_WITH_PENALTY` |
| `penalty_per_incorrect` | conditional             | Required (`> 0`) when `scoring_strategy` is `PARTIAL_CREDIT_WITH_PENALTY`; must be empty/`0` otherwise, and never greater than `points` |
| `difficulty`            | optional                | `EASY` \| `MEDIUM` \| `HARD` |
| `external_ref`          | optional                | Your own unique key for the row. Prevents importing the same source question twice |

Header order does not matter. Unknown columns are ignored. Header matching is case-insensitive and
tolerates spaces or hyphens instead of underscores, plus common aliases
(`question` → `question_text`, `choices` → `options`, `tags` → `topics`, `rationale` → `explanation`,
`marks` → `points`, and others).

---

## Delimiters

* **Fields** are comma-separated. Use standard CSV double-quoting for any value containing a comma,
  a quote or a newline: `"He said ""yes"", then left"`. Semicolon- and tab-separated exports are
  detected automatically.
* **`|`** separates repeated values inside one field.
* **The first `:`** in an option splits its label from its text, so option text may itself contain
  colons: `A:Rule: always drain the line` → label `A`, text `Rule: always drain the line`.
* **Option labels** must match `[A-Za-z0-9][A-Za-z0-9_.-]*` — no `|` or `:`.
* UTF-8 is expected; a byte-order mark is tolerated, and CP1252/Latin-1 files are decoded as a
  fallback.

---

## How each type fills the columns

### Single choice — exactly four options, exactly one correct

```csv
type,question_text,options,correct_answers,explanation,topics,points,difficulty
SINGLE_CHOICE,"Which OSI layer routes packets between networks?","A:Layer 2 - Data Link|B:Layer 3 - Network|C:Layer 4 - Transport|D:Layer 7 - Application",B,"Layer 3 handles logical addressing and routing.",Networking|OSI Model,1,EASY
```

### True / False — `options` may be left blank

The `TRUE`/`FALSE` pair is implied. Put `TRUE` or `FALSE` in `correct_answers`.

```csv
type,question_text,options,correct_answers,explanation,topics
TRUE_FALSE,"TCP guarantees that data arrives in the order it was sent.",,TRUE,"TCP sequences segments and reassembles them in order.",Transport Protocols
```

If you do supply `options`, they must be exactly two, labelled `TRUE` and `FALSE`.

### Multi-select — at least three options, at least one correct, at least one distractor

List every correct label in `correct_answers`, pipe-separated.

```csv
type,question_text,options,correct_answers,explanation,topics,points,scoring_strategy,penalty_per_incorrect
MULTI_SELECT,"Which of the following are private IPv4 ranges?","A:10.0.0.0/8|B:172.16.0.0/12|C:192.168.0.0/16|D:8.8.8.0/24|E:203.0.113.0/24",A|B|C,"RFC 1918 reserves 10/8, 172.16/12 and 192.168/16.",Networking|IP Addressing,3,PARTIAL_CREDIT_WITH_PENALTY,0.5
```

`PARTIAL_CREDIT` and `PARTIAL_CREDIT_WITH_PENALTY` need at least two correct answers, because marks
cannot otherwise be divided.

### Scenario — vignette in `scenario_text`, exactly one label in `correct_answers`

For a scenario row, `correct_answers` must contain **exactly one** label; it becomes the question's
**primary answer**.

```csv
type,question_text,scenario_text,options,correct_answers,explanation,topics,points
SCENARIO,"What is the most likely cause of the outage?","A learner reports the course portal is unreachable from the office network but loads over mobile data. Other sites work normally from the office, the status page reports all systems operational, and a colleague at home can reach it.","A:The portal is down|B:An office DNS or firewall rule is blocking the portal|C:The learner's account is locked|D:The portal's TLS certificate has expired",B,"The portal is reachable elsewhere, so the fault is local to the office network.",Troubleshooting|Networking,2
```

### Drag-to-order — items in `options`, answer key in `correct_order`, `correct_answers` empty

`correct_order` must list **every** item label exactly once. It is the answer key, **not** the
presentation order — quiz delivery shuffles the display, and the correct sequence is unaffected.

```csv
type,question_text,options,correct_answers,correct_order,explanation,topics,points,scoring_strategy
DRAG_TO_ORDER,"Place the steps of the TCP handshake and data transfer in order.","A:Client sends SYN|B:Server replies SYN-ACK|C:Client sends ACK|D:Data transfer begins",,A|B|C|D,"SYN, SYN-ACK, ACK completes the handshake before data flows.",Transport Protocols,4,PARTIAL_CREDIT
```

---

## Validation

Each row is checked for CSV-shaped problems first (malformed option cell, an answer referencing a
label that is not in `options`, a column populated for the wrong type), then handed to the same
authoritative validator the JSON API uses. A row rejected by the API is rejected identically here.

### Representative row-level errors

| Code | Example message |
| ---- | --------------- |
| `INVALID_QUESTION_TYPE` | `Invalid question type: "multiplechoicee". Expected one of SINGLE_CHOICE, TRUE_FALSE, …` |
| `QUESTION_TEXT_REQUIRED` | `Question text is required.` |
| `SINGLE_CHOICE_REQUIRES_ONE_CORRECT` | `Single-choice question requires exactly one correct answer (received 2).` |
| `SINGLE_CHOICE_REQUIRES_FOUR_OPTIONS` | `Single-choice questions require exactly 4 answer options (received 3).` |
| `CORRECT_ANSWER_REFERENCES_UNKNOWN_OPTION` | `Correct answer "Z" references an option that does not exist. Available option labels: A, B, C, D.` |
| `OPTION_FORMAT_INVALID` | `Option 2 ("no-colon-here") is not in LABEL:Text format. Use for example A:Paris\|B:London.` |
| `SCENARIO_TEXT_REQUIRED` | `Scenario questions require a scenario / vignette before the question.` |
| `SCENARIO_REQUIRES_SINGLE_PRIMARY_ANSWER` | `A SCENARIO row must give exactly one label in correct_answers — it is the primary answer (received 2: A, B).` |
| `SCENARIO_TEXT_NOT_ALLOWED` | `scenario_text must be empty for SINGLE_CHOICE questions; it is only used by SCENARIO.` |
| `CORRECT_ORDER_REQUIRED` | `correct_order is required for DRAG_TO_ORDER questions. Give the option labels in the correct sequence, e.g. A\|B\|C\|D.` |
| `DRAG_TO_ORDER_MISSING_POSITIONS` | `correct_order must list every item exactly once. Missing: C.` |
| `CORRECT_ORDER_REFERENCES_UNKNOWN_OPTION` | `correct_order references item "Z", which is not one of the options. Available item labels: A, B.` |
| `CORRECT_ANSWERS_NOT_ALLOWED` | `correct_answers must be empty for DRAG_TO_ORDER questions. Use correct_order …` |
| `CORRECT_ORDER_NOT_ALLOWED` | `correct_order must be empty for SINGLE_CHOICE questions; it is only used by DRAG_TO_ORDER.` |
| `PENALTY_REQUIRED_FOR_STRATEGY` | `Scoring strategy PARTIAL_CREDIT_WITH_PENALTY requires a penaltyPerIncorrect greater than zero.` |
| `PARTIAL_CREDIT_REQUIRES_MULTIPLE_CORRECT` | `Scoring strategy PARTIAL_CREDIT requires at least two correct answers … (received 1).` |
| `INVALID_POINTS` / `INVALID_DIFFICULTY` | `Scoring points must be a number (received "abc").` |
| `DUPLICATE_ROW_IN_FILE` | `This question duplicates row 2 in the same file (same type, text and answer key).` |
| `DUPLICATE_EXTERNAL_REF_IN_FILE` | `external_ref "SRC-1" is already used by row 2 in the same file.` |
| `DUPLICATE_QUESTION` | `Duplicate of Q-000007: same type, text and answer key.` |
| `MALFORMED_CSV_ROW` / `ROW_HAS_EXTRA_COLUMNS` | `The row has 15 values but the header defines 13 columns. Check for an unescaped comma or quote.` |

### Whole-file errors (nothing imported, run marked `FAILED`)

`EMPTY_FILE`, `MISSING_HEADER_ROW`, `MISSING_HEADERS`, `DUPLICATE_HEADERS`, `NO_DATA_ROWS`,
`TOO_MANY_ROWS`, `MALFORMED_CSV`, `UNREADABLE_FILE`.

---

## Data integrity

* Duplicate rows **within one file** are detected by content hash — the first is imported, later
  copies are rejected.
* Duplicate `external_ref` within one file is rejected.
* A row duplicating a question already in the bank is rejected with `DUPLICATE_QUESTION`. Retired
  questions are excluded from that check, so a replacement can be authored.
* Blank lines are skipped silently rather than reported as errors.
* A database failure on one row rejects that row and the run continues.

---

## Import result

```json
{
  "id": "…",
  "filename": "questions.csv",
  "status": "COMPLETED",
  "totalRows": 5,
  "importedRows": 3,
  "rejectedRows": 2,
  "errorMessage": null,
  "imported": [
    { "rowNumber": 2, "questionId": "…", "reference": "Q-000001", "questionText": "…" }
  ],
  "rejected": [
    {
      "rowNumber": 4,
      "errors": [
        {
          "rowNumber": 4,
          "field": "correct_answers",
          "code": "CORRECT_ANSWER_REFERENCES_UNKNOWN_OPTION",
          "message": "Correct answer \"Z\" references an option that does not exist. Available option labels: A, B, C, D."
        }
      ],
      "rawRow": { "type": "SINGLE_CHOICE", "question_text": "…" }
    }
  ]
}
```

`importedRows + rejectedRows == totalRows` always holds for a `COMPLETED` run.
