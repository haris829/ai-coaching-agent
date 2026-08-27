"""Every user-facing string UC-01 can emit.

Centralised so that (a) the wording is consistent, (b) tests can assert that no
technical detail ever reaches a user, and (c) localisation later is a single-file change.

Rule: nothing in this file may contain a stack trace, exception class, SQL, URL,
credential or upstream error message.
"""

from __future__ import annotations

# --- Courses -------------------------------------------------------------- #
COURSES_UNAVAILABLE = "Courses are temporarily unavailable."
COURSES_EMPTY = "You do not have any courses available yet."
COURSE_LINKED_NEEDS_COURSES = "Courses are temporarily unavailable."
COURSE_SELECTION_REQUIRED = "Please choose a course and a lesson for this session."
LESSON_SELECTION_REQUIRED = "Please choose a lesson from the selected course."
COURSE_NOT_ACCESSIBLE = "That course is not available for your account."
LESSON_NOT_ACCESSIBLE = "That lesson is not available for your account."

# --- Case files ----------------------------------------------------------- #
CASES_UNAVAILABLE = "Case files are temporarily unavailable."
CASES_EMPTY = "No accessible case files."
CASE_SELECTION_REQUIRED = "Please choose a case file for this session."
CASE_NOT_ACCESSIBLE = "That case file is not available for your account."

# --- NARIC ---------------------------------------------------------------- #
NARIC_UNAVAILABLE_NOTICE = (
    "NARIC calibration is unavailable right now. You can continue without "
    "calibration — your coaching explanations will use Level 5 by default."
)
NARIC_INCOMPLETE_NOTICE = (
    "Your NARIC calibration is not complete yet. You can continue without "
    "calibration — your coaching explanations will use Level 5 by default."
)
NARIC_CALIBRATING_NOTICE = (
    "Your NARIC calibration is still being worked out. You can continue without "
    "calibration — your coaching explanations will use Level 5 by default."
)
NARIC_CONTINUE_WITHOUT_CALIBRATION_LABEL = "Continue without calibration"
NARIC_DEFAULT_APPLIED_NOTICE = (
    "This session is using Level 5 by default because calibrated NARIC data was not "
    "available."
)

# --- Profile / personalisation -------------------------------------------- #
PROFILE_UNAVAILABLE_NOTICE = (
    "We could not load your personalised profile information right now, but you can "
    "continue your session normally."
)
PROFILE_INCOMPLETE_NOTICE = (
    "Some of your profile details are missing, so parts of this session will be less "
    "personalised."
)

# --- Modes ---------------------------------------------------------------- #
MODE_AVAILABLE = None
FREE_FORM_ALWAYS_AVAILABLE = "Free-form coaching is always available."

# --- Generic -------------------------------------------------------------- #
GENERIC_DEGRADED_SESSION = (
    "Your session is open, but some features are temporarily limited."
)
