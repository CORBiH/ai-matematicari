# -*- coding: utf-8 -*-
"""mathcheck ne smije izmisliti jednakost preko granice segmenta.

ŽIVI NALAZ (Explain, lekcija o procentima): tačno objašnjenje je palo zatvoreno s

    numeric_equality_mismatch: '300\\ \\text{KM}' (300) != '\\frac{15}{100}\\cdot300\\ \\text{KM}' (45)

Model je napisao `$15\\%$ od $300\\ \\text{KM} = \\ldots = 45\\ \\text{KM}$`:
kvalifikator „15% od“ ostao je u PROZI, pa segment počinje NEPOTPUNOM lijevom
stranom. Red koji učenik vidi glasi „15% od 300 KM = … = 45 KM“ i tačan je.
"""
import pytest

from matbot.mathcheck import find_numeric_inconsistencies as issues

HISTORICAL = ("$15\\%$ od $300\\ \\text{KM} = \\frac{15}{100}\\cdot300"
              "\\ \\text{KM} = 45\\ \\text{KM}$")


def test_historical_percentage_false_positive_is_gone():
    assert issues(HISTORICAL) == []


# ---------------------------------------------------------------------------
# VALIDNA OBJAŠNJENJA PROCENTA — sva prolaze
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    HISTORICAL,
    "$15\\%$ od $300 = \\frac{15}{100}\\cdot300 = 45$",
    "$15\\% \\text{ od } 300\\ \\text{KM} = \\frac{15}{100}\\cdot300\\ \\text{KM} = 45\\ \\text{KM}$",
    "$15\\%$ od $300$ KM je $45$ KM.",
    "$300 \\cdot \\frac{15}{100}=45$.",
    "Računamo $\\frac{15}{100}\\cdot300$, pa dobijemo $45$.",
    "$300$ KM, a $15\\%$ od tog iznosa je $45$ KM.",
    "$12,5\\%$ od $80$ je $10$.",
    "$10\\%$ od $200$ je $20$, pa je rezultat $220$.",
    "$25\\%$ od $500$ je $125$. Zatim $500-125=375$.",
    "$20\\%$ od $50 = \\frac{20}{100}\\cdot50 = 10$",
])
def test_valid_percentage_explanations_publish(text):
    assert issues(text) == [], text


# ---------------------------------------------------------------------------
# STVARNE NEJEDNAKOSTI — i dalje padaju
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "$300 = 45$",
    "$\\frac{15}{100}\\cdot300 = 300$",
    "$2+2=5$",
    "$300\\ \\text{KM} = 45\\ \\text{KM}$",
    "$\\frac{3}{4}+\\frac{2}{5} = \\frac{5}{9}$",
    "$15\\%$ od $300 = \\frac{15}{100}\\cdot300 = 50$",   # ostatak NE potvrđuje
])
def test_genuine_false_equalities_still_rejected(text):
    assert issues(text), text


@pytest.mark.parametrize("text", [
    "$2+2$ je $5 = 6$",            # ostatak ima samo jedan član — ne može posvjedočiti
    "$7$ pa $3 = 4$",
])
def test_short_chain_after_bridge_is_still_checked(text):
    assert issues(text), text


def test_long_prose_bridge_does_not_excuse_a_mismatch():
    text = "$15\\%$ zatim nakon dugog objasnjenja slijedi $300 = 45$"
    assert issues(text)


def test_sentence_end_in_bridge_does_not_excuse_a_mismatch():
    assert issues("$15\\%$. Dakle $300 = 45$")


def test_first_segment_in_text_is_always_checked():
    """Bez prethodnog segmenta nema šta da nastavlja prozu."""
    assert issues("$300\\ \\text{KM} = 45\\ \\text{KM}$")


# ---------------------------------------------------------------------------
# OBIČNE ARITMETIČKE PROVJERE OSTAJU
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,ok", [
    ("$7+5+9+6=27$", True),
    ("$7+5+9+6=28$", False),
    ("$\\frac{2}{7}+\\frac{3}{7}=\\frac{5}{7}$", True),
    ("$\\frac{2}{7}+\\frac{3}{7}=\\frac{5}{14}$", False),
    ("$\\sqrt{225}=15$", True),
    ("$\\sqrt{225}=25$", False),
    ("$1\\,\\text{cm} = 10\\,\\text{mm}$", True),      # pretvaranje jedinica
    ("$18,75 \\cdot 3,6 = 67,5$", True),
])
def test_ordinary_arithmetic_checks_unchanged(text, ok):
    assert (issues(text) == []) is ok, (text, issues(text))
