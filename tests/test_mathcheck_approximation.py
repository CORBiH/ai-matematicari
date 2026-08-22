# -*- coding: utf-8 -*-
"""Relacija `\\approx` nije `=` (matbot/mathcheck.py).

ŽIVI NALAZ D1 — velika evaluacija Explaina (143 turna, razredi 6–9): cijela
tema zaokruživanja bila je NEDOSTUPNA. Ciljana sonda: 8/8 zahtjeva za
zaokruživanjem palo je na `numeric_equality_mismatch`, a sve kontrole
(π, egzaktna aritmetika) su prolazile. Pogođene lekcije:

    6-05-007  Zaokruživanje decimalnih brojeva
    7-03-021  Procjena, zaokruživanje i približan račun
    8-01-007  Savršeni kvadrati i procjena
    8-01-011  Približne vrijednosti kvadratnog korijena
    8-01-017  Naučni zapis broja

Uzrok NIJE bio „approx se čita kao =“. `_decimal_places` je uzimao MAKSIMUM
decimala obje strane, pa je tolerancija dolazila iz NEZAOKRUŽENOG broja
(`4,738 \\approx 4,74` → 3 decimale → 0,00055 < 0,002), a grana `if places:`
vraćala je prije nego se `relation` uopšte pogleda.

Ovi testovi čuvaju obje strane ravnoteže: ispravno zaokruživanje mora proći,
a `\\approx` NE SMIJE postati propusnica za netačnu matematiku.
"""
import pytest

from matbot import mathcheck

AP = "\\approx"


def rejected(segment):
    return bool(mathcheck.check_segment(segment))


# ---------------------------------------------------------------------------
# ISPRAVNO ZAOKRUŽIVANJE — mora proći
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("segment", [
    "4,738" + AP + "4,74",          # na dvije decimale
    "4,738" + AP + "4,7",           # na jednu decimalu
    "4,738" + AP + "5",             # na cijeli broj
    "15,96" + AP + "16",
    "12,349" + AP + "12,35",
    "7,486" + AP + "7,5",           # tačan slučaj iz evaluacije (g7-10)
    "0,96" + AP + "1,0",
    "5,555" + AP + "5,56",
    "19,7" + AP + "20",
    "49,7" + AP + "50",
    "2,444" + AP + "2,44",
])
def test_correct_rounding_is_accepted(segment):
    assert not rejected(segment)


def test_precision_comes_from_the_rounded_side_not_the_source():
    """Srž ispravke: mjera je pola jedinice posljednjeg mjesta REZULTATA."""
    assert not rejected("4,738" + AP + "4,74")     # 3 decimale lijevo, 2 desno
    assert not rejected("4,738" + AP + "4,7")      # 3 decimale lijevo, 1 desno


# ---------------------------------------------------------------------------
# NETAČNO ZAOKRUŽIVANJE — mora pasti (`\approx` nije propusnica)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("segment", [
    "4,738" + AP + "4,75",          # tačno je 4,74
    "4,738" + AP + "4,73",          # zaokruženo naniže
    "15,96" + AP + "15",
    "4,738" + AP + "9",
    "4,738" + AP + "5,9",
    "15,96" + AP + "10",
    "100" + AP + "3",
    "2" + AP + "200",
    "\\pi" + AP + "8",
    "\\sqrt{2}" + AP + "1,5",       # tačno je 1,4
])
def test_wrong_approximation_is_still_rejected(segment):
    assert rejected(segment)


# ---------------------------------------------------------------------------
# PROCJENA (operandi zaokruženi prije računa) — 7-03-021
# ---------------------------------------------------------------------------

def test_estimation_expression_is_accepted():
    assert not rejected("3,98+2,04" + AP + "4+2")


def test_estimation_that_is_simply_wrong_is_rejected():
    assert rejected("3,98+2,04" + AP + "10")
    assert rejected("3,98+2,04" + AP + "7")


# ---------------------------------------------------------------------------
# NAUČNI ZAPIS — 8-01-017. Skala se izvodi iz izraza, bez tvrdo kodirane 10^n.
# ---------------------------------------------------------------------------

def test_scientific_notation_coefficient_rounding_is_accepted():
    assert not rejected("4,732\\cdot10^4" + AP + "4,7\\cdot10^4")


