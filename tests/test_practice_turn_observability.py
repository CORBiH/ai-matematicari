r"""Jedan strukturisan red po Practice turnu — bez ijedne tajne.

ZAŠTO POSTOJI: dijagnostika je bila razasuta po nekoliko log redova, pa se za
jedan turn nije moglo pročitati šta je učenik tražio, koji je zadatak bio
aktivan, šta je objavljeno i da li je stanje mutirano. Faza 4F to sažima u
`tutor_turn_diagnostics`.

Logovanje NE SMIJE mijenjati ponašanje i NE SMIJE nositi ključ, tajnu, prompt,
sirov izlaz modela, tekst zadatka, tekst opcija ni učenikovu poruku
(CLAUDE.md, pravilo 7).
"""
import logging

import pytest

from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import (FakeLLM, make_task_payload, make_tutor_draft,
                            queue_two_call)

LESSON, GRADE = "6-03-004", 6
TASK = "Koji od sljedećih brojeva je djeljiv sa 25?"
OPTIONS = ("322", "390", "349", "375")
SECRET_LOOKING_MESSAGE = "Daj mi zadatak sk-test-not-a-real-key"


@pytest.fixture(autouse=True)
def _universal_runtime(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def _turn(session_id, message="Daj mi zadatak.", **changes):
    turn = {
        "session_id": session_id, "grade": GRADE, "selected_topic": LESSON,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "turn-1234",
    }
    turn.update(changes)
    return turn


def _payload(text=TASK, options=OPTIONS, marked=3):
    return make_task_payload(text=text, options=options,
                             correct_option_index=marked, expected=options[marked])


def _diagnostics_line(caplog):
    lines = [r.getMessage() for r in caplog.records
             if r.getMessage().startswith("tutor_turn_diagnostics")]
    assert lines, "nema tutor_turn_diagnostics reda"
    return lines[-1]


def test_a_published_turn_emits_every_required_field(caplog):
    store, fake = SessionStore(), FakeLLM()
    queue_two_call(fake, draft=make_tutor_draft(intent="generate_task", new_task=_payload()))
    with caplog.at_level(logging.INFO, logger="matbot.tutor"):
        run_practice_turn(store, fake, _turn("obs-1"))

    line = _diagnostics_line(caplog)
    for field in ("request_id=", "session=", "topic=6-03-004", "client_turn_id=turn-1234",
                  "intent=generate_task", "interaction_phase=", "calls=2", "published=True",
                  "task_preserved=True", "state_mutated=True", "previous_identity=",
                  "final_identity=", "committed_level="):
        assert field in line, f"nedostaje {field}\n{line}"


def test_a_rejected_turn_reports_no_mutation(caplog):
    store, fake = SessionStore(), FakeLLM()
    queue_two_call(fake, draft=make_tutor_draft(intent="generate_task", new_task=_payload()))
    run_practice_turn(store, fake, _turn("obs-2"))

    # Duplikat: server ga odbija i stanje ostaje netaknuto.
    queue_two_call(fake, draft=make_tutor_draft(intent="next_task", new_task=_payload()))
    with caplog.at_level(logging.INFO, logger="matbot.tutor"):
        run_practice_turn(store, fake, _turn("obs-2", "Daj mi novi zadatak."))

    line = _diagnostics_line(caplog)
    assert "published=False" in line
    assert "state_mutated=False" in line
    assert "task_preserved=True" in line
    assert "rejection_code=" in line


def test_the_line_never_carries_content_or_secrets(caplog):
    store, fake = SessionStore(), FakeLLM()
    queue_two_call(fake, draft=make_tutor_draft(intent="generate_task", new_task=_payload()))
    with caplog.at_level(logging.INFO, logger="matbot.tutor"):
        run_practice_turn(store, fake, _turn("obs-3", SECRET_LOOKING_MESSAGE))

    line = _diagnostics_line(caplog)
    assert "sk-test-not-a-real-key" not in line
    assert TASK not in line
    for option in OPTIONS:
        assert f"={option}" not in line
    assert "375" not in line               # ni tačan odgovor
    assert "Zadatak" not in line


def test_the_session_id_is_never_logged_in_full(caplog):
    store, fake = SessionStore(), FakeLLM()
    long_session = "session-with-a-very-long-identifier-0123456789"
    queue_two_call(fake, draft=make_tutor_draft(intent="generate_task", new_task=_payload()))
    with caplog.at_level(logging.INFO, logger="matbot.tutor"):
        run_practice_turn(store, fake, _turn(long_session))
    assert long_session not in _diagnostics_line(caplog)


def test_logging_does_not_change_the_published_result(caplog):
    store_a, fake_a = SessionStore(), FakeLLM()
    queue_two_call(fake_a, draft=make_tutor_draft(intent="generate_task", new_task=_payload()))
    with caplog.at_level(logging.INFO, logger="matbot.tutor"):
        with_logging = run_practice_turn(store_a, fake_a, _turn("obs-4"))

    store_b, fake_b = SessionStore(), FakeLLM()
    queue_two_call(fake_b, draft=make_tutor_draft(intent="generate_task", new_task=_payload()))
    with caplog.at_level(logging.CRITICAL, logger="matbot.tutor"):
        without_logging = run_practice_turn(store_b, fake_b, _turn("obs-5"))

    assert with_logging["answer"] == without_logging["answer"]
    assert with_logging["status"] == without_logging["status"]
