"""Faza 4H, Workstream B/C/D — deterministički generator paketa za porodicu
fraction_arithmetic_direct (lekcije 6-04-009…6-04-012).

Svaki generisan paket mora proći ISTE serverske validatore kao model-paket:
strukturu (validate_task), preflight (opcije, ekvivalencija, orakl direktnog
računa, semantički ugovor porodice, dokaz težine) i numeričku dosljednost
rješenja. Matematički autoritet je isključivo egzaktni `fractions.Fraction` —
nikad binarni float.
"""
import random
from fractions import Fraction

import pytest

from matbot.deterministic import fractions as detfrac
from matbot.mathcheck import find_numeric_inconsistencies, safe_numeric_value
from matbot.mathsafe import sanitize_and_validate_math_text
from matbot.tutor import package_preflight
from matbot.tutor.lesson_context import build
from matbot.tutor.schema import validate_task, difficulty_evidence_errors

LESSONS = ("6-04-009", "6-04-010", "6-04-011", "6-04-012")
GRADE = 6


def context_for(lesson_id):
    context = build(GRADE, lesson_id)
    assert context is not None and context.semantic_contract is not None
    return context


def package_for(lesson_id, level, seed):
    context = context_for(lesson_id)
    return detfrac.generate_package(
        lesson_id=context.topic_id, lesson_title=context.title,
        parameters=context.semantic_contract.parameters, level=level,
        rng=random.Random(seed))


ALL_CASES = [(lesson, level, seed)
             for lesson in LESSONS for level in (1, 2, 3) for seed in range(6)]


# ---------------------------------------------------------------------------
# 1) SVAKI PAKET PROLAZI SVE POSTOJEĆE SERVERSKE VALIDATORE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lesson,level,seed", ALL_CASES)
def test_package_passes_every_existing_validator(lesson, level, seed):
    context = context_for(lesson)
    package = package_for(lesson, level, seed)
    payload = package.task_payload()

    validate_task(payload)
    issues = package_preflight.collect_package_issues(
        payload, contract=context.semantic_contract)
    assert issues == (), (lesson, level, seed,
                          package_preflight.describe_issues(issues))
    assert difficulty_evidence_errors(payload.difficulty_evidence, level) == ()
    assert payload.target_difficulty_level == level


@pytest.mark.parametrize("lesson,level,seed", ALL_CASES)
def test_marked_option_is_the_exact_answer(lesson, level, seed):
    package = package_for(lesson, level, seed)
    assert package.correct_index == 0
    status, value = safe_numeric_value(package.option_texts[0].strip("$"))
    assert status == "value"
    assert abs(value - float(package.answer)) < 1e-9
    # Tačno jedna opcija nosi tačnu vrijednost.
    matches = 0
    for option in package.option_texts:
        option_status, option_value = safe_numeric_value(option.strip("$"))
        assert option_status == "value", option
        if abs(option_value - float(package.answer)) < 1e-9:
            matches += 1
    assert matches == 1


@pytest.mark.parametrize("lesson,level,seed", ALL_CASES)
def test_solution_and_hints_are_safe_and_consistent(lesson, level, seed):
    package = package_for(lesson, level, seed)
    assert len(package.hints) == 3
    for text in (package.solution, *package.hints):
        cleaned, safe = sanitize_and_validate_math_text(text)
        assert safe, text
        assert find_numeric_inconsistencies(cleaned) == [], text
    # Hint 1 ne otkriva odgovor.
    assert package.display_answer not in package.hints[0]
    # Rješenje završava tačnim rezultatom.
    assert package.display_answer in package.solution


# ---------------------------------------------------------------------------
# 2) MATEMATIČKE INVARIJANTE PO LEKCIJI
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("level", (1, 2, 3))
@pytest.mark.parametrize("seed", range(8))
def test_like_denominators_stay_equal(level, seed):
    package = package_for("6-04-009", level, seed)
    assert package.operation in ("add", "subtract")
    denominators = {den for _num, den in package.operands}
    assert len(denominators) == 1
    assert package.answer > 0


@pytest.mark.parametrize("level", (1, 2, 3))
@pytest.mark.parametrize("seed", range(8))
def test_unlike_denominators_stay_different(level, seed):
    package = package_for("6-04-010", level, seed)
    assert package.operation in ("add", "subtract")
    d1 = package.operands[0][1]
    d2 = package.operands[1][1]
    assert d1 != d2
    assert package.answer > 0


@pytest.mark.parametrize("level", (1, 2, 3))
@pytest.mark.parametrize("seed", range(8))
def test_multiplication_is_multiplication(level, seed):
    package = package_for("6-04-011", level, seed)
    assert package.operation == "multiply"
    expected = Fraction(1)
    for num, den in package.operands:
        expected *= Fraction(num, den)
    assert package.answer == expected


@pytest.mark.parametrize("level", (1, 2, 3))
@pytest.mark.parametrize("seed", range(8))
def test_division_uses_a_nonzero_divisor_and_exact_reciprocal(level, seed):
    package = package_for("6-04-012", level, seed)
    assert package.operation == "divide"
    dividend = Fraction(*package.operands[0])
    divisor = Fraction(*package.operands[1])
    assert divisor != 0
    assert package.answer == dividend / divisor


# ---------------------------------------------------------------------------
# 3) DETERMINIZAM, JEDINSTVENOST, VERZIJA
# ---------------------------------------------------------------------------

def test_same_seed_gives_identical_package():
    first = package_for("6-04-009", 2, 42)
    second = package_for("6-04-009", 2, 42)
    assert first == second


def test_different_seeds_give_mostly_different_questions():
    questions = {package_for("6-04-010", 1, seed).question for seed in range(12)}
    assert len(questions) >= 8


def test_package_carries_generator_version_and_family():
    package = package_for("6-04-011", 1, 0)
    assert package.generator_version
    assert package.family_id == "fraction_arithmetic_direct"


def test_supports_only_complete_fraction_parameters():
    assert detfrac.supports({"allowed_operations": ["add", "subtract"],
                             "denominator_relation": "equal"})
    assert detfrac.supports({"allowed_operations": ["divide"],
                             "denominator_relation": "any"})
    assert not detfrac.supports({"allowed_operations": ["integrate"],
                                 "denominator_relation": "equal"})
    assert not detfrac.supports({})
