# -*- coding: utf-8 -*-
"""Integritet strukturne matematike — živi nalazi N-1 i N-3 (2026-08-16).

Dvije objavljene greške ISTE KLASE: LaTeX sintaksno ispravan, svaka komanda na
dozvoljenoj listi — a ono što je učenik vidio nije bila matematika.

    N-1  `$\\sqrt{ posibilidades }\\,\\text{cm}$`  (kontrolni 8-04, distraktor)
    N-3  `$AB\\beta CD$`                            (kontrolni 7-05, u zadatku)

Testovi voze STVARNI publikacijski put (Kontrolni validator, Quick i Explain
turnove), ne samo pomoćnu funkciju.
"""
import pytest

from matbot import explain, kontrolni, quick
from matbot.mathsafe import find_unsafe_math_issues, sanitize_and_validate_math_text
from matbot.schema import KontrolniQuestionOutput
from tests.conftest import FakeLLM, make_explain_output, make_quick_output

# ---------------------------------------------------------------------------
# TAČNI ISTORIJSKI PAYLOADI
# ---------------------------------------------------------------------------

N1_OPTION = "$\\sqrt{ posibilidades }\\,\\text{cm}$"
N1_TEXT = ("Pravougli trougao ima katete dužine $a=9\\,\\text{cm}$ i "
           "$b=12\\,\\text{cm}$. Kolika je dužina hipotenuze $c$ prema "
           "Pitagorinoj teoremi?")
N1_OPTIONS = ["$15\\,\\text{cm}$", "$12\\,\\text{cm}$", N1_OPTION, "$21\\,\\text{cm}$"]
N1_SOLUTION = "Po Pitagorinoj teoremi je $c=\\sqrt{9^2+12^2}=\\sqrt{225}=15$."

N3_TEXT = ("Pri konstrukciji trapeza $ABCD$ važi $AB\\beta CD$. Ako je osnovica "
           "$AB$ duga $8\\,\\text{cm}$, a osnovica $CD$ duga $5\\,\\text{cm}$, "
           "kolika je razlika dužina osnovica?")
N3_OPTIONS = ["$3\\,\\text{cm}$", "$2\\,\\text{cm}$", "$40\\,\\text{cm}$",
              "$13\\,\\text{cm}$"]
N3_SOLUTION = "Razlika osnovica je $8-5=3$."


class _Ctx:
    geometry_scope = "planimetrija"
    geometry_figures = ("trougao", "trapez")


def _validate(text, options, correct_index, solution, lesson="8-04-001"):
    slot = {"slot": 1, "lesson_id": lesson, "lesson_title": "Lekcija",
            "difficulty": "medium"}
    parsed = KontrolniQuestionOutput(
        slot=1, lesson_id=lesson, text=text, options=options,
        correct_option_index=correct_index, expected_answer=options[correct_index],
        solution=solution, difficulty="medium")
    return kontrolni.validate_generated_question(parsed, slot, _Ctx(), set())


def test_n1_historical_option_is_rejected_before_publication():
    clean, code = _validate(N1_TEXT, N1_OPTIONS, 0, N1_SOLUTION)
    assert clean is None
    assert code == "unsafe_or_long_option"


def test_n3_historical_stem_is_rejected_before_publication():
    clean, code = _validate(N3_TEXT, N3_OPTIONS, 0, N3_SOLUTION, lesson="7-05-001")
    assert clean is None
    assert code == "unsafe_or_long_text"


def test_n1_and_n3_carry_precise_internal_reasons():
    assert any(i.startswith("prose_in_math_argument:sqrt:posibilidades")
               for i in find_unsafe_math_issues(N1_OPTION))
    assert any(i.startswith("nonrelational_command_between_objects:beta")
               for i in find_unsafe_math_issues("$AB\\beta CD$"))


