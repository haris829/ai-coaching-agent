"""Conformance suites for the persistence ports.

These are fully self-describing: they build their own records, so an implementer supplies only
an empty adapter instance.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ..domain.enums import (
    FramingStrategy,
    Grounding,
    NaricLevel,
    QuestionClass,
    RatingState,
)
from ..domain.models import FalsePositiveRecord, FramingAttempt, InteractionRecord

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _record(interaction_id: str = "int_1", session_id: str = "sess_1", user_id: str = "u1") -> InteractionRecord:
    return InteractionRecord(
        interaction_id=interaction_id,
        session_id=session_id,
        user_id=user_id,
        asked_at=_NOW,
        question_text="[redacted:not_persisted]",
        topic_tag="evidence",
        question_class=QuestionClass.CONCEPT_EXPLANATION,
        naric_level=NaricLevel.LEVEL_5,
        response_id="res_1",
        course_id="c1",
        lesson_id="l1",
        lesson_section_id="s1",
        concept_tag="hearsay",
        grounding=Grounding.LESSON,
        quiz_intent_detected=False,
        quiz_detection_confirmed=None,
        framing_used=FramingStrategy.FIRST_PRINCIPLES,
        explain_differently_count=0,
        follow_up_of=None,
        rating_state=RatingState.PENDING,
    )


class InteractionLogConformance:
    @pytest.fixture
    def adapter(self):  # pragma: no cover - overridden by the implementer
        raise NotImplementedError("provide an `adapter` fixture")

    def test_append_then_get_round_trips(self, adapter) -> None:
        record = _record()
        adapter.append(record)
        loaded = adapter.get(record.interaction_id)
        assert loaded is not None
        assert loaded.interaction_id == record.interaction_id
        assert loaded.concept_tag == record.concept_tag

    def test_rating_state_is_preserved_as_pending(self, adapter) -> None:
        """UC-04 writes ``pending`` and never changes it. A store must not alter it either."""
        adapter.append(_record())
        loaded = adapter.get("int_1")
        assert loaded is not None
        assert loaded.rating_state is RatingState.PENDING

    def test_unknown_id_returns_none_rather_than_raising(self, adapter) -> None:
        assert adapter.get("no_such_interaction") is None

    def test_list_for_session_is_scoped(self, adapter) -> None:
        adapter.append(_record("int_a", session_id="sess_a"))
        adapter.append(_record("int_b", session_id="sess_b"))
        ids = {r.interaction_id for r in adapter.list_for_session("sess_a")}
        assert ids == {"int_a"}

    def test_list_for_unknown_session_is_empty(self, adapter) -> None:
        assert adapter.list_for_session("sess_never_used") == []

    def test_false_positives_round_trip(self, adapter) -> None:
        record = FalsePositiveRecord(
            record_id="fp_1",
            interaction_id="int_1",
            session_id="sess_1",
            user_id="u1",
            recorded_at=_NOW,
            classifier_label="quiz_answer_request",
            classifier_confidence=0.9,
            classifier_signals=("option_selection",),
            known_item_matched=False,
            concept_tag="hearsay",
        )
        adapter.append_false_positive(record)
        stored = adapter.list_false_positives("sess_1")
        assert [r.record_id for r in stored] == ["fp_1"]

    def test_false_positive_records_carry_no_question_text(self, adapter) -> None:
        """The tuning log must not become a copy of what learners typed."""
        record = FalsePositiveRecord(
            record_id="fp_2",
            interaction_id="int_1",
            session_id="sess_1",
            user_id="u1",
            recorded_at=_NOW,
            classifier_label="ambiguous",
            classifier_confidence=0.5,
            classifier_signals=(),
            known_item_matched=False,
            concept_tag="hearsay",
        )
        adapter.append_false_positive(record)
        assert not hasattr(record, "question_text")


class FramingRegistryConformance:
    @pytest.fixture
    def adapter(self):  # pragma: no cover - overridden by the implementer
        raise NotImplementedError("provide an `adapter` fixture")

    def _record(self, adapter, session: str, concept: str, framing: FramingStrategy) -> None:
        adapter.record(
            session_id=session,
            concept_tag=concept,
            framing=framing,
            fingerprint=f"fp_{framing.value}",
            fingerprint_tokens=(framing.value,),
            recorded_at=_NOW,
        )

    def test_empty_registry_reports_nothing_used(self, adapter) -> None:
        assert adapter.used_framings("sess_1", "hearsay") == []

    def test_recorded_framing_is_returned(self, adapter) -> None:
        self._record(adapter, "sess_1", "hearsay", FramingStrategy.ANALOGY)
        used = adapter.used_framings("sess_1", "hearsay")
        assert [a.framing for a in used] == [FramingStrategy.ANALOGY]
        assert all(isinstance(a, FramingAttempt) for a in used)

    def test_scoped_to_session_and_concept(self, adapter) -> None:
        """History must not leak between sessions or between concepts."""
        self._record(adapter, "sess_1", "hearsay", FramingStrategy.ANALOGY)
        assert adapter.used_framings("sess_2", "hearsay") == []
        assert adapter.used_framings("sess_1", "burden_of_proof") == []

    def test_explain_differently_count_excludes_the_first_explanation(self, adapter) -> None:
        assert adapter.explain_differently_count("sess_1", "hearsay") == 0
        self._record(adapter, "sess_1", "hearsay", FramingStrategy.FIRST_PRINCIPLES)
        assert adapter.explain_differently_count("sess_1", "hearsay") == 0
        self._record(adapter, "sess_1", "hearsay", FramingStrategy.ANALOGY)
        assert adapter.explain_differently_count("sess_1", "hearsay") == 1

    def test_fingerprint_tokens_survive_the_round_trip(self, adapter) -> None:
        """Paraphrase detection depends on them, so a store that drops them breaks it."""
        self._record(adapter, "sess_1", "hearsay", FramingStrategy.ANALOGY)
        attempt = adapter.used_framings("sess_1", "hearsay")[0]
        assert attempt.fingerprint == "fp_analogy"
        assert tuple(attempt.fingerprint_tokens) == ("analogy",)
