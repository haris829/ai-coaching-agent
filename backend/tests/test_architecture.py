"""The two capabilities stay separate, and the shared kernel stays shared.

Separation that is only a convention decays the first time someone reaches for a convenient import.
These tests read the actual import statements, so the boundaries are enforced rather than described:

    app/core/          the shared kernel — depends on nothing above it
    app/db/            persistence plumbing
    app/modules/identity/           the authentication + platform seam
    app/modules/question_bank/      UC-02 — knows nothing about UC-01 or UC-03
    app/modules/quiz_configuration/ UC-01 — reaches other capabilities only via integration/
    app/modules/attempt_delivery/   UC-03 — reaches other capabilities only via integration/
    app/modules/scoring/            UC-04 — reaches UC-03 and UC-02 only via integration/
    app/modules/certification/      UC-05 — reaches UC-04 and UC-03 only via integration/
    app/modules/feedback/           UC-06 — reaches UC-04, UC-05 and UC-02 only via integration/
    app/modules/coaching/           UC-07 — reaches UC-03, UC-04 and UC-06 only via integration/

The general rule, which scales as UC-04 onwards arrive: **a capability may import another capability
only from inside its own ``integration/`` package.** That keeps every cross-capability dependency in
a named adapter behind a port, where it can be reviewed and replaced, instead of spreading through
services.

They also assert the duplication that was removed during the merge does not come back: one clock,
one set of coercion primitives, one error envelope, one question-type vocabulary.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

QUESTION_BANK = "app.modules.question_bank"
QUIZ_CONFIGURATION = "app.modules.quiz_configuration"
ATTEMPT_DELIVERY = "app.modules.attempt_delivery"
SCORING = "app.modules.scoring"
CERTIFICATION = "app.modules.certification"
FEEDBACK = "app.modules.feedback"
COACHING = "app.modules.coaching"
RETAKES = "app.modules.retakes"

#: The capabilities, and the prefix each must not reach into outside its own ``integration/``.
CAPABILITIES = {
    "question_bank": QUESTION_BANK,
    "quiz_configuration": QUIZ_CONFIGURATION,
    "attempt_delivery": ATTEMPT_DELIVERY,
    "scoring": SCORING,
    "certification": CERTIFICATION,
    "feedback": FEEDBACK,
    "coaching": COACHING,
    "retakes": RETAKES,
}

#: The single file in UC-01 that is allowed to know the question bank exists.
THE_SEAM = APP / "modules" / "quiz_configuration" / "integration" / "question_bank_adapter.py"


def _python_files(*parts: str) -> list[Path]:
    root = APP.joinpath(*parts)
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_modules(path: Path) -> set[str]:
    """Every module name the file imports, from both `import x` and `from x import y`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def _relative(path: Path) -> str:
    return path.relative_to(APP.parent).as_posix()


# ---------------------------------------------------------------------------
# Capability separation
# ---------------------------------------------------------------------------


def test_the_question_bank_knows_nothing_about_quiz_configuration() -> None:
    """UC-02 must be independently deployable: zero knowledge of UC-01.

    This is the direction that matters most — the question bank was built standalone and must stay
    that way, so it can be extracted, reused or replaced on its own.
    """
    offenders = [
        _relative(path)
        for path in _python_files("modules", "question_bank")
        if any(name.startswith(QUIZ_CONFIGURATION) for name in _imported_modules(path))
    ]
    assert offenders == [], (
        "the question bank must not import quiz configuration; offending files: " + str(offenders)
    )


def test_quiz_configuration_touches_the_question_bank_only_through_the_adapter() -> None:
    """UC-01's dependency on UC-02 goes through one file, so the seam stays reviewable."""
    offenders = [
        _relative(path)
        for path in _python_files("modules", "quiz_configuration")
        if path != THE_SEAM
        and any(name.startswith(QUESTION_BANK) for name in _imported_modules(path))
    ]
    assert offenders == [], (
        "only question_bank_adapter.py may import the question bank; offending files: "
        + str(offenders)
    )


