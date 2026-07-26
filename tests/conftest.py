import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from matbot.llm import LLMResult, LLMTimeout, LLMUnavailable  # noqa: E402
from matbot.schema import NewTask, PracticeTurnOutput  # noqa: E402
from matbot.session_store import SessionStore  # noqa: E402


def make_output(reply="U redu.", evaluation=None, gave_hint=False, new_task=None):
    return PracticeTurnOutput(
        reply=reply, evaluation=evaluation, gave_hint=gave_hint, new_task=new_task
    )


def make_task(text="Skrati razlomak $\\frac{20}{32}$.", expected="5/8", difficulty="standard"):
    return NewTask(text=text, expected_answer=expected, difficulty=difficulty)


class FakeLLM:
    """Deterministički LLM za testove: redom vraća pripremljene odgovore ili
    baca pripremljene izuzetke; broji pozive i pamti promptove."""

    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []  # (instructions, input_text)

    def queue(self, item):
        self.results.append(item)

    @property
    def call_count(self):
        return len(self.calls)

    def practice_turn(self, instructions, input_text):
        self.calls.append((instructions, input_text))
        if not self.results:
            raise AssertionError("FakeLLM: nije pripremljen odgovor za ovaj poziv")
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResult(output=item, latency_ms=5, usage={"input_tokens": 100, "output_tokens": 50})


@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest.fixture
def store():
    return SessionStore()


@pytest.fixture
def flask_app(fake_llm, store):
    from app import app

    app.testing = True
    app.config["MATBOT_LLM"] = fake_llm
    app.config["MATBOT_SESSION_STORE"] = store
    yield app
    app.config.pop("MATBOT_LLM", None)
    app.config.pop("MATBOT_SESSION_STORE", None)


@pytest.fixture
def client(flask_app):
    return flask_app.test_client()


# eksporti za testove
__all__ = ["FakeLLM", "make_output", "make_task", "LLMTimeout", "LLMUnavailable"]
