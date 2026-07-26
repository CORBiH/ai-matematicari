import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# FLASK_SECRET_KEY je obavezan za produkciju (app.py odbija start bez njega).
# Testovi EKSPLICITNO postavljaju jasno označen nesiguran default — ovo je
# jedino mjesto gdje se to smije desiti (vidi matbot/config.py).
os.environ.setdefault("FLASK_SECRET_KEY", "test-only-insecure-secret-DO-NOT-USE-IN-PRODUCTION")

from matbot import auth  # noqa: E402
from matbot.llm import LLMResult, LLMTimeout, LLMUnavailable  # noqa: E402
from matbot.ratelimit import RateLimiter  # noqa: E402
from matbot.schema import ExplainTurnOutput, NewTask, PracticeTurnOutput  # noqa: E402
from matbot.session_store import SessionStore  # noqa: E402
from matbot.turnlock import TurnLockRegistry  # noqa: E402


def make_output(reply="U redu.", evaluation=None, gave_hint=False, new_task=None):
    return PracticeTurnOutput(
        reply=reply, evaluation=evaluation, gave_hint=gave_hint, new_task=new_task
    )


def make_explain_output(reply="Evo objašnjenja."):
    return ExplainTurnOutput(reply=reply)


def make_task(text="Skrati razlomak $\\frac{20}{32}$.", expected="5/8", difficulty="standard"):
    return NewTask(text=text, expected_answer=expected, difficulty=difficulty)


class FakeLLM:
    """Deterministički LLM za testove: redom vraća pripremljene odgovore ili
    baca pripremljene izuzetke; broji pozive i pamti promptove.
    call_count broji SVE pozive (practice + explain) — testovi „tačno jedan
    LLM poziv po turnu“ time hvataju i eventualni skriveni poziv drugog moda."""

    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []           # (instructions, input_text) — svi pozivi redom
        self.explain_calls = []   # samo explain pozivi (podskup calls)

    def queue(self, item):
        self.results.append(item)

    @property
    def call_count(self):
        return len(self.calls)

    def _next(self, instructions, input_text):
        self.calls.append((instructions, input_text))
        if not self.results:
            raise AssertionError("FakeLLM: nije pripremljen odgovor za ovaj poziv")
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResult(output=item, latency_ms=5, usage={"input_tokens": 100, "output_tokens": 50})

    def practice_turn(self, instructions, input_text):
        return self._next(instructions, input_text)

    def explain_turn(self, instructions, input_text):
        self.explain_calls.append((instructions, input_text))
        return self._next(instructions, input_text)


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
    # Svjež limiter/lock-registry PO TESTU — bez ovoga bi testovi dijelili
    # brojače kroz zajednički (module-level) Flask app singleton i lažno
    # trošili jedni drugima rate-limit budžet. Limiti su namjerno VISOKI da
    # regresijski testovi (koji nisu O rate limitingu) nikad slučajno okinu 429.
    app.config["MATBOT_SESSION_LIMITER"] = RateLimiter(per_minute=100000, per_hour=100000)
    app.config["MATBOT_IP_LIMITER"] = RateLimiter(per_minute=100000, per_hour=100000)
    app.config["MATBOT_TURN_LOCKS"] = TurnLockRegistry()
    yield app
    app.config.pop("MATBOT_LLM", None)
    app.config.pop("MATBOT_SESSION_STORE", None)
    app.config.pop("MATBOT_SESSION_LIMITER", None)
    app.config.pop("MATBOT_IP_LIMITER", None)
    app.config.pop("MATBOT_TURN_LOCKS", None)


@pytest.fixture
def client(flask_app):
    """Test klijent sa VAŽEĆIM tokenom već postavljenim na svaki zahtjev —
    ogleda stvarno frontend ponašanje (token se šalje na svaki API poziv) bez
    potrebe da svaki postojeći test ručno dodaje header. Testovi koji SPECIFIČNO
    provjeravaju auth ponašanje prave svoj test_client() bez ovog defaulta."""
    c = flask_app.test_client()
    c.environ_base["HTTP_X_TUTOR_TOKEN"] = auth.issue_token()
    return c


# eksporti za testove
__all__ = ["FakeLLM", "make_output", "make_task", "LLMTimeout", "LLMUnavailable"]
