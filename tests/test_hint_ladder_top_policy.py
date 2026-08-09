"""Vrh ljestvice nagovještaja: NIVO 3 DAJE konačan rezultat — obje rute.

ODLUKA (PP-1 LIVE-150): ljestvica je trostepena i njen vrh je namjerno pun
odgovor:

    hint 1  →  smjer, BEZ rezultata
    hint 2  →  konkretan međukorak, JOŠ BEZ rezultata
    hint 3  →  cijeli postupak I konačan rezultat

Model-put je to već radio: `_HINT_LEVEL_GUIDANCE[2]` traži „CIJELI postupak i
konačan rezultat“, a anti-leak gate zato izričito izuzima vrh ljestvice
(`_guard_answer_leak(..., is_hint_ladder_top=...)`). Deterministički motori su
treći nagovještaj pisali kao POSTUPAK BEZ rezultata, pa je isti učenik na istoj
lekciji dobijao različitu pomoć zavisno od toga koja ruta ga je uslužila. Ovaj
modul zaključava harmonizaciju te jedne razlike.

GRANICA: izuzetak važi SAMO za vrh ljestvice. Curenje na nivou 1 i 2 i dalje
pada, i to isti `feedback.leaks_answer` detektor koji je i ranije čuvao te
nivoe — nijedan novi detektor i nijedno globalno popuštanje.

ZERO poziva modela: deterministička ruta ne zove model ni za jedan nagovještaj.
"""
import pytest

from matbot import config
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import prompts as tutor_prompts
from tests.conftest import FakeLLM

SESSION = "hint-top"

# Namjerno RAZLIČITE porodice — harmonizacija je centralna, pa mora vrijediti
# za svaku determinističku porodicu, ne samo za jednačine.
FAMILIES = [
    ("6-04-009", 6, "fraction_arithmetic_direct"),
    ("6-07-001", 6, "linear_equation_direct"),
    ("9-04-013", 9, "linear_inequality_direct"),
    ("7-02-007", 7, "integer_arithmetic_direct"),
    ("6-05-008", 6, "decimal_arithmetic_direct"),
    ("6-03-001", 6, "common_divisors_multiples"),
]


