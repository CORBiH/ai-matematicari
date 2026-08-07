"""Faza 4H — deterministička ruta mora biti BRZA, ne samo besplatna.

Cilj iz plana: obrada na serveru ispod 200 ms po determinističkoj akciji u
običnim testnim uslovima (mjereno POSLIJE zagrijavanja — prvo učitavanje
kurikuluma i kompajliranih ugovora se ne računa u akciju)."""
import time

import pytest

from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import FakeLLM

GRADE, LESSON, SESSION = 6, "6-04-010", "det-perf"
BUDGET_S = 0.2


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


def timed(store, fake, payload):
    started = time.perf_counter()
    response = run_practice_turn(store, fake, payload)
    elapsed = time.perf_counter() - started
    return response, elapsed


def test_every_deterministic_action_is_fast_and_free(universal):
    store, fake = SessionStore(), FakeLLM()
    run_practice_turn(store, fake, turn())          # zagrijavanje (učitavanja)

    measurements = {}
    _response, measurements["new_task"] = timed(
        store, fake, turn("Daj mi novi zadatak."))
    _response, measurements["harder"] = timed(
        store, fake, turn("Daj mi teži zadatak.", difficulty_request="harder"))
    _response, measurements["easier"] = timed(
        store, fake, turn("Daj mi lakši zadatak.", difficulty_request="easier"))

    session = store.peek(SESSION)
    wrong = next(option["id"] for option in session["current_options"]
                 if option["id"] != session["correct_option_id"])
    _response, measurements["mcq_click"] = timed(store, fake, turn(
        message="[odgovor]", interaction_type="choice_answer",
        selected_option_id=wrong, client_turn_id="p1"))
    _response, measurements["hint"] = timed(store, fake, turn(
        message="Ne znam.", intent="hint_request",
        interaction_phase="practice_help", client_turn_id="p2"))
    _response, measurements["solution"] = timed(store, fake, turn(
        message="Uradi ga ti.", intent="solution_request",
        interaction_phase="practice_help", client_turn_id="p3"))

    assert fake.call_count == 0, "deterministička ruta ne smije zvati model"
    slow = {name: seconds for name, seconds in measurements.items()
            if seconds > BUDGET_S}
    assert not slow, f"akcije preko budžeta od {BUDGET_S}s: {slow}"


# ---------------------------------------------------------------------------
# KAPACITETNA EKSPANZIJA: benchmark preko REPREZENTATIVNIH novih porodica.
# Mjeri se raspodjela (10 uzastopnih novih zadataka po lekciji), ne jedan
# srećan uzorak — svaka akcija ostaje unutar istog budžeta od 200 ms.
# ---------------------------------------------------------------------------

_EXPANSION_LESSONS = (
    (6, "6-02-007"),   # redoslijed operacija
    (6, "6-03-004"),   # pravila djeljivosti
    (7, "7-03-011"),   # množenje racionalnih
    (8, "8-01-015"),   # zakoni stepena
    (9, "9-04-003"),   # jednačina sa zagradama
)


@pytest.mark.parametrize("grade,lesson", _EXPANSION_LESSONS,
                         ids=[lesson for _g, lesson in _EXPANSION_LESSONS])
def test_expansion_lessons_meet_the_latency_budget(universal, grade, lesson):
    store, fake = SessionStore(), FakeLLM()
    session_id = f"det-perf-{lesson}"

    def expansion_turn(message, **changes):
        payload = turn(message, **changes)
        payload["grade"] = grade
        payload["selected_topic"] = lesson
        payload["session_id"] = session_id
        return payload

    run_practice_turn(store, fake, expansion_turn(
        "Daj mi jedan zadatak za vježbu iz ove teme."))   # zagrijavanje

    slow = []
    for attempt in range(10):
        _response, elapsed = timed(store, fake, expansion_turn(
            "Daj mi novi zadatak."))
        if elapsed > BUDGET_S:
            slow.append((attempt, round(elapsed, 3)))
    assert fake.call_count == 0
    assert not slow, f"{lesson}: nova zadatka preko {BUDGET_S}s: {slow}"
