# -*- coding: utf-8 -*-
"""Zapis stepena: `$90^\\circ$`, nikad `$90^0$` (matbot/geometrycheck.py).

Živi nalaz — produkcijski smoke odmah poslije izdanja `04baada`, Explain,
7. razred, pitanje „Šta je hipotenuza?“. Objavljeno je:

    „Nalazi se nasuprot pravom uglu, odnosno uglu od $90^0$.“

`90^0` je 90 na nulti stepen, dakle $1$ — objavljena tvrdnja je matematički
netačna. Nijedan sloj je nije odbio: mathsafe vidi samo dozvoljene komande,
mathcheck nema jednakost koju bi opovrgao (samostalna mjera ništa ne
protivrječi), a geometrycheck dotad nije poznavao zapis stepena.

Ključna tenzija koju ovi testovi čuvaju: `2^0` i `x^0` su ISPRAVNA matematika
(stepen nula) i moraju ostati dozvoljeni — zabranjuje se samo mjera ugla
zapisana kao stepen nula.
"""
import pytest

from matbot import geometrycheck as gc

DEG = "^\\circ"
CODE = gc.ANGLE_DEGREE_SUPERSCRIPT_ZERO


def issues(text, scope="", **kwargs):
    return gc.find_geometry_issues(text, scope, **kwargs)


def flagged(text, scope="", **kwargs):
    return CODE in issues(text, scope, **kwargs)


# ---------------------------------------------------------------------------
# MORA UHVATITI — mjera ugla zapisana kao stepen nula
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Ugao iznosi $90^0$.",
    "Pravi ugao je $90^0$.",
    "Pravi ugao ima $90^0$.",
    "Ugao ima mjeru $45^0$.",
    "Ugao ABC je $45^0$.",
    "Ugao $\\alpha$ je $60^0$.",
    "Mjeru ugla dobijamo kao $60^0$.",
    "Zbir uglova trougla je $180^0$.",
    "Puni ugao ima $360^0$.",
])
def test_degree_zero_in_angle_context_is_rejected(text):
    assert flagged(text)


def test_live_finding_from_production_smoke():
    """Tačan tekst koji je 2026-08-22 stigao do učenika."""
    text = ("Hipotenuza je najduža stranica pravouglog trougla. Nalazi se "
            "nasuprot pravom uglu, odnosno uglu od $90^0$.")
    assert flagged(text)


@pytest.mark.parametrize("text", [
    "Ugao je jednak $30^{0}$.",       # vitičaste zagrade
    "Ugao iznosi $90 ^ 0$.",          # razmaci oko ^
    "Zbir uglova je $180^{0}$.",
])
def test_brace_and_spacing_variants_are_rejected(text):
    assert flagged(text)


@pytest.mark.parametrize("text", [
    "Ugao iznosi $90^o$.",            # slovo „o" je ista greška
    "Ugao iznosi $90^{o}$.",
])
def test_superscript_letter_o_is_the_same_error(text):
    assert flagged(text)


def test_reverse_word_order_is_rejected():
    assert flagged("$90^0$ je pravi ugao.")
    assert flagged("$120^0$ je tup ugao.")


def test_mixed_notation_is_rejected_even_with_a_correct_sibling():
    assert flagged("Ugao je $90^0$, a drugi $45%s$." % DEG)


def test_repeated_bad_measure_reports_the_code_once():
    assert issues("Zbir uglova je $90^0 + 90^0$.").count(CODE) == 1


# ---------------------------------------------------------------------------
# MORA OSTATI DOZVOLJENO — kanonski zapis stepena ugla
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Ugao iznosi $90%s$." % DEG,
    "Pravi ugao je $90%s$." % DEG,
    "Ugao ima mjeru $45%s$." % DEG,
    "Zbir uglova trougla je $180%s$." % DEG,
    "Ugao je $35%s 20'$." % DEG,      # stepeni i minute
])
def test_canonical_circ_notation_is_allowed(text):
    assert not flagged(text)


