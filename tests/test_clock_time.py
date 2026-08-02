"""D35-4: usko prepoznavanje direktnog pitanja o vremenu na satu u Rezultat modu.

Regresija je vezana za poziv 28 kampanje od 35: „Sastanak je u 12:30. Koliko je
sati?“ dobio je doslovno rules.OFF_TOPIC_ANSWER umjesto odgovora."""
import pytest

from matbot.prompts import build_quick_instructions
from matbot.quick import clock_time_answer, direct_clock_time_question, run_quick_turn
from matbot.rules import OFF_TOPIC_ANSWER
from tests.conftest import FakeLLM, make_quick_output


def payload(msg):
    return {
        "session_id": "clock-sess", "grade": 6, "selected_topic": "",
        "selected_oblast": "", "student_message": msg, "intent": "",
        "difficulty_request": "", "interaction_phase": "",
        "last_tutor_task": "", "last_tutor_message": "", "conversation_history": [],
    }


def answer_when_model_refuses(msg):
    fake = FakeLLM()
    fake.queue(make_quick_output(reply=OFF_TOPIC_ANSWER))
    return run_quick_turn(fake, payload(msg))["answer"], fake


@pytest.mark.parametrize("message,expected", [
    ("Sastanak je u 12:30. Koliko je sati?", "12:30"),
    ("Vrijeme je 08:15, koliko je sati?", "08:15"),
    ("Šta znači 17:45 na satu?", "17:45"),
    ("Koje je vrijeme ako sat pokazuje 00:05?", "00:05"),
    ("Koliko je sati u 9:05?", "9:05"),
])
def test_valid_clock_questions_are_recognized(message, expected):
    assert direct_clock_time_question(message) == expected


@pytest.mark.parametrize("message", [
    "Sastanak je u 27:90. Koliko je sati?",   # nevažeći sat i minuta
    "Sastanak je u 12:75. Koliko je sati?",   # nevažeća minuta
    "Koliko je 60:15?",                        # školsko dijeljenje, ne sat
    "Izračunaj 12:30",                         # račun bez pitanja o vremenu
    "Odnos je 3:4, koliko je sati",            # omjer (jedna cifra u minutama)
    "Otvori https://sat.ba:8080 koliko je sati",  # dvotačka u URL-u
    "Napomena: ovo je važno.",                 # obična dvotačka u prozi
    "Koliko je 2+2?",                          # običan matematički zadatak
])
def test_non_clock_messages_are_not_recognized(message):
    assert direct_clock_time_question(message) is None


def test_server_answer_is_deterministic_for_every_valid_time():
    assert clock_time_answer("12:30") == "Vrijeme je 12:30 (12 sati i 30 minuta)."
    assert clock_time_answer("08:15") == "Vrijeme je 08:15 (8 sati i 15 minuta)."
    assert clock_time_answer("17:45") == "Vrijeme je 17:45 (17 sati i 45 minuta)."


def test_generic_refusal_is_replaced_for_a_direct_clock_question():
    answer, _fake = answer_when_model_refuses("Sastanak je u 12:30. Koliko je sati?")
    assert answer != OFF_TOPIC_ANSWER
    assert "12:30" in answer


def test_leading_zero_time_works_end_to_end():
    answer, _fake = answer_when_model_refuses("Vrijeme je 08:15, koliko je sati?")
    assert "08:15" in answer


def test_24_hour_time_works_end_to_end():
    answer, _fake = answer_when_model_refuses("Šta znači 17:45 na satu?")
    assert "17:45" in answer


def test_invalid_time_keeps_the_generic_refusal():
    answer, _fake = answer_when_model_refuses("Sastanak je u 27:90. Koliko je sati?")
    assert answer == OFF_TOPIC_ANSWER


def test_generic_non_math_request_remains_out_of_scope():
    answer, _fake = answer_when_model_refuses("Ko je pobijedio prvenstvo?")
    assert answer == OFF_TOPIC_ANSWER


def test_fallback_uses_exactly_one_model_call():
    _answer, fake = answer_when_model_refuses("Sastanak je u 12:30. Koliko je sati?")
    assert len(fake.quick_calls) == 1


def test_model_answer_is_kept_when_it_is_not_the_generic_refusal():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="Sastanak je u pola jedan."))
    answer = run_quick_turn(fake, payload("Sastanak je u 12:30. Koliko je sati?"))["answer"]
    assert answer == "Sastanak je u pola jedan."


def test_prompt_declares_everyday_measurement_in_scope():
    instructions = build_quick_instructions(6)
    assert "SVAKODNEVNA MJERENJA SU MATEMATIKA" in instructions
    assert "je vrijeme, a ne dijeljenje" in instructions


def test_division_expression_still_reaches_the_model_unchanged():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="$60:15=4$"))
    answer = run_quick_turn(fake, payload("Koliko je 60:15?"))["answer"]
    assert answer == "$60:15=4$"


# --- Uski clock cleanup iz kampanje od 14 poziva (poziv 7) -------------------

def test_bare_math_wrapped_time_is_replaced_with_plain_prose():
    """Živi nalaz: model je vratio „$12:30$“, a unutar $...$ dvotačka je u ovom
    projektu oznaka za DIJELJENJE, pa taj zapis sugeriše 12 podijeljeno sa 30."""
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="$12:30$"))
    answer = run_quick_turn(fake, payload("Sastanak je u 12:30. Koliko je sati?"))["answer"]
    assert answer == "Vrijeme je 12:30 (12 sati i 30 minuta)."
    assert "$" not in answer
    assert len(fake.quick_calls) == 1


def test_a_real_explanation_about_the_time_is_left_alone():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="Sastanak je u 12:30, dakle pola jedan."))
    answer = run_quick_turn(fake, payload("Sastanak je u 12:30. Koliko je sati?"))["answer"]
    assert answer == "Sastanak je u 12:30, dakle pola jedan."


def test_division_result_in_math_is_never_rewritten_as_time():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="$4$"))
    assert run_quick_turn(fake, payload("Koliko je 60:15?"))["answer"] == "$4$"
