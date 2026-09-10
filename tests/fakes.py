"""One fake model for every test that needs one.

There were four of these, near-identical, one per test file. Four copies of a fake is four places
a test can be quietly testing something slightly different from its neighbour - and the one that
matters here is thread safety, which only the concurrency file's copy had.
"""

from __future__ import annotations

import json
import threading


def questions_reply(*texts: str, answer: str = "C") -> str:
    """A well-formed reply to a question-generation prompt."""
    return json.dumps(
        {
            "questions": [
                {
                    "question": text,
                    "options": {"A": "one", "B": "two", "C": "three", "D": "four"},
                    "answer": answer,
                    "explanation": "because two is not three",
                }
                for text in texts
            ]
        }
    )


def answer_reply(text: str = "An answer.", used: tuple[int, ...] = ()) -> str:
    """A well-formed reply to an answering prompt."""
    return json.dumps({"answer": text, "used": list(used)})


class FakeLLM:
    """Returns the replies it was given, in order, and records the prompts it saw.

    A reply that is an exception is raised instead of returned, which is how an outage or a
    throttle is simulated. When the given replies run out, ``spare`` supplies more - by default a
    fresh question each time, since two identical ones would be dropped as duplicates and a test
    would then be measuring the de-duplicator rather than what it meant to.
    """

    configured = True

    def __init__(self, *replies, spare=None) -> None:
        self._replies = list(replies)
        self._spare = spare or (lambda n: questions_reply(f"spare question {n}?"))
        self._lock = threading.Lock()
        self.prompts: list[str] = []
        self.calls = 0

    def complete(self, prompt: str, *, max_tokens: int) -> str:
        # Locked because the generation service calls this from a thread pool, and an unlocked
        # list pop under five threads loses replies in a way that looks like a product bug.
        with self._lock:
            self.calls += 1
            self.prompts.append(prompt)
            result = self._replies.pop(0) if self._replies else self._spare(self.calls)
        if isinstance(result, Exception):
            raise result
        return result


class AnsweringLLM(FakeLLM):
    """A fake whose spare replies are answers rather than questions."""

    def __init__(self, *replies) -> None:
        super().__init__(*replies, spare=lambda n: answer_reply())
