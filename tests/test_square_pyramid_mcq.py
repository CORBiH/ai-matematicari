# -*- coding: utf-8 -*-
"""Pravilna četvorostrana piramida — živi nalaz N-4 (post-deploy 70bb514).

Kontrolni je objavio:

    „Pravilna četvorostrana piramida ima stranicu kvadratne baze $a=10$ cm i
     apotemu $h_a=13$ cm. Kolika je dužina bočne ivice $s$?“
    $\\sqrt{219}$ / $\\sqrt{194}$ / $\\sqrt{169}$ / $\\sqrt{269}$ — označeno
    $\\sqrt{219}$.

Tačno je $s^2 = h_a^2 + (a/2)^2 = 194$, a $\\sqrt{194}$ je BILA ponuđena.
$\\sqrt{219}$ nastaje samo zamjenom uloga: apotema upotrijebljena kao visina.
Modelov račun je pri tom ARITMETIČKI TAČAN ($169+50=219$), pa ga `mathcheck`
nije mogao oboriti, a oznake su uredne, pa ni `geometrycheck`.
"""
import pytest
from fractions import Fraction

from matbot import kontrolni, linear_system_mcq, square_pyramid_mcq
from matbot.schema import KontrolniQuestionOutput

N4_TEXT = ("Pravilna četvorostrana piramida ima stranicu kvadratne baze "
           "$a=10\\,\\text{cm}$ i apotemu $h_a=13\\,\\text{cm}$. "
           "Kolika je dužina bočne ivice $s$?")
N4_OPTIONS = ["$\\sqrt{219}\\,\\text{cm}$", "$\\sqrt{194}\\,\\text{cm}$",
              "$\\sqrt{169}\\,\\text{cm}$", "$\\sqrt{269}\\,\\text{cm}$"]
N4_SOLUTION = ("Bočna ivica se računa iz visine i poludijagonale baze: "
               "$s^2 = 13^2 + 50 = 219$, pa je $s=\\sqrt{219}$ cm.")


class _Ctx:
    geometry_scope = "stereometrija"
    geometry_figures = ("piramida", "kvadrat")


def _validate(text, options, marked, solution="Rješenje.", lesson="9-07-014"):
    slot = {"slot": 5, "lesson_id": lesson,
            "lesson_title": "Primjena Pitagorine teoreme na piramidu",
            "difficulty": "medium"}
    parsed = KontrolniQuestionOutput(
        slot=5, lesson_id=lesson, text=text, options=options,
        correct_option_index=marked, expected_answer=options[marked],
        solution=solution, difficulty="medium")
    return kontrolni.validate_generated_question(parsed, slot, _Ctx(), set())


def _stem(given_role, given_value, target_words, side=10):
    role_text = {"h_a": f"apotemu $h_a={given_value}\\,\\text{{cm}}$",
                 "H": f"visinu $H={given_value}\\,\\text{{cm}}$",
                 "s": f"bočnu ivicu $s={given_value}\\,\\text{{cm}}$"}[given_role]
    return (f"Pravilna četvorostrana piramida ima stranicu kvadratne baze "
            f"$a={side}\\,\\text{{cm}}$ i {role_text}. {target_words}")


ASK_S = "Kolika je dužina bočne ivice $s$?"
ASK_HA = "Kolika je apotema $h_a$?"
ASK_H = "Kolika je visina piramide $H$?"


# ---------------------------------------------------------------------------
# ISTORIJSKI SLUČAJ
# ---------------------------------------------------------------------------

def test_B_historical_n4_is_rejected():
    clean, code = _validate(N4_TEXT, N4_OPTIONS, 0, N4_SOLUTION)
    assert clean is None
    assert code == "square_pyramid_marked_option_math_mismatch"


def test_A_same_question_with_true_option_marked_publishes():
    """Isti zadatak s ISPRAVNO označenim $\\sqrt{194}$ ostaje objavljiv.

    Rješenje mora pratiti označenu opciju — inače ga (ispravno) obori
    postojeći `solution_marked_value_divergence`, prije ovog orakla."""
    solution = ("Bočna ivica: $s^2 = h_a^2 + (a/2)^2 = 169 + 25 = 194$, "
                "pa je $s=\\sqrt{194}$ cm.")
    clean, code = _validate(N4_TEXT, N4_OPTIONS, 1, solution)
    assert clean is not None, code