def test_the_adapter_is_the_implementation_of_the_port() -> None:
    """A guard against the seam becoming a general-purpose back door."""
    assert THE_SEAM.exists()
    imported = _imported_modules(THE_SEAM)
    bank_imports = {name for name in imported if name.startswith(QUESTION_BANK)}
    # The adapter reads the bank's models and goes through its delivery service. It must not reach
    # into the bank's HTTP layer or its CSV importer.
    for forbidden in (f"{QUESTION_BANK}.api", f"{QUESTION_BANK}.csv_import"):
        assert not any(name.startswith(forbidden) for name in bank_imports), (
            f"the adapter must not depend on {forbidden}"
        )


def test_cross_capability_imports_live_only_in_integration_packages() -> None:
    """The general boundary rule, applied to every capability.

    A service or router that imports another capability directly is how a "module" quietly becomes a
    monolith. Confining it to ``integration/`` means each such dependency has a name, a port, and a
    single file to read when it needs replacing.
    """
    offenders: list[str] = []
    for name in CAPABILITIES:
        others = {other for label, other in CAPABILITIES.items() if label != name}
        for path in _python_files("modules", name):
            if "integration" in path.parts:
                continue
            for imported in _imported_modules(path):
                for other in others:
                    if imported.startswith(other):
                        offenders.append(f"{_relative(path)} -> {imported}")
    assert offenders == [], (
        "cross-capability imports must live in an integration/ package: " + str(offenders)
    )


def test_the_question_bank_depends_on_no_other_capability_at_all() -> None:
    """UC-02 was built standalone and must stay independently deployable — even via adapters."""
    offenders = [
        f"{_relative(path)} -> {imported}"
        for path in _python_files("modules", "question_bank")
        for imported in _imported_modules(path)
        if imported.startswith(
            (QUIZ_CONFIGURATION, ATTEMPT_DELIVERY, SCORING, CERTIFICATION, FEEDBACK, COACHING)
        )
    ]
    assert offenders == [], "the question bank must depend on nothing else: " + str(offenders)


def test_the_shared_kernel_depends_on_no_capability() -> None:
    """``app.core`` is shared precisely because it knows nothing about either capability."""
    offenders: list[str] = []
    for path in _python_files("core"):
        for name in _imported_modules(path):
            if name.startswith("app.modules"):
                offenders.append(f"{_relative(path)} -> {name}")
    assert offenders == [], "app/core must not import any module: " + str(offenders)


def test_the_domain_layers_stay_free_of_http_and_persistence() -> None:
    """Business rules must be testable without a web server or a database."""
    offenders: list[str] = []
    for parts in (
        ("modules", "question_bank", "domain"),
        ("modules", "quiz_configuration", "domain"),
        ("modules", "attempt_delivery", "domain"),
        ("modules", "scoring", "domain"),
        ("modules", "certification", "domain"),
        ("modules", "feedback", "domain"),
        ("modules", "coaching", "domain"),
        ("modules", "retakes", "domain"),
    ):
        for path in _python_files(*parts):
            for name in _imported_modules(path):
                if name.startswith(("fastapi", "sqlalchemy", "starlette", "app.db")):
                    offenders.append(f"{_relative(path)} -> {name}")
    assert offenders == [], "domain packages must stay free of HTTP/persistence: " + str(offenders)


# ---------------------------------------------------------------------------
# The duplication removed during the merge must not come back
# ---------------------------------------------------------------------------


def _definition_sites(name: str, kind: str = "def") -> list[str]:
    """Files that define a given top-level function or class."""
    sites: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if kind == "def" and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == name:
                    sites.append(_relative(path))
            elif kind == "class" and isinstance(node, ast.ClassDef) and node.name == name:
                sites.append(_relative(path))
    return sites


def test_there_is_exactly_one_clock() -> None:
    assert _definition_sites("utcnow") == ["app/core/time.py"]


