# -*- coding: utf-8 -*-
r"""DVA ŽIVA RELEASE-GATE BLOKATORA, provjerena na NIVOU PAKETA.

Jedinični testovi dokazuju uzrok (tests/test_backslash_n_command_boundary.py i
tests/test_uppercase_label_option_equivalence.py); ovaj fajl dokazuje da isti
paketi sada prolaze `package_preflight.collect_package_issues` — dakle tačno
ono što je u gateu obaralo turn PRIJE objave, i po drugi put nakon recenzenta.

    release_gate_migrated_deterministic — lekcija o skupovima N i N0
        unsafe_option_notation: option IDs c and d (prose_atom_in_math:otin)

    release_gate_grade7_rotating — lekcija o podudarnosti trouglova (SSU)
        semantically_duplicate_options: option IDs b and d (symbolic_commutative)

Uz svaki popravljen paket stoji njegov ADVERSARIJALNI blizanac: stvarno
neispravan paket iste klase mora i dalje pasti.
"""
import pytest

from matbot.tutor import package_preflight
from tests.conftest import make_task_payload


def _codes(task):
    return [issue.code for issue in package_preflight.collect_package_issues(task)]


def _mcq(text, options, marked=0, solution=None):
    return make_task_payload(
        text=text, options=options, correct_option_index=marked,
        expected=options[marked], solution=solution or options[marked])


# ---------------------------------------------------------------------------
# BLOKATOR 1 — skupovi N i N0
# ---------------------------------------------------------------------------

SET_STEM = ("Neka je $N=\\{1,2,3,\\dots\\}$, a $N_0=\\{0,1,2,3,\\dots\\}$. "
            "Koja tvrdnja je tačna?")
SET_OPTIONS = ("$0 \\in N$", "$-1 \\in N_0$", "$0 \\notin N$", "$1 \\notin N_0$")


def test_set_membership_package_publishes():
    assert _codes(_mcq(SET_STEM, SET_OPTIONS, marked=2)) == []


def test_every_notin_option_survives_preflight():
    for index in (2, 3):
        assert "unsafe_option_notation" not in _codes(
            _mcq(SET_STEM, SET_OPTIONS, marked=index))


@pytest.mark.parametrize("bad", ["$0 \\ni N$", "$0 \\ty N$", "$0 nula N$"])
def test_unknown_notation_in_a_set_option_still_blocks(bad):
    options = ("$0 \\in N$", bad, "$0 \\notin N$", "$1 \\notin N_0$")
    assert "unsafe_option_notation" in _codes(_mcq(SET_STEM, options, marked=2))


# ---------------------------------------------------------------------------
# BLOKATOR 2 — kriteriji podudarnosti trouglova
# ---------------------------------------------------------------------------

SSU_STEM = ("Za trouglove $ABC$ i $DEF$ dati su podaci $AB=DE$, $AC=DF$ i "
            "$\\angle B=\\angle E$. Koji kriterij podudarnosti odgovara ovim "
            "podacima?")
SSU_OPTIONS = ("$SSS$", "$SUS$", "$USU$", "$SSU$")


def test_congruence_criteria_package_publishes():
    assert _codes(_mcq(SSU_STEM, SSU_OPTIONS, marked=3)) == []


def test_no_duplicate_is_claimed_between_sus_and_ssu():
    codes = _codes(_mcq(SSU_STEM, SSU_OPTIONS, marked=3))
    assert "semantically_duplicate_options" not in codes
    assert "duplicate_option_text" not in codes


def test_a_real_repeated_criterion_still_blocks():
    options = ("$SSS$", "$SUS$", "$SUS$", "$SSU$")
    assert "duplicate_option_text" in _codes(_mcq(SSU_STEM, options, marked=3))


def test_a_real_semantic_duplicate_still_blocks():
    stem = "Koliko je $\\frac{1}{2}$ od $8$?"
    options = ("$4$", "$\\frac{8}{2}$", "$6$", "$2$")
    assert "semantically_duplicate_options" in _codes(_mcq(stem, options, marked=0))


# ---------------------------------------------------------------------------
# SATNICA U RJEŠENJU (blokator 3, isti put kroz numeričku provjeru)
# ---------------------------------------------------------------------------

CLOCK_STEM = ("Voz polazi u $9:15$ i putuje $1$ sat i $36$ minuta. "
              "U koliko sati stiže?")
CLOCK_OPTIONS = ("$10:51$", "$10:41$", "$11:51$", "$9:51$")
CLOCK_SOLUTION = ("Prvo dodamo pun sat: $9:15 + 1:00 = 10:15$. "
                  "Zatim dodamo minute: $10:15 + 0:36 = 10:51$.")


def test_clock_solution_package_publishes():
    assert _codes(_mcq(CLOCK_STEM, CLOCK_OPTIONS, solution=CLOCK_SOLUTION)) == []


def test_a_wrong_clock_solution_still_blocks():
    wrong = ("Prvo dodamo pun sat: $9:15 + 1:00 = 10:15$. "
             "Zatim dodamo minute: $10:15 + 0:36 = 10:41$.")
    assert "numeric_inconsistency" in _codes(
        _mcq(CLOCK_STEM, CLOCK_OPTIONS, solution=wrong))


def test_a_wrong_division_in_a_solution_still_blocks():
    wrong = "Podijelimo: $60:15=5$, dakle rezultat je $5$."
    assert "numeric_inconsistency" in _codes(
        _mcq("Koliko je $60:15$?", ("$4$", "$5$", "$6$", "$3$"), solution=wrong))
