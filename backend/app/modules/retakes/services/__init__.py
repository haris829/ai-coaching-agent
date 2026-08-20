"""Application services: the orchestration between the ports and the domain.

``allowance_service``      how many attempts a learner has left
``eligibility_service``    whether a retake may be created, and against which configuration version
``question_plan_service``  what a retake should avoid delivering
``retake_service``         creating a retake, safely, once
``grant_service``          administrator additional-attempt grants
``history_service``        the read-only attempt history
"""
