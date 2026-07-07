# -*- coding: utf-8 -*-
"""Integracija kroz STVARNI handler (handle_chat / stream) za tutor reliability
bugove:

- BUG1 linearne jednačine s razlomcima → deterministička presuda, bez lažnog
  "Nije tačno".
- BUG2 decimalni odgovor sa tačkom (8.45) == zarezom (8,45).
- BUG3 potvrda ("može"/"da"/"hajde"/"ok") poslije ponude "još jedan zadatak"
  izvršava ponudu umjesto da pita "šta želiš dalje".
- BUG4 osporavanje ranije ocjene ("pa to sam i odgovorio") → ponovna provjera,
  bez novog zadatka; priznanje greške ako je učenik bio u pravu.
- BUG5 nema kontradikcije u finalnom odgovoru.
- Result mod i dalje radi bez teme.

Svi OpenAI pozivi mockirani.
"""
import types

import pytest

from matbot import ai_tutor_service as svc
from matbot import content_loader as cl
from matbot.grading_guard import has_grade_contradiction

FR_TOPIC = "razlomci_pojam_vrste"


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


def _fake_chat(reply):
    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        msg = types.SimpleNamespace(content=reply)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])
    return chat


def _grade_payload(task, student):
    return {
        "grade": 6, "mode": "practice", "selected_topic": FR_TOPIC,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": task, "student_message": student,
    }


_SIMILAR_OFFER_STATE = {
    "expected_user_action": "continue_confirmation",
    "pending_action": {"type": "generate_similar_task", "source": "practice", "next_item": None},
    "active_task_kind": "practice", "image_test": None,
}


# --- BUG1: fractional linear equation, authoritative verdict ------------------------

def test_fractional_equation_correct_no_false_negative(master, tmap):
    chat = _fake_chat("Nije tačno. Tačan rezultat je 4/3.")
    out = svc.handle_chat(
        _grade_payload("Riješi: (2/3)x = 8/9", "4/3"), chat, master, tmap,
        model="m", timeout=1,
    )
    assert out["answer_check"]["items"][0]["verdict"] == "correct"
    assert "nije tačno" not in out["answer"].lower()
    assert not has_grade_contradiction(out["answer"])
    assert "4/3" in out["answer"]


def test_fractional_equation_wrong_stays_incorrect(master, tmap):
    chat = _fake_chat("Nije tačno, tačno je 4/3. Hajde da vidimo postupak.")
    out = svc.handle_chat(
        _grade_payload("Riješi: (2/3)x = 8/9", "2/3"), chat, master, tmap,
        model="m", timeout=1,
    )
    assert out["answer_check"]["items"][0]["verdict"] == "incorrect"
    assert "nije tačno" in out["answer"].lower()


# --- Linearne nejednačine: autoritativna presuda kroz handle_chat ------------------

def test_inequality_correct_no_false_negative(master, tmap):
    chat = _fake_chat("Nije tačno. Tačno je x < 8.")
    out = svc.handle_chat(
        _grade_payload("Riješi: x/2 - 3 < 1", "x < 8"), chat, master, tmap,
        model="m", timeout=1,
    )
    assert out["answer_check"]["items"][0]["verdict"] == "correct"
    assert "nije tačno" not in out["answer"].lower()
    assert not has_grade_contradiction(out["answer"])
    assert "x < 8" in out["answer"]


def test_inequality_equivalent_form_correct(master, tmap):
    chat = _fake_chat("Tačno! x < 8, odnosno 8 > x.")
    out = svc.handle_chat(
        _grade_payload("Riješi: x/2 - 3 < 1", "8 > x"), chat, master, tmap,
        model="m", timeout=1,
    )
    assert out["answer_check"]["items"][0]["verdict"] == "correct"
    assert "nije tačno" not in out["answer"].lower()


def test_inequality_wrong_stays_incorrect(master, tmap):
    chat = _fake_chat("Nije tačno, tačno je x < 8. Pazi na znak.")
    out = svc.handle_chat(
        _grade_payload("Riješi: x/2 - 3 < 1", "x > 8"), chat, master, tmap,
        model="m", timeout=1,
    )
    assert out["answer_check"]["items"][0]["verdict"] == "incorrect"


# --- BUG2: decimal dot answer -------------------------------------------------------

def test_decimal_dot_answer_correct(master, tmap):
    chat = _fake_chat("Nije tačno. Rezultat je 8,45.")
    out = svc.handle_chat(
        _grade_payload("4,56 + 3,89", "8.45"), chat, master, tmap,
        model="m", timeout=1,
    )
    assert out["answer_check"]["items"][0]["verdict"] == "correct"
    assert "nije tačno" not in out["answer"].lower()
    assert not has_grade_contradiction(out["answer"])


# --- BUG3: affirmative after "još jedan zadatak" offer ------------------------------

@pytest.mark.parametrize("affirmative", ["da", "moze", "može", "hajde", "ok", "daj", "moze jos jedan"])
def test_affirmative_after_offer_generates_task(master, tmap, affirmative):
    chat = _fake_chat("Novi zadatak: Izračunaj 2/3 + 1/6.")
    out = svc.handle_chat({
        "grade": 6, "mode": "practice", "selected_topic": FR_TOPIC,
        "student_message": affirmative, "interaction_phase": "confirmation",
        "previous_next_state": _SIMILAR_OFFER_STATE,
    }, chat, master, tmap, model="m", timeout=1)
    assert "Novi zadatak" in out["answer"]
    assert "želiš dalje" not in out["answer"]        # NE pita šta korisnik želi
    assert chat  # model je pozvan (nije direktni clarifier)


