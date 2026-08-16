# -*- coding: utf-8 -*-
"""Regresije za DVA P0 nalaza finalne prijemne kampanje (kandidat e767cac).

P0-1  Kontrolni je objavio MCQ nad sistemom dvije linearne jednačine kod kojeg
      NIJEDNA opcija nije tačna, a označena je pogrešna (test K5, pitanje q5).
P0-2  „Samo rezultat“ je objavio PRAZAN odgovor sa `status="ready"` (turn D08).

Oba se ovdje dokazuju na TAČNIM istorijskim ulazima, ne na parafrazi.
"""
import pytest

from matbot import kontrolni, linear_system_mcq, quick
from matbot.mathsafe import visible_text_is_empty
from matbot.schema import KontrolniQuestionOutput
from tests.conftest import FakeLLM, make_quick_image_output, make_quick_output

# ---------------------------------------------------------------------------
# P0-1 — TAČAN ISTORIJSKI ZADATAK
# ---------------------------------------------------------------------------

K5Q5_TEXT = ("Cijena jedne sveske je $x$ KM, a cijena jedne olovke je $y$ KM. "
             "Za $2$ sveske i $3$ olovke plaćeno je $9$ KM, a za $4$ sveske i "
             "$1$ olovku plaćeno je $11$ KM. Koliko iznosi cijena jedne sveske?")
K5Q5_OPTIONS = ["$2$ KM", "$4$ KM", "$3$ KM", "$1$ KM"]
K5Q5_SOLUTION = ("Iz sistema $2x+3y=9$ i $4x+y=11$ slijedi $x=2$ i $y=1$. "
                 "Cijena jedne sveske je $2$ KM.")


class _Ctx:
    geometry_scope = ""
    geometry_figures = ()


def _slot(lesson_id="9-05-003", number=5, difficulty="medium"):
    return {"slot": number, "lesson_id": lesson_id,
            "lesson_title": "Pojam sistema dvije linearne jednačine",
            "difficulty": difficulty}


def _question(text, options, correct_index, solution, expected=None, slot=None):
    slot = slot or _slot()
    return KontrolniQuestionOutput(
        slot=slot["slot"], lesson_id=slot["lesson_id"], text=text, options=options,
        correct_option_index=correct_index,
        expected_answer=expected if expected is not None else options[correct_index],
        solution=solution, difficulty=slot["difficulty"])


def _validate(text, options, correct_index, solution, expected=None, slot=None):
    slot = slot or _slot()
    parsed = _question(text, options, correct_index, solution, expected, slot)
    return kontrolni.validate_generated_question(parsed, slot, _Ctx(), set())


def test_historical_k5_q5_is_now_rejected():
    """Tačan objavljeni paket iz kampanje više ne smije proći."""
    clean, code = _validate(K5Q5_TEXT, K5Q5_OPTIONS, 0, K5Q5_SOLUTION)
    assert clean is None
    assert code == "linear_system_no_correct_option"


def test_historical_k5_q5_true_solution_is_computed_exactly():
    from fractions import Fraction
    result = linear_system_mcq.evaluate_system_mcq(
        K5Q5_TEXT, K5Q5_OPTIONS, K5Q5_SOLUTION)
    assert result.applicable and not result.valid
    assert dict(result.solution) == {"x": Fraction(12, 5), "y": Fraction(7, 5)}
    assert result.target == "x"


def test_model_claimed_values_are_never_used_as_input():
    """`$x=2$` iz rješenja je tvrdnja koju provjeravamo, ne ulaz."""
    equations = linear_system_mcq._usable_equations(K5Q5_TEXT, K5Q5_SOLUTION)
    assert all(len(equation.coeffs) == 2 for equation in equations)
    assert len(equations) == 2


def test_solution_equations_must_be_corroborated_by_the_stem():
    """Izmišljen sistem u rješenju se ne prihvata kao istina."""
    invented = "Iz sistema $7x+5y=31$ i $2x+8y=34$ slijedi $x=2$."
    assert linear_system_mcq._usable_equations(K5Q5_TEXT, invented) == []


# ---------------------------------------------------------------------------
# P0-1 — MATRICA A–I
# ---------------------------------------------------------------------------

SYSTEM_TEXT = ("Cijena jedne sveske je $x$ KM, a cijena jedne olovke je $y$ KM. "
               "Vrijedi $2x+3y=12$ i $x-y=1$. Koliko iznosi cijena jedne sveske?")
# 2x+3y=12, x-y=1  ->  x=3, y=2


def test_A_unique_solution_marked_correctly_publishes():
    clean, code = _validate(SYSTEM_TEXT, ["$3$ KM", "$2$ KM", "$5$ KM", "$1$ KM"],
                            0, "Rješenje sistema je $x=3$ i $y=2$.")
    assert clean is not None, code


