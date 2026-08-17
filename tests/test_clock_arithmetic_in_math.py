# -*- coding: utf-8 -*-
"""ŽIVI PRODUKCIJSKI NALAZ: sat u matematičkom segmentu čitan kao dijeljenje.

Učenik je riješio zadatak s polaskom u `9:15` i trajanjem $1\\frac{3}{5}$ sata;
objavljen rezultat `10:51` bio je TAČAN. Na „Objasni mi postupak korak po
korak.“ produkcija je vratila generičku sigurnu poruku, uz log:

    quick_turn request_id=61f0acd12a4d category=invalid_output
    detail=numeric_equality_mismatch: nevaljan izraz (dijeljenje nulom)

Uzrok: `mathcheck` svaku dvotačku unutar `$…$` bezuslovno prevodi u dijeljenje
(bosansko školsko `60:15`), pa je korak `$9:15 + 1:00 = 10:15$` postao
`9/15 + 1/00`, dakle dijeljenje nulom, i cio TAČAN postupak je pao zatvoreno.

Ispravka NIJE „svako cifra:cifra je vrijeme“. Sat se priznaje samo kad je
DOKAZAN: svi članovi lanca su oblika `H:MM` s minutama `00`–`59`, spojeni su
isključivo sa `+`/`-`, ima ih bar tri (dakle bar jedna operacija i rezultat) i
obje strane jednakosti daju ISTI ukupan broj minuta. Sve ostalo ostaje
školsko dijeljenje i provjerava se kao i dosad.
"""
import pytest

from matbot import mathcheck


# ---------------------------------------------------------------------------
# 1) ŽIVI SLUČAJ — dokazana satnica prolazi
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("segment", [
    "$9:15 + 1:00 = 10:15$",
    "$10:15 + 0:36 = 10:51$",
    "$9:15 + 1:36 = 10:51$",
    "$10:51 - 1:36 = 9:15$",
    "$9:15 + 0:45 + 0:51 = 10:51$",
])
def test_proven_clock_arithmetic_is_accepted(segment):
    assert mathcheck.find_numeric_inconsistencies(segment) == []


def test_the_whole_live_explanation_passes():
    text = (
        "Polazak je u $9:15$, a vožnja traje $1\\frac{3}{5}$ sata, "
        "što je $1$ sat i $36$ minuta.\n"
        "Prvo dodamo pun sat: $9:15 + 1:00 = 10:15$.\n"
        "Zatim dodamo minute: $10:15 + 0:36 = 10:51$.\n"
        "Dakle, dolazak je u $10:51$."
    )
    assert mathcheck.find_numeric_inconsistencies(text) == []


# ---------------------------------------------------------------------------
# 2) ŠKOLSKO DIJELJENJE OSTAJE PROVJERENO (adversarijalno)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("segment", [
    "$60:15=4$",
    "$72:9=8$",
    "$(24+6):5=6$",
    "$3,5:0,5=7$",
    "$3:4=6:8$",
    "$12:\\;1+2=3$",
])
def test_correct_school_division_still_passes(segment):
    assert mathcheck.find_numeric_inconsistencies(segment) == []


@pytest.mark.parametrize("segment", [
    "$60:15=5$",          # pogrešan količnik
    "$72:9=7$",           # pogrešan količnik
    "$60:0=0$",           # dijeljenje nulom
    "$12:\\;1+2=4$",      # pogrešan zbir cifara
    "$8:40=3$",           # `H:MM` oblik, ali NIJE lanac sa satima → dijeljenje
])
def test_wrong_school_division_still_fails(segment):
    assert mathcheck.find_numeric_inconsistencies(segment) != []


# ---------------------------------------------------------------------------
# 3) LAŽNA SATNICA SE NE AMNESTIRA (adversarijalno)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("segment", [
    "$9:15 + 1:00 = 10:20$",     # tačan oblik, netačan zbir
    "$10:15 + 0:36 = 10:41$",
    "$9:15 + 1:36 = 11:51$",
    "$10:51 - 1:36 = 9:25$",
])
def test_wrong_clock_arithmetic_is_still_rejected(segment):
    assert mathcheck.find_numeric_inconsistencies(segment) != []


@pytest.mark.parametrize("segment", [
    "$9:15 + 1:60 = 10:75$",     # minute izvan 00-59 nisu vrijeme
    "$9:5 + 1:0 = 10:5$",        # jednocifrene minute nisu H:MM
    "$115:15 + 1:00 = 116:15$",  # trocifren sat nije H:MM
])
def test_clock_shaped_but_unproven_forms_stay_division(segment):
    """Ne mogu se dokazati kao sat → ostaju dijeljenje i padaju kao i dosad."""
    assert mathcheck.find_numeric_inconsistencies(segment) != []


def test_single_colon_term_is_never_treated_as_clock():
    """Za sat treba BAR jedna operacija i rezultat; `$12:30$` sam za sebe ostaje
    dijeljenje, kako Rezultat mod već pretpostavlja (tests/test_clock_time.py)."""
    assert mathcheck._verified_clock_arithmetic("12:30") is False
    assert mathcheck._verified_clock_arithmetic("12:30 = 0,4") is False


def test_approximation_is_never_read_as_clock():
    assert mathcheck.find_numeric_inconsistencies(
        "$9:15 + 1:36 \\approx 10:51$") != []
