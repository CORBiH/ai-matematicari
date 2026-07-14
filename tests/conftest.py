"""Pytest konfiguracija za MAT-BOT.

VAŽNO: sve env varijable se postavljaju PRIJE importa `app` modula, jer se
konfiguracija čita pri importu. `load_dotenv(override=False)` u app.py ne može
pregaziti ovo (eksplicitni env ima prednost), pa testovi nikad ne koriste
stvarne ključeve ni stvarne servise.
"""
import os
import sys
import types

import pytest

# --- Env MORA biti postavljen prije importa app ---
os.environ["LOCAL_MODE"] = "1"
os.environ["OPENAI_API_KEY"] = "test-key-not-real"
os.environ["FLASK_SECRET_KEY"] = "test-secret-key"
os.environ["RATE_LIMIT_ENABLED"] = "1"
os.environ["RATE_LIMIT_SUBMIT"] = "100000 per minute"   # darežljivo; poseban test ovo suzi
os.environ["RATE_LIMIT_DIAG"] = "100000 per minute"
os.environ["MAX_CONTENT_LENGTH_MB"] = "1"               # za 413 test
os.environ["USE_MATHPIX"] = "0"
os.environ["MATHPIX_APP_ID"] = ""
os.environ["MATHPIX_APP_KEY"] = ""

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as matbot  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Izolacija svakog testa: resetovan limiter, bez mreže, bez stvarnog OpenAI-ja."""
    try:
        matbot.limiter.reset()
    except Exception:
        pass

    # Sigurnosna mreža: nijedan test ne smije napraviti stvaran HTTP poziv.
    def _blocked(*args, **kwargs):
        raise AssertionError("Test je pokušao stvarni mrežni poziv (requests).")
    monkeypatch.setattr(matbot.requests, "get", _blocked)
    monkeypatch.setattr(matbot.requests, "post", _blocked)

    # OpenAI: mora biti eksplicitno mockiran preko fake_openai fixtura.
    def _no_openai(*args, **kwargs):
        raise AssertionError("OpenAI poziv nije mockiran — koristi fake_openai fixture.")
    monkeypatch.setattr(matbot, "_openai_chat", _no_openai)
    yield


@pytest.fixture()
def fake_openai(monkeypatch):
    """Zamjena za _openai_chat: snima primljene poruke i vraća zadani odgovor.

    state["reply"]        — tekst koji 'model' vraća
    state["raise_fast"]   — exception samo za fast=True pozive (sync pokušaj)
    state["raise_always"] — exception za svaki poziv
    """
    calls = types.SimpleNamespace(messages=[], models=[], fast_flags=[], max_tokens=[], kwargs=[])
    state = {"reply": "Test odgovor: x = 3", "raise_fast": None, "raise_always": None}

    def _fake(model, messages, timeout=None, max_tokens=None, fast=False, **kwargs):
        calls.messages.append(messages)
        calls.models.append(model)
        calls.fast_flags.append(fast)
        calls.max_tokens.append(max_tokens)
        calls.kwargs.append(kwargs)
        if state["raise_always"] is not None:
            raise state["raise_always"]
        if fast and state["raise_fast"] is not None:
            raise state["raise_fast"]
        msg = types.SimpleNamespace(content=state["reply"])
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=msg)],
            model=f"{model}-test",
        )

    monkeypatch.setattr(matbot, "_openai_chat", _fake)
    return types.SimpleNamespace(calls=calls, state=state)


@pytest.fixture()
def sync_enqueue(monkeypatch):
    """Async jobovi se izvršavaju odmah (bez threadova) — deterministični testovi."""
    monkeypatch.setattr(matbot, "_enqueue", lambda payload: matbot._local_worker(payload))


@pytest.fixture()
def client():
    matbot.app.config["TESTING"] = True
    with matbot.app.test_client() as c:
        yield c
