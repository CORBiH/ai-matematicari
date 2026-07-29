"""Ugovori porodica zadataka (matbot/task_family_validation.py).

Testovi koriste STVARNE primjere iz živog smoke testa (4 realna poziva,
lekcija „Proširivanje razlomaka“) gdje je server ispravno rotirao porodice, a
model ipak vratio zadatak druge porodice. Bez ovog sloja te greške su prolazile
sve do učenika.
"""
import pytest

from matbot import task_families as tf
from matbot.task_family_validation import (
    CONTRACTS, FamilyContractError, contract_for, is_fraction_option,
    is_integer_option, is_prose_option, missing_contracts, prompt_block,
    validate_task_family,
)


def check(family, question, options, correct_index=0, expected="", declared=None):
    """Pozovi validator; vrati None ako je prošlo, inače poruku greške."""
    try:
        validate_task_family(family, question=question, option_texts=list(options),
                             correct_option_index=correct_index,
                             expected_answer=expected, declared=declared)
        return None
    except FamilyContractError as e:
        return str(e)


# ---------------------------------------------------------------------------
# Pokrivenost kataloga
# ---------------------------------------------------------------------------

def test_every_catalog_family_has_a_contract():
    assert missing_contracts() == [], f"Porodice bez ugovora: {missing_contracts()}"


def test_no_contract_exists_for_unknown_family():
    assert contract_for("izmisljena_porodica") is None


def test_every_contract_declares_prompt_guidance():
    for family_id, contract in CONTRACTS.items():
        assert contract.objective, family_id
        assert contract.prompt_must_be_unknown, family_id
        assert contract.prompt_options_must_be, family_id
        assert contract.prompt_positive_example, family_id


def test_prompt_block_contains_positive_and_forbidden_example():
    block = prompt_block("find_expansion_factor")
    assert "ISPRAVAN PRIMJER" in block
    assert "ZABRANJEN PRIMJER" in block
    assert "NIKAD razlomci" in block


def test_prompt_block_empty_for_unknown_family():
    assert prompt_block("nepostojeca") == ""


def test_unknown_family_is_not_blocked():
    assert check("nepostojeca_porodica", "Bilo šta.", ["1", "2", "3", "4"]) is None


def test_empty_family_skips_validation():
    assert check("", "Bilo šta.", ["1", "2", "3", "4"]) is None


# ---------------------------------------------------------------------------
# ŽIVI NALAZI — tačni primjeri iz smoke testa
# ---------------------------------------------------------------------------

LIVE_EXPAND_QUESTION = "Proširi razlomak $\\frac{3}{8}$ tako da nazivnik bude $24$. Koja opcija je tačna?"
LIVE_EXPAND_OPTIONS = ["$\\frac{9}{24}$", "$\\frac{3}{24}$", "$\\frac{9}{8}$", "$\\frac{6}{24}$"]

LIVE_WRONG_FACTOR_QUESTION = "Proširi razlomak $\\frac{2}{5}$ tako da nazivnik bude $20$. Koja opcija je tačna?"
LIVE_WRONG_FACTOR_OPTIONS = ["$\\frac{8}{20}$", "$\\frac{2}{20}$", "$\\frac{6}{20}$", "$\\frac{4}{10}$"]

LIVE_WRONG_NUMERATOR_QUESTION = "Proširi razlomak $\\frac{3}{7}$ tako da nazivnik bude $35$. Koja opcija je tačna?"
LIVE_WRONG_NUMERATOR_OPTIONS = ["$\\frac{15}{35}$", "$\\frac{9}{35}$", "$\\frac{6}{35}$", "$\\frac{12}{35}$"]

LIVE_EQUIVALENT_QUESTION = "Koji od navedenih razlomaka je jednak razlomku $\\frac{4}{9}$?"
LIVE_EQUIVALENT_OPTIONS = ["$\\frac{16}{36}$", "$\\frac{4}{36}$", "$\\frac{12}{36}$", "$\\frac{20}{36}$"]


