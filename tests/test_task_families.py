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

def test_first_selection_takes_first_applicable_family():
    assert tf.select_family(["a", "b", "c"]) == "a"


def test_completed_family_is_not_selected_again_while_others_remain():
    chosen = tf.select_family(
        ["a", "b", "c"], recently_used=["a"], completed_families=["a"], current_family="a"
    )
    assert chosen != "a"
    assert chosen in ("b", "c")


def test_retry_keeps_the_same_family():
    chosen = tf.select_family(
        ["a", "b", "c"], recently_used=["b"], completed_families=[],
        retry_required=True, current_family="b",
    )
    assert chosen == "b"


def test_retry_ignored_when_current_family_not_applicable_anymore():
    """Promjena lekcije može ukloniti porodicu iz skupa — tada retry ne smije
    zaglaviti na nepostojećoj porodici."""
    chosen = tf.select_family(
        ["x", "y"], retry_required=True, current_family="b"
    )
    assert chosen in ("x", "y")


def test_never_repeats_immediately_previous_family_when_alternatives_exist():
    chosen = tf.select_family(["a", "b", "c"], recently_used=["a"], current_family="a")
    assert chosen != "a"


def test_all_completed_starts_second_cycle_with_least_recently_used():
    # 'a' je korišten najdavnije, 'c' zadnji → drugi ciklus mora uzeti 'a'.
    chosen = tf.select_family(
        ["a", "b", "c"],
        recently_used=["a", "b", "c"],
        completed_families=["a", "b", "c"],
        current_family="c",
    )
    assert chosen == "a"


def test_all_completed_never_repeats_the_immediately_previous_family():
    chosen = tf.select_family(
        ["a", "b"], recently_used=["b", "a"], completed_families=["a", "b"], current_family="a"
    )
    assert chosen == "b"


def test_family_never_used_wins_over_used_ones():
    chosen = tf.select_family(["a", "b", "c"], recently_used=["a", "b"], current_family="b")
    assert chosen == "c"  # 'c' se nikad nije koristio


def test_empty_applicable_returns_empty_string():
    assert tf.select_family([]) == ""


def test_selection_is_deterministic():
    args = dict(recently_used=["b"], completed_families=["a"], current_family="b")
    results = {tf.select_family(["a", "b", "c", "d"], **args) for _ in range(20)}
    assert len(results) == 1


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


def test_identical_question_text_is_detected_as_duplicate():
    sig = tf.task_signature("f1", "Izračunaj obim kvadrata.", "6-01-001", "standard")
    assert tf.is_duplicate_signature(sig, [dict(sig)])


def test_same_text_in_different_lesson_is_not_duplicate():
    sig = tf.task_signature("f1", "Izračunaj obim kvadrata.", "6-01-001", "standard")
    other = tf.task_signature("f1", "Izračunaj obim kvadrata.", "7-02-005", "standard")
    assert not tf.is_duplicate_signature(sig, [other])


def test_different_numbers_are_not_duplicates():
    sig_a = tf.task_signature("f1", "Proširi $\\frac{3}{8}$ na 24.", "6-01-001", "standard")
    sig_b = tf.task_signature("f1", "Proširi $\\frac{5}{7}$ na 28.", "6-01-001", "standard")
    assert not tf.is_duplicate_signature(sig_b, [sig_a])


def test_duplicate_check_survives_empty_history():
    sig = tf.task_signature("f1", "Bilo šta.", "6-01-001", "standard")
    assert not tf.is_duplicate_signature(sig, [])
    assert not tf.is_duplicate_signature(sig, None)
