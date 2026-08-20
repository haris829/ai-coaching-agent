"""UC-10 - Analytics & Reporting.

A standalone, database-agnostic analytics and reporting layer that consumes
assessment results produced by external systems.

Layering (strictly one direction):

    FastAPI route -> Analytics/Review service -> Repository interface -> External provider

The module owns no storage: every number it reports is computed from data handed
to it through :mod:`app.modules.analytics.repositories.base`.
"""

__all__ = ["__version__"]

__version__ = "1.0.0"