# ---------------------------------------------------------------------------
# ISTI KVAR SE OBJAVLJIVAO I KROZ QUICK I KROZ EXPLAIN — zato dijeljeni sloj
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("reply", [
    "Hipotenuza je $\\sqrt{ posibilidades }\\,\\text{cm}$.",
    "U trapezu vrijedi $AB\\beta CD$.",
])
def test_quick_no_longer_publishes_damaged_math(reply):
    fake = FakeLLM()
    fake.queue(make_quick_output(reply=reply))
    response = quick.run_quick_turn(fake, {
        "session_id": "n13", "grade": 8, "selected_topic": "", "selected_oblast": "",
        "student_message": "Koliko je?", "conversation_history": [],
        "interaction_phase": ""})
    assert response["answer"] == quick.SAFE_ERROR_MESSAGE
    assert "status" not in response
    assert fake.call_count == 1


@pytest.mark.parametrize("reply", [
    "Hipotenuza je $\\sqrt{ posibilidades }\\,\\text{cm}$.",
    "U trapezu vrijedi $AB\\beta CD$.",
])
def test_explain_no_longer_publishes_damaged_math(reply):
    fake = FakeLLM()
    fake.queue(make_explain_output(reply=reply))
    response = explain.run_explain_turn(fake, {
        "grade": 8, "selected_topic": "8-04-001", "selected_oblast": "",
        "student_message": "Objasni", "conversation_history": [],
        "interaction_phase": "", "last_tutor_message": ""})
    assert "status" not in response
    assert fake.call_count == 1


# ---------------------------------------------------------------------------
# STRUKTURNA MATRICA A–K
# ---------------------------------------------------------------------------

def _safe(text):
    return sanitize_and_validate_math_text(text)[1]


A_VALID_SQRT_NUMERIC = ["$\\sqrt{2}$", "$\\sqrt[3]{27}$", "$6\\sqrt{2}$",
                        "$16\\sqrt{3}\\,\\text{cm}^2$", "$\\sqrt{25}\\,\\text{cm}$"]
B_VALID_SQRT_ALGEBRAIC = ["$\\sqrt{x^2+1}$", "$\\sqrt{a^2+b^2}$",
                          "$\\sqrt{\\frac{3}{5}}$", "$\\sqrt{ab}$",
                          "$\\sqrt{abc}$", "$\\sqrt{AB}$"]
C_PROSE_IN_SQRT = ["$\\sqrt{ posibilidades }$", "$\\sqrt{ odgovor }$",
                   "$\\sqrt{broj}$", "$\\sqrt{rezultat}$"]
D_PROSE_IN_FRACTION = ["$\\frac{ tekst }{2}$", "$\\frac{x}{ odgovor}$",
                       "$\\frac{rezultat}{3}$"]
E_VALID_UNITS = ["$5\\,\\text{cm}$", "$12\\,\\text{cm}^2$", "$8\\,\\text{KM}$",
                 "$18,75\\ \\text{m/s}$", "$2\\,000\\ \\text{KM}$"]
F_VALID_GREEK = ["$\\alpha = 30^\\circ$", "$\\beta = 45^\\circ$",
                 "$\\alpha+\\beta=90^\\circ$", "$\\alpha=\\delta=50^\\circ$",
                 "$\\beta=\\varepsilon=60^\\circ$", "$2\\pi r$",
                 "$\\angle ABC = \\beta$", "$\\beta + \\gamma = 180^\\circ$"]
G_BOGUS_RELATION = ["$AB\\beta CD$", "$AB\\alpha CD$", "$AB \\gamma CD$",
                    "$\\overline{AB}\\beta\\overline{CD}$", "$AB\\angle CD$"]
H_VALID_RELATIONS = ["$AB \\parallel CD$", "$AB \\perp CD$", "$AB = CD$",
                     "$AB \\cong CD$", "$AB \\sim CD$", "$AB \\cdot CD$",
                     "$\\overline{AB} \\parallel \\overline{CD}$",
                     "$\\triangle ABC \\sim \\triangle DEF$"]
