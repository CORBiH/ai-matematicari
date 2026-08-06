"""Faza 4H — akcije nad AKTIVNIM determinističkim zadatkom bez ijednog poziva.

Ocjena klika je oduvijek SERVERSKA činjenica; model je dosad samo verbalizovao
povratnu poruku (~7,7 s medijalno). Za server-generisan zadatak i povratna
poruka, ljestvica nagovještaja i potpuno rješenje su SERVERSKE činjenice
pohranjene pri objavi — pa se služe trenutno, bez modela.
"""
import logging

import pytest

from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import FakeLLM, make_tutor_draft

GRADE, LESSON, SESSION = 6, "6-04-009", "det-zero"


def turn(message="Daj mi jedan zadatak za vježbu iz ove teme.", **changes):
    payload = {
        "session_id": SESSION, "grade": GRADE, "selected_topic": LESSON,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    payload.update(changes)
    return payload


@pytest.fixture
def universal(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


@pytest.fixture
def published(universal):
    store, fake = SessionStore(), FakeLLM()
    response = run_practice_turn(store, fake, turn())
    assert response["status"] == "ready" and fake.call_count == 0
    return store, fake


def session_of(store):
    return store.peek(SESSION)


def click(store, fake, option_id, turn_id):
    return run_practice_turn(store, fake, turn(
        message="[odgovor]", interaction_type="choice_answer",
        selected_option_id=option_id, client_turn_id=turn_id))


def wrong_ids(store):
    session = session_of(store)
    return [option["id"] for option in session["current_options"]
            if option["id"] != session["correct_option_id"]]


# ---------------------------------------------------------------------------
# 1) OCJENA KLIKA — NULA POZIVA (Workstream E)
# ---------------------------------------------------------------------------

def test_correct_click_grades_with_zero_calls(published, caplog):
    caplog.set_level(logging.INFO, logger="matbot.tutor")
    store, fake = published
    correct = session_of(store)["correct_option_id"]
    annex = session_of(store)["deterministic_task"]

    response = click(store, fake, correct, "t1")

    assert fake.call_count == 0
    assert response["answer_verdict"] == "correct"
    assert annex["display_answer"] in response["answer"]
    assert session_of(store)["task_completed"] is True
    line = next(l for l in caplog.text.splitlines() if "tutor_choice" in l)
    assert "route=deterministic_grading" in line and "calls=0" in line


def test_first_wrong_click_hints_without_revealing(published):
    store, fake = published
    annex = session_of(store)["deterministic_task"]

    response = click(store, fake, wrong_ids(store)[0], "t1")

    assert fake.call_count == 0
    assert response["answer_verdict"] == "incorrect"
    assert "revealed_correct_option_id" not in response
    assert annex["display_answer"] not in response["answer"]
    session = session_of(store)
    assert session["task_completed"] is False
    assert session["wrong_option_ids"] == [wrong_ids(store)[0]] or \
        len(session["wrong_option_ids"]) == 1


def test_second_wrong_click_reveals_the_stored_answer(published):
    store, fake = published
    first, second = wrong_ids(store)[:2]
    click(store, fake, first, "t1")

    response = click(store, fake, second, "t2")

    assert fake.call_count == 0
    assert response["answer_verdict"] == "incorrect"
    assert response["revealed_correct_option_id"] == \
        session_of(store)["correct_option_id"]
    assert session_of(store)["task_completed"] is True


def test_repeated_click_is_idempotent_and_free(published):
    store, fake = published
    correct = session_of(store)["correct_option_id"]
    first = click(store, fake, correct, "t1")
    before = session_of(store)

    again = click(store, fake, correct, "t1")

    assert fake.call_count == 0
    assert again == first
    assert session_of(store) == before


def test_click_after_solution_is_blocked_without_calls(published):
    store, fake = published
    run_practice_turn(store, fake, turn(
        message="Uradi ga ti.", intent="solution_request"))
    before = session_of(store)

    response = click(store, fake, before["correct_option_id"], "t9")

    assert fake.call_count == 0
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert session_of(store) == before


def test_stale_deterministic_annex_never_grades_another_task(published):
    """Ako identitet dodatka NE odgovara aktivnom zadatku, deterministička
    ocjena se NE SMIJE upotrijebiti — turn ide model-putem."""
    store, fake = published
    session = session_of(store)
    session["deterministic_task"]["task_identity"] = "tudji-identitet"
    store.save(session)
    fake.queue(make_tutor_draft(intent="answer_attempt", reply="Tačno!",
                                grading="correct"))

    response = click(store, fake, session["correct_option_id"], "t1")

    assert fake.call_count == 1        # model je verbalizovao, ne dodatak
    assert response["answer_verdict"] == "correct"


# ---------------------------------------------------------------------------
# 2) NAGOVJEŠTAJI — NULA POZIVA (Workstream G)
# ---------------------------------------------------------------------------

def hint_turn(turn_id):
    return turn(message="Ne znam.", intent="hint_request",
                interaction_phase="practice_help", client_turn_id=turn_id)


def test_hint_ladder_serves_stored_hints_in_order(published, caplog):
    caplog.set_level(logging.INFO, logger="matbot.tutor")
    store, fake = published
    annex = session_of(store)["deterministic_task"]

    for index, turn_id in enumerate(("h1", "h2", "h3")):
        response = run_practice_turn(store, fake, hint_turn(turn_id))
        assert fake.call_count == 0
        assert annex["hints"][index] in response["answer"]
        assert response["last_tutor_task"] == session_of(store)["current_task"]
        assert session_of(store)["hint_level"] == index + 1

    assert "route=deterministic_hint" in caplog.text
    # Četvrti zahtjev ostaje na vrhu ljestvice, bez pada.
    top = run_practice_turn(store, fake, hint_turn("h4"))
    assert annex["hints"][2] in top["answer"]
    assert fake.call_count == 0


def test_first_hint_never_reveals_the_answer(published):
    store, fake = published
    annex = session_of(store)["deterministic_task"]
    response = run_practice_turn(store, fake, hint_turn("h1"))
    assert annex["display_answer"] not in response["answer"]
    assert "revealed_correct_option_id" not in response


def test_hint_retry_with_same_turn_id_is_idempotent(published):
    store, fake = published
    first = run_practice_turn(store, fake, hint_turn("h1"))
    level_before = session_of(store)["hint_level"]

    again = run_practice_turn(store, fake, hint_turn("h1"))

    assert fake.call_count == 0
    assert again == first
    assert session_of(store)["hint_level"] == level_before


# ---------------------------------------------------------------------------
# 3) CIJELO RJEŠENJE — NULA POZIVA (Workstream F)
# ---------------------------------------------------------------------------

def test_full_solution_serves_stored_steps_with_zero_calls(published, caplog):
    caplog.set_level(logging.INFO, logger="matbot.tutor")
    store, fake = published
    session = session_of(store)
    annex = session["deterministic_task"]

    response = run_practice_turn(store, fake, turn(
        message="Uradi ga ti.", intent="solution_request",
        interaction_phase="practice_help", client_turn_id="s1"))

    assert fake.call_count == 0
    assert annex["solution"] in response["answer"]
    assert annex["display_answer"] in response["answer"]
    assert response["revealed_correct_option_id"] == session["correct_option_id"]
    assert response["last_tutor_task"] == session["current_task"]
    after = session_of(store)
    assert after["task_completed"] is True
    assert after["last_result"] == "full_solution"
    assert after["current_task"] == session["current_task"]
    assert "route=deterministic_solution" in caplog.text


def test_model_task_still_uses_the_model_for_help(universal):
    """Model-objavljen zadatak (bez dodatka) i dalje traži model za pomoć."""
    store, fake = SessionStore(), FakeLLM()
    run_practice_turn(store, fake, turn())          # deterministički zadatak
    session = store.peek(SESSION)
    session["deterministic_task"] = None            # kao da je model objavio
    store.save(session)
    fake.queue(make_tutor_draft(intent="hint_request", reply="Pogledaj imenioce.",
                                hint="Pogledaj imenioce."))

    response = run_practice_turn(store, fake, hint_turn("h1"))

    assert response["status"] == "ready"
    assert fake.call_count == 1
