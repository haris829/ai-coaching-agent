"""The documented CSV bulk-import format (UC-02 §18).

ONE format covers all five question types. Type-specific fields live in dedicated columns and
are required/forbidden per type, so a row is never ambiguous.

Columns
-------
=====================  ========  ==================================================================
Column                 Required  Meaning
=====================  ========  ==================================================================
type                   always    SINGLE_CHOICE | TRUE_FALSE | MULTI_SELECT | SCENARIO |
                                 DRAG_TO_ORDER
question_text          always    The question asked.
scenario_text          SCENARIO  The vignette shown before the question. Must be EMPTY for every
                                 other type.
options                see note  Pipe-separated ``LABEL:Text`` pairs, e.g.
                                 ``A:Paris|B:London|C:Rome|D:Madrid``.
                                 Optional for TRUE_FALSE (the TRUE/FALSE pair is implied).
correct_answers        not D2O   Pipe-separated option LABELS that are correct, e.g. ``A`` or
                                 ``A|C``. For TRUE_FALSE use ``TRUE`` or ``FALSE``. For SCENARIO
                                 give exactly one label — it becomes the primary answer. Must be
                                 EMPTY for DRAG_TO_ORDER.
correct_order          D2O only  Pipe-separated option LABELS in the CORRECT sequence, e.g.
                                 ``A|B|C|D``. Must be EMPTY for every other type. This is the
                                 answer key, NOT the presentation order.
explanation            always    Why the answer is right.
topics                 always    Pipe-separated topic names, e.g. ``Networking|OSI Model``.
                                 Unknown topics are created automatically.
points                 optional  Marks for the question. Default 1.
scoring_strategy       optional  ALL_OR_NOTHING (default) | PARTIAL_CREDIT |
                                 PARTIAL_CREDIT_WITH_PENALTY. PARTIAL_CREDIT* is only valid for
                                 MULTI_SELECT and DRAG_TO_ORDER (PARTIAL_CREDIT only).
penalty_per_incorrect  optional  Marks deducted per incorrect selection. Required (> 0) when
                                 scoring_strategy is PARTIAL_CREDIT_WITH_PENALTY, and must be
                                 empty/0 otherwise.
difficulty             optional  EASY | MEDIUM | HARD.
external_ref           optional  Your own stable key for the row. Must be unique; lets you spot
                                 and avoid re-importing the same source question twice.
=====================  ========  ==================================================================

Delimiters
----------
* Fields are separated by commas; use standard CSV double-quoting for values containing a
  comma, a quote or a newline (``"He said ""yes"", then left"``).
* ``|`` separates repeated values inside one field.
* The FIRST ``:`` in an option splits its label from its text, so option text may itself
  contain colons (``A:Rule: always drain the line`` → label ``A``, text
  ``Rule: always drain the line``).
* Option labels must match ``[A-Za-z0-9][A-Za-z0-9_.-]*`` — no ``|`` or ``:``.

Notes
-----
* Header order does not matter; unknown columns are ignored; header matching is
  case-insensitive and tolerates spaces or hyphens instead of underscores.
* Every row is validated independently. Valid rows are imported even when other rows fail.
* Row numbers in the import report are spreadsheet row numbers — the header is row 1, so the
  first data row is row 2.
"""

from __future__ import annotations

import csv
import io

# ---------------------------------------------------------------------------
# Canonical header
# ---------------------------------------------------------------------------

REQUIRED_HEADERS: tuple[str, ...] = (
    "type",
    "question_text",
    "options",
    "correct_answers",
    "explanation",
    "topics",
)

OPTIONAL_HEADERS: tuple[str, ...] = (
    "scenario_text",
    "correct_order",
    "points",
    "scoring_strategy",
    "penalty_per_incorrect",
    "difficulty",
    "external_ref",
)

CSV_HEADERS: tuple[str, ...] = (
    "type",
    "question_text",
    "scenario_text",
    "options",
    "correct_answers",
    "correct_order",
    "explanation",
    "topics",
    "points",
    "scoring_strategy",
    "penalty_per_incorrect",
    "difficulty",
    "external_ref",
)