@pytest.fixture(autouse=True)
def _universal(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def turn(lesson, grade, message, **changes):
    payload = {
        "session_id": SESSION, "grade": grade, "selected_topic": lesson,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    payload.update(changes)
    return payload


def publish(store, fake, lesson, grade):
    response = run_practice_turn(
        store, fake, turn(lesson, grade, "Daj mi jedan zadatak za vježbu iz ove teme."))
    assert response["status"] == "ready", response
    assert fake.call_count == 0, "deterministička objava ne smije zvati model"
    return response


def ladder(store, fake, lesson, grade):
    """Tri uzastopna zahtjeva za nagovještajem — vraća tri SLUŽENA teksta."""
    return [run_practice_turn(store, fake, turn(
        lesson, grade, "Ne znam.", intent="hint_request",
        interaction_phase="practice_help", client_turn_id=f"h{index}"))["answer"]
        for index in range(1, 4)]


# ---------------------------------------------------------------------------
# 1) DETERMINISTIČKA LJESTVICA — nivoi 1/2 bez rezultata, nivo 3 s rezultatom
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lesson,grade,family", FAMILIES)
def test_deterministic_hint_ladder_reveals_only_at_the_top(lesson, grade, family):
    store, fake = SessionStore(), FakeLLM()
    publish(store, fake, lesson, grade)
    answer_reply = store.peek(SESSION)["deterministic_task"]["answer_reply"]

    first, second, third = ladder(store, fake, lesson, grade)

    assert answer_reply not in first, (family, "nivo 1 ne smije dati rezultat")
    assert answer_reply not in second, (family, "nivo 2 ne smije dati rezultat")
    assert answer_reply in third, (family, "nivo 3 MORA dati konačan rezultat")


@pytest.mark.parametrize("lesson,grade,family", FAMILIES)
def test_top_hint_keeps_the_stored_procedure_before_the_result(lesson, grade, family):
    """Nije golo „Odgovor je X“: pohranjeni postupak ostaje, rezultat se dopisuje
    NA KRAJ — postupak prvo, rezultat na kraju."""
    store, fake = SessionStore(), FakeLLM()
    publish(store, fake, lesson, grade)
    stored = store.peek(SESSION)["deterministic_task"]["hints"]
    answer_reply = store.peek(SESSION)["deterministic_task"]["answer_reply"]

    third = ladder(store, fake, lesson, grade)[2]

    assert third.startswith(stored[2]), (family, "postupak trećeg hinta mora ostati prvi")
    assert third.index(stored[2]) < third.rindex(answer_reply), (family, "rezultat ide NA KRAJ")
    assert len(stored[2]) >= 20, (family, "treći hint nije samo rezultat")


def test_repeated_requests_past_the_top_keep_serving_the_full_answer():
    """Četvrti „ne znam“ ostaje na vrhu ljestvice (hint_level je ograničen)."""
    lesson, grade, _family = FAMILIES[0]
    store, fake = SessionStore(), FakeLLM()
    publish(store, fake, lesson, grade)
    ladder(store, fake, lesson, grade)
    answer_reply = store.peek(SESSION)["deterministic_task"]["answer_reply"]

    fourth = run_practice_turn(store, fake, turn(
        lesson, grade, "Ne znam.", intent="hint_request",
        interaction_phase="practice_help", client_turn_id="h4"))

    assert answer_reply in fourth["answer"]
    assert store.peek(SESSION)["hint_level"] == config.MAX_HINT_LEVEL
    assert fake.call_count == 0


# ---------------------------------------------------------------------------
# 2) STANJE — nagovještaj ne dira zadatak, rješenje, lekciju ni težinu
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lesson,grade,family", FAMILIES)
def test_hint_ladder_never_mutates_the_task_or_the_stored_solution(lesson, grade, family):
    store, fake = SessionStore(), FakeLLM()
    publish(store, fake, lesson, grade)
    before = store.peek(SESSION)
    snapshot = {
        "identity": before["current_task_identity"],
        "task": before["current_task"],
        "solution": before["deterministic_task"]["solution"],
        "hints": list(before["deterministic_task"]["hints"]),
        "answer_reply": before["deterministic_task"]["answer_reply"],
        "level": before["difficulty_level"],
        "lesson": before["lesson_id"],
        "correct": before["correct_option_id"],
    }

    ladder(store, fake, lesson, grade)

    after = store.peek(SESSION)
    assert after["current_task_identity"] == snapshot["identity"]
    assert after["current_task"] == snapshot["task"]
    assert after["deterministic_task"]["solution"] == snapshot["solution"]
    # Pohranjena ljestvica ostaje NETAKNUTA — rezultat se dopisuje pri usluživanju.
    assert after["deterministic_task"]["hints"] == snapshot["hints"]
    assert after["deterministic_task"]["answer_reply"] == snapshot["answer_reply"]
    assert after["difficulty_level"] == snapshot["level"]
    assert after["lesson_id"] == snapshot["lesson"]
    assert after["correct_option_id"] == snapshot["correct"]
    assert after["task_completed"] is False


@pytest.mark.parametrize("lesson,grade,family", FAMILIES)
def test_the_whole_deterministic_hint_flow_is_zero_call(lesson, grade, family):
    store, fake = SessionStore(), FakeLLM()
    publish(store, fake, lesson, grade)
    ladder(store, fake, lesson, grade)
    assert fake.call_count == 0, (family, "nijedan nagovještaj ne smije zvati model")


# ---------------------------------------------------------------------------
# 3) MODEL-PUT — ugovor prompta i anti-leak izuzetka ostaje isti
# ---------------------------------------------------------------------------

def test_model_prompt_still_asks_the_third_hint_for_the_final_result():
    guidance = tutor_prompts._HINT_LEVEL_GUIDANCE
    assert "BEZ" in guidance[0] and "rezultat" in guidance[0]
    assert "BEZ" in guidance[1].upper() or "bez" in guidance[1]
    top = guidance[2]
    assert "NIVO 3" in top and "konačan rezultat" in top


def test_leak_guard_exempts_only_the_top_of_the_ladder():
    """Ugovor izuzetka: nivoi 1 i 2 su čuvani, vrh nije — ista granica koju
    deterministička ruta sada koristi za dopisivanje rezultata."""
    from matbot.tutor import pipeline as tutor_pipeline

    session = {"current_task": "Riješi: $x+1=5$", "task_completed": False,
               "correct_option_id": "a", "hint_level": 0,
               "current_options": [{"id": "a", "text": "$x=4$"}],
               "expected_answer_summary": "$x=4$"}
    leaking = "Rezultat je $x=4$."

    guarded = tutor_pipeline._guard_answer_leak(
        session, "req", _Context(), "hint_request", leaking, is_hint_ladder_top=False)
    assert guarded == tutor_pipeline.LEAK_BLOCKED_REPLY

    allowed = tutor_pipeline._guard_answer_leak(
        session, "req", _Context(), "hint_request", leaking, is_hint_ladder_top=True)
    assert allowed == leaking


class _Context:
    topic_id = "6-04-009"
