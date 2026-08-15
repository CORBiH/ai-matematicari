"""Jedinični testovi kataloga i izbora porodica zadataka (matbot/task_families.py).

Porodica opisuje PEDAGOŠKU OPERACIJU, ne brojeve — ovi testovi štite pravilo da
se ista vještina ne servira dva puta zaredom samo s drugim brojevima.
"""
from matbot import task_families as tf


# ---------------------------------------------------------------------------
# Routing: koja lekcija dobija koje porodice
# ---------------------------------------------------------------------------

def test_fraction_lesson_gets_fraction_families():
    families = tf.applicable_families(6, "Razlomci", "Proširivanje razlomaka")
    assert "expand_to_given_denominator" in families
    assert "find_expansion_factor" in families
    assert "solve_system" not in families


def test_system_lesson_gets_system_families():
    families = tf.applicable_families(9, "Sistemi linearnih jednačina", "Metoda supstitucije")
    assert "solve_system" in families
    assert "verify_ordered_pair" in families
    assert "determine_number_of_solutions" in families
    assert "expand_to_given_denominator" not in families


def test_geometry_lesson_gets_geometry_families():
    families = tf.applicable_families(9, "Geometrijska tijela", "Zapremina prizme")
    assert "direct_formula_application" in families
    assert "inverse_formula_problem" in families
    assert "choose_correct_formula" in families


def test_construction_lesson_gets_step_families_not_computation():
    families = tf.applicable_families(6, "Trougao", "Konstrukcija simetrale ugla")
    assert "identify_next_step" in families
    assert "direct_formula_application" not in families


def test_unknown_domain_falls_back_to_general_families():
    families = tf.applicable_families(6, "Skupovi i skupovne operacije", "Unija skupova")
    assert families == list(tf._GENERAL_FAMILIES)


def test_every_family_id_has_a_description():
    seen = set()
    for group in (tf._FRACTION_FAMILIES, tf._SYSTEM_FAMILIES, tf._EQUATION_FAMILIES,
                  tf._GEOMETRY_FAMILIES, tf._GENERAL_FAMILIES, tf._CONSTRUCTION_FAMILIES):
        seen.update(group)
    missing = [f for f in seen if not tf.describe(f)]
    assert not missing, f"Porodice bez opisa: {missing}"


# ---------------------------------------------------------------------------
# Izbor porodice — jezgro progresije
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Potpis zadatka — zaštita od doslovnog ponavljanja
# ---------------------------------------------------------------------------

def test_normalize_question_ignores_case_spacing_and_punctuation():
    a = tf.normalize_question("  Proširi razlomak   na nazivnik 24.  ")
    b = tf.normalize_question("proširi razlomak na nazivnik 24")
    assert a == b


def test_normalize_question_keeps_numbers_distinct():
    a = tf.normalize_question("Proširi $\\frac{3}{8}$ na nazivnik 24.")
    b = tf.normalize_question("Proširi $\\frac{5}{7}$ na nazivnik 28.")
    assert a != b
