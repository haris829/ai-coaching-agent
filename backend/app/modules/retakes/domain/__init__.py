"""UC-08's domain: the rules, the entities and the arithmetic.

Nothing in this package does I/O, imports FastAPI, or knows how anything is stored. The services
gather inputs through ports and hand them here; every decision this module makes is reproducible
from its arguments alone, which is what makes the retake rules testable without a database and
portable onto the company's.
"""
