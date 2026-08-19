"""UC-02 — Question Bank Management.

A self-contained module. Its only outward dependencies are ``app.core`` (config, logging,
errors) and ``app.db`` (engine/session), which keeps it straightforward to merge into the
larger Courses Quiz Agent system alongside another team's quiz-delivery module.

Layout
------
``domain/``       pure business rules: enums, policy, validator, grading, content hashing
``models.py``     SQLAlchemy tables (all prefixed ``qb_``)
``schemas/``      pydantic request/response contracts
``services/``     transactional use cases
``csv_import/``   CSV template, parser and row mapper
``api/``          FastAPI routers
"""
