"""Dvije PRODUKCIJSKE greske nadjene rucnim QA — regresija zauvijek.

Obje su bile nula-tolerancija: ucenik je izabrao matematicki tacan odgovor i
dobio ocjenu netacno.
"""
import pytest

from matbot import option_equivalence as oe
from matbot import solution_consistency as sc
from matbot.tutor import package_preflight as preflight


class _Option:
    def __init__(self, option_id, text):
        self.id = option_id
        self.text = text


class _Task:
    def __init__(self, text, options, correct_index, solution, expected):
        self.text = text
        self.options = [_Option(cid, t) for cid, t in zip("abcd", options)]
        self.correct_option_index = correct_index
        self.correct_option_id = self.options[correct_index].id
        self.solution = solution
        self.expected_answer = expected


# ===========================================================================
# REGRESIJA A — pogresno oznacen odgovor (9,50 naspram 11,50)
# ===========================================================================

SOLUTION = ("Ukupan iznos je $4,75 + 3,25 + 2,50 = 10,50$ KM. "
            "Kusur je $20,00 - 10,50 = 9,50$ KM.")


def test_a_marked_answer_absent_from_its_own_solution_is_rejected():
    """Objavljeno u produkciji: rjesenje racuna 9,50, a oznaceno je 11,50."""
    code, detail = sc.divergence("$11,50$ KM", SOLUTION)
    assert code == "solution_answer_divergence"
    assert "11.5" in detail


def test_a_correct_marked_answer_passes():
    assert sc.divergence("$9,50$ KM", SOLUTION) == ("", "")


def test_a_solution_ending_in_a_verification_is_not_a_false_alarm():
    """Rjesenje smije zavrsiti provjerom; odgovor je i dalje unutra."""
    solution = SOLUTION + " Provjera: $9,50 + 10,50 = 20,00$ KM."
    assert sc.divergence("$9,50$ KM", solution) == ("", "")


def test_a_divergence_reaches_the_shared_preflight_entry_point():
    """Nalaz mora ici istim kanalom kao svi ostali — inace nije popravljiv."""
    task = _Task("Koliki je kusur?", ["$11,50$ KM", "$9,50$ KM", "$8,00$ KM", "$12,00$ KM"],
                 0, SOLUTION, "$11,50$ KM")
    codes = [issue.code for issue in preflight.collect_package_issues(task)]
    assert preflight.SOLUTION_DIVERGENCE_CODE in codes


def test_a_reviewer_is_told_how_to_repair_it():
    from matbot.tutor import prompts

    rule = prompts._REVIEWER_PREFLIGHT_RULE
    assert "solution_answer_divergence" in rule
    assert "RE-DERIVE" in rule


@pytest.mark.parametrize("marked", ["$x$", r"$\frac{1}{2}$", r"$2\sqrt{3}$", "dva i po"])
def test_a_unprovable_marked_answers_are_skipped_not_guessed(marked):
    assert sc.divergence(marked, SOLUTION) == ("", "")


def test_a_solution_without_any_value_is_skipped():
    assert sc.divergence("$9,50$ KM", "Saberi cijene i oduzmi od placenog.") == ("", "")


def test_a_decimal_comparison_is_exact_not_binary_float():
    """0,1+0,2 mora biti 0,3 — binarni float bi ovdje lagao."""
    solution = "Zbir je $0,1 + 0,2 = 0,3$."
    assert sc.divergence("$0,30$", solution) == ("", "")
    assert sc.divergence("$0,31$", solution)[0] == "solution_answer_divergence"


# ===========================================================================
# REGRESIJA B — dvije matematicki tacne MCQ opcije
# ===========================================================================

SET_SQRT12 = r"$x \in \{-\sqrt{12}, \sqrt{12}\}$"
SET_2SQRT3 = r"$x \in \{-2\sqrt{3}, 2\sqrt{3}\}$"


def test_b_equivalent_radical_solution_sets_are_detected():
    """Objavljeno u produkciji za $x^2=12$: obje opcije su tacne."""
    assert oe.options_are_equivalent(SET_SQRT12, SET_2SQRT3)


def test_b_production_option_quartet_is_rejected_before_publication():
    options = [SET_2SQRT3, SET_SQRT12, r"$x \in \{-6, 6\}$", r"$x \in \{12\}$"]
    pairs = oe.find_equivalent_option_pairs(options)
    assert (0, 1) in pairs
    kinds = {kind for _, _, kind in oe.find_equivalent_option_pairs_with_types(options)}
    assert kinds


def test_b_reordered_set_is_the_same_answer():
    assert oe.options_are_equivalent(r"$\{\sqrt{12}, -\sqrt{12}\}$",
                                     r"$\{-\sqrt{12}, \sqrt{12}\}$")


def test_b_plus_minus_form_equals_the_set_form():
    assert oe.options_are_equivalent(r"$x = \pm\sqrt{12}$", r"$x = \pm 2\sqrt{3}$")


@pytest.mark.parametrize("left,right", [
    (r"$\frac{2}{4}$", r"$\frac{1}{2}$"),
    (r"$0,5$", r"$\frac{1}{2}$"),
    (r"$\sqrt{12}$", r"$2\sqrt{3}$"),
])
def test_b_previously_supported_equivalences_still_hold(left, right):
    assert oe.options_are_equivalent(left, right)


@pytest.mark.parametrize("left,right", [
    (r"$\{-2, 2\}$", r"$\{-3, 3\}$"),
    (r"$\{-\sqrt{12}, \sqrt{12}\}$", r"$\{-\sqrt{13}, \sqrt{13}\}$"),
    (r"$\{-2, 2\}$", r"$\{2\}$"),
    (r"$x \in \{-2, 2\}$", r"$y \in \{-2, 2\}$"),
])
def test_b_genuinely_different_answers_are_never_called_equivalent(left, right):
    """Nesigurnost nikad ne smije postati lazna tvrdnja o jednakosti."""
    assert not oe.options_are_equivalent(left, right)


def test_b_unparsable_set_is_unknown_not_equivalent():
    assert oe.finite_answer_set(r"$x \in \mathbb{R}$") is None
    assert not oe.options_are_equivalent(r"$x \in \mathbb{R}$", r"$x \in \mathbb{Q}$")
