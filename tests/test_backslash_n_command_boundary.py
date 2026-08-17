# -*- coding: utf-8 -*-
"""ŽIVI RELEASE GATE (`release_gate_migrated_deterministic`, lekcija o skupovima
N i N0): ispravna komanda `\\notin` je stizala do validatora kao `otin`.

    reviewer_final_mcq_integrity_rejection:
    decision=correct in_tutor_preflight=True unchanged=False
    unsafe_option_notation: option IDs c and d (prose_atom_in_math:otin)

`notin` JESTE u `MATHJAX_COMMAND_ALLOWLIST`. Kriv je bio popravljač doslovnog
`\\n` preloma: njegova zaštita je bila ZATVORENA LISTA nastavaka
(`eq|e|ot|u|abla`). `\\notin` nije ni u jednoj grani te liste (iza `ot` stoji
još jedno slovo), pa je `\\n` obrisan i ostalo je `otin`.

Ista rupa je bila i TIŠA nego ovaj slučaj: `$N \\ni 1$` je postajao `$N i 1$` —
jednoslovni ostatak ne pada ni na jednom detektoru, pa je izmijenjen sadržaj
mogao biti objavljen. Zato se granica sada pita JEDINU mjerodavnu instancu —
allowlistu komandi — umjesto ručno prepisane liste nastavaka.
"""
import pytest

from matbot import mathsafe


def _codes(text):
    return mathsafe.sanitize_and_validate_math_text_with_issues(text)[1]


# ---------------------------------------------------------------------------
# 1) ŽIVI SLUČAJ I SUSJEDNA SKUPOVNA NOTACIJA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "$0 \\in N$",
    "$0 \\notin N$",
    "$0 \\in N_0$",
    "$0 \\notin N_0$",
    "$1 \\notin N$",
    "$-3 \\notin N_0$",
    "$A \\subset B$",
    "$A \\subseteq B$",
    "$A \\supset B$",
    "$A \\supseteq B$",
    "$A \\cup B$",
    "$A \\cap B$",
    "$\\{1,2,3\\}$",
    "$\\mathbb{Z}$",
    "$\\emptyset$",
])
def test_school_set_notation_publishes_unchanged(text):
    cleaned, codes = mathsafe.sanitize_and_validate_math_text_with_issues(text)
    assert codes == [], (text, codes)
    assert cleaned.count("\\") == text.count("\\"), (text, cleaned)


@pytest.mark.parametrize("text", [
    "$x \\neq 0$",
    "$x \\ne 0$",
    "$\\nu$",
    "$\\nabla f$",
])
def test_other_n_commands_stay_protected(text):
    assert _codes(text) == [], text


def test_live_option_pair_of_the_failing_gate():
    """Obje opcije koje su pale u gateu sada prolaze zajedno."""
    for option in ("$0 \\in N_0$", "$0 \\notin N$"):
        cleaned, codes = mathsafe.sanitize_and_validate_math_text_with_issues(option)
        assert codes == [], (option, codes)
        assert "\\" in cleaned


# ---------------------------------------------------------------------------
# 2) DOSLOVAN PRELOM REDA I DALJE RADI
# ---------------------------------------------------------------------------

def test_literal_newline_outside_math_still_becomes_a_break():
    assert mathsafe.replace_literal_newline_escapes(
        "Prvi red.\\nDrugi red.") == "Prvi red.\nDrugi red."


def test_literal_newline_before_a_lowercase_word_still_becomes_a_break():
    assert mathsafe.replace_literal_newline_escapes(
        "Prvi red.\\nema više.") == "Prvi red.\nema više."


def test_bare_literal_newline_outside_math_still_becomes_a_break():
    assert mathsafe.replace_literal_newline_escapes("a\\n b") == "a\n b"


def test_bare_literal_newline_inside_math_is_still_removed():
    assert mathsafe.replace_literal_newline_escapes(
        "$d = \\n\\sqrt{128}$") == "$d = \\sqrt{128}$"


# ---------------------------------------------------------------------------
# 3) NEPOZNATA KOMANDA I DALJE PADA ZATVORENO (adversarijalno)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "$N \\ni 1$",            # nije u allowlisti — pada, NE briše se tiho
    "$0 \\nsubseteq N$",
    "$0 \\notinn N$",
    "$0 \\noti N$",
    "$x \\nema y$",
    "$0 \\ty N$",
])
def test_unknown_n_command_inside_math_fails_closed(text):
    cleaned, codes = mathsafe.sanitize_and_validate_math_text_with_issues(text)
    assert codes != [], (text, cleaned)


@pytest.mark.parametrize("text", [
    "$N \\ni 1$",
    "$x \\nema y$",
])
def test_unknown_n_command_is_never_silently_deleted(text):
    """Tiho brisanje `\\n` je mijenjalo SADRŽAJ, a ostatak je prolazio."""
    cleaned, _codes = mathsafe.sanitize_and_validate_math_text_with_issues(text)
    assert cleaned.count("\\") == text.count("\\"), (text, cleaned)


@pytest.mark.parametrize("text", [
    "$ treinta\\,\\text{cm}$",
    "$x = trideset$",
    "$\\frac{jedan}{dva}$",
])
def test_genuine_prose_atoms_in_math_still_fail(text):
    assert _codes(text) != [], text


# ---------------------------------------------------------------------------
# 4) GRANICA JE VEZANA ZA ALLOWLISTU, NE ZA PREPISANU LISTU NASTAVAKA
# ---------------------------------------------------------------------------

def test_every_allowlisted_n_command_survives_inside_math():
    n_commands = sorted(c for c in mathsafe.MATHJAX_COMMAND_ALLOWLIST
                        if c.startswith("n"))
    assert "notin" in n_commands
    for command in n_commands:
        text = "$a \\" + command + " b$"
        cleaned = mathsafe.replace_literal_newline_escapes(text)
        assert cleaned == text, command
