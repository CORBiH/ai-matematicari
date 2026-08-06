"""Faza 4G, Workstream E — uski orakl DIREKTNOG RAČUNA za MCQ pakete.

ZAŠTO POSTOJI: produkcijski nalaz koji je otvorio ovaj program bio je MCQ bez
ijedne tačne opcije (djeljivost). Za lekcije direktnog računa s razlomcima
(6-04-009…6-04-012) istu klasu („nijedna opcija nije vrijednost izraza“,
„označena opcija nije vrijednost izraza“) dosad je držao SAMO recenzent —
nijedan deterministički validator nije poredio vrijednost vidljivog izraza s
ponuđenim opcijama.

GRANICE (namjerno uske, isti princip kao divisibility orakl):
  • proza mora izričito tražiti račun („Izračunaj“, „Koliko je“…);
  • TAČNO JEDAN matematički segment je izračunljiv izraz s vidljivim
    operatorom; sve ostalo → orakl ćuti (unsupported ≠ dokaz ispravnosti);
  • sve opcije moraju biti izračunljive vrijednosti (razlomak, cio broj,
    decimalni, mješoviti) — prozna opcija isključuje cio orakl;
  • računa ISKLJUČIVO postojeći restricted-AST evaluator (mathcheck), nikad
    eval() i nikad novi parser.
"""
from matbot import mcq_integrity

TASK = "Izračunaj: $\\frac{2}{7}+\\frac{3}{7}$"
GOOD = ("$\\frac{5}{7}$", "$\\frac{6}{7}$", "$\\frac{2}{7}$", "$\\frac{1}{7}$")


def evaluate(question, options=GOOD):
    return mcq_integrity.evaluate_direct_computation_mcq(question, options)


# ---------------------------------------------------------------------------
# 1) ANGAŽOVANJE — sve četiri porodice razlomaka
# ---------------------------------------------------------------------------

def test_like_denominator_addition_finds_the_correct_option():
    result = evaluate(TASK)
    assert result.applicable and result.valid
    assert result.correct_index == 0


def test_unlike_denominator_addition():
    result = evaluate("Izračunaj: $\\frac{1}{2}+\\frac{1}{3}$",
                      ("$\\frac{5}{6}$", "$\\frac{2}{5}$", "$\\frac{1}{6}$", "$\\frac{2}{6}$"))
    assert result.valid and result.correct_index == 0


def test_multiplication_with_reducible_result():
    result = evaluate("Izračunaj: $\\frac{2}{3}\\cdot\\frac{9}{4}$",
                      ("$\\frac{3}{2}$", "$\\frac{18}{12}$", "$\\frac{2}{4}$", "$6$"))
    # I 3/2 i 18/12 su ista vrijednost → dokazano više tačnih opcija.
    assert result.reason_code == "multiple_correct_options"


def test_division_by_fraction_uses_reciprocal_value():
    result = evaluate("Izračunaj: $\\frac{3}{4}:\\frac{1}{2}$",
                      ("$\\frac{3}{2}$", "$\\frac{3}{8}$", "$\\frac{1}{2}$", "$2$"))
    assert result.valid and result.correct_index == 0


def test_mixed_number_option_is_understood():
    result = evaluate("Koliko je $\\frac{7}{4}+\\frac{1}{4}$?",
                      ("$2$", "$1\\frac{3}{4}$", "$\\frac{8}{7}$", "$\\frac{6}{4}$"))
    assert result.valid and result.correct_index == 0


def test_decimal_option_matches_with_its_own_precision():
    result = evaluate("Izračunaj: $\\frac{1}{2}+\\frac{1}{4}$",
                      ("$0,75$", "$0,5$", "$0,25$", "$1,5$"))
    assert result.valid and result.correct_index == 0


def test_natural_number_times_fraction():
    result = evaluate("Izračunaj: $3\\cdot\\frac{2}{5}$",
                      ("$\\frac{6}{5}$", "$\\frac{5}{6}$", "$\\frac{2}{15}$", "$3$"))
    assert result.valid and result.correct_index == 0


# ---------------------------------------------------------------------------
# 2) KLASE NEISPRAVNOG PAKETA
# ---------------------------------------------------------------------------

def test_no_correct_option_is_proven():
    result = evaluate(TASK, ("$\\frac{6}{7}$", "$\\frac{2}{7}$",
                             "$\\frac{1}{7}$", "$\\frac{4}{7}$"))
    assert result.applicable and not result.valid
    assert result.reason_code == "no_correct_option"


def test_division_by_zero_fails_closed():
    result = evaluate("Izračunaj: $\\frac{3}{4}:0$",
                      ("$0$", "$1$", "$\\frac{3}{4}$", "$4$"))
    assert result.applicable and not result.valid
    assert result.reason_code == "division_by_zero_in_task"


def test_publication_failure_flags_a_wrongly_marked_option():
    failure, _result = mcq_integrity.publication_failure(TASK, GOOD, 1, GOOD[1])
    assert failure == "marked_option_math_mismatch"


def test_publication_failure_accepts_the_correct_mark():
    failure, _result = mcq_integrity.publication_failure(TASK, GOOD, 0, GOOD[0])
    assert failure == ""


# ---------------------------------------------------------------------------
# 3) GRANICE ANGAŽOVANJA — orakl NE SMIJE tvrditi ništa van svog oblika
# ---------------------------------------------------------------------------

def test_no_directive_means_not_applicable():
    # „Koji broj je suprotan…“ — vrijednost tačne opcije NIJE vrijednost izraza.
    result = evaluate("Koji broj je suprotan broju $\\frac{3}{4}$?",
                      ("$-\\frac{3}{4}$", "$\\frac{4}{3}$", "$\\frac{3}{4}$", "$0$"))
    assert not result.applicable


def test_bare_number_without_operator_is_not_engaged():
    # Lekcija ekvivalencije: tačna opcija JESTE numerički jednaka prikazanom
    # razlomku — orakl bez vidljive operacije ne smije tvrditi ništa.
    result = evaluate("Izračunaj koliko je proširen razlomak $\\frac{2}{4}$?",
                      ("$\\frac{1}{2}$", "$\\frac{2}{3}$", "$\\frac{3}{4}$", "$\\frac{4}{2}$"))
    assert not result.applicable


def test_prose_option_disables_the_oracle():
    result = evaluate(TASK, ("$\\frac{5}{7}$", "Ne može se izračunati",
                             "$\\frac{2}{7}$", "$\\frac{1}{7}$"))
    assert not result.applicable


def test_variable_in_expression_disables_the_oracle():
    result = evaluate("Izračunaj: $x+\\frac{3}{7}$")
    assert not result.applicable


def test_two_candidate_expressions_disable_the_oracle():
    result = evaluate("Izračunaj $\\frac{1}{2}+\\frac{1}{4}$ i $\\frac{1}{3}+\\frac{1}{6}$")
    assert not result.applicable


def test_trailing_result_placeholder_is_tolerated():
    result = evaluate("Izračunaj: $\\frac{2}{7}+\\frac{3}{7}=?$")
    assert result.applicable and result.valid
    assert result.correct_index == 0


def test_divisibility_questions_stay_with_the_divisibility_oracle():
    # Postojeći orakl ima prednost; njegov oblik se ne smije preoteti.
    failure, result = mcq_integrity.publication_failure(
        "Koji od ponuđenih brojeva je djeljiv sa 25?",
        ("725", "714", "738", "741"), 0, "725")
    assert failure == ""
    assert result.applicable