def test_live_call_1_expand_is_accepted():
    """Poziv 1 uživo: dodijeljeno expand_to_given_denominator, generisano
    ispravno — mora proći."""
    assert check("expand_to_given_denominator", LIVE_EXPAND_QUESTION, LIVE_EXPAND_OPTIONS) is None


def test_live_call_2_wrong_family_is_rejected():
    """Poziv 2 uživo: dodijeljeno find_expansion_factor, a generisan zadatak
    proširivanja — MORA pasti."""
    error = check("find_expansion_factor", LIVE_WRONG_FACTOR_QUESTION, LIVE_WRONG_FACTOR_OPTIONS)
    assert error is not None
    assert "find_expansion_factor" in error


def test_live_call_3_wrong_family_is_rejected():
    """Poziv 3 uživo: dodijeljeno find_missing_numerator, a generisan zadatak
    proširivanja — MORA pasti."""
    error = check("find_missing_numerator", LIVE_WRONG_NUMERATOR_QUESTION, LIVE_WRONG_NUMERATOR_OPTIONS)
    assert error is not None
    assert "find_missing_numerator" in error


def test_live_call_4_equivalent_is_accepted():
    assert check("recognize_equivalent_fraction", LIVE_EQUIVALENT_QUESTION, LIVE_EQUIVALENT_OPTIONS) is None


# ---------------------------------------------------------------------------
# Ispravni oblici traženih porodica (iz specifikacije)
# ---------------------------------------------------------------------------

def test_correct_find_expansion_factor_is_accepted():
    question = "Razlomak $\\frac{2}{5}$ proširen je na $\\frac{8}{20}$. Kojim brojem je proširen?"
    assert check("find_expansion_factor", question, ["4", "2", "3", "5"]) is None


def test_correct_find_missing_numerator_is_accepted():
    question = "Dopuni jednakost: $\\frac{3}{7} = \\frac{?}{35}$."
    assert check("find_missing_numerator", question, ["15", "10", "12", "21"]) is None


def test_expand_family_rejects_a_factor_question():
    question = "Razlomak $\\frac{2}{5}$ proširen je na $\\frac{8}{20}$. Kojim brojem je proširen?"
    assert check("expand_to_given_denominator", question, ["4", "2", "3", "5"]) is not None


def test_expand_family_rejects_fraction_options_missing_denominator_word():
    question = "Koji razlomak je jednak $\\frac{3}{8}$?"
    assert check("expand_to_given_denominator", question, LIVE_EXPAND_OPTIONS) is not None


def test_factor_family_rejects_fraction_options_even_with_factor_question():
    question = "Razlomak $\\frac{2}{5}$ proširen je na $\\frac{8}{20}$. Kojim brojem je proširen?"
    assert check("find_expansion_factor", question, LIVE_EXPAND_OPTIONS) is not None


def test_missing_numerator_rejects_fraction_options():
    question = "Dopuni jednakost: $\\frac{3}{7} = \\frac{?}{35}$."
    assert check("find_missing_numerator", question, LIVE_EXPAND_OPTIONS) is not None


def test_equivalent_family_rejects_expand_directive():
    question = "Proširi razlomak $\\frac{4}{9}$ na nazivnik $36$."
    assert check("recognize_equivalent_fraction", question, LIVE_EQUIVALENT_OPTIONS) is not None


def test_question_mark_alone_is_not_a_missing_slot():
    """„Koja opcija je tačna?“ sadrži upitnik, ali NIJE prazno mjesto u
    jednakosti — inače bi svaki zadatak lažno izgledao kao dopunjavanje."""
    assert check("expand_to_given_denominator", LIVE_EXPAND_QUESTION, LIVE_EXPAND_OPTIONS) is None


def test_passive_participle_prosiren_is_not_an_expand_directive():
    """„proširen je na“ je uvod u pitanje o faktoru, ne direktiva „proširi“."""
    question = "Razlomak $\\frac{2}{5}$ proširen je na $\\frac{8}{20}$. Kojim brojem je proširen?"
    assert check("find_expansion_factor", question, ["4", "2", "3", "5"]) is None


# ---------------------------------------------------------------------------
# Unakrsna provjera deklarisanih metapodataka
# ---------------------------------------------------------------------------

