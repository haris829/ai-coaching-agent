"""UC-05 — Pass / Fail & Certificate Gating.

Builds directly on UC-04's *confirmed* result and answers three questions:

* **Did the learner pass?** ``percentage >= pass mark`` -> PASS, otherwise FAIL. The pass mark comes
  from the attempt's **own configuration version**, never from the quiz's latest one, so
  reconfiguring a quiz cannot retroactively move the bar for an attempt already sat.
* **Should a certificate be issued?** Only on a pass, only through the
  :class:`CertificateServicePort` boundary, and at most once -- enforced by a partial unique index,
  not only by a service check. Generation is asynchronous in the sense that matters: it is a
  separate, retryable step whose failure leaves the quiz result untouched.
* **What does the CPD record look like?** Attempt date, score, pass/fail and course name, pushed
  across the :class:`CpdSyncPort` boundary. A CPD failure never changes the quiz result either.

Both outbound boundaries fail *softly*: a certificate service that is unavailable leaves a
``PENDING`` certificate row and a retryable operation, and the outcome row -- the actual pass/fail
determination -- is already durable by then. That ordering is the design: determine, persist, then
attempt the side effects.

Tables are prefixed ``qg_`` (quiz *gating*).
"""
