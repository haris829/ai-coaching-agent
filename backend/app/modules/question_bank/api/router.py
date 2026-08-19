"""Aggregate router for the Question Bank module.

The whole module mounts under one prefix, so merging it into the larger Courses Quiz Agent API
is a single ``include_router`` call and there is no chance of a route collision with another
team's endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.question_bank.api import delivery, imports, questions, topics

QUESTION_BANK_PREFIX = "/question-bank"

question_bank_router = APIRouter(prefix=QUESTION_BANK_PREFIX)

# Order matters only for documentation grouping.
question_bank_router.include_router(questions.router)
question_bank_router.include_router(topics.router)
question_bank_router.include_router(imports.router)
question_bank_router.include_router(delivery.router)
