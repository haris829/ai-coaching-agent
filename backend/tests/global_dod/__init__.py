"""UC-11 — Global Definition of Done.

**This package builds nothing.** It contains no domain rule, no scoring arithmetic, no quiz logic
and no analytics calculation. It calls the integrated system and checks what it answers, and
``test_no_new_domain_logic.py`` enforces that rather than trusting it.

WHY IT IS TESTS RATHER THAN A MODULE
------------------------------------
The specification asks for a validation layer that boots the real system, runs full lifecycle
flows, asserts the system's invariants and fails loudly. In this repository pytest is already the
orchestrator and ``app.main.create_app`` is already the real system on a real database, so a
``GlobalDoDRunner`` class would be new machinery duplicating both. What UC-11 needs that did not
already exist is *coverage*: the assertions that cross module boundaries and that no single
capability's own suite can make.

HOW THIS DIFFERS FROM ``tests/integration/``
--------------------------------------------
``tests/integration/`` proves each **seam** works: the results chain, the coaching chain, the retake
chain, the formal-assessment chain, the analytics chain. Those are per-capability integrations, each
owned by the capability that added it.

UC-11 sits above them and asks the questions no one capability owns:

* Is a submitted attempt immutable through *every* route the application exposes — learner, admin,
  and every capability that reads it?
* Do all five question types survive delivery, autosave, scoring, feedback and a retake?
* Does a configuration change disturb an attempt that already locked a version, anywhere?
* Is the answer key unreachable from every endpoint, not just the coaching one?
* Does the system fail safely — a transient outage, a permanent one, a duplicate request?

Each module maps to a numbered section of the Global DoD, and the mapping is in each file's
docstring so a failure points at a requirement rather than only at a line of code.
"""
