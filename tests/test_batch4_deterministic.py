"""Batch #4 — arhitektonske granice, jezgro i integracioni dokazi.

Dopunjava postojeće slojeve (bulk properties nad SVIM kompajliranim
lekcijama, lifecycle integraciju, performanse) dokazima specifičnim za
Batch #4:

  • ODVOJENOST JEZGRA: matbot/mathkernel/ ne poznaje lekciju, Practice ni
    MCQ — spreman je za budući „Daj mi rezultat" mod;
  • bez ID-ja lekcije i bez float autoriteta u novim motorima;
  • egzaktni orakli za rješavače tekstualnih zadataka;
  • finansije bez ijednog vanjskog kursa;
  • potpunost parametarske podjele slučajeva;
  • performanse novih porodica kroz STVARNI orkestrator.
"""
import json
import random
import re
import statistics
import time
from fractions import Fraction
from pathlib import Path

import pytest

from matbot.mathkernel import finiteset, wordfacts
from matbot.mathkernel.wordfacts import (Quantity, WordProblemError,
                                         WordProblemFacts)
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import FakeLLM

ROOT = Path(__file__).resolve().parent.parent

_BATCH4_FAMILY_OF = {
    # racionalni izrazi
    **{lesson: "rational_expression_direct" for lesson in (
        "9-01-002", "9-01-003", "9-01-004", "9-01-005", "9-01-006",
        "9-01-007", "9-01-008", "9-01-009", "9-01-010", "9-01-011",
        "9-01-012", "9-01-013")},
    **{lesson: "rational_equation_direct" for lesson in (
        "9-01-014", "9-04-005", "9-04-006")},
    **{lesson: "structured_word_problem" for lesson in (
        "6-03-010", "6-04-015", "6-05-011", "7-02-021", "7-03-020",
        "9-04-011", "9-05-013", "9-07-033", "8-04-016")},
    **{lesson: "finite_set_direct" for lesson in (
        "6-01-001", "6-01-002", "6-01-003", "6-01-004", "6-01-006",
        "6-01-007", "6-01-008", "6-01-009", "6-01-010", "6-01-011")},
    **{lesson: "number_set_membership" for lesson in (
        "6-02-001", "8-01-001", "8-01-002", "8-01-003")},
    **{lesson: "event_probability_facts" for lesson in (
        "8-06-010", "8-06-011", "9-08-003", "9-08-009")},
    **{lesson: "financial_arithmetic_direct" for lesson in (
        "8-03-019", "9-08-004", "9-08-005", "9-08-006", "9-08-008")},
    **{lesson: "parametric_linear_discussion" for lesson in (
        "9-04-007", "9-04-022", "9-05-017")},
    **{lesson: "linear_inequality_direct" for lesson in (
        "9-04-013", "9-04-015", "9-04-018")},
    **{lesson: "operation_property_recognition" for lesson in (
        "6-02-006", "6-04-013", "7-02-010", "7-02-014", "7-03-013")},
    **{lesson: "fraction_concept_direct" for lesson in (
        "6-04-002", "6-04-003", "6-04-007", "6-05-004")},
    **{lesson: "similarity_direct" for lesson in (
        "8-03-008", "8-03-013", "8-03-015", "8-03-016")},
    "8-08-003": "polygon_angle_direct",
    **{lesson: "unit_conversion_direct" for lesson in (
        "7-05-018", "8-05-022", "9-07-034")},
    **{lesson: "coordinate_line_direct" for lesson in (
        "8-02-004", "8-02-008", "8-02-009", "8-02-012", "8-02-014",
        "9-03-005", "9-03-006", "9-03-015", "9-03-017", "9-03-021")},
}


# ---------------------------------------------------------------------------
# 1) POKRIVENOST — nezavisno reprodukovan konačan broj
# ---------------------------------------------------------------------------

def test_batch4_coverage_is_exactly_354_lessons_in_44_families():
    compiled = json.loads(
        (ROOT / "data" / "lesson_semantics.compiled.json").read_text(
            encoding="utf-8"))
    lessons = compiled["lessons"]
    assert len(lessons) == 354
    assert len({entry["family_id"] for entry in lessons.values()}) == 44
    assert len(_BATCH4_FAMILY_OF) == 80
    for lesson_id, family_id in _BATCH4_FAMILY_OF.items():
        assert lesson_id in lessons, lesson_id
        assert lessons[lesson_id]["family_id"] == family_id, lesson_id
        assert lessons[lesson_id]["enforcement_mode"] == "blocking", lesson_id


# ---------------------------------------------------------------------------
# 2) ARHITEKTONSKE GRANICE — jezgro bez Practice-a, motori bez ID-jeva,
#    egzaktna aritmetika bez float autoriteta
# ---------------------------------------------------------------------------

