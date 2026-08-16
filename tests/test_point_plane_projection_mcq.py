# -*- coding: utf-8 -*-
"""Ortogonalna projekcija duži na ravan — živi nalaz N-6.

Kontrolni je objavio (oblast 9-02, „Ortogonalna projekcija duži na ravan"):

    „Tačke $A$ i $B$ nalaze se s iste strane ravni $\\alpha$. Njihove
     udaljenosti od ravni su $9$ cm i $12$ cm, a duž $AB$ ima dužinu $15$ cm.
     Kolika je dužina ortogonalne projekcije duži $AB$ na ravan $\\alpha$?"
    $9$ / $18$ / $6$ / $12$ cm — označeno $12$ cm.

Tačno je $\\Delta h=|12-9|=3$ i $p=\\sqrt{225-9}=\\sqrt{216}=6\\sqrt6$, pa
NIJEDNA opcija nije tačna. Modelov lanac $225-81=144$ je aritmetički tačan —
greška je u ODABIRU normalne razlike, dakle semantička.
"""
import pytest
from fractions import Fraction

from matbot import (kontrolni, linear_system_mcq, point_plane_projection_mcq,
                    square_pyramid_mcq)
from matbot.schema import KontrolniQuestionOutput

N6_TEXT = ("Tačke $A$ i $B$ nalaze se s iste strane ravni $\\alpha$. Njihove "
           "udaljenosti od ravni su $9$ cm i $12$ cm, a duž $AB$ ima dužinu "
           "$15$ cm. Kolika je dužina ortogonalne projekcije duži $AB$ na "
           "ravan $\\alpha$?")
N6_OPTIONS = ["$12$ cm", "$9$ cm", "$18$ cm", "$6$ cm"]
N6_SOLUTION = ("Projekcija se računa iz $p^2 = AB^2 - 9^2 = 225 - 81 = 144$, "
               "pa je $p = 12$ cm.")


class _Ctx:
    geometry_scope = "stereometrija"
    geometry_figures = ("ravan",)


def _validate(text, options, marked, solution="Rješenje.", lesson="9-02-012"):
    slot = {"slot": 5, "lesson_id": lesson,
            "lesson_title": "Ortogonalna projekcija duži na ravan",
            "difficulty": "medium"}
    parsed = KontrolniQuestionOutput(
        slot=5, lesson_id=lesson, text=text, options=options,
        correct_option_index=marked, expected_answer=options[marked],
        solution=solution, difficulty="medium")
    return kontrolni.validate_generated_question(parsed, slot, _Ctx(), set())


def _stem(side_phrase, d_a, d_b, *, length=None, projection=None, ask):
    given = []
    if length is not None:
        given.append(f"a duž $AB$ ima dužinu ${length}$ cm")
    if projection is not None:
        given.append(f"a ortogonalna projekcija duži $AB$ na ravan ima dužinu "
                     f"${projection}$ cm")
    return (f"Tačke $A$ i $B$ nalaze se {side_phrase} ravni $\\alpha$. "
            f"Njihove udaljenosti od ravni su ${d_a}$ cm i ${d_b}$ cm, "
            + ", ".join(given) + f". {ask}")


SAME, OPPOSITE = "s iste strane", "s različitih strana"
ASK_P = "Kolika je dužina ortogonalne projekcije duži $AB$ na ravan $\\alpha$?"
ASK_L = "Kolika je dužina duži $AB$?"


# ---------------------------------------------------------------------------
# ISTORIJSKI SLUČAJ (B) I NJEGOV ISPRAVAN BLIZANAC (A)
# ---------------------------------------------------------------------------

def test_B_historical_n6_is_rejected():
    clean, code = _validate(N6_TEXT, N6_OPTIONS, 0, N6_SOLUTION)
    assert clean is None
    assert code == "point_plane_no_correct_option"


def test_A_same_question_with_true_option_publishes():
    options = ["$6\\sqrt{6}$ cm", "$9$ cm", "$18$ cm", "$12$ cm"]
    solution = ("Normalna razlika je $12-9=3$, pa je "
                "$p=\\sqrt{15^2-3^2}=\\sqrt{216}=6\\sqrt{6}$ cm.")
    clean, code = _validate(N6_TEXT, options, 0, solution)
    assert clean is not None, code


def test_server_derives_the_exact_truth():
    result = point_plane_projection_mcq.evaluate_projection_mcq(
        N6_TEXT, N6_OPTIONS)
    assert result.applicable and not result.valid
    assert result.target == "p"
    assert result.truth_squared == Fraction(216)      # (6√6)^2
    assert result.correct_indices == ()


def test_model_solution_is_never_an_input():
    import inspect
    signature = inspect.signature(point_plane_projection_mcq.publication_failure)
    assert list(signature.parameters) == ["question", "option_texts", "marked_index"]


