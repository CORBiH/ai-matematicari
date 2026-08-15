"""Testovi kratkog feedbacka na pogrešan odgovor (matbot/feedback.py + Practice).

Živi nalaz koji ovi testovi zaključavaju: nakon netačnog klika tutor je pisao
dugačak dokaz zašto je izabrana opcija pogrešna. Ugovor je sada: „Netačno.“ +
JEDAN sažet hint, bez otkrivanja tačne opcije.
"""
from matbot import config, feedback
from tests.conftest import (make_tutor_draft,
                            FakeLLM, make_options, make_output, make_task,
                            make_task_payload, queue_two_call)
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore


# ---------------------------------------------------------------------------
# Jedinični testovi oblikovanja
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# C. Otkrivanje odgovora kroz eksplicitne fraze — i KRATKI odgovori
# ---------------------------------------------------------------------------

def test_reveal_phrase_catches_short_numeric_answer():
    assert feedback.leaks_answer("Odgovor je 2.", expected_answer="2")


def test_reveal_phrase_catches_negative_number():
    assert feedback.leaks_answer("Rješenje je -3.", expected_answer="-3")


def test_reveal_phrase_catches_pi():
    assert feedback.leaks_answer("Tačno je π.", expected_answer="π")


def test_reveal_phrase_catches_latex_pi():
    assert feedback.leaks_answer("Tačno je \\pi.", expected_answer="π")


def test_reveal_phrase_catches_letter_answer():
    assert feedback.leaks_answer("Tačna opcija je A.", correct_option_text="A")


def test_reveal_phrase_catches_option_letter_variant():
    assert feedback.leaks_answer("Izaberi opciju B.", correct_option_text="B")


def test_reveal_phrase_catches_variable_equals_short_value():
    assert feedback.leaks_answer("Dakle x=2.", expected_answer="2")


def test_reveal_phrase_catches_dobijes_form():
    assert feedback.leaks_answer("Kad podijeliš, dobiješ 5.", expected_answer="5")


def test_reveal_phrase_catches_zato_je_form():
    assert feedback.leaks_answer("Zato je 5 tačan odgovor.", expected_answer="5")


def test_reveal_phrase_ignores_unrelated_value():
    """Fraza postoji, ali izgovorena vrijednost NIJE tačan odgovor — hint i
    dalje ne otkriva ništa relevantno (npr. objašnjava zašto NEŠTO DRUGO nije
    tačno) i ne smije se lažno tretirati kao curenje ovog zadatka."""
    assert not feedback.leaks_answer("Odgovor je 7.", expected_answer="2")


def test_legitimate_hint_divide_both_sides_is_not_a_leak():
    assert not feedback.leaks_answer("Podijeli obje strane sa 2.", expected_answer="2")


def test_legitimate_hint_multiply_denominator_is_not_a_leak():
    assert not feedback.leaks_answer(
        "Pomnoži nazivnik 8 sa 3.", expected_answer="24", correct_option_text="24/8"
    )


def test_legitimate_hint_mentioning_factor_is_not_a_leak():
    assert not feedback.leaks_answer("Posmatraj faktor 5.", expected_answer="5")


def test_legitimate_hint_about_sign_after_division_is_not_a_leak():
    assert not feedback.leaks_answer(
        "Provjeri znak nakon dijeljenja sa -2.", expected_answer="-2"
    )


def test_x_equals_form_does_not_misfire_on_unrelated_equation_hint():
    """'x = 2' oblik ne smije se okinuti kad hint samo UPOREĐUJE dvije stvari
    bez ikakve veze s tačnim odgovorom ovog zadatka."""
    assert not feedback.leaks_answer("Provjeri da li je a = b prije zamjene.", expected_answer="7")


# ---------------------------------------------------------------------------
# D. Garantovana gornja granica prvog netačnog odgovora
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Integracija kroz Practice
# ---------------------------------------------------------------------------

def _payload(msg="Daj zadatak.", **kw):
    base = {
        "session_id": "sess-fb", "grade": 6, "selected_topic": "6-04-007",
        "selected_oblast": "", "student_message": msg, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "", "selected_option_id": "", "client_turn_id": "",
    }
    base.update(kw)
    return base


def _start(store, fake):
    # Zadatak mora zadovoljiti ugovor prve dodijeljene porodice za ovu lekciju
    # (Razlomci → expand_to_given_denominator). `expected` MORA biti označena
    # opcija: jedini motor odbija paket u kojem se to dvoje razilazi
    # (`expected_answer_not_marked_option`), i to je ispravno — stari motor je
    # tu nedosljednost puštao, pa je fixture mogao tvrditi „5/8“.
    queue_two_call(fake, new_task=make_task_payload(
        text="Proširi razlomak $\\frac{5}{8}$ tako da nazivnik bude $32$.",
        expected="$\frac{20}{32}$",
        options=["$\\frac{20}{32}$", "$\\frac{5}{32}$",
                 "$\\frac{10}{32}$", "$\\frac{15}{32}$"]))
    run_practice_turn(store, fake, _payload())
    return store.peek("sess-fb")


def _click(store, fake, option_id, turn_id):
    return run_practice_turn(store, fake, _payload(
        msg="[klik]", interaction_type="choice_answer",
        selected_option_id=option_id, client_turn_id=turn_id))


def _wrong_id(sess):
    return next(o["id"] for o in sess["current_options"] if o["id"] != sess["correct_option_id"])


def test_practice_first_wrong_never_reveals_correct_option():
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    correct_text = next(o["text"] for o in sess["current_options"]
                        if o["id"] == sess["correct_option_id"])
    queue_two_call(fake, draft=make_tutor_draft(
        intent="clarification", new_task=None, reply="x", hint=f"Tačan odgovor je {correct_text}."))
    r = _click(store, fake, _wrong_id(sess), "t1")
    assert "revealed_correct_option_id" not in r
    assert correct_text.strip("$") not in r["answer"]


def test_practice_first_wrong_never_reveals_expected_answer():
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    queue_two_call(fake, draft=make_tutor_draft(
        intent="clarification", new_task=None, reply="x", hint="Rezultat je 5/8 naravno."))
    r = _click(store, fake, _wrong_id(sess), "t1")
    assert "5/8" not in r["answer"]


def test_practice_correct_answer_never_gets_netacno():
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    queue_two_call(fake, draft=make_tutor_draft(
        intent="clarification", new_task=None, reply="Tačno! Skratio si do kraja."))
    r = _click(store, fake, sess["correct_option_id"], "t1")
    assert not r["answer"].startswith("Netačno.")
    assert r["answer_verdict"] == "correct"


def test_student_question_is_never_marked_netacno():
    """Tekstualna poruka nije pokušaj odgovora — ne smije dobiti ocjenu."""
    store, fake = SessionStore(), FakeLLM()
    _start(store, fake)
    queue_two_call(fake, intent="clarification")
    r = run_practice_turn(store, fake, _payload(msg="Šta znači brojnik?"))
    assert not r["answer"].startswith("Netačno.")
    assert r["answer_verdict"] is None


def test_first_wrong_makes_exactly_one_llm_call():
    store, fake = SessionStore(), FakeLLM()
    sess = _start(store, fake)
    queue_two_call(fake, draft=make_tutor_draft(
        intent="clarification", new_task=None, reply="x", hint="Hint."))
    _click(store, fake, _wrong_id(sess), "t1")
    assert len(fake.tutor_calls) == 2  # 1 bootstrap + 1 klik
