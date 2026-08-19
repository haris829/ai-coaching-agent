"""UC-04 — Answer Validation & Scoring.

Runs after a *confirmed* UC-03 submission and turns a frozen attempt into a durable, immutable
result: a score per question, a total, a maximum and a percentage.

Three properties define the capability, and each is enforced rather than intended:

* **Historical results are never rewritten.** One result row per attempt
  (``ux_qr_result_attempt``), and a database trigger rejects any ``UPDATE`` to a result that has
  reached ``SCORED``. Re-running scoring for a scored attempt is refused by the service *and* by the
  schema.
* **Scoring reads only frozen data.** The attempt's locked configuration snapshot, its frozen
  question snapshots, and the immutable question-bank snapshot for the exact version delivered.
  Editing or retiring a question afterwards cannot change a score that has already been produced,
  and cannot change one produced later either.
* **A failure is a state, not a lost score.** If scoring cannot complete — a missing answer key, a
  zero maximum, a persistence error — the attempt is recorded ``PENDING_SCORE``
  ("Submitted — Pending Score") and the run is safely retryable. Nothing about the learner's
  submission is undone.

Tables are prefixed ``qr_`` (quiz *results*). Cross-capability reads go through the two ports in
``integration/``: UC-03 for the attempt and its frozen answers, UC-02 for the answer key.
"""
