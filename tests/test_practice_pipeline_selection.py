"""KAPIJA ROLLBACKA: bez eksplicitne zastavice produkcija koristi STABILAN put.

ZAŠTO POSTOJI (ručni test, 2026-08-03): univerzalni dvopozivni Tutor+Reviewer
je nakratko bio podrazumijevan i pao je na četiri stvari uživo — uredan zahtjev
za zadatak je pao zatvoreno, „Ne znam“ je najavilo hint bez hinta, uvodna
lekcija je dobila prevelike brojeve, a ponovljeno „Ne znam“ je ostavljalo UI da
čeka. Put je vraćen iza zastavice; ovaj fajl čuva da se ne vrati sam od sebe.

Testovi NE koriste univerzalni put — namjerno rade s podrazumijevanim
okruženjem, tačno kao produkcija.
"""
import os

import pytest

from matbot import practice
from matbot.session_store import SessionStore
from tests.conftest import FakeLLM, make_output, make_task


def _turn(**changes):
    payload = {
        "session_id": "pipeline-default", "grade": 6,
        "selected_topic": "6-04-009", "selected_oblast": "",
        "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    payload.update(changes)
    return payload


def test_missing_flag_selects_the_stable_single_call_path(monkeypatch):
    monkeypatch.delenv("MATBOT_PRACTICE_PIPELINE", raising=False)
    assert practice._universal_pipeline_enabled() is False


@pytest.mark.parametrize("value", [
    "", "   ", "legacy_single_call", "universal", "UNIVERSAL", "two_call",
    "universal-two-call", "true", "1", "on", "yes", "tipfeler",
])
def test_only_the_exact_flag_value_enables_the_universal_path(monkeypatch, value):
    """„Nije prazno“ ili „nije legacy“ bi značilo da tipfeler u okruženju tiho
    uključi neprovjeren put u produkciji."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", value)
    assert practice._universal_pipeline_enabled() is False


def test_exact_flag_value_enables_the_universal_path(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", practice.UNIVERSAL_PIPELINE_FLAG)
    assert practice._universal_pipeline_enabled() is True
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "  Universal_Two_Call  ")
    assert practice._universal_pipeline_enabled() is True


def test_default_turn_really_runs_the_single_call_path(monkeypatch):
    """Ponašajni dokaz, ne samo zastavica: JEDAN poziv i JEDNA stara šema."""
    monkeypatch.delenv("MATBOT_PRACTICE_PIPELINE", raising=False)
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task()))
    response = run = practice.run_practice_turn(store, fake, _turn())

    assert response["status"] == "ready"
    assert fake.call_count == 1                      # ne dva
    assert len(fake.tutor_calls) == 0                # univerzalni put nije dirnut
    assert len(fake.reviewer_calls) == 0
    assert store.peek("pipeline-default")["current_task"]


def test_default_turn_never_enters_the_universal_pipeline(monkeypatch):
    monkeypatch.delenv("MATBOT_PRACTICE_PIPELINE", raising=False)

    def _explode(*args, **kwargs):
        raise AssertionError("univerzalni put NE SMIJE biti aktivan bez zastavice")

    monkeypatch.setattr(practice.tutor_pipeline, "run_turn", _explode)
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task()))
    assert practice.run_practice_turn(store, fake, _turn())["status"] == "ready"


def test_stable_path_still_serves_contracted_and_legacy_lessons(monkeypatch):
    """Rollback čuva OBA zatečena ponašanja: 6 lekcija kroz deterministički
    motor, ostale kroz legacy porodice."""
    monkeypatch.delenv("MATBOT_PRACTICE_PIPELINE", raising=False)
    from matbot.contracts import registry

    # Lekcija S ugovorom → server sam gradi zadatak (modelov sadržaj se ignoriše).
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(text="Izračunaj $999+999$.")))
    practice.run_practice_turn(store, fake, _turn(session_id="contracted"))
    contracted = store.peek("contracted")["current_task"]
    assert registry.state_for_topic("6-04-009") == registry.STATE_ENGINE
    assert "999" not in contracted and contracted.startswith("Izračunaj:")

    # Lekcija BEZ ugovora → nepromijenjen legacy put. Porodicu bira SERVER, pa
    # test mora poslati zadatak baš te porodice (kao i prije pivota).
    from matbot import task_families as tf
    from matbot.topics import lesson_info
    from tests.conftest import make_task_for_family

    topic = "7-03-008"
    info = lesson_info(7, topic)
    family = tf.applicable_families(
        7, info["oblast"], info["title"], lesson_id=topic)[0]
    store2, fake2 = SessionStore(), FakeLLM()
    fake2.queue(make_output(reply="Evo zadatka.", new_task=make_task_for_family(family)))
    response = practice.run_practice_turn(store2, fake2, _turn(
        session_id="legacy", grade=7, selected_topic=topic))
    assert response["status"] == "ready"
    assert registry.state_for_topic(topic) == registry.STATE_LEGACY
    assert store2.peek("legacy")["current_family"] == family


def test_reply_fidelity_protection_survives_the_rollback(monkeypatch):
    """Zaštita proze (izmišljen broj u odgovoru) mora i dalje raditi na
    stabilnom putu — ona je i pisana za njega."""
    monkeypatch.delenv("MATBOT_PRACTICE_PIPELINE", raising=False)
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task()))
    practice.run_practice_turn(store, fake, _turn(session_id="fidelity"))
    before = store.peek("fidelity")

    fake.queue(make_output(reply=r"Rezultat je $\frac{47}{99}$."))
    response = practice.run_practice_turn(store, fake, _turn(
        session_id="fidelity", student_message="Kako da riješim ovo?"))
    assert response["answer"] == practice.SAFE_ERROR_MESSAGE
    assert store.peek("fidelity") == before


def test_environment_documents_the_flag():
    """Zastavica mora biti vidljiva u .env.example da niko ne uključi put
    slučajno ni ne traži je po kodu."""
    from pathlib import Path

    example = (Path(__file__).resolve().parent.parent / ".env.example").read_text(
        encoding="utf-8")
    assert "MATBOT_PRACTICE_PIPELINE" in example
    assert practice.UNIVERSAL_PIPELINE_FLAG in example