def test_declared_family_must_match_assigned_family():
    error = check("expand_to_given_denominator", LIVE_EXPAND_QUESTION, LIVE_EXPAND_OPTIONS,
                  declared={"task_family": "find_expansion_factor"})
    assert error is not None
    assert "deklarisao drugu porodicu" in error


def test_matching_declared_family_passes():
    assert check("expand_to_given_denominator", LIVE_EXPAND_QUESTION, LIVE_EXPAND_OPTIONS,
                 declared={"task_family": "expand_to_given_denominator"}) is None


def test_declared_answer_kind_must_be_allowed_for_family():
    error = check("expand_to_given_denominator", LIVE_EXPAND_QUESTION, LIVE_EXPAND_OPTIONS,
                  declared={"answer_kind": "method"})
    assert error is not None


def test_declared_student_must_find_is_informational_only():
    """student_must_find je (kao i task_form) opisna oznaka bez objektivnog
    kriterija — neslaganje s ugovorom više NIKAD samo po sebi ne odbija
    strukturno ispravan zadatak (vidi test_family_metadata_trust.py za puni
    živi nalaz i regresiju)."""
    error = check("find_expansion_factor",
                  "Razlomak $\\frac{2}{5}$ proširen je na $\\frac{8}{20}$. Kojim brojem je proširen?",
                  ["4", "2", "3", "5"],
                  declared={"student_must_find": "ordered_pair"})
    assert error is None, error


def test_missing_declared_metadata_still_runs_structural_check():
    """Metapodaci su opcionalni; njihov izostanak NE smije isključiti
    strukturnu provjeru — inače bi model mogao zaobići ugovor ćutanjem."""
    error = check("find_expansion_factor", LIVE_WRONG_FACTOR_QUESTION,
                  LIVE_WRONG_FACTOR_OPTIONS, declared=None)
    assert error is not None


def test_declared_metadata_alone_cannot_rescue_a_wrong_task():
    """Model tvrdi ispravnu porodicu i ispravne enume, ali je VIDLJIVI zadatak
    pogrešan — strukturna provjera i dalje odbija."""
    error = check("find_expansion_factor", LIVE_WRONG_FACTOR_QUESTION, LIVE_WRONG_FACTOR_OPTIONS,
                  declared={"task_family": "find_expansion_factor",
                            "student_must_find": "expansion_factor",
                            "answer_kind": "integer",
                            "task_form": "recognition"})
    assert error is not None


# ---------------------------------------------------------------------------
# Ostale grupe porodica
# ---------------------------------------------------------------------------

def test_verify_ordered_pair_requires_a_pair_and_a_check_question():
    good = "Da li je par $(2,3)$ rješenje sistema $x+y=5$ i $x-y=-1$?"
    assert check("verify_ordered_pair", good,
                 ["Jeste, zadovoljava obje jednačine.", "Nije, ne zadovoljava prvu.",
                  "Nije, ne zadovoljava drugu.", "Nije moguće odrediti."]) is None


def test_verify_ordered_pair_rejects_plain_solve_task():
    bad = "Riješi sistem $x+y=5$ i $x-y=-1$."
    assert check("verify_ordered_pair", bad,
                 ["$(2,3)$", "$(3,2)$", "$(1,4)$", "$(4,1)$"]) is not None


def test_determine_number_of_solutions_rejects_ordered_pair_options():
    bad = "Riješi sistem $x+y=3$ i $2x+2y=6$."
    assert check("determine_number_of_solutions", bad,
                 ["$(1,2)$", "$(2,1)$", "$(0,3)$", "$(3,0)$"]) is not None


def test_determine_number_of_solutions_accepts_count_question():
    good = "Koliko rješenja ima sistem $x+y=3$ i $2x+2y=6$?"
    assert check("determine_number_of_solutions", good,
                 ["Beskonačno mnogo rješenja.", "Tačno jedno rješenje.",
                  "Nema rješenja.", "Tačno dva rješenja."]) is None


def test_choose_method_rejects_numeric_answer_task():
    bad = "Riješi sistem $y=2x$ i $x+y=9$."
    assert check("choose_method", bad, ["$(3,6)$", "$(6,3)$", "$(2,4)$", "$(4,2)$"]) is not None


