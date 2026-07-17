# -*- coding: utf-8 -*-
"""Deterministička provjera prostih linearnih jednačina (BUG1) i tolerancije
decimalnog separatora tačka/zarez (BUG2).

Kategorijski, ne primjeri: sve jednačine koje stanu u podržanu gramatiku i sve
ekvivalentne decimalne notacije rješavaju se egzaktno (fractions.Fraction).
"""
import pytest

from matbot.answer_checker import check_practice_answer, derive_expected


def _verdict(task, ans):
    cr = check_practice_answer(task, ans)
    if not cr.checkable or not cr.items:
        return "not_checkable"
    return cr.items[0].verdict


# --- BUG1: linearne jednačine — tačni odgovori ------------------------------------

@pytest.mark.parametrize("task,ans", [
    ("Riješi: 2/3 x = 8/9", "4/3"),
    ("Riješi: (2/3)x = 8/9", "4/3"),
    ("Riješi: \\frac{2}{3}x = \\frac{8}{9}", "4/3"),
    ("Riješi: 2/3 * x = 8/9", "4/3"),
    ("Riješi: x / 2 = 4", "8"),
    ("Riješi: x/2 - 3 = 1", "8"),
    ("Riješi: 3x = 12", "4"),
    ("Riješi: 3*x + 2 = 14", "4"),
    ("Odredi x: x/2 - 3 = 1", "x = 8"),
    ("Nađi nepoznati broj: 3x + 2 = 14", "4"),
    ("Riješi jednačinu: 2/3 x = 8/9", "x=4/3"),
    # ekvivalentni oblici učenikovog odgovora
    ("Riješi: (2/3)x = 8/9", "x = 4/3"),
    ("Riješi: (2/3)x = 8/9", "1 1/3"),        # mješoviti broj
    ("Riješi: (2/3)x = 8/9", "1.333333"),     # decimalna aproksimacija (tolerancija)
    ("Riješi: 3x = 12", " 4. "),               # razmaci + interpunkcija
])
def test_linear_equation_correct(task, ans):
    assert _verdict(task, ans) == "correct"


# --- BUG1: linearne jednačine — netačni odgovori ----------------------------------

@pytest.mark.parametrize("task,ans", [
    ("Riješi: (2/3)x = 8/9", "2/3"),
    ("Riješi: x/2 - 3 = 1", "x = 4"),
    ("Riješi: 3x + 2 = 14", "5"),
    ("Nađi nepoznati broj: 3x + 2 = 14", "5"),
])
def test_linear_equation_incorrect(task, ans):
    assert _verdict(task, ans) == "incorrect"


def test_linear_equation_solves_general_category():
    # nepovezan primjer koji nije u listi iznad — pokazuje da je kategorijski
    assert derive_expected("Riješi: 5x - 4 = 3x + 8").value == 6


# --- guard: NE smije lažno parsirati tekstualne/da-ne zadatke ---------------------

@pytest.mark.parametrize("task,ans", [
    ("Marko ima x kuglica, 3 je izgubio, ostalo mu 7. Koliko x?", "10"),
    ("Da li je 3 + 4 = 7? Odgovori da/ne.", "da"),
])
def test_non_equation_stays_unverified(task, ans):
    assert _verdict(task, ans) == "not_checkable"


# --- BUG2: decimalni separator tačka/zarez su ekvivalentni ------------------------

@pytest.mark.parametrize("ans", [
    "8,45", "8.45", "8,450", "8.450", "8.45.", " 8,45 ", " 8.45 ",
])
def test_decimal_dot_and_comma_equivalent_correct(ans):
    assert _verdict("4,56 + 3,89", ans) == "correct"


@pytest.mark.parametrize("ans", ["8.44", "8,46", "9.45"])
def test_decimal_wrong_value_incorrect(ans):
    assert _verdict("4,56 + 3,89", ans) == "incorrect"


def test_dot_decimal_not_misread_as_list_marker():
    # "8.45" ranije čitano kao "stavka 8 = 45"; sad je jedan decimalni broj
    cr = check_practice_answer("4,56 + 3,89", "8.45")
    assert cr.checkable and len(cr.items) == 1
    assert cr.items[0].given.raw == "8,45"


# --- Linearne nejednačine (kategorijski): znak se okreće, ekvivalentni oblici ------

@pytest.mark.parametrize("task,ans", [
    ("Riješi: x/2 - 3 < 1", "x < 8"),
    ("Riješi: x/2 - 3 < 1", "x<8"),
    ("Riješi: x/2 - 3 < 1", "8 > x"),
    ("Riješi: 2x + 3 > 7", "x > 2"),
    ("Riješi: 2x + 3 > 7", "2 < x"),
    ("Riješi: -2x < 4", "x > -2"),           # dijeljenje negativnim → znak se okreće
    ("Riješi: (2/3)x < 8/9", "x < 4/3"),
    ("Riješi: (2/3)x < 8/9", "4/3 > x"),
    ("Riješi: x/2 - 3 <= 1", "x <= 8"),
    ("Riješi: x/2 - 3 ≤ 1", "x ≤ 8"),
    ("Riješi: 2x + 3 >= 7", "x >= 2"),
    ("Riješi: 2x + 3 ≥ 7", "x ≥ 2"),
    ("Riješi nejednačinu: 2x + 3 > 7", "x > 2"),
])
def test_linear_inequality_correct(task, ans):
    assert _verdict(task, ans) == "correct"


@pytest.mark.parametrize("task,ans", [
    ("Riješi: x/2 - 3 < 1", "x > 8"),
    ("Riješi: -2x < 4", "x < -2"),
    ("Riješi: 2x + 3 > 7", "x < 2"),
    ("Riješi: x/2 - 3 < 1", "x <= 8"),        # strogo vs. nestrogo se razlikuje
    ("Riješi: x/2 - 3 <= 1", "x < 8"),
])
def test_linear_inequality_incorrect(task, ans):
    assert _verdict(task, ans) == "incorrect"


def test_inequality_summary_shows_operator_and_bound():
    cr = check_practice_answer("Riješi: x/2 - 3 < 1", "x < 8")
    item = check_practice_answer("Riješi: x/2 - 3 < 1", "x < 8").items[0]
    assert item.verdict == "correct"
    from matbot.answer_checker import summarize_result
    assert summarize_result(cr)["items"][0]["expected"] == "x < 8"


def test_inequality_bare_number_is_not_complete_solution():
    # broj na granici nije rješenje, a jedan primjer svakako nije cijeli skup rješenja
    assert _verdict("Riješi: x/2 - 3 < 1", "8") == "incorrect"
    assert _verdict("Riješi: x > 3", "4") == "incomplete"
