"""UC-07 -- Progress & Knowledge Gap Identification.

A read-only aggregator: it reads coaching history, feedback, learner profile and
course data through ports, and derives a deterministic, explainable knowledge-gap
report. The only data it persists is the report it generates.
"""

__all__ = ["REPORT_VERSION", "ANALYSIS_VERSION"]

#: Shape/schema version of the emitted GapReport document.
REPORT_VERSION = "1.0.0"

#: Version of the analysis rules (signals, thresholds semantics, ordering).
#: Bump whenever the derivation logic changes, even if the shape does not.
ANALYSIS_VERSION = "1.0.0"
