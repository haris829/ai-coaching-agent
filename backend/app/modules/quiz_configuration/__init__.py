"""UC-01 — Quiz Configuration & Rules.

An administrator configures a quiz; every meaningful change creates a new **immutable
configuration version**; learners see a rules summary built from the active version; and
pressing **Start quiz** creates an attempt permanently locked to the version that was active at
that moment.

Relationship to UC-02
---------------------
This module owns configuration, versioning and the start-quiz decision. It owns **no questions**
— the single question bank lives in ``app.modules.question_bank``. Everything this module needs
from the bank (how many eligible questions exist per type, which questions to hand an attempt)
goes through :class:`app.modules.quiz_configuration.ports.QuestionBankPort`, so the capacity rule
and the delivery rule cannot drift apart and retired questions are excluded structurally.

Relationship to UC-03
---------------------
UC-03 (Quiz Attempt Delivery) is not implemented here. What it will need — the locked
configuration version, its rules, the eligible-question pool and the attempt's pinned question
snapshots — is exposed through this module's services and the question bank's delivery service.
"""

from __future__ import annotations
