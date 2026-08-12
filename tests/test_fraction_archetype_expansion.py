"""Dva nova arhetipa razlomačkih tekstualnih zadataka (6-04-015).

ŽIVI NALAZ (ciljani živi retest kandidata 895e912/b0ad37d): sva tri Tutorova
nacrta na nivou 3 posegla su za bogatijom strukturom nego što su nudila dva
postojeća arhetipa, pa ih je serverska kapija ispravno odbila — 0 objavljenih
od 3 pokušaja. Kapija nije bila problem; rječnik arhetipa lekcije jeste bio
pretanak: oba postojeća tipa su JEDNOKORAČNA, a eskalacija traži i teže i
strukturno drugačije.

KURIKULARNI OSNOV (KS_2018-0045, sadržaji 6. razreda): „Množenje razlomka
razlomkom“ i „Sabiranje i oduzimanje razlomaka različitih imenilaca“ su
izričito gradivo; KS_2018-0057 nosi osnovne operacije, KS_2018-0073 tekstualne
zadatke. Treći kandidat (uzastopno uklanjanje od TEKUĆEG ostatka) NIJE dodan —
nijedna stavka 6. razreda ga ne imenuje.
"""
import json
import random
from fractions import Fraction
from pathlib import Path

import pytest

from matbot import deterministic as det
from matbot.mathkernel import wordfacts
from matbot.mathkernel.wordfacts import (Quantity, WordProblemError,
                                         WordProblemFacts)
from matbot.semantics import contracts as semantic_contracts
from matbot.tutor import creative_escalation as esc
from matbot.tutor import lesson_context

ROOT = Path(__file__).resolve().parent.parent

LESSON = "6-04-015"
TITLE = "Tekstualni zadaci s razlomcima"
NEW = ("fraction_of_fraction", "multi_fraction_remainder")
EXPECTED_ENUM = ("fraction_of_fraction", "fraction_of_quantity",
                 "fraction_remainder", "multi_fraction_remainder")


def facts_of_fraction(total, first, second):
    return WordProblemFacts(
        semantic_type="fraction_of_fraction", entities=("x", "y"),
        known=(Quantity("total", Fraction(total)),
               Quantity("first_fraction", Fraction(first)),
               Quantity("second_fraction", Fraction(second))),
        unknown="part")


def facts_multi(total, *fractions):
    known = [Quantity("total", Fraction(total))]
    for number, fraction in enumerate(fractions, start=1):
        known.append(Quantity(f"fraction_{number}", Fraction(fraction)))
    return WordProblemFacts(
        semantic_type="multi_fraction_remainder", entities=("x", "y"),
        known=tuple(known), unknown="remainder")


# ---------------------------------------------------------------------------
# 1) EGZAKTNI RJEŠAVAČI
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("total,first,second,expected", [
    (48, Fraction(2, 3), Fraction(1, 4), 8),
    (24, Fraction(1, 2), Fraction(1, 3), 4),
    (30, Fraction(1, 2), Fraction(1, 3), 5),
    (12, Fraction(1, 1), Fraction(1, 2), 6),      # p/q = 1 je dozvoljeno
])
def test_fraction_of_fraction_is_exact(total, first, second, expected):
    solved = wordfacts.solve(facts_of_fraction(total, first, second))
    assert solved.answer.value == Fraction(expected)
    assert solved.auxiliary["middle"] == Fraction(total) * first


@pytest.mark.parametrize("total,first,second", [
    (37, Fraction(2, 3), Fraction(1, 4)),   # međurezultat nije cio broj
    (48, Fraction(2, 3), Fraction(1, 5)),   # rezultat nije cio broj
    (48, Fraction(0), Fraction(1, 2)),      # nula nije dio
    (48, Fraction(3, 2), Fraction(1, 2)),   # razlomak veći od cjeline
    (48, Fraction(1, 2), Fraction(-1, 2)),  # negativan dio
])
def test_fraction_of_fraction_rejects_invalid_instances(total, first, second):
    with pytest.raises(WordProblemError):
        wordfacts.solve(facts_of_fraction(total, first, second))


@pytest.mark.parametrize("total,fractions,expected", [
    (24, (Fraction(1, 3), Fraction(1, 4), Fraction(1, 6)), 6),
    (12, (Fraction(1, 3), Fraction(1, 4)), 5),
    (8, (Fraction(1, 8), Fraction(1, 2), Fraction(1, 4)), 1),
])
def test_multi_fraction_remainder_is_exact(total, fractions, expected):
    solved = wordfacts.solve(facts_multi(total, *fractions))
    assert solved.answer.value == Fraction(expected)
    assert solved.auxiliary["taken"] == Fraction(total) - Fraction(expected)