# ---------------------------------------------------------------------------
# C–E: ISPRAVNI SLUČAJEVI
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("side,d_a,d_b,length,truth_sq,options", [
    # C: iste udaljenosti -> dh=0 -> p = L
    (SAME, 5, 5, 13, 169, ["$13$ cm", "$12$ cm", "$5$ cm", "$8$ cm"]),
    # D: dh=6, L=10 -> p=8
    (SAME, 2, 8, 10, 64, ["$8$ cm", "$6$ cm", "$10$ cm", "$4$ cm"]),
    # E: suprotne strane, dh=7, L=12 -> p=sqrt(95)
    (OPPOSITE, 3, 4, 12, 95, ["$\\sqrt{95}$ cm", "$\\sqrt{143}$ cm",
                              "$12$ cm", "$5$ cm"]),
    # A(matrica): dh=3, L=15 -> p=6sqrt6
    (SAME, 9, 12, 15, 216, ["$6\\sqrt{6}$ cm", "$12$ cm", "$9$ cm", "$18$ cm"]),
])
def test_projection_cases_publish_when_marked_correctly(side, d_a, d_b, length,
                                                        truth_sq, options):
    text = _stem(side, d_a, d_b, length=length, ask=ASK_P)
    result = point_plane_projection_mcq.evaluate_projection_mcq(text, options)
    assert result.applicable, text
    assert result.truth_squared == Fraction(truth_sq)
    assert result.correct_indices == (0,)
    assert point_plane_projection_mcq.publication_failure(text, options, 0) == ""


def test_F_same_givens_different_side_changes_the_answer():
    """ISTA brojčana slika, različita strana → različita istina."""
    options = ["$\\sqrt{95}$ cm", "$\\sqrt{143}$ cm", "$12$ cm", "$5$ cm"]
    opposite = _stem(OPPOSITE, 3, 4, length=12, ask=ASK_P)   # dh=7 -> 95
    same = _stem(SAME, 3, 4, length=12, ask=ASK_P)           # dh=1 -> 143
    assert point_plane_projection_mcq.evaluate_projection_mcq(
        opposite, options).truth_squared == Fraction(95)
    assert point_plane_projection_mcq.evaluate_projection_mcq(
        same, options).truth_squared == Fraction(143)
    # Označena opcija koja vrijedi za JEDNU stranu pada za drugu.
    assert point_plane_projection_mcq.publication_failure(opposite, options, 0) == ""
    assert point_plane_projection_mcq.publication_failure(same, options, 0) == \
        "marked_option_math_mismatch"


def test_K_segment_length_from_projection_and_distances():
    """Obrnut smjer: date udaljenosti + projekcija, tražena dužina duži."""
    text = _stem(SAME, 2, 8, projection=8, ask=ASK_L)        # dh=6 -> L=10
    options = ["$10$ cm", "$8$ cm", "$6$ cm", "$14$ cm"]
    result = point_plane_projection_mcq.evaluate_projection_mcq(text, options)
    assert result.applicable and result.valid
    assert result.target == "L"
    assert result.truth_squared == Fraction(100)
    assert point_plane_projection_mcq.publication_failure(text, options, 0) == ""


# ---------------------------------------------------------------------------
# G–J: ODBIJANJA I UZDRŽAVANJE
# ---------------------------------------------------------------------------

def test_G_true_present_but_wrong_option_marked_rejects():
    text = _stem(SAME, 2, 8, length=10, ask=ASK_P)           # p=8
    options = ["$8$ cm", "$6$ cm", "$10$ cm", "$4$ cm"]
    assert point_plane_projection_mcq.publication_failure(text, options, 2) == \
        "marked_option_math_mismatch"


def test_H_equivalent_correct_options_reject():
    """`\\sqrt{216}` i `6\\sqrt6` su ISTA vrijednost."""
    options = ["$\\sqrt{216}$ cm", "$6\\sqrt{6}$ cm", "$12$ cm", "$9$ cm"]
    assert point_plane_projection_mcq.publication_failure(N6_TEXT, options, 0) == \
        "multiple_correct_options"


def test_I_normal_gap_larger_than_segment_is_impossible():
    """Suprotne strane: dh = 9+12 = 21 > 15 — takva duž ne postoji."""
    text = _stem(OPPOSITE, 9, 12, length=15, ask=ASK_P)
    assert point_plane_projection_mcq.publication_failure(text, N6_OPTIONS, 0) == \
        "impossible_geometry"


