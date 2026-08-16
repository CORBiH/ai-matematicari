# -*- coding: utf-8 -*-
"""Akcenat nad znakom množenja se kanonizuje, ostali akcenti se ne diraju.

ŽIVI NALAZ (Quick, turn Q04): model je napisao `$2\\bar{\\text{·}}5+5=15$`.
Račun je bio tačan, ali se `\\bar{\\text{·}}` iscrtava kao tačka s crtom iznad —
notacija koja ne postoji.

Kanonizacija je dopuštena jer je NAMJERA DOKAZANA SADRŽAJEM: argument je
doslovno znak množenja. „Crta nad znakom množenja“ nema legitimno čitanje.
"""
import pytest

from matbot.mathsafe import (repair_accented_multiplication_dot,
                             sanitize_and_validate_math_text)


def _clean(text):
    return sanitize_and_validate_math_text(text)[0]


def test_historical_quick_q04_is_canonicalised():
    text = "Provjera: $2\\bar{\\text{·}}5+5=15$, a ne $13$."
    assert _clean(text) == "Provjera: $2\\cdot5+5=15$, a ne $13$."


@pytest.mark.parametrize("bad,good", [
    ("$2\\bar{\\text{·}}5$", "$2\\cdot5$"),
    ("$2\\overline{\\text{·}}5$", "$2\\cdot5$"),
    ("$2\\bar{·}5$", "$2\\cdot5$"),
    ("$2\\overline{·}5$", "$2\\cdot5$"),
    ("$2\\bar{\\cdot}5$", "$2\\cdot5$"),
    ("$2\\bar{\\mathrm{·}}5$", "$2\\cdot5$"),
    ("$3\\bar{\\text{·}}4=12$", "$3\\cdot4=12$"),
])
def test_accented_multiplication_dot_variants(bad, good):
    assert _clean(bad) == good


# ---------------------------------------------------------------------------
# LEGITIMNA NOTACIJA — nikad se ne dira
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "$\\bar{x}$",
    "$\\bar{v}$",
    "$\\bar{2}$",
    "$\\overline{AB}$",
    "$\\overline{ABC}$",
    "$0,\\overline{3}$",
    "$\\overline{0,25}$",
    "$1,\\overline{27}$",
    "$\\overline{AB} \\parallel \\overline{CD}$",
    "$\\overline{AB} \\perp \\overline{CD}$",
    "$\\bar{a}+\\bar{b}$",
    "$\\overline{x}=\\frac{a+b}{2}$",
])
def test_legitimate_accents_are_preserved(text):
    assert _clean(text) == text, text
    assert repair_accented_multiplication_dot(text) == text


def test_repair_is_idempotent():
    once = repair_accented_multiplication_dot("$2\\bar{\\text{·}}5$")
    assert repair_accented_multiplication_dot(once) == once


def test_plain_cdot_is_untouched():
    assert _clean("$2\\cdot5=10$") == "$2\\cdot5=10$"


def test_result_stays_numerically_checkable():
    """Poslije kanonizacije mathcheck vidi ispravan lanac."""
    from matbot.mathcheck import find_numeric_inconsistencies
    cleaned = _clean("$2\\bar{\\text{·}}5+5=15$")
    assert find_numeric_inconsistencies(cleaned) == []
    wrong = _clean("$2\\bar{\\text{·}}5+5=16$")
    assert find_numeric_inconsistencies(wrong)
