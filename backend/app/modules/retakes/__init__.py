"""UC-08 — Retake Management.

A learner who has completed an attempt may retake the quiz while attempts remain. This module
owns the retake decision, the learner-specific attempt grants an administrator can issue, and the
exclusion rules that stop a retake being the same paper again. It owns no attempt, no score, no
pass/fail result and no question content: those belong to UC-03, UC-04, UC-05 and UC-02, and are
reached through the read-only ports in ``integration``.
"""