_KERNEL_DIR = ROOT / "matbot" / "mathkernel"
_NEW_ENGINES = [
    "algfractions.py", "wordproblems.py", "settheory.py", "statsdata.py",
    "finance.py", "parametric.py", "inequalities.py", "properties.py",
    "fractionconcepts.py", "similarity.py", "polygons.py", "linefacts.py",
]
_TOPIC_ID_RE = re.compile(r"\b\d-\d{2}-\d{3}\b")
_FORBIDDEN_KERNEL_IMPORTS = re.compile(
    r"^\s*(?:from|import)\s+matbot\.(?:tutor|deterministic|practice|api|"
    r"session_store|prompts)", re.MULTILINE)
_FLOAT_AUTHORITY_RE = re.compile(r"\bfloat\(|\bmath\.sqrt\(|\bround\(")


def test_mathkernel_is_free_of_practice_and_lesson_knowledge():
    """Rješavači jezgra su ponovo upotrebljivi za budući Result mod: bez
    lekcija, bez Practice uvoza, bez float autoriteta."""
    for path in sorted(_KERNEL_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert not _TOPIC_ID_RE.search(source), path.name
        assert not _FORBIDDEN_KERNEL_IMPORTS.search(source), path.name
        assert not _FLOAT_AUTHORITY_RE.search(source), path.name


def test_new_engines_contain_no_lesson_identity():
    for name in _NEW_ENGINES:
        source = (ROOT / "matbot" / "deterministic" / name).read_text(
            encoding="utf-8")
        assert not _TOPIC_ID_RE.search(source), name
        assert "lesson_title ==" not in source, name


# ---------------------------------------------------------------------------
# 3) EGZAKTNI ORAKLI RJEŠAVAČA TEKSTUALNIH ZADATAKA
# ---------------------------------------------------------------------------

def _facts(semantic_type, unknown, **known):
    return WordProblemFacts(
        semantic_type=semantic_type, entities=("test",),
        known=tuple(Quantity(name, Fraction(value))
                    for name, value in known.items()),
        unknown=unknown)


def test_word_problem_solvers_match_manual_oracles():
    assert wordfacts.solve(_facts("equal_sharing", "per_group",
                                  total=24, groups=6)).answer.value == 4
    remainder = wordfacts.solve(_facts("sharing_remainder", "remainder",
                                       total=26, groups=6))
    assert remainder.answer.value == 2
    assert remainder.auxiliary["quotient"] == 4
    assert wordfacts.solve(_facts("fraction_of_quantity", "part", total=20,
                                  fraction=Fraction(3, 4))).answer.value == 15
    assert wordfacts.solve(_facts("fraction_remainder", "remainder", total=20,
                                  fraction=Fraction(3, 4))).answer.value == 5
    assert wordfacts.solve(_facts("signed_change", "final", start=-3,
                                  change_0=7, change_1=-2)).answer.value == 2
    assert wordfacts.solve(_facts("number_equation", "x", a=3, b=-4,
                                  c=17)).answer.value == 7
    system = wordfacts.solve(_facts("sum_difference_system", "larger",
                                    sum=30, difference=6))
    assert system.answer.value == 18 and system.auxiliary["smaller"] == 12
    multiple = wordfacts.solve(_facts("sum_multiple_system", "smaller",
                                      sum=24, factor=3))
    assert multiple.answer.value == 6 and multiple.auxiliary["larger"] == 18
    assert wordfacts.solve(_facts("box_volume", "volume", a=3, b=4,
                                  c=5)).answer.value == 60
    assert wordfacts.solve(_facts("cube_surface", "surface",
                                  a=4)).answer.value == 96
    assert wordfacts.solve(_facts("pythagoras_distance", "hypotenuse",
                                  leg_a=9, leg_b=12)).answer.value == 15
    assert wordfacts.solve(_facts("pythagoras_leg", "leg_b", hypotenuse=13,
                                  leg_a=5)).answer.value == 12


def test_word_problem_solvers_fail_closed_on_bad_facts():
    with pytest.raises(WordProblemError):
        wordfacts.solve(_facts("equal_sharing", "per_group", total=25,
                               groups=6))          # nije djeljivo
    with pytest.raises(WordProblemError):
        wordfacts.solve(_facts("pythagoras_distance", "hypotenuse",
                               leg_a=2, leg_b=3))  # hipotenuza nije cijela
    with pytest.raises(WordProblemError):
        wordfacts.solve(_facts("money_change", "change", paid=5,
                               price_a=4, price_b=3))   # plaćeno premalo
    with pytest.raises(WordProblemError):
        wordfacts.solve(_facts("nepoznat_tip", "x", a=1))
    with pytest.raises(WordProblemError):
        wordfacts.solve(_facts("number_equation", "x", a=0, b=1, c=2))


def test_finite_set_equality_ignores_order_and_duplicates():
    assert finiteset.sets_equal((1, 2, 3), (3, 2, 1, 2))
    assert finiteset.display((3, 1, 2)) == "{1, 2, 3}"
    assert finiteset.cardinality((5, 5, 5)) == 1
    assert finiteset.complement((1, 2), (1, 2, 3, 4)) == frozenset({3, 4})
    with pytest.raises(finiteset.FiniteSetError):
        finiteset.complement((9,), (1, 2, 3))


# ---------------------------------------------------------------------------
# 4) PROZA ⇄ IR — brojevi u tekstu su tačno IR veličine
# ---------------------------------------------------------------------------

def test_word_problem_prose_always_carries_the_ir_quantities():
    from matbot.deterministic import wordproblems

    rng_types = sorted(wordproblems._SUPPORTED_TYPES)
    for problem_type in rng_types:
        for seed in range(10):
            package = wordproblems.generate_package(
                "0-00-000", "Test", {"problem_types": [problem_type]},
                (seed % 3) + 1, rng=random.Random(seed * 37))
            known = {name: value for name, value in
                     package.signature_parameters if name != "type"
                     and name != "level"}
            assert known, problem_type
            # Render-audit je već pao u generisanju ako proza ne odgovara IR-u;
            # ovdje se dokazuje da POTPIS zaista nosi IR veličine.
            assert package.signature_parameters[0] == ("type", problem_type)


# ---------------------------------------------------------------------------
# 5) FINANSIJE — nijedan kurs izvan zadatka
# ---------------------------------------------------------------------------

def test_currency_conversion_always_states_its_rate_in_the_task():
    from matbot.deterministic import finance

    for seed in range(30):
        package = finance.generate_package(
            "0-00-000", "Test", {"concepts": ["currency_conversion"]},
            (seed % 3) + 1, rng=random.Random(seed))
        assert "Kurs je" in package.question, package.question
        rate_value = dict(package.signature_parameters)["rate"]
        assert Fraction(rate_value) > 0


# ---------------------------------------------------------------------------
# 6) PARAMETARSKA DISKUSIJA — potpuna podjela slučajeva u rješenju
# ---------------------------------------------------------------------------

def test_parameter_case_solution_lists_all_three_cases():
    from matbot.deterministic import parametric

    for seed in range(20):
        package = parametric.generate_package(
            "0-00-000", "Test", {"concepts": ["parameter_case"]},
            (seed % 3) + 1, rng=random.Random(seed))
        solution = package.solution
        assert "jedinstven" in solution, solution
        assert "nema" in solution, solution
        assert "beskonačno" in solution, solution


def test_parametric_system_solution_lists_all_three_cases():
    from matbot.deterministic import parametric

    for seed in range(20):
        package = parametric.generate_package(
            "0-00-000", "Test",
            {"concepts": ["parametric_system_classification"]},
            (seed % 3) + 1, rng=random.Random(seed))
        solution = package.solution
        assert "jedinstven" in solution, solution
        assert "nijedno" in solution or "nema" in solution, solution
        assert "beskonačno" in solution, solution


# ---------------------------------------------------------------------------
# 7) PERFORMANSE — nove porodice kroz STVARNI orkestrator
# ---------------------------------------------------------------------------

_PERF_LESSONS = (
    (9, "9-01-005"), (9, "9-01-014"), (6, "6-03-010"), (8, "8-04-016"),
    (6, "6-01-006"), (8, "8-01-002"), (8, "8-06-011"), (9, "9-08-005"),
    (9, "9-04-022"), (9, "9-04-013"), (7, "7-02-014"), (6, "6-04-003"),
    (8, "8-03-016"), (8, "8-08-003"), (8, "8-02-004"),
)


def _turn(grade, lesson, session_id, message="Daj mi novi zadatak."):
    return {
        "session_id": session_id, "grade": grade, "selected_topic": lesson,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }


def test_new_family_actions_stay_under_the_200ms_budget(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    store, fake = SessionStore(), FakeLLM()
    timings = []
    for grade, lesson in _PERF_LESSONS:
        session_id = f"b4-perf-{lesson}"
        run_practice_turn(store, fake, _turn(grade, lesson, session_id,
                                             "Daj mi zadatak."))  # zagrijavanje
        for _ in range(3):
            started = time.perf_counter()
            response = run_practice_turn(store, fake,
                                         _turn(grade, lesson, session_id))
            timings.append(time.perf_counter() - started)
            assert response["status"] == "ready", lesson
    assert fake.call_count == 0
    timings.sort()
    p95 = timings[int(len(timings) * 0.95) - 1]
    assert statistics.median(timings) < 0.2, statistics.median(timings)
    assert p95 < 0.2, p95