def test_choose_method_accepts_method_question_with_prose_options():
    good = "Koja metoda je najprikladnija za sistem $y=2x$ i $x+y=9$?"
    assert check("choose_method", good,
                 ["Metoda zamjene, jer je $y$ već izražen.",
                  "Metoda suprotnih koeficijenata.",
                  "Grafička metoda.",
                  "Nijedna metoda ne odgovara."]) is None


def test_choose_correct_formula_rejects_bare_numeric_options():
    bad = "Izračunaj zapreminu piramide sa $B=12$ i $H=5$."
    assert check("choose_correct_formula", bad, ["20", "60", "30", "15"]) is not None


def test_choose_correct_formula_accepts_formula_question():
    good = "Koja formula daje zapreminu piramide?"
    assert check("choose_correct_formula", good,
                 ["$V = \\frac{BH}{3}$", "$V = BH$", "$V = 2BH$", "$V = \\frac{BH}{2}$"]) is None


def test_detect_student_error_requires_error_question_and_prose_options():
    good = "Učenik je napisao $\\frac{1}{2}+\\frac{1}{3}=\\frac{2}{5}$. Šta je pogriješio?"
    assert check("detect_student_error", good,
                 ["Sabrao je brojnike i nazivnike odvojeno.",
                  "Pogrešno je skratio rezultat.",
                  "Zamijenio je brojnik i nazivnik.",
                  "Pomnožio je umjesto da sabere."]) is None


def test_detect_student_error_rejects_plain_computation():
    bad = "Izračunaj $\\frac{1}{2}+\\frac{1}{3}$."
    assert check("detect_student_error", bad,
                 ["$\\frac{5}{6}$", "$\\frac{2}{5}$", "$\\frac{1}{6}$", "$\\frac{3}{5}$"]) is not None


def test_solve_equation_rejects_next_step_question():
    bad = "Koji je sljedeći korak u rješavanju $x+5=12$?"
    assert check("solve_equation", bad,
                 ["Oduzmi 5 od obje strane.", "Dodaj 5.", "Podijeli sa 5.", "Pomnoži sa 5."]) is not None


def test_solve_equation_accepts_plain_solve_task():
    good = "Riješi jednačinu $x+5=12$."
    assert check("solve_equation", good, ["7", "17", "5", "12"]) is None


def test_identify_next_step_requires_prose_options():
    good = "Koji je sljedeći korak nakon što obje strane podijeliš sa 2?"
    assert check("identify_next_step", good,
                 ["Sredi lijevu stranu jednačine.", "Pomnoži obje strane sa 2.",
                  "Oduzmi 2 od obje strane.", "Zamijeni strane."]) is None


def test_word_problem_requires_real_context():
    bad = "Izračunaj $12-5$."
    assert check("word_problem", bad, ["7", "17", "5", "12"]) is not None


def test_word_problem_accepts_contextual_task():
    good = "Amar ima 12 KM i kupi svesku za 5 KM u prodavnici. Koliko mu novca ostaje?"
    assert check("word_problem", good, ["7", "17", "5", "12"]) is None


def test_unit_conversion_requires_units():
    bad = "Izračunaj $25 \\cdot 4$."
    assert check("unit_conversion", bad, ["100", "90", "110", "120"]) is not None


def test_unit_conversion_accepts_unit_task():
    good = "Koliko je $2{,}5\\,\\text{dm}^2$ izraženo u $\\text{cm}^2$?"
    assert check("unit_conversion", good, ["250", "25", "2500", "0,25"]) is None


# ---------------------------------------------------------------------------
# Pomoćni prepoznavači oblika opcija
# ---------------------------------------------------------------------------

def test_fraction_option_detection():
    assert is_fraction_option("$\\frac{9}{24}$")
    assert is_fraction_option("5/8")
    assert not is_fraction_option("4")
    assert not is_fraction_option("Metoda zamjene.")


