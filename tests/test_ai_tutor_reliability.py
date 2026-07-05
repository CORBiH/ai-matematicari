"""Phase 1 (audit) — pouzdanost tutor odgovora: prazan/odsječen odgovor,
retry sa većim max_tokens, quick cap 400, tutor-specifični max_retries.

Sve OpenAI pozive mockiraju lokalni fake-ovi — nikad stvarni API.
"""
import types

import pytest

from matbot import ai_tutor_service as svc
from matbot import content_loader as cl

CHAT_URL = "/api/ai-tutor/chat"
TOPIC = "skupovi_uvod"


@pytest.fixture(autouse=True)
def _tmp_activity_db(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "activity.sqlite3"))
    yield


@pytest.fixture(scope="module")
def master():
    return cl.load_master_content()


@pytest.fixture(scope="module")
def tmap():
    return cl.load_thinkific_map()


def _resp(text, finish=None):
    """OpenAI-like odgovor; finish_reason je opcion (kao i kod mockova)."""
    choice = types.SimpleNamespace(message=types.SimpleNamespace(content=text))
    if finish is not None:
        choice.finish_reason = finish
    return types.SimpleNamespace(choices=[choice])


def _seq_chat(responses):
    """Fake openai_chat koji redom vraća zadane odgovore (ili baca izuzetak)."""
    calls = {"n": 0, "max_tokens": []}

    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kwargs):
        calls["max_tokens"].append(max_tokens)
        r = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        if isinstance(r, Exception):
            raise r
        return r

    chat.calls = calls
    return chat


def _payload(mode="explain"):
    return {"selected_topic": TOPIC, "mode": mode, "student_message": "test"}


# --- retry na prazan/odsječen odgovor ----------------------------------------------

def test_normal_answer_no_extra_calls(master, tmap):
    chat = _seq_chat([_resp("Sve jasno objašnjeno.")])
    out = svc.handle_chat(_payload(), chat, master, tmap, model="m", timeout=1)
    assert out["answer"] == "Sve jasno objašnjeno."
    assert chat.calls["n"] == 1                       # bez nepotrebnog retry-a


def test_empty_answer_retried_with_higher_cap(master, tmap):
    chat = _seq_chat([_resp(""), _resp("Drugi pokušaj radi.")])
    out = svc.handle_chat(_payload("explain"), chat, master, tmap, model="m", timeout=1)
    assert out["answer"] == "Drugi pokušaj radi."
    assert chat.calls["n"] == 2
    # retry ide sa duplo većim budžetom (700 → 1400)
    assert chat.calls["max_tokens"] == [700, 1400]


def test_truncated_answer_retried(master, tmap):
    chat = _seq_chat([_resp("Pola odgovora...", finish="length"),
                      _resp("Cijeli odgovor.")])
    out = svc.handle_chat(_payload(), chat, master, tmap, model="m", timeout=1)
    assert out["answer"] == "Cijeli odgovor."
    assert chat.calls["n"] == 2


def test_truncated_retry_failure_keeps_partial_answer(master, tmap):
    chat = _seq_chat([_resp("Pola odgovora...", finish="length"),
                      RuntimeError("api pao")])
    out = svc.handle_chat(_payload(), chat, master, tmap, model="m", timeout=1)
    assert out["answer"] == "Pola odgovora..."        # bolje išta nego ništa
    assert out["status"] == "ready"


def test_empty_twice_returns_friendly_message(master, tmap):
    chat = _seq_chat([_resp(""), _resp("")])
    out = svc.handle_chat(_payload(), chat, master, tmap, model="m", timeout=1)
    assert out["answer"] == svc._EMPTY_ANSWER_FALLBACK
    assert "Pokušaj ponovo" in out["answer"]          # razumljivo djetetu
    assert chat.calls["n"] == 2                       # tačno JEDAN retry


def test_first_call_exception_still_propagates(master, tmap):
    """Postojeće ponašanje: pad PRVOG poziva ide do rute (500) — bez tihog gutanja."""
    chat = _seq_chat([RuntimeError("api pao")])
    with pytest.raises(RuntimeError):
        svc.handle_chat(_payload(), chat, master, tmap, model="m", timeout=1)


# --- max_tokens po modu -------------------------------------------------------------

def test_quick_mode_cap_raised_to_400(master, tmap):
    chat = _seq_chat([_resp("4")])
    svc.handle_chat(_payload("quick"), chat, master, tmap, model="m", timeout=1)
    assert chat.calls["max_tokens"][0] == 400


# --- ruta koristi tutor-specifični timeout i max_retries ----------------------------

def test_route_uses_tutor_retries_and_timeout(client, fake_openai):
    import app as app_mod
    resp = client.post(CHAT_URL, json={"selected_topic": TOPIC, "mode": "explain"})
    assert resp.status_code == 200
    # ruta ide kroz _tutor_openai_chat → max_retries=AI_TUTOR_MAX_RETRIES (default 1)
    assert fake_openai.calls.kwargs[-1].get("max_retries") == app_mod.AI_TUTOR_MAX_RETRIES == 1
    # tutor timeout je kraći od legacy HARD_TIMEOUT-a
    assert app_mod.AI_TUTOR_TIMEOUT == 45.0
    assert app_mod.AI_TUTOR_TIMEOUT < app_mod.HARD_TIMEOUT_S