def test_nothing_calls_datetime_now_directly() -> None:
    """Every timestamp goes through ``utcnow()`` so "all times are UTC" is enforced, not hoped for."""
    offenders: list[str] = []
    for path in _python_files():
        if path == APP / "core" / "time.py":
            continue
        source = path.read_text(encoding="utf-8")
        if "datetime.now(" in source:
            offenders.append(_relative(path))
    assert offenders == [], "use app.core.time.utcnow() instead of datetime.now(): " + str(offenders)


def test_there_is_exactly_one_set_of_coercion_primitives() -> None:
    for helper in ("to_int", "to_number", "is_blank", "trimmed", "truthy", "round4", "parse_enum"):
        assert _definition_sites(helper) == ["app/core/coercion.py"], helper


def test_there_is_exactly_one_question_type_vocabulary() -> None:
    assert _definition_sites("QuestionType", kind="class") == ["app/core/question_types.py"]
    assert _definition_sites("QuestionStatus", kind="class") == ["app/core/question_types.py"]
    # UC-01 and UC-03 independently coined "DeliveryMode" for different things — grading policy and
    # pagination. The pagination one is now `QuestionPresentation`, defined once in the kernel.
    assert _definition_sites("QuestionPresentation", kind="class") == [
        "app/core/question_types.py"
    ]
    assert _definition_sites("DeliveryMode", kind="class") == [
        "app/modules/quiz_configuration/domain/enums.py"
    ]


def test_there_is_exactly_one_owner_of_attempts() -> None:
    """UC-01 carried a provisional attempt table before UC-03 arrived. Two would be one too many."""
    assert _definition_sites("QuizAttempt", kind="class") == [
        "app/modules/attempt_delivery/models.py"
    ]

    tables = [
        _relative(path)
        for path in _python_files()
        if '__tablename__ = "qc_attempts"' in path.read_text(encoding="utf-8")
    ]
    assert tables == [], "qc_attempts was superseded by qd_attempts: " + str(tables)


def test_no_provisional_stand_ins_for_a_merged_capability_remain() -> None:
    """UC-03 shipped ``ext_*`` projections of UC-01/UC-02 while they were separate workspaces.

    They were always meant to be deleted at integration. This fails if one creeps back, because a
    stand-in that outlives the thing it stood in for is how two sources of truth appear.
    """
    offenders: list[str] = []
    for path in _python_files():
        source = path.read_text(encoding="utf-8")
        for marker in ('__tablename__ = "ext_', "class Ext"):
            if marker in source:
                offenders.append(_relative(path))
    assert offenders == [], "provisional ext_* stand-ins must not return: " + str(set(offenders))


def test_there_is_exactly_one_error_envelope() -> None:
    assert _definition_sites("CamelModel", kind="class") == ["app/core/schemas.py"]
    assert _definition_sites("ErrorResponse", kind="class") == ["app/core/schemas.py"]
    assert _definition_sites("FieldIssue", kind="class") == ["app/core/errors.py"]


def test_there_is_exactly_one_error_hierarchy() -> None:
    """A module may adapt ``AppError``'s signature, but must not define a rival exception.

    UC-03 subclasses it to keep its positional ``(status, code, message)`` factories readable. That
    is fine — it still renders through the one handler into the one envelope. What must never happen
    is a second, independent error base, because then some failures would serialise differently.
    """
    rivals: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or node.name != "AppError":
                continue
            if _relative(path) == "app/core/errors.py":
                continue
            base_names = {
                base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
                for base in node.bases
            }
            if not any(name.endswith("AppError") for name in base_names):
                rivals.append(f"{_relative(path)} (bases: {sorted(base_names)})")
    assert rivals == [], "AppError must subclass the shared one, not redefine it: " + str(rivals)


def test_the_request_session_dependency_is_declared_once() -> None:
    """``DbSession`` used to be redeclared identically in six routers."""
    sites = [
        _relative(path)
        for path in _python_files()
        if "\nDbSession = Annotated" in path.read_text(encoding="utf-8")
    ]
    assert sites == ["app/core/deps.py"]