@pytest.mark.parametrize("total,fractions", [
    (24, (Fraction(1, 3),)),                              # samo jedan dio
    (24, (Fraction(1, 2), Fraction(1, 2))),               # ostatak nula
    (24, (Fraction(2, 3), Fraction(1, 2))),               # premašuje cjelinu
    (10, (Fraction(1, 3), Fraction(1, 4))),               # dijelovi nisu cijeli
    (24, (Fraction(1, 3), Fraction(0))),                  # nula nije dio
])
def test_multi_fraction_remainder_rejects_invalid_instances(total, fractions):
    with pytest.raises(WordProblemError):
        wordfacts.solve(facts_multi(total, *fractions))


def test_the_two_new_types_are_registered_and_distinct():
    assert set(NEW) <= wordfacts.SUPPORTED_TYPES
    # Uzastopno uklanjanje NIJE dodano — nema kurikularnog dokaza.
    assert "successive_fraction_remainder" not in wordfacts.SUPPORTED_TYPES
    # Isti brojevi, RAZLIČITA struktura → različit odgovor.
    of_fraction = wordfacts.solve(
        facts_of_fraction(24, Fraction(1, 2), Fraction(1, 3))).answer.value
    multi = wordfacts.solve(
        facts_multi(24, Fraction(1, 2), Fraction(1, 3))).answer.value
    assert of_fraction == 4 and multi == 4      # slučajno isti broj…
    assert wordfacts.solve(
        facts_of_fraction(36, Fraction(1, 2), Fraction(1, 3))).answer.value == 6
    assert wordfacts.solve(
        facts_multi(36, Fraction(1, 2), Fraction(1, 3))).answer.value == 6


def test_solver_never_parses_prose():
    source = (ROOT / "matbot" / "mathkernel" / "wordfacts.py").read_text(
        encoding="utf-8")
    for forbidden in ("re.compile", "re.search", "re.findall", "split()"):
        assert forbidden not in source, forbidden


# ---------------------------------------------------------------------------
# 2) REKONSTRUKCIJA IZ POTPISA (bez proze)
# ---------------------------------------------------------------------------

def test_solve_from_parameters_rebuilds_the_answer():
    assert wordfacts.solve_from_parameters("fraction_of_fraction", {
        "type": "fraction_of_fraction", "total": "48",
        "first_fraction": "2/3", "second_fraction": "1/4"}) == 8
    assert wordfacts.solve_from_parameters("multi_fraction_remainder", {
        "type": "multi_fraction_remainder", "total": "24",
        "fraction_1": "1/3", "fraction_2": "1/4", "fraction_3": "1/6"}) == 6


@pytest.mark.parametrize("parameters", [
    {"total": "48"},                                   # nedostaju razlomci
    {"total": "x", "first_fraction": "2/3", "second_fraction": "1/4"},
    {},
])
def test_solve_from_parameters_fails_closed(parameters):
    with pytest.raises(WordProblemError):
        wordfacts.solve_from_parameters("fraction_of_fraction", parameters)


# ---------------------------------------------------------------------------
# 3) UGOVOR I OBIM
# ---------------------------------------------------------------------------

def test_contract_enum_grew_only_for_the_target_lesson():
    compiled = json.loads(
        (ROOT / "data" / "lesson_semantics.compiled.json").read_text(
            encoding="utf-8"))["lessons"]
    assert tuple(compiled[LESSON]["parameters"]["problem_types"]) == EXPECTED_ENUM
    siblings = [lesson_id for lesson_id, entry in compiled.items()
                if entry["family_id"] == "structured_word_problem"
                and lesson_id != LESSON]
    assert len(siblings) >= 5
    for lesson_id in siblings:
        types = set(compiled[lesson_id]["parameters"]["problem_types"])
        assert not (types & set(NEW)), lesson_id


def test_each_allowed_archetype_has_a_definition_for_the_model():
    contract = semantic_contracts.contract_for(LESSON)
    definitions = dict(contract.archetype_definitions)
    assert set(definitions) == set(EXPECTED_ENUM)
    for name, meaning in definitions.items():
        assert len(meaning) > 30, name          # stvarna definicija, ne eho


def test_escalation_block_carries_definitions_and_required_facts():
    from matbot import difficulty_level
    context = lesson_context.build(6, LESSON)
    decision = esc.decide(context, {"difficulty_level": 3,
                                    "recent_task_signatures": []},
                          "harder_task", difficulty_level.transition(3, "harder"))
    block = esc.prompt_block(decision)
    for name in EXPECTED_ENUM:
        assert name in block, name
    assert "← CILJ" in block
    assert "normalized_parameters" in block
    for field in wordfacts.REQUIRED_FACTS[decision.target_archetype]:
        assert field in block, field