@pytest.mark.parametrize("segment", [
    "4,732\\cdot10^4" + AP + "4,8\\cdot10^4",
    "4,732\\cdot10^4" + AP + "9,9\\cdot10^4",
])
def test_scientific_notation_wrong_coefficient_is_rejected(segment):
    assert rejected(segment)


def test_scale_is_derived_not_hardcoded():
    """Ista logika mora vrijediti za bilo koju potenciju, ne samo 10^4."""
    assert not rejected("2,349\\cdot10^6" + AP + "2,3\\cdot10^6")
    assert rejected("2,349\\cdot10^6" + AP + "2,9\\cdot10^6")


# ---------------------------------------------------------------------------
# π NE SMIJE REGRESIRATI
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("segment", [
    "\\pi" + AP + "3,14",
    "\\pi" + AP + "3,1",
    "9\\pi" + AP + "28,26",         # tačno uz π=3,14
    "2\\pi" + AP + "6,28",
    "\\sqrt{2}" + AP + "1,41",
])
def test_pi_and_radical_approximations_still_pass(segment):
    assert not rejected(segment)


def test_founding_case_of_the_module_still_fails():
    """`24\\sqrt{3}\\approx83,14` — model je zaboravio podijeliti s 2."""
    assert rejected("24\\sqrt{3}" + AP + "83,14")


# ---------------------------------------------------------------------------
# EGZAKTNA JEDNAKOST OSTAJE STROGA — ovo je granica koja se ne smije pomjeriti
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("segment", [
    "4,738=4,74",
    "2+2=5",
    "3,98+2,04=6",
    "7/2=3",
    "15,96=16",
    "0,3+0,25=0,54",
])
def test_exact_equality_remains_strict(segment):
    assert rejected(segment)


@pytest.mark.parametrize("segment", [
    "2+2=4",
    "3,98+2,04=6,02",
    "\\frac{3}{4}=0,75",
])
def test_true_exact_equalities_still_pass(segment):
    assert not rejected(segment)


def test_same_pair_differs_by_relation_only():
    """Isti brojevi: `=` pada, `\\approx` prolazi. To je cijela poenta."""
    assert rejected("15,96=16")
    assert not rejected("15,96" + AP + "16")


# ---------------------------------------------------------------------------
# ZAPIS I OBLIK
# ---------------------------------------------------------------------------

def test_unicode_approx_sign_behaves_the_same():
    assert not rejected("4,738≈4,74")
    assert rejected("4,738≈4,75")


def test_decimal_point_form_is_handled_like_the_comma_form():
    assert not rejected("4.738" + AP + "4.74")
    assert rejected("4.738" + AP + "4.75")


def test_spacing_does_not_change_the_verdict():
    assert not rejected("4,738 " + AP + " 4,74")
    assert rejected("4,738 " + AP + " 4,75")


def test_negative_numbers_round_too():
    assert not rejected("-4,738" + AP + "-4,74")
    assert rejected("-4,738" + AP + "-4,75")


def test_chain_with_rounding_at_the_end():
    """Tipičan školski lanac: tačan račun pa zaokružen rezultat."""
    assert not rejected("12,5\\cdot0,4=5,0" + AP + "5")


def test_approximation_makes_no_model_call(monkeypatch):
    """mathcheck je čisto deterministički — nijedan poziv modela."""
    import matbot.mathcheck as mc
    assert not hasattr(mc, "llm")
    assert not rejected("4,738" + AP + "4,74")


# ---------------------------------------------------------------------------
# GRANICA: „zaokruženo na cijeli broj" vrijedi samo ako desna strana JESTE
# cijeli broj. Bez ovoga bi apsolutnih pola jedinice amnestiralo sve sitno.
# ---------------------------------------------------------------------------

def test_places_zero_requires_an_integer_right_hand_side():
    """Regresija uhvaćena postojećim testom sata pri izradi ove ispravke."""
    assert mathcheck.find_numeric_inconsistencies(
        "$9:15 + 1:36 " + AP + " 10:51$") != []


def test_small_magnitude_approximation_is_not_amnestied():
    """0,63 ≈ 0,20 nije zaokruživanje ni na koje mjesto."""
    assert rejected("3:5 + 1:36" + AP + "10:51")