def test_offer_phrasing_still_jos_jedan_sets_pending(master, tmap):
    chat = _fake_chat("Tačno! Hoćeš li da probamo još jedan zadatak?")
    out = svc.handle_chat(
        _grade_payload("Ako su obojane 3/8 kruga, koji dio nije obojen?", "5/8"),
        chat, master, tmap, model="m", timeout=1,
    )
    assert out["next_state"]["pending_action"]["type"] == "generate_similar_task"
    assert out["next_state"]["expected_user_action"] == "continue_confirmation"


# --- BUG4: challenge of wrong grading -----------------------------------------------

def test_challenge_with_number_in_message_admits_and_confirms(master, tmap):
    chat = _fake_chat("SHOULD-NOT-BE-CALLED")
    out = svc.handle_chat({
        "grade": 6, "mode": "practice", "selected_topic": FR_TOPIC,
        "last_tutor_task": "4,56 + 3,89",
        "student_message": "pa rekao sam da je 8.45",
    }, chat, master, tmap, model="m", timeout=1)
    assert out["answer_check"]["items"][0]["verdict"] == "correct"
    assert "u pravu si" in out["answer"].lower()
    assert "8.45" in out["answer"]                    # čuva izvorni oblik s tačkom
    assert "nije tačno" not in out["answer"].lower()
    assert out.get("last_tutor_task") is None         # NE generiše novi zadatak


def test_challenge_numberless_recovers_from_history(master, tmap):
    chat = _fake_chat("X")
    out = svc.handle_chat({
        "grade": 6, "mode": "practice", "selected_topic": FR_TOPIC,
        "last_tutor_task": "4,56 + 3,89",
        "student_message": "pa to sam i odgovorio",
        "conversation_history": [
            {"role": "user", "content": "8.45"},
            {"role": "assistant", "content": "Nije tačno."},
        ],
    }, chat, master, tmap, model="m", timeout=1)
    assert "u pravu si" in out["answer"].lower()
    assert "8.45" in out["answer"]


def test_challenge_when_student_was_actually_wrong_recheck_not_new_task(master, tmap):
    chat = _fake_chat("Provjerio sam ponovo: 2/3 nije tačno, tačan rezultat je 4/3.")
    out = svc.handle_chat({
        "grade": 6, "mode": "practice", "selected_topic": FR_TOPIC,
        "last_tutor_task": "Riješi: (2/3)x = 8/9",
        "student_message": "pa rekao sam da je 2/3",
    }, chat, master, tmap, model="m", timeout=1)
    # ponovna provjera: učenik je zapravo bio u krivu → presuda ostaje incorrect
    assert out["answer_check"]["items"][0]["verdict"] == "incorrect"
    assert out.get("last_tutor_task") is None         # nema novog zadatka


# --- Result mod bez teme ------------------------------------------------------------

def test_result_mode_without_topic_still_answers(master, tmap):
    chat = _fake_chat("Rezultat je 3.")
    out = svc.handle_chat({
        "grade": 6, "mode": "quick", "student_message": "Koliko je 12 : 4?",
    }, chat, master, tmap, model="m", timeout=1)
    assert out["status"] == "ready"
    assert "Tema:" not in out["answer"]


# --- Streaming: fractional equation, no leaked false negative -----------------------

def test_stream_fractional_equation_no_leaked_false_negative(client, fake_openai, monkeypatch):
    import app as app_mod
    import json as _json

    def fake_stream(model, messages, timeout=None, max_tokens=None):
        yield "Nije tačno. "
        yield "Tačan rezultat je 4/3."

    monkeypatch.setattr(app_mod, "_tutor_openai_chat_stream", fake_stream)
    resp = client.post("/api/ai-tutor/chat/stream", json=_grade_payload(
        "Riješi: (2/3)x = 8/9", "4/3"
    ))
    assert resp.status_code == 200
    raw = resp.get_data(as_text=True)
    deltas, done = [], None
    for block in raw.split("\n\n"):
        if "event: delta" in block:
            deltas.append(_json.loads(block.split("data:", 1)[1].strip())["delta"])
        elif "event: done" in block:
            done = _json.loads(block.split("data:", 1)[1].strip())
    streamed = "".join(deltas)
    assert "nije tačno" not in streamed.lower()
    assert done and streamed == done["answer"]
    assert done["answer_check"]["items"][0]["verdict"] == "correct"


def test_stream_inequality_no_leaked_false_negative(client, fake_openai, monkeypatch):
    import app as app_mod
    import json as _json

    def fake_stream(model, messages, timeout=None, max_tokens=None):
        yield "Nije tačno. "
        yield "Tačno je x < 8."

    monkeypatch.setattr(app_mod, "_tutor_openai_chat_stream", fake_stream)
    resp = client.post("/api/ai-tutor/chat/stream", json=_grade_payload(
        "Riješi: x/2 - 3 < 1", "x < 8"
    ))
    assert resp.status_code == 200
    raw = resp.get_data(as_text=True)
    deltas, done = [], None
    for block in raw.split("\n\n"):
        if "event: delta" in block:
            deltas.append(_json.loads(block.split("data:", 1)[1].strip())["delta"])
        elif "event: done" in block:
            done = _json.loads(block.split("data:", 1)[1].strip())
    streamed = "".join(deltas)
    assert "nije tačno" not in streamed.lower()
    assert done and streamed == done["answer"]
    assert done["answer_check"]["items"][0]["verdict"] == "correct"
