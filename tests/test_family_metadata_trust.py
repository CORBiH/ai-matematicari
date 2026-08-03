"""Metadata false-rejection fixes (matbot/task_family_validation.py).

Živi nalaz #1: pet ISPRAVNIH `find_expansion_factor` zadataka odbijeno jer je
model deklarisao task_form="direct_calculation" dok je ugovor dozvoljavao samo
2 od 8 mogućih vrijednosti (provjera svih porodica: SVAKA ima ≤2 dozvoljene
task_form vrijednosti — sistemski propust).

Živi nalaz #2 (ista klasa propusta, druga runda testiranja): ispravan
`verify_ordered_pair` zadatak odbijen jer je model deklarisao
student_must_find="ordered_pair" dok je ugovor dozvoljavao samo "statement".

Rješenje: i task_form i student_must_find su SAMO INFORMATIVNI — nijedan više
ne odbija zadatak samostalno. Autoritativni ostaju: task_family (strogo,
identitet) i answer_kind (strogo, ali samo kad je OBJEKTIVNO prepoznatljivo iz
stvarnog tačnog odgovora — ne protiv statične liste po porodici, isti razlog
zašto su prethodna dva propusta uopšte bila moguća).
"""
import pytest

from matbot.task_family_validation import (
    CONTRACTS, FamilyContractError, detected_answer_kind, validate_task_family,
)


def check(family, question, options, correct_index=0, expected="", declared=None):
    try:
        validate_task_family(family, question=question, option_texts=list(options),
                             correct_option_index=correct_index,
                             expected_answer=expected, declared=declared)
        return None
    except FamilyContractError as e:
        return str(e)


# ---------------------------------------------------------------------------
# 1. Exact live find_expansion_factor example now passes
# ---------------------------------------------------------------------------

LIVE_FACTOR_QUESTION = "Razlomak $\\frac{3}{7}$ proširen je na $\\frac{12}{28}$. Kojim brojem je proširen?"
LIVE_FACTOR_OPTIONS = ["$2$", "$3$", "$4$", "$6$"]
LIVE_FACTOR_DECLARED = {
    "task_family": "find_expansion_factor",
    "student_must_find": "expansion_factor",
    "answer_kind": "integer",
    "task_form": "direct_calculation",
}


def test_live_false_rejection_case_now_passes():
    error = check("find_expansion_factor", LIVE_FACTOR_QUESTION, LIVE_FACTOR_OPTIONS,
                  correct_index=2, declared=LIVE_FACTOR_DECLARED)
    assert error is None, error


@pytest.mark.parametrize("declared_task_form", [
    "recognition", "missing_value", "direct_calculation", "method_selection",
    "interpretation", "word_problem", "construction_step", "error_detection",
])
def test_any_declared_task_form_is_accepted_for_a_structurally_valid_task(declared_task_form):
    """task_form je informativna oznaka — NIJEDNA od 8 mogućih vrijednosti ne
    smije sama odbiti zadatak koji je već strukturno ispravan."""
    declared = dict(LIVE_FACTOR_DECLARED, task_form=declared_task_form)
    error = check("find_expansion_factor", LIVE_FACTOR_QUESTION, LIVE_FACTOR_OPTIONS,
                  correct_index=2, declared=declared)
    assert error is None, f"task_form={declared_task_form} nije smio odbiti: {error}"


# ---------------------------------------------------------------------------
# 3. Invalid "Proširi razlomak..." example still fails for find_expansion_factor
# ---------------------------------------------------------------------------

def test_invalid_expand_directive_still_fails_for_find_expansion_factor():
    bad_question = "Proširi razlomak $\\frac{2}{5}$ tako da nazivnik bude $20$."
    bad_options = ["$\\frac{8}{20}$", "$\\frac{2}{20}$", "$\\frac{6}{20}$", "$\\frac{4}{10}$"]
    error = check("find_expansion_factor", bad_question, bad_options)
    assert error is not None
    assert "ima_direktivu_prosirivanja" in error


def test_invalid_case_still_fails_even_with_generous_metadata():
    """Model koji lažno tvrdi ispravnu poziciju i dalje ne smije proći —
    metapodaci nikad ne mogu spasiti strukturno pogrešan zadatak."""
    bad_question = "Proširi razlomak $\\frac{2}{5}$ tako da nazivnik bude $20$."
    bad_options = ["$\\frac{8}{20}$", "$\\frac{2}{20}$", "$\\frac{6}{20}$", "$\\frac{4}{10}$"]
    declared = {"task_family": "find_expansion_factor", "student_must_find": "expansion_factor",
               "answer_kind": "integer", "task_form": "direct_calculation"}
    error = check("find_expansion_factor", bad_question, bad_options, declared=declared)
    assert error is not None


# ---------------------------------------------------------------------------
# 4. False task_family declaration still fails (unchanged — strict)
# ---------------------------------------------------------------------------

def test_false_task_family_declaration_still_fails():
    error = check("expand_to_given_denominator",
                  "Proširi razlomak $\\frac{3}{8}$ tako da nazivnik bude $24$.",
                  ["$\\frac{9}{24}$", "$\\frac{3}{24}$", "$\\frac{9}{8}$", "$\\frac{6}{24}$"],
                  declared={"task_family": "recognize_equivalent_fraction"})
    assert error is not None
    assert "deklarisao drugu porodicu" in error


# ---------------------------------------------------------------------------
# 5. Wrong answer_kind still fails when OBJECTIVELY contradicted
# ---------------------------------------------------------------------------