#: Accepted spellings for each canonical column (case/spacing/hyphen tolerant).
HEADER_ALIASES: dict[str, str] = {
    "type": "type",
    "question_type": "type",
    "questiontype": "type",
    "question_text": "question_text",
    "question": "question_text",
    "questiontext": "question_text",
    "scenario_text": "scenario_text",
    "scenario": "scenario_text",
    "vignette": "scenario_text",
    "scenariotext": "scenario_text",
    "options": "options",
    "answer_options": "options",
    "choices": "options",
    "items": "options",
    "correct_answers": "correct_answers",
    "correct_answer": "correct_answers",
    "correctanswers": "correct_answers",
    "answer": "correct_answers",
    "primary_answer": "correct_answers",
    "correct_order": "correct_order",
    "correctorder": "correct_order",
    "order": "correct_order",
    "explanation": "explanation",
    "rationale": "explanation",
    "topics": "topics",
    "topic": "topics",
    "tags": "topics",
    "points": "points",
    "marks": "points",
    "score": "points",
    "scoring_strategy": "scoring_strategy",
    "scoringstrategy": "scoring_strategy",
    "strategy": "scoring_strategy",
    "penalty_per_incorrect": "penalty_per_incorrect",
    "penalty": "penalty_per_incorrect",
    "difficulty": "difficulty",
    "external_ref": "external_ref",
    "externalref": "external_ref",
    "external_id": "external_ref",
    "reference": "external_ref",
}


def normalise_header(raw: str) -> str | None:
    """Map a raw header cell to its canonical name, or ``None`` if unrecognised."""
    if raw is None:
        return None
    key = raw.strip().lower().lstrip("﻿")
    key = key.replace(" ", "_").replace("-", "_")
    while "__" in key:
        key = key.replace("__", "_")
    return HEADER_ALIASES.get(key)


# ---------------------------------------------------------------------------
# Example template
# ---------------------------------------------------------------------------

#: One fully worked example per question type. Downloadable from
#: ``GET /api/question-bank/imports/template``.
TEMPLATE_ROWS: list[dict[str, str]] = [
    {
        "type": "SINGLE_CHOICE",
        "question_text": (
            "Which layer of the OSI model is responsible for routing packets between networks?"
        ),
        "scenario_text": "",
        "options": (
            "A:Layer 2 - Data Link|B:Layer 3 - Network|"
            "C:Layer 4 - Transport|D:Layer 7 - Application"
        ),
        "correct_answers": "B",
        "correct_order": "",
        "explanation": (
            "Layer 3, the Network layer, handles logical addressing and routing between networks."
        ),
        "topics": "Networking|OSI Model",
        "points": "1",
        "scoring_strategy": "ALL_OR_NOTHING",
        "penalty_per_incorrect": "",
        "difficulty": "EASY",
        "external_ref": "TEMPLATE-SC-001",
    },
    {
        "type": "TRUE_FALSE",
        "question_text": "TCP guarantees that data arrives in the order it was sent.",
        "scenario_text": "",
        # options may be left blank for TRUE_FALSE — the TRUE/FALSE pair is implied.
        "options": "",
        "correct_answers": "TRUE",
        "correct_order": "",
        "explanation": "TCP sequences segments and reassembles them in order before delivery.",
        "topics": "Networking|Transport Protocols",
        "points": "1",
        "scoring_strategy": "ALL_OR_NOTHING",
        "penalty_per_incorrect": "",
        "difficulty": "EASY",
        "external_ref": "TEMPLATE-TF-001",
    },
    {
        "type": "MULTI_SELECT",
        "question_text": "Which of the following are private IPv4 address ranges?",
        "scenario_text": "",
        "options": "A:10.0.0.0/8|B:172.16.0.0/12|C:192.168.0.0/16|D:8.8.8.0/24|E:203.0.113.0/24",
        "correct_answers": "A|B|C",
        "correct_order": "",
        "explanation": "RFC 1918 reserves 10/8, 172.16/12 and 192.168/16 for private use.",
        "topics": "Networking|IP Addressing",
        "points": "3",
        "scoring_strategy": "PARTIAL_CREDIT_WITH_PENALTY",
        "penalty_per_incorrect": "0.5",
        "difficulty": "MEDIUM",
        "external_ref": "TEMPLATE-MS-001",
    },
    {
        "type": "SCENARIO",
        "question_text": "What is the most likely cause of the outage?",
        "scenario_text": (
            "A learner reports that the course portal is unreachable from the office network "
            "but loads correctly over mobile data. Other sites work normally from the office. "
            "The portal's status page reports all systems operational, and a colleague working "
            "from home can also reach it without any problem."
        ),
        "options": (
            "A:The portal is down|"
            "B:An office DNS or firewall rule is blocking the portal|"
            "C:The learner's account is locked|"
            "D:The portal's TLS certificate has expired"
        ),
        "correct_answers": "B",
        "correct_order": "",
        "explanation": (
            "The portal is reachable from other networks, so the fault is local to the office "
            "network — most likely DNS resolution or a firewall rule."
        ),
        "topics": "Troubleshooting|Networking",
        "points": "2",
        "scoring_strategy": "ALL_OR_NOTHING",
        "penalty_per_incorrect": "",
        "difficulty": "MEDIUM",
        "external_ref": "TEMPLATE-SN-001",
    },
    {
        "type": "DRAG_TO_ORDER",
        "question_text": (
            "Place the four steps of the TCP three-way handshake and data transfer "
            "in the correct order."
        ),
        "scenario_text": "",
        "options": (
            "A:Client sends SYN|B:Server replies SYN-ACK|"
            "C:Client sends ACK|D:Data transfer begins"
        ),
        # correct_answers MUST be empty for DRAG_TO_ORDER.
        "correct_answers": "",
        # correct_order is the ANSWER KEY, not the presentation order.
        "correct_order": "A|B|C|D",
        "explanation": (
            "SYN, then SYN-ACK, then ACK completes the handshake; only then does data flow."
        ),
        "topics": "Networking|Transport Protocols",
        "points": "4",
        "scoring_strategy": "PARTIAL_CREDIT",
        "penalty_per_incorrect": "",
        "difficulty": "HARD",
        "external_ref": "TEMPLATE-DO-001",
    },
]