# ---------------------------------------------------------------------------
# 4) DETERMINISTIČKI GENERATORI
# ---------------------------------------------------------------------------

def generate(problem_type, level, seed):
    module = det.GENERATORS["structured_word_problem"]
    return module.generate_package(LESSON, TITLE,
                                   {"problem_types": [problem_type]}, level,
                                   rng=random.Random(seed))


@pytest.mark.parametrize("problem_type", NEW)
@pytest.mark.parametrize("level", (1, 2, 3))
def test_new_generators_are_exact_and_integral(problem_type, level):
    for seed in range(60):
        package = generate(problem_type, level, seed)
        signature = dict(package.signature_parameters)
        assert signature["type"] == problem_type
        truth = wordfacts.solve_from_parameters(problem_type, signature)
        assert truth.denominator == 1                     # broje se predmeti
        values = [Fraction(text.strip("$")) for text in package.option_texts]
        assert values[package.correct_index] == truth
        assert sum(1 for value in values if value == truth) == 1
        assert len(set(package.option_texts)) == 4


@pytest.mark.parametrize("problem_type", NEW)
def test_new_generators_carry_full_facts_in_the_signature(problem_type):
    package = generate(problem_type, 3, 5)
    signature = dict(package.signature_parameters)
    for field in wordfacts.REQUIRED_FACTS[problem_type]:
        assert field in signature, field


def test_multi_fraction_remainder_scales_parts_with_level():
    def part_count(level):
        counts = set()
        for seed in range(40):
            signature = dict(generate("multi_fraction_remainder", level, seed)
                             .signature_parameters)
            counts.add(sum(1 for name in signature if name.startswith("fraction_")))
        return counts
    assert part_count(1) == {2}
    assert part_count(3) == {3}


def test_fraction_of_fraction_prose_never_mentions_a_number_outside_the_facts():
    """RENDER-AUDIT porodice već to čuva; ovdje se zaključava za novi tip."""
    for level in (1, 2, 3):
        for seed in range(40):
            package = generate("fraction_of_fraction", level, seed)
            signature = dict(package.signature_parameters)
            assert str(signature["total"]) in package.question


# ---------------------------------------------------------------------------
# 5) ŽIVI NACRTI POD NOVIM UGOVOROM
# ---------------------------------------------------------------------------

def test_live_draft_three_becomes_a_valid_fraction_of_fraction():
    assert wordfacts.solve(
        facts_of_fraction(48, Fraction(2, 3), Fraction(1, 4))).answer.value == 8


def test_live_draft_two_becomes_a_valid_multi_fraction_remainder():
    assert wordfacts.solve(
        facts_multi(24, Fraction(1, 3), Fraction(1, 4),
                    Fraction(1, 6))).answer.value == 6


def test_live_draft_one_stays_outside_the_enum():
    """48 → 3/4 → 2/3 → oduzmi 5: dodatno oduzimanje cijelog broja nije
    nijedan server-definisan tip, pa arhetip daje 24, a nacrt je tvrdio 19."""
    assert wordfacts.solve(
        facts_of_fraction(48, Fraction(3, 4), Fraction(2, 3))).answer.value == 24
    # nema tipa koji bi taj graf opisao
    assert "fraction_of_fraction_then_subtract" not in wordfacts.SUPPORTED_TYPES


# ---------------------------------------------------------------------------
# 6) PLANER NAD PROŠIRENIM ENUMOM
# ---------------------------------------------------------------------------

def test_planner_rotates_all_four_archetypes_without_avoidable_repeats():
    supported = EXPECTED_ENUM
    history, avoidable = [], 0
    for _ in range(500):
        target = esc.select_target(supported, tuple(history[-esc.RECENT_WINDOW:]))
        if history and target == history[-1]:
            avoidable += 1
        history.append(target)
    assert avoidable == 0
    assert set(history) == set(supported)


def test_planner_ignores_polluted_history_with_the_wider_enum():
    session = {"recent_task_signatures": [
        {"lesson_id": LESSON, "structured_signature": json.dumps(
            {"operation_or_relation": "fraction_remainder"})},
        {"lesson_id": LESSON, "structured_signature": json.dumps(
            {"operation_or_relation": "successive subtraction of fractions"})},
        {"lesson_id": LESSON, "structured_signature": json.dumps(
            {"operation_or_relation": "fraction_of_quantity"})},
    ]}
    recent = esc.recent_archetypes(session, LESSON, supported=EXPECTED_ENUM)
    assert recent == ("fraction_remainder", "fraction_of_quantity")
    assert esc.select_target(EXPECTED_ENUM, recent) == "fraction_of_fraction"