def test_integer_option_detection():
    assert is_integer_option("4")
    assert is_integer_option("$-3$")
    assert not is_integer_option("$\\frac{9}{24}$")
    assert not is_integer_option("Metoda zamjene.")


def test_prose_option_detection():
    assert is_prose_option("Sabrao je brojnike i nazivnike.")
    assert not is_prose_option("4")
    assert not is_prose_option("$\\frac{9}{24}$")


def test_invalid_correct_index_is_rejected():
    assert check("expand_to_given_denominator", LIVE_EXPAND_QUESTION,
                 LIVE_EXPAND_OPTIONS, correct_index=9) is not None


# ---------------------------------------------------------------------------
# PEDAGOŠKI POTPIS OBLIKA
# ---------------------------------------------------------------------------

def test_three_live_expand_tasks_share_one_pedagogical_shape():
    """Tačno tri teksta koja su uživo prošla kao „različiti“ zadaci."""
    shapes = {
        tf.pedagogical_shape("Proširi razlomak $\\frac{3}{8}$ tako da nazivnik bude $24$."),
        tf.pedagogical_shape("Proširi razlomak $\\frac{2}{5}$ tako da nazivnik bude $20$."),
        tf.pedagogical_shape("Proširi razlomak $\\frac{3}{7}$ tako da nazivnik bude $35$."),
    }
    assert len(shapes) == 1


def test_different_operations_get_different_shapes():
    a = tf.pedagogical_shape("Proširi razlomak $\\frac{3}{8}$ tako da nazivnik bude $24$.")
    b = tf.pedagogical_shape("Koji od navedenih razlomaka je jednak razlomku $\\frac{4}{9}$?")
    assert a != b


def test_shape_does_not_collapse_different_operators():
    """Ne smijemo proglasiti svaku algebarsku jednačinu istom samo zato što su
    se brojevi promijenili — operatori ostaju dio oblika."""
    a = tf.pedagogical_shape("Riješi jednačinu $x+5=12$.")
    b = tf.pedagogical_shape("Riješi jednačinu $x-3=8$.")
    assert a != b


def test_shape_collapses_only_superficial_numbers():
    a = tf.pedagogical_shape("Riješi jednačinu $x+5=12$.")
    b = tf.pedagogical_shape("Riješi jednačinu $x+7=20$.")
    assert a == b


def test_duplicate_shape_rejected_across_different_families():
    previous = tf.task_signature("expand_to_given_denominator",
                                 "Proširi razlomak $\\frac{3}{8}$ tako da nazivnik bude $24$.",
                                 "6-04-005", "easy")
    current = tf.task_signature("find_expansion_factor",
                                "Proširi razlomak $\\frac{2}{5}$ tako da nazivnik bude $20$.",
                                "6-04-005", "easy")
    assert tf.is_duplicate_shape(current, [previous])


def test_duplicate_shape_allowed_for_same_family_retry():
    previous = tf.task_signature("expand_to_given_denominator",
                                 "Proširi razlomak $\\frac{3}{8}$ tako da nazivnik bude $24$.",
                                 "6-04-005", "easy")
    current = tf.task_signature("expand_to_given_denominator",
                                "Proširi razlomak $\\frac{2}{5}$ tako da nazivnik bude $20$.",
                                "6-04-005", "easy")
    assert not tf.is_duplicate_shape(current, [previous], retry_required=True)


def test_duplicate_shape_ignores_other_lessons():
    previous = tf.task_signature("expand_to_given_denominator",
                                 "Proširi razlomak $\\frac{3}{8}$ tako da nazivnik bude $24$.",
                                 "6-04-005", "easy")
    current = tf.task_signature("find_expansion_factor",
                                "Proširi razlomak $\\frac{2}{5}$ tako da nazivnik bude $20$.",
                                "7-03-016", "easy")
    assert not tf.is_duplicate_shape(current, [previous])


def test_task_signature_carries_both_signatures():
    signature = tf.task_signature("expand_to_given_denominator",
                                  "Proširi razlomak $\\frac{3}{8}$ tako da nazivnik bude $24$.",
                                  "6-04-005", "easy")
    assert signature["question"]
    assert signature["shape"]
    assert signature["question"] != signature["shape"]
