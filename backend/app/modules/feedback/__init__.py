"""UC-06 — Detailed Feedback Report.

Generated after a *successful* score and, where one exists, a determined pass/fail outcome. For
every question it reports the question, the learner's answer, the correct answer, an explanation,
the marks scored and a lesson reference; for the attempt it reports the total, the percentage,
pass/fail, the time taken and the correct/incorrect counts. Multi-select questions additionally
report each option's correct/incorrect status and what it contributed to the marks.

Three properties are worth stating up front, because they are what the module is shaped around:

* **A report is generated once and then frozen.** Both the per-question rows and a complete payload
  are persisted, and a trigger rejects edits once the report is ``GENERATED``. Later edits to the
  question bank, the configuration or the topics cannot change a report a learner has already seen
  -- the historical-consistency requirement is a property of the schema, not of a convention.
* **Nothing is invented.** A missing explanation or lesson reference is rendered as a defined
  fallback, and that fallback is a constant in :mod:`app.modules.feedback.domain.fallbacks`. There
  is no generated prose anywhere in this capability.
* **A generation failure costs nothing.** The score and the pass/fail outcome are already durable
  when generation is attempted, so a failure leaves a ``PENDING`` report and a retryable operation,
  and removes nothing.

Tables are prefixed ``qf_`` (quiz *feedback*).
"""
