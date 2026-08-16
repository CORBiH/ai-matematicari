# -*- coding: utf-8 -*-
"""SUPERLATIV NAD USLOVOM NIJE SUPERLATIV NAD OPCIJAMA.

ŽIVI NALAZ (Faza F, forenzika odbijenih slotova kontrolnog, lekcija 7-02-019):

    „Odredi najveći cijeli broj $x$ koji zadovoljava nejednačinu $-9<x+4\\leq6$.“
    opcije: 2 / -13 / 10 / -5      (tačno je 2, jer je $-13<x\\le2$)

`_evaluate_superlative_mcq` je uzimao najveću PONUĐENU vrijednost i proglašavao
$10$ tačnim. Presuda je time bila OBRNUTA u oba smjera:

    označeno $2$  (TAČNO)   → `marked_option_math_mismatch`  → valjan test propao
    označeno $10$ (NETAČNO) → ''                             → server POTVRDIO ključ

Drugi smjer je teži: server je vlastitim oraklom ovjeravao netačan odgovor.

Popravka je SUŽENJE DOMETA, ne slabljenje: kad matematika zadatka sadrži
relaciju, traženi ekstrem je ekstrem SKUPA RJEŠENJA, koji ovaj orakl ne zna
izvesti — pa ćuti. Ćutanje znači „bez presude“, nikad „odobreno“.
"""
import pytest

from matbot import mcq_integrity

CONSTRAINED = "Odredi najveći cijeli broj $x$ koji zadovoljava nejednačinu $-9<x+4\\leq6$."
CONSTRAINED_OPTIONS = ("2", "-13", "10", "-5")


# ---------------------------------------------------------------------------
# 1) ŽIVI SLUČAJ — ORAKL VIŠE NE PRESUĐUJE NI U JEDNOM SMJERU
# ---------------------------------------------------------------------------

def test_constrained_superlative_no_longer_engages():
    result = mcq_integrity.evaluate_comparison_mcq(CONSTRAINED, CONSTRAINED_OPTIONS)
    assert not result.applicable


def test_correct_marked_option_is_no_longer_rejected():
    """Tačno označeno $2$ je ranije padalo na `marked_option_math_mismatch`."""
    failure, _ = mcq_integrity.publication_failure(CONSTRAINED, CONSTRAINED_OPTIONS, 0, "2")
    assert failure == ""


def test_wrong_marked_option_is_no_longer_certified():
    """KLJUČNI SMJER: netačno označeno $10$ je ranije dobijalo ''. I dalje dobija
    '' (ovaj orakl ćuti), ali ga sada NIJEDAN orakl ne proglašava tačnim —
    presuda više ne postoji, umjesto da postoji i bude pogrešna."""
    result = mcq_integrity.evaluate_comparison_mcq(CONSTRAINED, CONSTRAINED_OPTIONS)
    assert not result.applicable
    assert result.correct_indices == ()


@pytest.mark.parametrize("question", [
    "Odredi najveći cijeli broj $x$ za koji vrijedi $x<7$.",
    "Koji je najmanji cijeli broj $x$ takav da je $2x\\geq10$?",
    "Odredi najveće rješenje nejednačine $3x-1\\leq14$.",
    "Za koje je najveće $n$ ispunjeno $n+3=9$?",
])
def test_any_relation_in_the_stem_silences_the_superlative_oracle(question):
    result = mcq_integrity.evaluate_comparison_mcq(question, ("2", "5", "7", "10"))
    assert not result.applicable


# ---------------------------------------------------------------------------
# 2) NEUSLOVLJEN SUPERLATIV MORA OSTATI NETAKNUT
# ---------------------------------------------------------------------------

def test_plain_largest_question_still_resolves():
    result = mcq_integrity.evaluate_comparison_mcq(
        "Koji od ponuđenih brojeva je najveći?",
        ("$\\frac{3}{4}$", "$\\frac{2}{3}$", "$0,5$", "$\\frac{7}{12}$"))
    assert result.applicable and result.valid
    assert result.correct_index == 0


def test_plain_smallest_question_still_resolves():
    result = mcq_integrity.evaluate_comparison_mcq(
        "Koji od ponuđenih brojeva je najmanji?",
        ("$\\frac{3}{4}$", "$\\frac{2}{3}$", "$-\\frac{1}{2}$", "$0,6$"))
    assert result.applicable and result.valid
    assert result.correct_index == 2


def test_numbers_listed_in_the_stem_without_a_relation_still_resolve():
    """Brojevi u zadatku NISU uslov — uslov je tek relacija među njima."""
    result = mcq_integrity.evaluate_comparison_mcq(
        "Koji je najveći među brojevima $12$, $7$, $19$ i $3$?",
        ("$12$", "$7$", "$19$", "$3$"))
    assert result.applicable and result.valid
    assert result.correct_index == 2


def test_duplicate_extremes_still_fail_closed():
    """Suženje ne smije progutati postojeće zatvoreno padanje."""
    result = mcq_integrity.evaluate_comparison_mcq(
        "Koji od ponuđenih brojeva je najveći?",
        ("$\\frac{3}{4}$", "$\\frac{6}{8}$", "$0,5$", "$\\frac{1}{3}$"))
    assert result.applicable and not result.valid
    assert result.reason_code == "multiple_correct_options"


def test_gcd_question_still_never_engages():
    result = mcq_integrity.evaluate_comparison_mcq(
        "Koliki je najveći zajednički djelilac brojeva $25$ i $35$?",
        ("$5$", "$10$", "$15$", "$175$"))
    assert not result.applicable


# ---------------------------------------------------------------------------
# 3) ZNAKOVNI ORAKL SE NE SMIJE POMJERITI
# ---------------------------------------------------------------------------
# `_evaluate_sign_mcq` se pita PRIJE superlativnog i namjerno radi nad
# zadatkom koji SADRŽI dvije vrijednosti; suženje iznad ga ne dodiruje.

def test_sign_oracle_is_unaffected_by_the_narrowing():
    result = mcq_integrity.evaluate_comparison_mcq(
        "Koji znak stoji između $\\frac{2}{3}$ i $\\frac{3}{4}$?",
        ("$<$", "$>$", "$=$"))
    assert result.applicable and result.valid
    assert result.correct_index == 0
