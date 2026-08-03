"""Izolacija Practice stanja po kanonskom kurikularnom kontekstu.

Lekcije korištene ovdje imaju UKLJUČEN ugovor (matbot/contracts/), pa zadatak
KONSTRUIŠE server (generator kostura) — modelov new_task je samo signal.
Zaštite koje se dokazuju su serverske i potpuno nezavisne od ugovora: otisak
kurikuluma, invalidacija aktivnog zadatka, zaštita starog odgovora,
nemogućnost obnove zadatka iz browsera i očuvanje vještine kroz ponovni
pokušaj.
"""
from matbot.contracts import registry
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.topics import lesson_info
from tests.conftest import (FakeLLM, make_output, make_task,
                            make_task_for_family)

EXPANSION = "6-04-005"
EQUAL_ADD_SUB = "6-04-009"
UNLIKE_ADD_SUB = "6-04-010"
MULTIPLICATION = "6-04-011"
GRADE7_SIGNS = "7-02-008"          # bez ugovora → legacy put


def _turn(topic=EXPANSION, grade=6, session_id="lesson-context", **changes):
    turn = {
        "session_id": session_id,
        "grade": grade,
        "selected_topic": topic,
        "selected_oblast": "nepouzdan-klijentski-unos",
        "student_message": "Daj mi jedan zadatak za vježbu.",
        "intent": "",
        "difficulty_request": "",
        "interaction_phase": "",
        "last_tutor_task": "",
        "interaction_type": "student_question",
        "selected_option_id": "",
        "client_turn_id": "",
    }
    turn.update(changes)
    return turn


# --- zadaci po lekciji -------------------------------------------------------
# Lekcija s ugovorom: server generiše zadatak; modelov new_task je SAMO signal
# (sadržaj se ignoriše). Lekcija bez ugovora: nepromijenjen legacy put.

def _give_task(store, fake, topic=EXPANSION, grade=6, suffix="", **changes):
    """Pošalji zadatak koji odgovara načinu rada TE lekcije (ugovor ili legacy)."""
    contract = registry.contract_for(topic)
    if contract is not None:
        shape = contract.effective_archetypes[0]
        new_task = make_task()   # signal — server objavljuje SVOJ kostur
    else:
        info = lesson_info(grade, topic)
        from matbot import task_families as tf

        shape = tf.select_family(
            tf.applicable_families(grade, info["oblast"], info["title"], lesson_id=topic)
        )
        new_task = make_task_for_family(shape, suffix=suffix)
    fake.queue(make_output(reply="Evo zadatka.", new_task=new_task))
    response = run_practice_turn(store, fake, _turn(topic, grade, **changes))
    assert response.get("status") == "ready", response
    return response, shape


def test_same_session_topic_and_grade_switches_replace_the_entire_context():
    store, fake = SessionStore(), FakeLLM()

    expansion_response, expansion_shape = _give_task(store, fake)
    expansion_task = expansion_response["last_tutor_task"]
    assert expansion_shape == "identify_equivalent"

    equal_response, equal_shape = _give_task(store, fake, EQUAL_ADD_SUB, suffix=" (B)")
    equal_state = store.peek("lesson-context")
    assert equal_shape == "direct_computation"
    assert expansion_task != equal_response["last_tutor_task"]
    assert equal_state["current_family"] == equal_shape
    assert equal_state["curriculum_fingerprint"] == "6|6-04|6-04-009|practice|1"

    _, unlike_shape = _give_task(store, fake, UNLIKE_ADD_SUB, suffix=" (C)")
    assert store.peek("lesson-context")["current_family"] == unlike_shape

    _, _ = _give_task(store, fake, MULTIPLICATION, suffix=" (D)")

    # Lekcija bez ugovora u istoj sesiji → legacy put, i dalje izolovano stanje.
    _, grade7_shape = _give_task(store, fake, GRADE7_SIGNS, grade=7, suffix=" (7)")
    assert grade7_shape == "direct_computation"
    assert store.peek("lesson-context")["curriculum_fingerprint"] == \
        "7|7-02|7-02-008|practice|"

    # Povratak na šesti razred ne oživljava nijedan raniji zadatak.
    before_calls = fake.call_count
    stale_click = run_practice_turn(store, fake, _turn(
        EXPANSION, interaction_type="choice_answer", selected_option_id="a",
        student_message="Izabrana opcija A.", client_turn_id="stale-return",
    ))
    assert "status" not in stale_click
    assert fake.call_count == before_calls
    returned = store.peek("lesson-context")
    assert returned["current_task"] == ""
    assert returned["current_family"] == ""