def test_answer_kind_integer_declared_but_correct_option_is_fraction_fails():
    """Deklarisano „integer“ dok je tačna opcija razlomak — stvarna
    kontradikcija, mora pasti čak i kad je struktura inače u redu."""
    error = check("fraction_operation", "Izračunaj $\\frac{2}{7} + \\frac{3}{7}$.",
                  ["$\\frac{5}{7}$", "$\\frac{5}{14}$", "$\\frac{6}{7}$", "$\\frac{1}{7}$"],
                  declared={"answer_kind": "integer"})
    assert error is not None
    assert "suprotnosti sa stvarnim" in error


def test_answer_kind_matching_actual_type_passes():
    error = check("fraction_operation", "Izračunaj $\\frac{2}{7} + \\frac{3}{7}$.",
                  ["$\\frac{5}{7}$", "$\\frac{5}{14}$", "$\\frac{6}{7}$", "$\\frac{1}{7}$"],
                  declared={"answer_kind": "fraction"})
    assert error is None


def test_answer_kind_skipped_when_type_not_objectively_detectable():
    """Proza (metoda, tvrdnja...) se ne može mehanički klasifikovati — provjera
    se preskače umjesto da lažno odbije."""
    error = check("choose_method", "Koja metoda je najprikladnija za sistem $y=2x$ i $x+y=9$?",
                  ["Metoda zamjene, jer je $y$ već izražen.", "Metoda suprotnih koeficijenata.",
                   "Grafička metoda.", "Nijedna metoda ne odgovara."],
                  declared={"answer_kind": "short_text"})
    assert error is None


# ---------------------------------------------------------------------------
# 6. Wrong student_must_find still fails when it contradicts the visible task
# ---------------------------------------------------------------------------

def test_student_must_find_mismatch_no_longer_independently_rejects():
    """Živi nalaz #2: student_must_find je opisna oznaka bez objektivnog
    kriterija (baš kao task_form) — neslaganje s ugovorom više NIKAD samo po
    sebi ne smije odbiti strukturno ispravan zadatak."""
    error = check("find_expansion_factor", LIVE_FACTOR_QUESTION, LIVE_FACTOR_OPTIONS,
                  correct_index=2, declared={"student_must_find": "ordered_pair"})
    assert error is None, error


def test_student_must_find_matching_declaration_passes():
    error = check("find_expansion_factor", LIVE_FACTOR_QUESTION, LIVE_FACTOR_OPTIONS,
                  correct_index=2, declared={"student_must_find": "expansion_factor"})
    assert error is None


# ---------------------------------------------------------------------------
# 7. Canonical server metadata never leaks to the browser
# ---------------------------------------------------------------------------

def test_canonical_task_form_is_a_plain_string_not_exposed_by_default():
    """canonical_task_form nije poslano ničemu osim FamilyContract-a — ovaj
    test dokumentuje da je čisto interna vrijednost (string), a stvarno
    odsustvo curenja u browser dokazuje test_practice_family_enforcement.py."""
    for family_id, contract in CONTRACTS.items():
        assert isinstance(contract.canonical_task_form, str), family_id
        if contract.task_form:
            assert contract.canonical_task_form, family_id


# ---------------------------------------------------------------------------
# 8. Sve porodice ostaju registrovane i pokrivene ugovorom
# ---------------------------------------------------------------------------

def test_all_families_still_have_contracts():
    from matbot.task_families import FAMILY_DESCRIPTIONS
    # 36 porodica: pet „fraction_*“ porodica opslužuje SAMO nemigrirane
    # lekcije kroz legacy granicu (matbot/legacy/practice_routing.py).
    # Brišu se tek kad njihovi potrošači dobiju ugovor — vidi
    # tests/test_legacy_routing_parity.py.
    assert len(FAMILY_DESCRIPTIONS) == 36
    assert len(CONTRACTS) == 36
    assert set(FAMILY_DESCRIPTIONS) == set(CONTRACTS)


# ---------------------------------------------------------------------------
# detected_answer_kind helper
# ---------------------------------------------------------------------------

def test_detected_answer_kind_fraction():
    assert detected_answer_kind("$\\frac{9}{24}$") == "fraction"
    assert detected_answer_kind("5/8") == "fraction"


def test_detected_answer_kind_integer():
    assert detected_answer_kind("4") == "integer"
    assert detected_answer_kind("$-3$") == "integer"


def test_detected_answer_kind_decimal():
    assert detected_answer_kind("2,5") == "decimal"


def test_detected_answer_kind_ordered_pair():
    assert detected_answer_kind("$(2,3)$") == "ordered_pair"


def test_detected_answer_kind_none_for_prose():
    assert detected_answer_kind("Metoda zamjene.") is None
    assert detected_answer_kind("$V=BH$") is None


# ---------------------------------------------------------------------------
# Full 31-family audit: task_form is never risk-bearing anymore
# ---------------------------------------------------------------------------

def test_no_family_rejects_solely_on_declared_task_form():
    """Sistemska regresija svih porodica: nijedna od 8 mogućih task_form
    vrijednosti smije sama izazvati odbijanje kad je struktura ispravna."""
    from tests.conftest import _FAMILY_TASK_TEMPLATES, make_task_for_family

    all_task_forms = ("direct_calculation", "missing_value", "recognition",
                      "error_detection", "method_selection", "interpretation",
                      "word_problem", "construction_step")
    for family_id in _FAMILY_TASK_TEMPLATES:
        task = make_task_for_family(family_id)
        for form in all_task_forms:
            error = check(family_id, task.text, [o.text for o in task.options],
                          correct_index=task.correct_option_index,
                          declared={"task_family": family_id, "task_form": form})
            assert error is None, f"{family_id} lažno odbijen za task_form={form}: {error}"
