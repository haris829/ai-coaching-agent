"""The HTTP routes, against the real database and a fake model.

The test that matters most is the one asserting the answer key is not in the payload. A page that
receives the key is an answer sheet one "view source" away, and no amount of care in the
JavaScript changes that.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from qgen import storage
from qgen.config import Settings
from qgen.errors import GenerationUnavailable
from qgen.web import create_app
from tests.fakes import AnsweringLLM, FakeLLM, answer_reply, questions_reply

CRIMINOLOGY = "LL-34590"


@pytest.fixture
def client(database_url, conn):
    """The real app, with a fake model.

    ``conn`` comes from the rolled-back fixture and is not what the app uses - the app opens its
    own connections and commits them, so anything these tests generate is cleaned up below
    rather than rolled back.
    """
    app = create_app(Settings(database_url=database_url), FakeLLM())
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def cleanup(database_url):
    """Remove the runs these tests commit, whatever happens."""
    import psycopg

    from qgen.db import normalise_url

    created: list[int] = []
    yield created
    if created:
        with psycopg.connect(normalise_url(database_url)) as conn, conn.cursor() as cur:
            cur.executemany("DELETE FROM qgen_runs WHERE id = %s", [(i,) for i in created])
            conn.commit()


def make(client, cleanup, course="Criminology", count=1, **body):
    response = client.post("/api/generate", json={"course": course, "count": count, **body})
    if response.status_code == 200:
        cleanup.append(_run_id_of(client, response.json()))
    return response


def _run_id_of(client, payload) -> int:
    import psycopg

    from qgen.config import load_settings
    from qgen.db import normalise_url

    with psycopg.connect(
        normalise_url(load_settings().database_url), row_factory=psycopg.rows.dict_row
    ) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT run_id FROM qgen_questions WHERE id = %s", (payload["questions"][0]["id"],)
        )
        return int(cur.fetchone()["run_id"])


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_the_catalogue_is_served_with_what_each_course_holds(client):
    courses = client.get("/api/courses").json()

    assert len(courses) >= 33
    entry = next(c for c in courses if c["code"] == CRIMINOLOGY)
    assert set(entry) == {"code", "title", "has_description", "generated"}
    assert isinstance(entry["generated"], int)


def test_the_page_is_served_at_the_root(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "Ask your courses" in response.text


def test_health_says_whether_a_model_is_configured(client):
    assert client.get("/api/health").json() == {"model_configured": True}


def test_existing_questions_can_be_read_without_calling_a_model(client):
    payload = client.get("/api/questions", params={"course": "Criminology"}).json()

    assert payload["matched"] == CRIMINOLOGY
    assert isinstance(payload["questions"], list)


def test_reading_an_unmatched_topic_says_so_rather_than_guessing(client):
    payload = client.get("/api/questions", params={"course": "International"}).json()

    assert payload["matched"] is None
    assert "no course matched" in payload["grounding"]


# ---------------------------------------------------------------------------
# The key
# ---------------------------------------------------------------------------


def test_a_generated_question_reaches_the_page_without_its_answer(client, cleanup):
    payload = make(client, cleanup).json()

    # Searched as text, so a field added later cannot leak the key past this test.
    body = json.dumps(payload["questions"])
    assert "is_correct" not in body
    assert "answer" not in body
    assert "explanation" not in body
    assert len(payload["questions"][0]["options"]) == 4


def test_stored_questions_are_served_without_their_answer_either(client, cleanup):
    make(client, cleanup)

    payload = client.get("/api/questions", params={"course": "Criminology"}).json()

    assert "answer" not in json.dumps(payload["questions"])


# ---------------------------------------------------------------------------
# Generating
# ---------------------------------------------------------------------------


def test_generating_reports_the_match_the_grounding_and_what_was_stored(client, cleanup):
    payload = make(client, cleanup, count=2).json()

    assert payload["matched"] == CRIMINOLOGY
    assert payload["title"] == "Criminology (Postgraduate)"
    assert "course name only" in payload["grounding"]
    assert payload["stored"] == len(payload["questions"])


def test_every_generated_question_is_stored_as_a_draft(client, cleanup, conn):
    payload = make(client, cleanup).json()

    stored = storage.read_questions(conn, [payload["questions"][0]["id"]])
    assert stored[0].status == "DRAFT"


def test_a_short_run_is_reported_as_short_rather_than_rounded_up(client, cleanup, database_url):
    app = create_app(Settings(database_url=database_url), FakeLLM(questions_reply("only one?")))
    with TestClient(app) as short:
        payload = short.post("/api/generate", json={"course": "Criminology", "count": 5}).json()
        cleanup.append(_run_id_of(short, payload))

    assert payload["stored"] == 1
    assert payload["requested"] == 5
    assert payload["short"] is True


def test_a_throttle_comes_back_as_503_retryable_and_not_as_a_prompt_problem(
    client, database_url
):
    app = create_app(
        Settings(database_url=database_url), FakeLLM(GenerationUnavailable(reason="HTTP_429"))
    )

    with TestClient(app, raise_server_exceptions=False) as throttled:
        response = throttled.post("/api/generate", json={"course": "Criminology", "count": 1})

    assert response.status_code == 503
    assert response.json()["retryable"] is True


def test_nothing_is_written_when_the_model_cannot_be_reached(client, database_url, conn):
    before = storage.count_for_course(conn, CRIMINOLOGY)
    app = create_app(
        Settings(database_url=database_url), FakeLLM(GenerationUnavailable(reason="TIMEOUT"))
    )

    with TestClient(app, raise_server_exceptions=False) as broken:
        broken.post("/api/generate", json={"course": "Criminology", "count": 1})

    assert storage.count_for_course(conn, CRIMINOLOGY) == before


@pytest.mark.parametrize("count", [0, -1, 51])
def test_an_impossible_count_is_refused_by_the_schema(client, count):
    response = client.post("/api/generate", json={"course": "Criminology", "count": count})

    assert response.status_code == 422


def test_an_empty_topic_is_refused(client):
    assert client.post("/api/generate", json={"course": "", "count": 1}).status_code == 422


# ---------------------------------------------------------------------------
# Asking - the live route the page uses
# ---------------------------------------------------------------------------


GROUNDED = answer_reply("Layer 3, the network layer.", (1,))


@pytest.fixture
def asker(database_url):
    """A client whose model answers. Defaults to an answer that cites the first item."""

    def build(reply=GROUNDED):
        return TestClient(create_app(Settings(database_url=database_url), AnsweringLLM(reply)))

    return build


def test_a_question_is_answered_with_the_material_it_used(asker):
    with asker() as client:
        payload = client.post(
            "/api/ask", json={"question": "Which OSI layer routes packets between networks?"}
        ).json()

    assert payload["answer"] == "Layer 3, the network layer."
    assert payload["grounded"] is True
    assert payload["material"]
    assert payload["material"][0]["used"] is True
    assert "n" in payload["material"][0] and "course" in payload["material"][0]


def test_the_material_returned_is_the_material_the_model_was_given(database_url):
    """So "grounded in the course" is something a reader can check, not a claim they must take.

    The page shows this list; if it were assembled separately from the prompt, the two could
    drift and the citations would point at items the model never saw.
    """
    llm = AnsweringLLM()
    with TestClient(create_app(Settings(database_url=database_url), llm)) as client:
        payload = client.post(
            "/api/ask", json={"question": "Which OSI layer routes packets between networks?"}
        ).json()

    prompt = llm.prompts[0]
    assert payload["material"]
    for item in payload["material"]:
        assert item["text"][:60] in " ".join(prompt.split())


def test_an_ungrounded_answer_says_so_rather_than_borrowing_a_course(asker):
    reply = json.dumps({"answer": "From general knowledge.", "used": []})
    with asker(reply) as client:
        payload = client.post("/api/ask", json={"question": "Which OSI layer routes?"}).json()

    assert payload["grounded"] is False
    assert "general subject knowledge" in payload["provenance"]


def test_asking_writes_nothing(asker, conn):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM qgen_questions")
        before = cur.fetchone()["n"]

    with asker() as client:
        client.post("/api/ask", json={"question": "Which OSI layer routes packets?"})

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM qgen_questions")
        assert cur.fetchone()["n"] == before


def test_a_question_naming_a_course_is_reported_as_that_course(asker):
    with asker() as client:
        payload = client.post(
            "/api/ask", json={"question": "In Medical Law MA (Postgraduate), what is consent?"}
        ).json()

    assert payload["course_code"] == "LL-45165"


@pytest.mark.parametrize("question", ["", "   ", "x" * 1001])
def test_an_empty_or_absurd_question_is_refused(asker, question):
    with asker() as client:
        assert client.post("/api/ask", json={"question": question}).status_code == 422


def test_a_throttle_while_answering_is_503_retryable(asker):
    with asker(GenerationUnavailable(reason="HTTP_429")) as client:
        response = client.post("/api/ask", json={"question": "What is mens rea?"})

    assert response.status_code == 503
    assert response.json()["retryable"] is True


# ---------------------------------------------------------------------------
# Practice - a question from the named course, a different one each time
# ---------------------------------------------------------------------------


def test_a_practice_question_comes_back_for_a_named_course(client):
    payload = client.post("/api/practice", json={"course": "Criminology"}).json()

    assert payload["course"] == "Criminology (Postgraduate)"
    assert payload["course_code"] == CRIMINOLOGY
    assert payload["question"]
    assert payload["options"]
    assert payload["ref"].startswith(("qgen-", "qb-"))


def test_a_practice_question_never_carries_its_answer(client):
    payload = client.post("/api/practice", json={"course": "Criminology"}).json()

    body = json.dumps(payload)
    assert "is_correct" not in body
    assert '"answer"' not in body


def test_the_draft_status_is_shown_rather_than_hidden(client):
    payload = client.post("/api/practice", json={"course": "Criminology"}).json()

    assert payload["status"] in {"DRAFT", "ACTIVE"}


def test_asking_again_with_what_was_seen_gives_a_different_question(client):
    first = client.post("/api/practice", json={"course": "Criminology"}).json()

    second = client.post(
        "/api/practice", json={"course": "Criminology", "exclude": [first["ref"]]}
    ).json()

    assert second["ref"] != first["ref"]


def test_the_count_left_falls_as_questions_are_seen(client):
    first = client.post("/api/practice", json={"course": "Criminology"}).json()
    second = client.post(
        "/api/practice", json={"course": "Criminology", "exclude": [first["ref"]]}
    ).json()

    assert second["remaining"] == first["remaining"] - 1


def test_an_ambiguous_course_is_refused_rather_than_guessed(client):
    """Four courses contain "International". One of them is not an answer."""
    response = client.post("/api/practice", json={"course": "International"})

    assert response.status_code == 422
    assert "International" in response.json()["message"]


def test_marking_a_practice_answer_reveals_the_key_only_then(client):
    question = client.post("/api/practice", json={"course": "Criminology"}).json()

    result = client.post(
        "/api/practice/answer", json={"ref": question["ref"], "label": "A"}
    ).json()

    assert set(result) == {"correct", "answer", "explanation", "status"}
    assert isinstance(result["correct"], bool)
    assert result["answer"]


def test_every_option_offered_can_be_marked(client):
    """Whatever labels the source used - the bank has true/false pairs as well as A-D."""
    question = client.post("/api/practice", json={"course": "Criminology"}).json()

    for option in question["options"]:
        response = client.post(
            "/api/practice/answer", json={"ref": question["ref"], "label": option["label"]}
        )
        assert response.status_code == 200

    correct = [
        option
        for option in question["options"]
        if client.post(
            "/api/practice/answer", json={"ref": question["ref"], "label": option["label"]}
        ).json()["correct"]
    ]
    assert len(correct) >= 1


def test_marking_a_reference_that_does_not_exist_is_a_404(client):
    assert client.post(
        "/api/practice/answer", json={"ref": "qgen-99999999", "label": "A"}
    ).status_code == 404
