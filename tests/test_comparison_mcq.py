"""Faza 4G, Workstream F — uski orakl UPOREĐIVANJA za MCQ pakete (7-03-006).

Lekcija „Upoređivanje racionalnih brojeva“ nema semantički ugovor porodice i
nijedan deterministički validator dosad nije provjeravao SAMU relaciju: zadatak
„Koji znak stoji između $\\frac{2}{3}$ i $\\frac{3}{4}$?“ s pogrešno označenim
znakom prolazio je sve serverske kapije (relaciju je držao samo recenzent).

GRANICE (isti princip kao ostali uski orakli — nedokazivo znači ćutanje):
  • znak-oblik: opcije su isključivo simboli <, >, = (po jedan), a pitanje ima
    TAČNO DVA izračunljiva broja i zatvorenu proznu direktivu poređenja;
  • superlativ-oblik: proza traži najveći/najmanji, sve opcije su izračunljive
    i međusobno različite vrijednosti;
  • svaki oblik s „koliko“ („Za koliko je veći…“) se NIKAD ne angažuje —
    to je pitanje razlike, ne relacije.
"""
from matbot import mcq_integrity

SIGN_OPTIONS = ("$<$", "$>$", "$=$")


def evaluate(question, options=SIGN_OPTIONS):
    return mcq_integrity.evaluate_comparison_mcq(question, options)


# ---------------------------------------------------------------------------
# 1) ZNAK-OBLIK
# ---------------------------------------------------------------------------

def test_unlike_denominators_less_than():
    result = evaluate("Koji znak stoji između $\\frac{2}{3}$ i $\\frac{3}{4}$?")
    assert result.applicable and result.valid
    assert result.relation == "<"
    assert result.correct_index == 0


def test_negative_versus_positive():
    result = evaluate("Koji znak stoji između $-\\frac{1}{2}$ i $\\frac{1}{3}$?")
    assert result.valid and result.relation == "<"


def test_two_negatives():
    result = evaluate("Uporedi brojeve: $-\\frac{3}{4}$ i $-\\frac{1}{4}$.")
    assert result.valid and result.relation == "<"


def test_equivalent_fractions_are_equal():
    result = evaluate("Koji znak stoji između $\\frac{2}{4}$ i $\\frac{1}{2}$?")
    assert result.valid and result.relation == "="
    assert result.correct_index == 2


def test_decimal_against_fraction():
    result = evaluate("Koji znak stoji između $0,4$ i $\\frac{1}{2}$?")
    assert result.valid and result.relation == "<"


def test_greater_than_direction():
    result = evaluate("Koji znak stoji između $\\frac{5}{6}$ i $\\frac{2}{3}$?")
    assert result.valid and result.relation == ">"
    assert result.correct_index == 1


# ---------------------------------------------------------------------------
# 2) SUPERLATIV-OBLIK
# ---------------------------------------------------------------------------

def test_largest_value_is_found():
    result = evaluate("Koji od ponuđenih brojeva je najveći?",
                      ("$\\frac{3}{4}$", "$\\frac{2}{3}$", "$0,5$", "$\\frac{7}{12}$"))
    assert result.applicable and result.valid
    assert result.correct_index == 0


def test_smallest_value_is_found():
    result = evaluate("Koji od ponuđenih brojeva je najmanji?",
                      ("$\\frac{3}{4}$", "$\\frac{2}{3}$", "$-\\frac{1}{2}$", "$0,6$"))
    assert result.valid and result.correct_index == 2


# ---------------------------------------------------------------------------
# 3) GRANICE ANGAŽOVANJA
# ---------------------------------------------------------------------------

def test_za_koliko_is_a_difference_question_and_never_engages():
    result = evaluate("Za koliko je $\\frac{3}{4}$ veći od $\\frac{1}{2}$?",
                      ("$\\frac{1}{4}$", "$\\frac{1}{2}$", "$\\frac{3}{8}$", "$1$"))
    assert not result.applicable


def test_three_numbers_disable_the_sign_oracle():
    result = evaluate(
        "Koji znak stoji između $\\frac{1}{2}$ i $\\frac{2}{3}$ i $\\frac{3}{4}$?")
    assert not result.applicable


def test_prose_options_disable_the_oracle():
    result = evaluate("Koji od ponuđenih brojeva je najveći?",
                      ("$\\frac{3}{4}$", "ne zna se", "$0,5$", "$\\frac{7}{12}$"))
    assert not result.applicable


def test_duplicate_extreme_values_fail_closed():
    result = evaluate("Koji od ponuđenih brojeva je najveći?",
                      ("$\\frac{3}{4}$", "$\\frac{6}{8}$", "$0,5$", "$\\frac{1}{3}$"))
    assert result.applicable and not result.valid
    assert result.reason_code == "multiple_correct_options"


def test_missing_sign_option_fails_closed():
    result = evaluate("Koji znak stoji između $\\frac{2}{3}$ i $\\frac{3}{4}$?",
                      ("$>$", "$=$", "$\\ge$"))
    assert not result.applicable


# ---------------------------------------------------------------------------
# 4) OBJAVA
# ---------------------------------------------------------------------------

def test_publication_failure_flags_a_wrong_sign_mark():
    failure, _ = mcq_integrity.publication_failure(
        "Koji znak stoji između $\\frac{2}{3}$ i $\\frac{3}{4}$?",
        SIGN_OPTIONS, 1, SIGN_OPTIONS[1])
    assert failure == "marked_option_math_mismatch"


def test_publication_failure_accepts_the_correct_sign():
    failure, _ = mcq_integrity.publication_failure(
        "Koji znak stoji između $\\frac{2}{3}$ i $\\frac{3}{4}$?",
        SIGN_OPTIONS, 0, SIGN_OPTIONS[0])
    assert failure == ""


# ---------------------------------------------------------------------------
# 5) SUPERLATIV KAO IME FUNKCIJE (NZD/NZS) — orakl se NE angažuje
# ---------------------------------------------------------------------------
# „NAJVEĆI zajednički djelilac“ imenuje funkciju, ne ekstrem među opcijama:
# tačan NZD gotovo nikad nije najveća opcija, pa bi angažovan superlativni
# orakl dokazano POGREŠNO odbijao ispravne deterministic pakete (kapacitetna
# ekspanzija, porodica common_divisors_multiples).

def test_gcd_question_never_engages_the_superlative_oracle():
    result = evaluate("Koliki je najveći zajednički djelilac brojeva $25$ i $35$?",
                      ("$5$", "$10$", "$15$", "$175$"))
    assert not result.applicable


def test_lcm_question_never_engages_the_superlative_oracle():
    result = evaluate("Koliki je najmanji zajednički sadržilac brojeva $9$ i $6$?",
                      ("$18$", "$54$", "$36$", "$9$"))
    assert not result.applicable


def test_publication_accepts_a_correct_gcd_package():
    failure, _ = mcq_integrity.publication_failure(
        "Koliki je najveći zajednički djelilac brojeva $25$ i $35$?",
        ("$5$", "$10$", "$15$", "$175$"), 0, "$5$")
    assert failure == ""


def test_decade_unit_question_never_engages_the_superlative_oracle():
    # Batch #2: „najveća dekadska jedinica kojom se broj dijeli“ imenuje
    # uslovljen objekat, ne ekstrem među opcijama.
    result = evaluate("Koja je najveća dekadska jedinica kojom se broj $340$ "
                      "može podijeliti bez ostatka?",
                      ("$10$", "$100$", "$1000$", "$10000$"))
    assert not result.applicable