def test_server_derives_the_exact_true_value():
    result = square_pyramid_mcq.evaluate_square_pyramid_mcq(N4_TEXT, N4_OPTIONS)
    assert result.applicable and result.valid
    assert result.target == "s"
    assert result.truth_squared == Fraction(194)
    assert result.correct_indices == (1,)


def test_model_solution_is_never_an_input():
    """Orakl ne prima `solution` — modelova tvrdnja se provjerava, ne koristi."""
    import inspect
    signature = inspect.signature(square_pyramid_mcq.publication_failure)
    assert list(signature.parameters) == ["question", "option_texts", "marked_index"]


# ---------------------------------------------------------------------------
# MATRICA SLUČAJEVA C–G (svi iz zadanog skupa odnosa)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("side,given_role,given,ask,truth_sq,options", [
    # C: a=6, H=4 -> h_a = 5
    (6, "H", 4, ASK_HA, 25, ["$5\\,\\text{cm}$", "$4\\,\\text{cm}$",
                             "$7\\,\\text{cm}$", "$3\\,\\text{cm}$"]),
    # D: a=6, H=4 -> s = sqrt(34)
    (6, "H", 4, ASK_S, 34, ["$\\sqrt{34}\\,\\text{cm}$", "$5\\,\\text{cm}$",
                            "$\\sqrt{52}\\,\\text{cm}$", "$6\\,\\text{cm}$"]),
    # E: dato a + s, traži se h_a. NAPOMENA: a=8, s=5 iz prvobitne matrice
    # NIJE ostvariva piramida (s mora nadmašiti $a\sqrt2/2\approx5{,}66$, inače
    # je vrh ispod baze), pa je uzet realizovan slučaj a=6, s=5 -> h_a=4.
    (6, "s", 5, ASK_HA, 16, ["$4\\,\\text{cm}$", "$3\\,\\text{cm}$",
                             "$5\\,\\text{cm}$", "$6\\,\\text{cm}$"]),
    # F: a=8, h_a=5 -> H = 3
    (8, "h_a", 5, ASK_H, 9, ["$3\\,\\text{cm}$", "$4\\,\\text{cm}$",
                             "$6\\,\\text{cm}$", "$7\\,\\text{cm}$"]),
    # G: a=6, s=5 -> H = sqrt(7)
    (6, "s", 5, ASK_H, 7, ["$\\sqrt{7}\\,\\text{cm}$", "$4\\,\\text{cm}$",
                           "$\\sqrt{11}\\,\\text{cm}$", "$3\\,\\text{cm}$"]),
    # 1: a=10, h_a=13 -> s = sqrt(194)  (istorijski oblik, ispravno označen)
    (10, "h_a", 13, ASK_S, 194, ["$\\sqrt{194}\\,\\text{cm}$", "$\\sqrt{219}\\,\\text{cm}$",
                                 "$13\\,\\text{cm}$", "$\\sqrt{169}\\,\\text{cm}$"]),
])
def test_solvable_cases_publish_when_marked_correctly(side, given_role, given,
                                                      ask, truth_sq, options):
    text = _stem(given_role, given, ask, side=side)
    result = square_pyramid_mcq.evaluate_square_pyramid_mcq(text, options)
    assert result.applicable, text
    assert result.truth_squared == Fraction(truth_sq)
    assert result.correct_indices == (0,)
    assert square_pyramid_mcq.publication_failure(text, options, 0) == ""


def test_I_true_value_present_but_wrong_option_marked_rejects():
    text = _stem("H", 4, ASK_HA, side=6)
    options = ["$5\\,\\text{cm}$", "$4\\,\\text{cm}$", "$7\\,\\text{cm}$",
               "$3\\,\\text{cm}$"]
    assert square_pyramid_mcq.publication_failure(text, options, 2) == \
        "marked_option_math_mismatch"