# ---------------------------------------------------------------------------
# MORA OSTATI DOZVOLJENO — stepen nula je ispravna matematika
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "$2^0 = 1$",
    "$5^0 = 1$",
    "$10^{0} = 1$",
    "$x^0 = 1$",
    "$a^0 = 1$",
    "Izračunaj $2^0$.",
    "Za $x \\neq 0$, vrijedi $x^0 = 1$.",
    "Broj $90^0$ jednak je 1.",
    "Stepenovanje: $2^0 = 1$ za svaki broj različit od nule.",
])
def test_legitimate_exponent_zero_is_allowed(text):
    assert not flagged(text)


def test_variable_base_can_never_be_an_angle_measure():
    """`x^0`/`a^0` su bezuslovno dozvoljeni — osnova mora biti cijeli broj."""
    assert not flagged("Ugao je $x^0$.")
    assert not flagged("Ugao ima mjeru $a^0$.")


# ---------------------------------------------------------------------------
# LAŽNI POZITIVI — geometrijska riječ drugdje u odgovoru NE smije okinuti
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    # Zadata kontrolna rečenica: „uglovima" je tema, `2^0` je stepen nula.
    "U zadatku o uglovima izračunaj i vrijednost izraza $2^0$.",
    "Uglovi se mjere uglomjerom. Vrijednost izraza $3^0$ je 1.",
    "U lekciji o uglovima vrijedi $x^0=1$.",
    "Ugao je prav. Izračunaj $2^0$.",
    "Uglomjer koristimo za mjerenje. Izračunaj $10^0$.",
    "Zbir uglova trougla je $180%s$, a $2^0=1$." % DEG,
])
def test_angle_word_elsewhere_does_not_flag_unrelated_exponent(text):
    assert not flagged(text)


@pytest.mark.parametrize("text", [
    "Trougao ima $2^0$ osobinu.",
    "Pravougaonik: izračunaj $10^0$.",
    "Četverougao i $5^0$ nisu povezani.",
])
def test_angle_substring_inside_other_nouns_never_counts(text):
    """„trougao"/„pravougaonik"/„četverougao" sadrže „ugao" bez granice riječi."""
    assert not flagged(text)


# ---------------------------------------------------------------------------
# UGRAĐIVANJE U POSTOJEĆI MODUL
# ---------------------------------------------------------------------------

def test_code_is_registered():
    assert CODE in gc.ALL_ISSUE_CODES


def test_runs_without_scope():
    """Lekcije o uglovima rutiraju scope "" — provjera mora biti PRIJE kapije."""
    assert flagged("Ugao iznosi $90^0$.", scope="")
    assert flagged("Ugao iznosi $90^0$.", scope="plane")


def test_distractor_option_is_never_checked():
    """Namjerno pogrešna opcija ne smije srušiti cio zadatak."""
    assert not flagged("Ugao iznosi $90^0$.", role=gc.ROLE_DISTRACTOR)


def test_intentional_violation_policy_is_respected():
    assert not flagged("Ugao iznosi $90^0$.", policy=gc.POLICY_ALLOW_INTENTIONAL)


def test_detector_never_mutates_text_and_makes_no_model_call():
    """Modul po ugovoru samo prijavljuje — nikad ne prepravlja `^0` u `^\\circ`."""
    text = "Ugao iznosi $90^0$."
    before = text
    assert flagged(text)
    assert text == before


def test_empty_text_is_safe():
    assert issues("") == []


# ---------------------------------------------------------------------------
# PROMPT — pravilo je globalno, u jednom kanonskom bloku
# ---------------------------------------------------------------------------

def test_shared_notation_rules_forbid_superscript_zero_degrees():
    from matbot import rules
    block = rules._MATH_NOTATION_RULES
    assert "90^\\circ" in block
    assert "$90^0$" in block


def test_degree_rule_is_not_duplicated_per_mode():
    """Jedno kanonsko mjesto — pravilo ne smije biti prepisano po modovima."""
    from matbot import rules
    assert rules._MATH_NOTATION_RULES.count("NIKAD ne piši $90^0$") == 1