def test_B_true_value_absent_from_all_options_rejects():
    clean, code = _validate(SYSTEM_TEXT, ["$4$ KM", "$5$ KM", "$6$ KM", "$7$ KM"],
                            0, "Rješenje sistema je $x=4$.")
    assert clean is None
    assert code == "linear_system_no_correct_option"


def test_C_true_value_present_but_wrong_option_marked_rejects():
    clean, code = _validate(SYSTEM_TEXT, ["$3$ KM", "$2$ KM", "$5$ KM", "$1$ KM"],
                            1, "Rješenje sistema je $x=2$.")
    assert clean is None
    assert code == "linear_system_marked_option_math_mismatch"


def test_D_two_equivalent_correct_options_reject():
    clean, code = _validate(SYSTEM_TEXT,
                            ["$3$ KM", "$3,0$ KM", "$5$ KM", "$1$ KM"],
                            0, "Rješenje sistema je $x=3$.")
    assert clean is None
    assert code in ("duplicate_options", "equivalent_options",
                    "linear_system_multiple_correct_options")


def test_E_fractional_solution_is_matched_exactly():
    text = ("Cijena jedne sveske je $x$ KM, a cijena jedne olovke je $y$ KM. "
            "Vrijedi $2x+3y=9$ i $4x+y=11$. Koliko iznosi cijena jedne sveske?")
    # x = 12/5 = 2,4
    ok, _code = _validate(text, ["$2,4$ KM", "$2$ KM", "$3$ KM", "$1$ KM"],
                          0, "Rješenje sistema je $x=2,4$.")
    assert ok is not None
    bad, code = _validate(text, ["$2$ KM", "$4$ KM", "$3$ KM", "$1$ KM"],
                          0, "Rješenje je $x=2$.")
    assert bad is None and code == "linear_system_no_correct_option"


def test_F_inconsistent_system_rejects():
    text = ("Cijena jedne sveske je $x$ KM, a cijena jedne olovke je $y$ KM. "
            "Vrijedi $x+y=5$ i $2x+2y=11$. Koliko iznosi cijena jedne sveske?")
    clean, code = _validate(text, ["$3$ KM", "$2$ KM", "$5$ KM", "$1$ KM"],
                            0, "Rješenje sistema je $x=3$.")
    assert clean is None
    assert code == "linear_system_system_not_uniquely_solvable"


def test_G_dependent_system_rejects_without_extra_given():
    text = ("Cijena jedne sveske je $x$ KM, a cijena jedne olovke je $y$ KM. "
            "Vrijedi $2x+4y=10$ i $x+2y=5$. Koliko iznosi cijena jedne sveske?")
    clean, code = _validate(text, ["$3$ KM", "$2$ KM", "$5$ KM", "$1$ KM"],
                            0, "Rješenje sistema je $x=3$.")
    assert clean is None
    assert code == "linear_system_system_not_uniquely_solvable"


def test_G_dependent_system_with_extra_given_stays_publishable():
    """Živi K5 q1: zavisan sistem + dati $x=3$ je JEDNOZNAČAN i tačan."""
    text = ("Cijena jedne sveske je $x$ KM, a cijena jedne olovke je $y$ KM. "
            "Vrijedi $2x+4y=10$ i $x+2y=5$. Ako je $x=3$, koliko iznosi cijena "
            "jedne sveske?")
    result = linear_system_mcq.evaluate_system_mcq(
        text, ["$3$ KM", "$1$ KM", "$2$ KM", "$4$ KM"], "")
    assert result.applicable and result.valid
    assert result.correct_indices == (0,)


def test_H_ordinary_numeric_mcqs_still_publish():
    """Geometrija koja traži IZVEDENU veličinu ne smije pasti na ovom oraklu."""
    for text, options, index, solution in [
        ("Pravougaonik ima stranice dužina $6\\,\\text{cm}$ i $8\\,\\text{cm}$. "
         "Kolika je dužina njegove dijagonale?",
         ["$10\\,\\text{cm}$", "$48\\,\\text{cm}$", "$14\\,\\text{cm}$", "$12\\,\\text{cm}$"],
         0, "Po Pitagorinoj teoremi je $d=10$."),
        ("Kolika je površina jednakostraničnog trougla čija je stranica dužine "
         "$8\\,\\text{cm}$?",
         ["$16\\sqrt{3}\\,\\text{cm}^2$", "$64\\sqrt{3}\\,\\text{cm}^2$",
          "$8\\sqrt{3}\\,\\text{cm}^2$", "$32\\sqrt{3}\\,\\text{cm}^2$"],
         0, "Površina je $16\\sqrt{3}$."),
    ]:
        assert linear_system_mcq.publication_failure(text, options, index, solution) == ""