I_NESTED_MALFORMED = ["$\\frac{2}{\\sqrt{rezultat}}$",
                      "$\\sqrt{\\frac{ tekst }{2}}$",
                      "$5 + \\sqrt{ odgovor } - 2$"]
VALID_SETS_AND_INEQUALITIES = ["$x \\in A$", "$A \\subseteq B$", "$A \\cap B$",
                               "$\\mathbb{Z}$", "$\\emptyset$", "$x \\le 4$",
                               "$a > b$", "$x \\geq \\frac{3}{2}$",
                               "$[0,\\frac{1}{2}]$"]


@pytest.mark.parametrize("text", A_VALID_SQRT_NUMERIC + B_VALID_SQRT_ALGEBRAIC
                         + E_VALID_UNITS + F_VALID_GREEK + H_VALID_RELATIONS
                         + VALID_SETS_AND_INEQUALITIES)
def test_valid_math_is_preserved(text):
    assert _safe(text), find_unsafe_math_issues(text)


@pytest.mark.parametrize("text", C_PROSE_IN_SQRT + D_PROSE_IN_FRACTION
                         + G_BOGUS_RELATION + I_NESTED_MALFORMED)
def test_damaged_math_is_rejected(text):
    assert not _safe(text)


def test_J_ordinary_numeric_controlni_mcq_still_publishes():
    """Uobičajena brojčana pitanja iz žive kampanje ostaju objavljiva."""
    clean, code = _validate(
        "Pravougli trougao ima katete dužine $9\\,\\text{cm}$ i "
        "$12\\,\\text{cm}$. Kolika je dužina hipotenuze?",
        ["$15\\,\\text{cm}$", "$21\\,\\text{cm}$", "$3\\,\\text{cm}$",
         "$\\sqrt{207}\\,\\text{cm}$"],
        0, "Po Pitagorinoj teoremi je $c=\\sqrt{81+144}=\\sqrt{225}=15$.")
    assert clean is not None, code


def test_J_geometry_relation_stem_still_publishes():
    clean, code = _validate(
        "U trapezu $ABCD$ vrijedi $AB \\parallel CD$. Osnovica $AB$ je "
        "$8\\,\\text{cm}$, a $CD$ je $5\\,\\text{cm}$. Kolika je razlika?",
        ["$3\\,\\text{cm}$", "$2\\,\\text{cm}$", "$40\\,\\text{cm}$",
         "$13\\,\\text{cm}$"],
        0, "Razlika osnovica je $8-5=3$.", lesson="7-05-001")
    assert clean is not None, code


@pytest.mark.parametrize("reply", [
    "Hipotenuza je $\\sqrt{225}=15\\,\\text{cm}$.",
    "U trapezu vrijedi $AB \\parallel CD$.",
    "Ugao je $\\alpha = 30^\\circ$, a $\\beta = 60^\\circ$.",
    "Rezultat je $\\frac{23}{20}$.",
    "$P = 12\\,\\text{cm}^2$",
])
def test_K_valid_mode_output_is_unchanged(reply):
    fake = FakeLLM()
    fake.queue(make_quick_output(reply=reply))
    response = quick.run_quick_turn(fake, {
        "session_id": "k", "grade": 8, "selected_topic": "", "selected_oblast": "",
        "student_message": "Koliko je?", "conversation_history": [],
        "interaction_phase": ""})
    assert response.get("status") == "ready", response.get("answer")


def test_no_silent_rewriting_of_damaged_notation():
    """Oštećen zapis se NE pretvara u pogođenu matematiku — samo se odbija."""
    cleaned, safe = sanitize_and_validate_math_text("$AB\\beta CD$")
    assert not safe
    assert "\\parallel" not in cleaned
    assert "\\beta" in cleaned          # ništa nije tiho prepisano
    cleaned2, safe2 = sanitize_and_validate_math_text(N1_OPTION)
    assert not safe2
    assert "posibilidades" in cleaned2  # ništa nije tiho obrisano
