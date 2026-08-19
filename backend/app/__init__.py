"""Courses Quiz Agent backend.

Two capabilities share one API, one database and one error envelope:

* ``app.modules.quiz_configuration`` — UC-01 Quiz Configuration & Rules
* ``app.modules.question_bank``      — UC-02 Question Bank Management

``app.modules.identity`` is the placeholder identity seam both of them resolve callers through.
"""

__version__ = "1.0.0"