def test_H_two_independent_givens_for_a_derived_quantity_stay_silent():
    """`$a=3$` i `$b=4$` nisu SPREGNUT sistem — orakl ćuti (odluka 2)."""
    text = ("Pravougaonik ima stranicu $a=3$ cm i stranicu $b=4$ cm. "
            "Kolika je njegova površina?")
    options = ["$12$ cm", "$7$ cm", "$14$ cm", "$10$ cm"]
    assert linear_system_mcq.publication_failure(text, options, 0, "") == ""


def test_I_prose_exactly_one_doctrine_is_untouched():
    """Tvrdnjski oblik i dalje ide kroz exactly_one, ne kroz ovaj orakl."""
    text = "Koja je tvrdnja o razlomku $\\frac{3}{5}$ tačna?"
    options = ["Brojnik je 3, a nazivnik je 5.", "Razlomačka crta je brojnik.",
               "Nazivnik pokazuje broj dijelova koje uzimamo.", "Brojnik je 5."]
    assert linear_system_mcq.publication_failure(text, options, 0, "") == ""


def test_unit_mismatch_makes_the_oracle_stay_silent():
    text = ("Dužina je $x$, a širina je $y$. Vrijedi $2x+3y=12$ i $x-y=1$. "
            "Koliko iznosi dužina?")
    assert linear_system_mcq.publication_failure(
        text, ["$3$ cm", "$3$ m", "$5$ cm", "$1$ cm"], 0, "") == ""


def test_unresolvable_target_fails_closed():
    text = ("Vrijedi $2x+3y=12$ i $x-y=1$. Koliko iznosi ukupna cijena?")
    assert linear_system_mcq.publication_failure(
        text, ["$3$ KM", "$2$ KM", "$5$ KM", "$1$ KM"], 0, "") == "system_target_unresolved"


# ---------------------------------------------------------------------------
# P0-2 — PRAZAN VIDLJIV ODGOVOR
# ---------------------------------------------------------------------------

def _quick_turn(fake, message="Objasni postupak.", image=None):
    return quick.run_quick_turn(fake, {
        "session_id": "p0-2", "grade": 7, "selected_topic": "", "selected_oblast": "",
        "student_message": message, "conversation_history": [],
        "interaction_phase": ""}, image=image)


@pytest.mark.parametrize("reply", [
    "$$", "$$$$", "$ $", "$\\quad$", "$\\qquad$", "$\\,$", "$\\;$", "$\\!$",
    "\\(\\)", " ", "​",
])
def test_quick_empty_final_answer_fails_closed(reply):
    fake = FakeLLM()
    fake.queue(make_quick_output(reply=reply))
    response = _quick_turn(fake)
    assert response["answer"] == quick.SAFE_ERROR_MESSAGE
    # Bez `status` frontend ne iscrtava NIJEDNU prečicu (templates/index.html).
    assert "status" not in response
    assert fake.call_count == 1          # i dalje TAČNO jedan poziv


@pytest.mark.parametrize("reply", [
    "12", "$12$", "0", "$0$", "-1", "$-1$", "$\\frac{1}{2}$", "$x=4$",
    "$\\emptyset$", "$45$ KM", "$-\\frac{1}{8}$",
    "Na slici ne vidim dovoljno podataka.",
])
def test_quick_meaningful_short_answers_still_publish(reply):
    fake = FakeLLM()
    fake.queue(make_quick_output(reply=reply))
    response = _quick_turn(fake, message="Koliko je $84:7$?")
    assert response.get("status") == "ready"
    assert response["answer"].strip()
    assert fake.call_count == 1


def test_quick_image_path_shares_the_same_invariant():
    fake = FakeLLM()
    fake.queue(make_quick_image_output(reply="$$"))

    class _Image:
        data_url = "data:image/jpeg;base64,AAAA"
        image_format, width, height, normalized_bytes = "JPEG", 10, 10, 32

        def log_metadata(self):
            return "test-image"

    response = _quick_turn(fake, message="", image=_Image())
    assert response["answer"] == quick.SAFE_ERROR_MESSAGE
    assert "status" not in response
    assert fake.call_count == 1


@pytest.mark.parametrize("text,empty", [
    ("", True), ("   ", True), ("\n\n", True), ("$$", True), ("$ $", True),
    ("$\\quad$", True), ("\\\\", True),
    ("0", False), ("$0$", False), ("12", False), ("$x=4$", False),
    ("1/2", False), ("∅", False), ("$-1$", False),
])
def test_visible_text_is_empty_predicate(text, empty):
    assert visible_text_is_empty(text) is empty