@pytest.mark.parametrize("text", [
    # J: strana nije navedena, a matematički je neophodna
    ("Tačke $A$ i $B$ date su u prostoru. Njihove udaljenosti od ravni "
     "$\\alpha$ su $9$ cm i $12$ cm, a duž $AB$ ima dužinu $15$ cm. "
     + ASK_P),
    # obje strane pomenute — dvosmisleno
    ("Tačke $A$ i $B$ nalaze se s iste strane, a moguće i s različitih strana "
     "ravni $\\alpha$. Njihove udaljenosti od ravni su $9$ cm i $12$ cm, "
     "a duž $AB$ ima dužinu $15$ cm. " + ASK_P),
    # nedostaje dužina duži
    ("Tačke $A$ i $B$ nalaze se s iste strane ravni $\\alpha$. Njihove "
     "udaljenosti od ravni su $9$ cm i $12$ cm. " + ASK_P),
])
def test_J_missing_or_ambiguous_semantics_stays_silent(text):
    assert point_plane_projection_mcq.publication_failure(
        text, N6_OPTIONS, 0) == ""


def test_mixed_units_stay_silent():
    text = ("Tačke $A$ i $B$ nalaze se s iste strane ravni $\\alpha$. Njihove "
            "udaljenosti od ravni su $9$ cm i $12$ m, a duž $AB$ ima dužinu "
            "$15$ cm. " + ASK_P)
    assert point_plane_projection_mcq.publication_failure(
        text, N6_OPTIONS, 0) == ""


def test_all_decimal_options_stay_silent():
    options = ["$14,70$ cm", "$12,00$ cm", "$9,00$ cm", "$18,00$ cm"]
    assert point_plane_projection_mcq.publication_failure(
        N6_TEXT, options, 0) == ""


# ---------------------------------------------------------------------------
# L–M: NESRODNA PITANJA OSTAJU NETAKNUTA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,options", [
    # L: druga tačka–ravan pitanja
    ("Tačka $A$ je izvan ravni $\\alpha$. Duž $AH$ je normala na ravan "
     "$\\alpha$. Ako je $AH=8$ cm, kolika je udaljenost tačke $A$ od ravni?",
     ["$8$ cm", "$16$ cm", "$4$ cm", "$64$ cm"]),
    ("Prava $p$ siječe ravan $\\alpha$. Ugao između prave $p$ i normale na "
     "ravan iznosi $30^\\circ$. Koliki je ugao između prave i ravni?",
     ["$60^\\circ$", "$90^\\circ$", "$120^\\circ$", "$30^\\circ$"]),
    # M: ravanska geometrija
    ("Pravougaonik ima stranice $6\\,\\text{cm}$ i $8\\,\\text{cm}$. Kolika je "
     "dužina njegove dijagonale?",
     ["$10\\,\\text{cm}$", "$14\\,\\text{cm}$", "$2\\,\\text{cm}$",
      "$\\sqrt{28}\\,\\text{cm}$"]),
])
def test_LM_unrelated_questions_are_untouched(text, options):
    assert point_plane_projection_mcq.publication_failure(text, options, 0) == ""


def test_L_unrelated_point_plane_question_still_publishes():
    clean, code = _validate(
        "Tačka $A$ je izvan ravni $\\alpha$. Duž $AH$ je normala na ravan "
        "$\\alpha$, a $H$ je podnožje normale. Ako je $AH=8$ cm, kolika je "
        "udaljenost tačke $A$ od ravni $\\alpha$?",
        ["$8$ cm", "$16$ cm", "$4$ cm", "$64$ cm"], 0,
        "Udaljenost tačke od ravni je dužina normale, dakle $8$ cm.",
        lesson="9-02-009")
    assert clean is not None, code


# ---------------------------------------------------------------------------
# N–O: RANIJI ORAKLI NEPROMIJENJENI
# ---------------------------------------------------------------------------

def test_N_square_pyramid_oracle_unchanged():
    text = ("Pravilna četvorostrana piramida ima stranicu kvadratne baze "
            "$a=10\\,\\text{cm}$ i apotemu $h_a=13\\,\\text{cm}$. "
            "Kolika je dužina bočne ivice $s$?")
    options = ["$\\sqrt{219}\\,\\text{cm}$", "$\\sqrt{194}\\,\\text{cm}$",
               "$\\sqrt{169}\\,\\text{cm}$", "$\\sqrt{269}\\,\\text{cm}$"]
    assert square_pyramid_mcq.publication_failure(text, options, 0) == \
        "marked_option_math_mismatch"
    assert square_pyramid_mcq.publication_failure(text, options, 1) == ""
    # a orakl projekcije na taj oblik ćuti
    assert point_plane_projection_mcq.publication_failure(text, options, 0) == ""


def test_O_linear_system_oracle_unchanged():
    text = ("Cijena jedne sveske je $x$ KM, a cijena jedne olovke je $y$ KM. "
            "Vrijedi $2x+3y=9$ i $4x+y=11$. Koliko iznosi cijena jedne sveske?")
    bad = ["$2$ KM", "$4$ KM", "$3$ KM", "$1$ KM"]
    assert linear_system_mcq.publication_failure(text, bad, 0) == "no_correct_option"
    assert point_plane_projection_mcq.publication_failure(text, bad, 0) == ""