def test_H_true_value_absent_from_all_options_rejects():
    text = _stem("H", 4, ASK_HA, side=6)          # istina je 5
    options = ["$4\\,\\text{cm}$", "$6\\,\\text{cm}$", "$7\\,\\text{cm}$",
               "$8\\,\\text{cm}$"]
    assert square_pyramid_mcq.publication_failure(text, options, 0) == \
        "no_correct_option"


def test_J_equivalent_correct_options_reject():
    """`5` i `\\sqrt{25}` su ISTA vrijednost — dvije tačne opcije."""
    text = _stem("H", 4, ASK_HA, side=6)
    options = ["$5\\,\\text{cm}$", "$\\sqrt{25}\\,\\text{cm}$",
               "$7\\,\\text{cm}$", "$3\\,\\text{cm}$"]
    assert square_pyramid_mcq.publication_failure(text, options, 0) == \
        "multiple_correct_options"


def test_L_lateral_edge_shorter_than_half_diagonal_is_impossible():
    """a=8, s=5: $s<a\\sqrt2/2\\approx5{,}66$, pa bi vrh bio ispod baze.

    Formalno $h_a^2 = s^2-(a/2)^2 = 9$ „izađe“, ali takvo tijelo ne postoji —
    server zato ne objavljuje ni prividno uredan rezultat."""
    text = _stem("s", 5, ASK_HA, side=8)
    options = ["$3\\,\\text{cm}$", "$4\\,\\text{cm}$", "$5\\,\\text{cm}$",
               "$6\\,\\text{cm}$"]
    assert square_pyramid_mcq.publication_failure(text, options, 0) == \
        "impossible_geometry"


def test_L_impossible_geometry_rejects():
    """Apotema manja od poluosnovice: visina bi imala negativan kvadrat."""
    text = _stem("h_a", 2, ASK_H, side=10)        # a/2 = 5 > h_a = 2
    options = ["$3\\,\\text{cm}$", "$4\\,\\text{cm}$", "$5\\,\\text{cm}$",
               "$6\\,\\text{cm}$"]
    assert square_pyramid_mcq.publication_failure(text, options, 0) == \
        "impossible_geometry"


# ---------------------------------------------------------------------------
# K: DVOSMISLENO / NEDOVOLJNO — orakl ĆUTI, ne objavljuje lažno
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    # bez stranice baze
    ("Pravilna četvorostrana piramida ima apotemu $h_a=13\\,\\text{cm}$. "
     "Kolika je dužina bočne ivice $s$?"),
    # tražena veličina nije imenovana
    ("Pravilna četvorostrana piramida ima stranicu kvadratne baze "
     "$a=10\\,\\text{cm}$ i apotemu $h_a=13\\,\\text{cm}$. Koliko iznosi?"),
    # dvije tražene veličine u pitanju
    ("Pravilna četvorostrana piramida ima stranicu kvadratne baze "
     "$a=10\\,\\text{cm}$ i apotemu $h_a=13\\,\\text{cm}$. "
     "Kolika je visina piramide i bočna ivica?"),
    # traži se veličina koja je VEĆ data
    ("Pravilna četvorostrana piramida ima stranicu kvadratne baze "
     "$a=10\\,\\text{cm}$ i apotemu $h_a=13\\,\\text{cm}$. Kolika je apotema?"),
])
def test_K_ambiguous_or_insufficient_stays_silent(text):
    options = ["$5\\,\\text{cm}$", "$6\\,\\text{cm}$", "$7\\,\\text{cm}$",
               "$8\\,\\text{cm}$"]
    assert square_pyramid_mcq.publication_failure(text, options, 0) == ""


def test_K_all_decimal_options_stay_silent():
    """Aproksimacije: orakl ne uvodi vlastitu toleranciju, nego ćuti."""
    text = _stem("h_a", 13, ASK_S, side=10)
    options = ["$13,93\\,\\text{cm}$", "$14,80\\,\\text{cm}$",
               "$13,00\\,\\text{cm}$", "$16,401\\,\\text{cm}$"]
    assert square_pyramid_mcq.publication_failure(text, options, 0) == ""