def test_old_answer_after_topic_change_is_not_graded_or_sent_to_the_model():
    store, fake = SessionStore(), FakeLLM()
    _give_task(store, fake)
    old = store.peek("lesson-context")
    old_option = old["correct_option_id"]
    old_task = old["current_task"]
    calls_before = fake.call_count

    response = run_practice_turn(store, fake, _turn(
        EQUAL_ADD_SUB,
        interaction_type="choice_answer",
        selected_option_id=old_option,
        student_message="Izabrana stara opcija.",
        client_turn_id="old-answer-new-topic",
        last_tutor_task=old_task,
    ))

    assert "status" not in response
    assert fake.call_count == calls_before
    current = store.peek("lesson-context")
    assert current["lesson_id"] == EQUAL_ADD_SUB
    assert current["current_task"] == ""
    assert current["current_options"] == []
    assert current["correct_streak"] == 0
    assert current["correctly_completed_families"] == []


def test_client_last_task_cannot_rehydrate_a_changed_curriculum_context():
    store, fake = SessionStore(), FakeLLM()
    first, _ = _give_task(store, fake)
    stale_task = first["last_tutor_task"]

    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task()))
    response = run_practice_turn(store, fake, _turn(
        EQUAL_ADD_SUB, last_tutor_task=stale_task,
    ))

    assert response["status"] == "ready"
    assert response["last_tutor_task"] != stale_task
    _, sent_input = fake.calls[-1]
    assert stale_task not in sent_input
    assert "Sabiranje i oduzimanje razlomaka jednakih imenilaca" in sent_input


def test_same_context_wrong_answer_and_retry_preserve_task_family():
    store, fake = SessionStore(), FakeLLM()
    first, shape = _give_task(store, fake, EQUAL_ADD_SUB)
    session = store.peek("lesson-context")
    wrong_id = next(
        option["id"] for option in session["current_options"]
        if option["id"] != session["correct_option_id"]
    )
    fake.queue(make_output(reply="", hint="Saberi brojnike, a imenilac ostaje isti."))
    wrong = run_practice_turn(store, fake, _turn(
        EQUAL_ADD_SUB,
        interaction_type="choice_answer",
        selected_option_id=wrong_id,
        student_message="Izabrana opcija.",
        client_turn_id="same-topic-wrong",
    ))
    after_wrong = store.peek("lesson-context")
    assert wrong["answer_verdict"] == "incorrect"
    assert after_wrong["current_task"] == first["last_tutor_task"]
    assert after_wrong["current_family"] == shape
    assert after_wrong["retry_required"] is True

    # Ponovni pokušaj ostaje na ISTOJ vještini (isti arhetip), s drugim
    # brojevima — brojeve sada bira SERVERSKI generator, ne model.
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task()))
    retry = run_practice_turn(store, fake, _turn(
        EQUAL_ADD_SUB, student_message="Daj mi novi zadatak za istu vještinu."))
    assert retry["status"] == "ready", retry
    after_retry = store.peek("lesson-context")
    assert after_retry["current_family"] == shape
    assert after_retry["current_task"] != first["last_tutor_task"]


def test_contract_version_is_part_of_the_curriculum_fingerprint():
    """Izmjena ugovora invalidira aktivni zadatak kroz POSTOJEĆI mehanizam."""
    contract = registry.contract_for(EQUAL_ADD_SUB)
    assert contract is not None
    assert registry.contract_version_for(EQUAL_ADD_SUB) == contract.contract_version
    assert registry.contract_version_for(GRADE7_SIGNS) == ""
