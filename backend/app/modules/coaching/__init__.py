"""UC-07 — AI Coaching Review Mode ("Review with Larry").

Post-submission coaching for the questions a learner got wrong. The module reads a submitted
attempt (UC-03), the confirmed question outcomes (UC-04) and the released feedback (UC-06), and
runs a Socratic coaching conversation over each incorrect question.

The one architectural rule that outranks everything else here: **the answer key never reaches the
model**. It is removed at a sanitisation boundary before a coaching context is constructed, so
there is nothing in the model's input for an adversarial prompt to extract (§12, §13, §26).
"""