def test_mixed_units_stay_silent():
    text = _stem("H", 4, ASK_HA, side=6)
    options = ["$5\\,\\text{cm}$", "$5\\,\\text{m}$", "$7\\,\\text{cm}$",
               "$3\\,\\text{cm}$"]
    assert square_pyramid_mcq.publication_failure(text, options, 0) == ""


# ---------------------------------------------------------------------------
# M: NESRODNA GEOMETRIJA OSTAJE NETAKNUTA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,options", [
    ("Valjak ima poluprečnik baze $r=4\\,\\text{cm}$ i visinu $H=7\\,\\text{cm}$. "
     "Koliki je prečnik njegove baze?",
     ["$8\\,\\text{cm}$", "$4\\,\\text{cm}$", "$14\\,\\text{cm}$", "$7\\,\\text{cm}$"]),
    ("Pravilna trostrana piramida ima stranicu baze $a=6\\,\\text{cm}$ i "
     "apotemu $h_a=5\\,\\text{cm}$. Kolika je dužina bočne ivice $s$?",
     ["$\\sqrt{34}\\,\\text{cm}$", "$5\\,\\text{cm}$", "$6\\,\\text{cm}$",
      "$7\\,\\text{cm}$"]),
    ("Zarubljena piramida ima stranicu baze $a=10\\,\\text{cm}$ i "
     "apotemu $h_a=13\\,\\text{cm}$. Kolika je dužina bočne ivice $s$?",
     ["$\\sqrt{219}\\,\\text{cm}$", "$\\sqrt{194}\\,\\text{cm}$",
      "$13\\,\\text{cm}$", "$14\\,\\text{cm}$"]),
    ("Pravougli trougao ima katete $9\\,\\text{cm}$ i $12\\,\\text{cm}$. "
     "Kolika je hipotenuza?",
     ["$15\\,\\text{cm}$", "$21\\,\\text{cm}$", "$3\\,\\text{cm}$",
      "$\\sqrt{63}\\,\\text{cm}$"]),
])
def test_M_unrelated_geometry_is_untouched(text, options):
    assert square_pyramid_mcq.publication_failure(text, options, 0) == ""


def test_M_unrelated_geometry_still_publishes_through_full_chain():
    clean, code = _validate(
        "Pravougli trougao ima katete dužine $9\\,\\text{cm}$ i "
        "$12\\,\\text{cm}$. Kolika je dužina hipotenuze?",
        ["$15\\,\\text{cm}$", "$21\\,\\text{cm}$", "$3\\,\\text{cm}$",
         "$\\sqrt{63}\\,\\text{cm}$"], 0,
        "Po Pitagorinoj teoremi je $c=\\sqrt{81+144}=\\sqrt{225}=15$.",
        lesson="8-04-003")
    assert clean is not None, code


# ---------------------------------------------------------------------------
# N: ORAKL SISTEMA OSTAJE NEPROMIJENJEN
# ---------------------------------------------------------------------------

def test_N_linear_system_oracle_unchanged():
    text = ("Cijena jedne sveske je $x$ KM, a cijena jedne olovke je $y$ KM. "
            "Vrijedi $2x+3y=9$ i $4x+y=11$. Koliko iznosi cijena jedne sveske?")
    bad = ["$2$ KM", "$4$ KM", "$3$ KM", "$1$ KM"]
    assert linear_system_mcq.publication_failure(text, bad, 0) == "no_correct_option"
    good = ["$2,4$ KM", "$4$ KM", "$3$ KM", "$1$ KM"]
    assert linear_system_mcq.publication_failure(text, good, 0) == ""
    # a piramidalni orakl na taj oblik ćuti
    assert square_pyramid_mcq.publication_failure(text, bad, 0) == ""


def test_N_pyramid_oracle_silent_on_linear_system_family():
    text = ("Pravilna četvorostrana piramida ima stranicu kvadratne baze "
            "$a=10\\,\\text{cm}$. Vrijedi $2x+3y=9$ i $4x+y=11$. "
            "Koliko iznosi $x$?")
    assert square_pyramid_mcq.publication_failure(
        text, ["$2$", "$3$", "$4$", "$1$"], 0) == ""