def render_template_csv() -> str:
    """Render the downloadable template: canonical header + one example per question type."""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(CSV_HEADERS), lineterminator="\r\n")
    writer.writeheader()
    for row in TEMPLATE_ROWS:
        writer.writerow({header: row.get(header, "") for header in CSV_HEADERS})
    return buffer.getvalue()


#: Short, human-readable field guide returned alongside the template by the API, so the admin
#: UI can render instructions without duplicating this knowledge in the frontend.
FIELD_GUIDE: list[dict[str, str]] = [
    {
        "column": "type",
        "required": "always",
        "description": "SINGLE_CHOICE | TRUE_FALSE | MULTI_SELECT | SCENARIO | DRAG_TO_ORDER",
    },
    {"column": "question_text", "required": "always", "description": "The question asked."},
    {
        "column": "scenario_text",
        "required": "SCENARIO only",
        "description": "Vignette shown before the question. Must be empty for other types.",
    },
    {
        "column": "options",
        "required": "all except TRUE_FALSE",
        "description": "Pipe-separated LABEL:Text pairs — A:Paris|B:London|C:Rome|D:Madrid",
    },
    {
        "column": "correct_answers",
        "required": "all except DRAG_TO_ORDER",
        "description": (
            "Pipe-separated correct labels — A, or A|C for multi-select, or TRUE/FALSE. "
            "Exactly one label for SCENARIO (the primary answer)."
        ),
    },
    {
        "column": "correct_order",
        "required": "DRAG_TO_ORDER only",
        "description": (
            "Labels in the correct sequence — A|B|C|D. The answer key, not the display order."
        ),
    },
    {"column": "explanation", "required": "always", "description": "Why the answer is right."},
    {
        "column": "topics",
        "required": "always",
        "description": "Pipe-separated topic names. Unknown topics are created automatically.",
    },
    {
        "column": "points",
        "required": "optional",
        "description": "Marks for the question (default 1).",
    },
    {
        "column": "scoring_strategy",
        "required": "optional",
        "description": (
            "ALL_OR_NOTHING (default) | PARTIAL_CREDIT | PARTIAL_CREDIT_WITH_PENALTY. "
            "Partial credit is only valid for MULTI_SELECT and DRAG_TO_ORDER."
        ),
    },
    {
        "column": "penalty_per_incorrect",
        "required": "conditional",
        "description": "Required (>0) for PARTIAL_CREDIT_WITH_PENALTY; must be empty otherwise.",
    },
    {"column": "difficulty", "required": "optional", "description": "EASY | MEDIUM | HARD."},
    {
        "column": "external_ref",
        "required": "optional",
        "description": "Your own unique key for the row; prevents re-importing the same question.",
    },
]
