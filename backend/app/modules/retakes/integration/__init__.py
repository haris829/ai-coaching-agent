"""Read-only ports onto the neighbouring use cases, plus the one write UC-08 performs.

``uc01``       quiz configuration versions and quiz availability (read-only)
``uc02``       the eligible question pool, for sizing alternatives (read-only)
``uc03``       attempts, delivered question ids, and retake attempt creation (the one write)
``downstream`` UC-04 / UC-05 / UC-06 / UC-07, for attempt history only (read-only)
``audit``      the outbound grant audit trail
"""
