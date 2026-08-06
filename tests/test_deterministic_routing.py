"""Faza 4H — deterministička ruta izrade zadatka unutar JEDNOG orkestratora.

Za lekcije porodice `fraction_arithmetic_direct` (blocking ugovor + potpun
generator) strukturisane akcije izrade zadatka — svjež, nov, lakši, teži —
ne prave NIJEDAN poziv modela: server generiše paket, dokaže ga istim
validatorima kao model-paket i objavi ISTIM kodom objave.

Ruta se bira isključivo iz server-vlasničkih činjenica (lekcija, porodica,
UI polja, ZATVOREN skup jednostavnih poruka) — nikad iz modelove proze.
Slobodne poruke i lekcije bez potpunog generatora ostaju na model-putu.
"""
import logging

import pytest

from matbot.deterministic import fractions as detfrac
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import pipeline as tutor_pipeline
from tests.conftest import (FakeLLM, make_reviewer_final, make_task_payload,
                            make_tutor_draft)

GRADE = 6
DET_LESSON = "6-04-009"          # porodica s potpunim generatorom
MODEL_LESSON = "6-03-004"        # bez generatora — ostaje model-put
SESSION = "det-route"


def turn(message="Daj mi jedan zadatak za vježbu iz ove teme.", lesson=DET_LESSON,
         **changes):
    payload = {
        "session_id": SESSION, "grade": GRADE, "selected_topic": lesson,
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


# ---------------------------------------------------------------------------
# 1) NULA POZIVA ZA SVJEŽ / NOV / LAKŠI / TEŽI
# ---------------------------------------------------------------------------

def test_fresh_deterministic_task_uses_zero_calls(universal, caplog):
    caplog.set_level(logging.INFO, logger="matbot.tutor")
    store, fake = SessionStore(), FakeLLM()

    response = run_practice_turn(store, fake, turn())

    assert response["status"] == "ready"
    assert fake.call_count == 0
    assert response["answer"].startswith("Evo zadatka.")
    session = store.peek(SESSION)
    assert session["current_task"]
    assert len(session["current_options"]) == 4
    assert session["deterministic_task"]
    assert session["deterministic_task"]["task_identity"] == \
        session["current_task_identity"]
    line = next(l for l in caplog.text.splitlines()
                if "tutor_turn_diagnostics" in l)
    assert "route=deterministic_package" in line
    assert "calls=0" in line


def test_new_task_is_canonically_different_and_zero_calls(universal):
    store, fake = SessionStore(), FakeLLM()
    run_practice_turn(store, fake, turn())
    first_identity = store.peek(SESSION)["current_task_identity"]

    response = run_practice_turn(store, fake, turn("Daj mi novi zadatak."))

    assert response["status"] == "ready"
    assert fake.call_count == 0
    assert response["answer"].startswith("Evo sljedećeg zadatka.")
    assert store.peek(SESSION)["current_task_identity"] != first_identity


def test_harder_then_easier_transitions_are_server_owned(universal):
    store, fake = SessionStore(), FakeLLM()
    run_practice_turn(store, fake, turn())

    harder = run_practice_turn(store, fake, turn(
        "Daj mi teži zadatak.", difficulty_request="harder"))
    assert harder["status"] == "ready"
    assert harder["answer"].startswith("Evo težeg zadatka.")
    assert store.peek(SESSION)["difficulty_level"] == 2

    easier = run_practice_turn(store, fake, turn(
        "Daj mi lakši zadatak.", difficulty_request="easier"))
    assert easier["status"] == "ready"
    assert easier["answer"].startswith("Evo lakšeg zadatka.")
    assert store.peek(SESSION)["difficulty_level"] == 1
    assert fake.call_count == 0


def test_easier_at_level_one_is_truthful(universal):
    store, fake = SessionStore(), FakeLLM()
    run_practice_turn(store, fake, turn())
    identity = store.peek(SESSION)["current_task_identity"]

    response = run_practice_turn(store, fake, turn(
        "Daj mi lakši zadatak.", difficulty_request="easier"))

    assert response["status"] == "ready"
    assert response["answer"].startswith(tutor_pipeline.INTRO_AT_EASIEST_LEVEL)
    session = store.peek(SESSION)
    assert session["difficulty_level"] == 1
    assert session["current_task_identity"] != identity
    assert fake.call_count == 0


def test_harder_at_level_three_is_truthful(universal):
    store, fake = SessionStore(), FakeLLM()
    run_practice_turn(store, fake, turn())
    for _ in range(2):
        run_practice_turn(store, fake, turn(
            "Daj mi teži zadatak.", difficulty_request="harder"))
    assert store.peek(SESSION)["difficulty_level"] == 3

    response = run_practice_turn(store, fake, turn(
        "Daj mi teži zadatak.", difficulty_request="harder"))

    assert response["answer"].startswith(tutor_pipeline.INTRO_AT_HARDEST_LEVEL)
    assert store.peek(SESSION)["difficulty_level"] == 3
    assert fake.call_count == 0


def test_typed_easier_without_chip_is_still_deterministic(universal):
    store, fake = SessionStore(), FakeLLM()
    run_practice_turn(store, fake, turn())
    response = run_practice_turn(store, fake, turn("Daj mi lakši zadatak."))
    assert response["status"] == "ready"
    assert fake.call_count == 0


# ---------------------------------------------------------------------------
# 2) SIGURAN NEUSPJEH I KONTINUITET
# ---------------------------------------------------------------------------

def test_failed_generation_preserves_the_active_task(universal, monkeypatch):
    store, fake = SessionStore(), FakeLLM()
    run_practice_turn(store, fake, turn())
    before = store.peek(SESSION)

    def boom(**_kwargs):
        raise detfrac.DeterministicGenerationError("test")

    monkeypatch.setattr(detfrac, "generate_package", boom)
    response = run_practice_turn(store, fake, turn("Daj mi novi zadatak."))

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert response["last_tutor_task"] == before["current_task"]
    after = store.peek(SESSION)
    assert after["current_task_identity"] == before["current_task_identity"]
    assert after["difficulty_level"] == before["difficulty_level"]
    assert fake.call_count == 0


def test_successive_new_tasks_never_repeat_canonically(universal):
    store, fake = SessionStore(), FakeLLM()
    run_practice_turn(store, fake, turn())
    identities = {store.peek(SESSION)["current_task_identity"]}
    for _ in range(5):
        response = run_practice_turn(store, fake, turn("Daj mi novi zadatak."))
        assert response["status"] == "ready"
        identities.add(store.peek(SESSION)["current_task_identity"])
    assert len(identities) == 6
    assert fake.call_count == 0


# ---------------------------------------------------------------------------
# 3) MODEL-PUT OSTAJE ZA SLOBODNE PORUKE I NEPOKRIVENE LEKCIJE
# ---------------------------------------------------------------------------

def test_free_form_question_still_uses_the_model(universal):
    store, fake = SessionStore(), FakeLLM()
    run_practice_turn(store, fake, turn())
    fake.queue(make_tutor_draft(intent="clarification",
                                reply="Sabiraju se samo brojnici."))

    response = run_practice_turn(store, fake, turn(
        "Zašto se imenioci ne sabiraju kad sabiram razlomke?"))

    assert response["status"] == "ready"
    assert fake.call_count == 1        # pomoćni turn: samo Tutor


def test_unsupported_lesson_still_uses_tutor_and_reviewer(universal):
    store, fake = SessionStore(), FakeLLM()
    task = make_task_payload(
        text="Koji od ponuđenih brojeva je djeljiv sa 25?",
        options=("725", "714", "738", "741"), correct_option_index=0,
        expected="725")
    draft = make_tutor_draft(intent="generate_task", new_task=task)
    fake.queue(draft)
    fake.queue(make_reviewer_final(final=draft))

    response = run_practice_turn(
        store, fake, turn(lesson=MODEL_LESSON, session_id="det-route-model"))

    assert response["status"] == "ready"
    assert fake.call_count == 2        # Tutor + Recenzent, kao i dosad


def test_model_published_task_clears_stale_deterministic_state(universal):
    """Model-paket NIKAD ne smije naslijediti deterministički dodatak sesije."""
    store, fake = SessionStore(), FakeLLM()
    run_practice_turn(store, fake, turn())
    assert store.peek(SESSION)["deterministic_task"]

    task = make_task_payload(
        text="Izračunaj: $\\frac{1}{5}+\\frac{2}{5}$",
        options=("$\\frac{3}{5}$", "$\\frac{3}{10}$", "$\\frac{2}{5}$",
                 "$\\frac{1}{5}$"),
        correct_option_index=0, expected="$\\frac{3}{5}$")
    task = task.model_copy(update={
        "selected_lesson_id": DET_LESSON,
        "selected_lesson_title": store.peek(SESSION)["lesson_title"]})
    draft = make_tutor_draft(intent="next_task", new_task=task)
    fake.queue(draft)
    fake.queue(make_reviewer_final(final=draft))

    # Slobodna formulacija → model-put, iako je lekcija deterministički pokrivena.
    response = run_practice_turn(store, fake, turn(
        "Molim te sastavi mi jedan lijep zadatak sabiranja razlomaka petina."))

    assert response["status"] == "ready"
    assert fake.call_count == 2
    assert not store.peek(SESSION)["deterministic_task"]
