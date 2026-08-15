"""Semantička (ne samo tekstualna) jednakost MC opcija (matbot/option_equivalence.py).

Defekt 4 (živi produkcijski nalaz, „Dijagonala kvadrata“, 8. razred): dvije
vizuelno različite opcije mogu predstavljati istu vrijednost ($8\\sqrt{2}\\,cm$
i $11,3\\,cm$) ili biti algebarski identične ($d=a\\sqrt{2}$ i $d=\\sqrt{2}a$).
Kategorije 16-30 iz zahtjeva."""
from matbot import task_families as tf
from matbot.option_equivalence import (
    canonicalize_expression, find_equivalent_option_pairs, options_are_equivalent,
)
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import FakeLLM, make_options, make_output, make_task
from tests.conftest import turn_payload


# --- Kategorija 16-25: numerička i simbolička ekvivalencija -----------------

def test_case16_rounded_numeric_value_same_as_exact_irrational():
    assert options_are_equivalent("$8\\sqrt{2}\\,\\text{cm}$", "$11,3\\,\\text{cm}$")


def test_case17_rounded_numeric_value_more_precise_rounding_still_matches():
    assert options_are_equivalent("$8\\sqrt{2}\\,\\text{cm}$", "$11,31\\,\\text{cm}$")


def test_case18_genuinely_different_numeric_values_not_equivalent():
    assert not options_are_equivalent("$8\\sqrt{2}\\,\\text{cm}$", "$12\\,\\text{cm}$")


def test_case19_commutative_multiplication_variable_then_sqrt():
    assert options_are_equivalent("$d=a\\sqrt{2}$", "$d=\\sqrt{2}a$")


def test_case20_commutative_multiplication_number_then_variable():
    assert options_are_equivalent("$d=2a$", "$d=a\\cdot2$")


def test_case21_commutative_multiplication_two_variables():
    assert options_are_equivalent("$d=ab$", "$d=ba$")


def test_case22_different_power_not_equivalent():
    assert not options_are_equivalent("$d=a^2$", "$d=2a$")


def test_case23_same_number_different_units_not_equivalent():
    assert not options_are_equivalent("$16\\,\\text{cm}$", "$16\\,\\text{cm}^2$")


def test_case24_equivalent_fractions_different_representation():
    assert options_are_equivalent("$\\frac{3}{8}$", "$\\frac{6}{16}$")


def test_case25_prose_options_never_falsely_claimed_equivalent():
    a = "$P = B + \\frac{O_B \\cdot h_a}{2}$"
    b = "$M = \\frac{O_B \\cdot h_a}{2}$"
    assert not options_are_equivalent(a, b)


# --- Kategorija 26-30: integritet tačne opcije / poredak parova ------------

def test_case26_canonicalize_expression_unknown_for_unsupported_construction():
    # Ugao/procenat/nejednakost — namjerno nepodržano, MORA biti None (nepoznato),
    # ne pogrešno "jednako" ili "različito".
    assert canonicalize_expression("30^\\circ") is None


def test_case27_find_equivalent_option_pairs_empty_when_all_distinct():
    opts = ["$4$", "$5$", "$6$", "$7$"]
    assert find_equivalent_option_pairs(opts) == []


def test_case28_find_equivalent_option_pairs_detects_non_adjacent_pair():
    # Dupla vrijednost na pozicijama 0 i 3 (ne susjedne) — mora biti uhvaćeno.
    opts = ["$5/8$", "$1/3$", "$1/4$", "$10/16$"]
    assert find_equivalent_option_pairs(opts) == [(0, 3)]
