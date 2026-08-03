"""Generički motor (serverski generator kostura): aritmetika kroz domene,
arhetipi i semantika opcija.

Iste garancije proizvoda kao i u ranijoj fazi — dozvoljene operacije, odnos
imenilaca, znak, opseg, TAČNO jedna tačna opcija, ispravan označeni indeks —
ali dokazane nad SERVERSKOM konstrukcijom (matbot/contracts/generator.py), ne
nad modelovim dokazom (taj smjer je ukinut nakon Live96). Sve što se ovdje
dokazuje mora važiti BEZ ijedne grane po lekciji.
"""
import json
import random
from fractions import Fraction
from math import gcd
from pathlib import Path

import pytest

from matbot.contracts import (constraints, difficulty, evidence as ev,
                              generator, pipeline, registry, schema, verifiers)

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = {
    key: value
    for key, value in json.loads(
        (ROOT / "data" / "contract_templates.json").read_text(encoding="utf-8")
    ).items()
    if not key.startswith("_")
}
CONTRACTS = registry.load_all()

# Sintetički cjelobrojni ugovor: ISTI motor mora opsluživati i cijele brojeve.
INTEGER_ROW = {
    "canonical_topic_id": "7-99-001", "grade": 7, "status": "enabled",
    "inherits": "arithmetic", "skill": "add_integers",
    "allowed_operations": ["add", "subtract"],
    "allowed_task_archetypes": ["direct_computation"],
    "operand_constraints": {"sign_policy": "non_negative", "integer_range": [1, 99]},
}
INTEGERS = schema.resolve_and_build(INTEGER_ROW, TEMPLATES)

SEEDS = range(20)


def build(topic_or_contract, seed=0, difficulty_request="", archetype=None):
    contract = (CONTRACTS[topic_or_contract]
                if isinstance(topic_or_contract, str) else topic_or_contract)
    archetype = archetype or contract.effective_archetypes[0]
    return contract, generator.generate(
        contract, archetype, difficulty_request=difficulty_request,
        rng=random.Random(seed),
    )


def option_values(skeleton):
    return [ev.evaluate(node) for node in skeleton.option_nodes]


# --- 7-13: generički aritmetički ugovori ------------------------------------

@pytest.mark.parametrize("seed", SEEDS)
def test_7_generated_operations_respect_the_contract(seed):
    for topic in ("6-04-009", "6-04-010", "6-04-011", "6-04-012"):
        contract, skeleton = build(topic, seed)
        facts = ev.facts_for(skeleton.primary_nodes)
        assert facts.operations <= set(contract.allowed_operations), topic


def test_8_multiplication_and_division_differ_by_one_contract_value():
    # Razlika između te dvije lekcije je JEDNA vrijednost ugovora, ne kod.
    assert CONTRACTS["6-04-011"].allowed_operations == ("multiply",)
    assert CONTRACTS["6-04-012"].allowed_operations == ("divide",)
    _, multiply = build("6-04-011", 3)
    _, divide = build("6-04-012", 3)
    assert ev.facts_for(multiply.primary_nodes).operations == {"multiply"}
    assert ev.facts_for(divide.primary_nodes).operations == {"divide"}


@pytest.mark.parametrize("seed", SEEDS)
def test_9_equal_denominator_constraint_is_generic(seed):
    contract, skeleton = build("6-04-009", seed)
    assert contract.constraint("denominator_relation") == "equal"
    pairs = ev.facts_for(skeleton.primary_nodes).binary_denominator_pairs
    assert pairs and all(left == right for left, right in pairs)


@pytest.mark.parametrize("seed", SEEDS)
def test_10_different_denominator_constraint_is_generic(seed):
    contract, skeleton = build("6-04-010", seed)
    assert contract.constraint("denominator_relation") == "different"
    pairs = ev.facts_for(skeleton.primary_nodes).binary_denominator_pairs
    assert pairs and any(left != right for left, right in pairs)


@pytest.mark.parametrize("seed", SEEDS)
def test_13_integer_and_fraction_contracts_use_the_same_engine(seed):
    contract, skeleton = build(INTEGERS, seed)
    facts = ev.facts_for(skeleton.primary_nodes)
    assert facts.operations <= {"add", "subtract"}
    assert skeleton.truth == ev.evaluate(skeleton.primary_nodes[0])
    assert INTEGERS.answer_verifier == CONTRACTS["6-04-009"].answer_verifier


@pytest.mark.parametrize("seed", SEEDS)
def test_13_grade6_sign_policy_never_produces_negative_operands(seed):
    for topic in ("6-04-009", "6-04-010", "6-04-011", "6-04-012"):
        _, skeleton = build(topic, seed)
        facts = ev.facts_for(skeleton.primary_nodes)
        assert facts.min_value >= 0, topic
        assert skeleton.truth >= 0, topic


@pytest.mark.parametrize("seed", SEEDS)
def test_13_operand_range_is_respected(seed):
    for topic in sorted(CONTRACTS):
        contract, skeleton = build(topic, seed)
        low, high = contract.constraint("integer_range")
        facts = ev.facts_for(skeleton.primary_nodes)
        assert facts.max_abs_operand <= high, topic


# --- 14-16: univerzalni arhetipi --------------------------------------------

@pytest.mark.parametrize("seed", SEEDS)
def test_14_direct_computation_truth_is_computed_from_the_expression(seed):
    for topic in ("6-04-009", "6-04-010", "6-04-011", "6-04-012"):
        _, skeleton = build(topic, seed)
        assert skeleton.truth == ev.evaluate(skeleton.primary_nodes[0]), topic


@pytest.mark.parametrize("seed", SEEDS)
def test_16_identify_equivalent_works_for_expansion_and_reduction(seed):
    _, expand = build("6-04-005", seed)
    _, reduce_ = build("6-04-006", seed)
    # Tačan zapis ima istu VRIJEDNOST kao referenca, ali drugi ZAPIS.
    for skeleton in (expand, reduce_):
        answer = skeleton.option_nodes[skeleton.correct_index]
        reference = skeleton.reference
        assert Fraction(answer.num, answer.den) == Fraction(reference.num, reference.den)
        assert (answer.num, answer.den) != (reference.num, reference.den)
    # Smjer skaliranja dolazi iz ugovora, bez grane po lekciji.
    expand_answer = expand.option_nodes[expand.correct_index]
    assert abs(expand_answer.num) > abs(expand.reference.num)
    reduce_answer = reduce_.option_nodes[reduce_.correct_index]
    assert abs(reduce_answer.num) < abs(reduce_.reference.num)


@pytest.mark.parametrize("seed", SEEDS)
def test_16_reduction_answer_is_irreducible(seed):
    contract, skeleton = build("6-04-006", seed)
    assert contract.representation("answer_form") == "irreducible"
    answer = skeleton.option_nodes[skeleton.correct_index]
    assert gcd(abs(answer.num), abs(answer.den)) == 1


def test_18_unimplemented_archetype_fails_closed_without_a_model_call():
    contract = CONTRACTS["6-04-009"]
    with pytest.raises(generator.GenerationError):
        generator.generate(contract, "find_missing_value", rng=random.Random(0))
    plan = pipeline.GenerationPlan("find_missing_value", "rotation")
    prepared = pipeline.prepare_task(contract, plan, rng=random.Random(0))
    assert not prepared.ok
    assert prepared.code == "archetype_not_allowed"


def test_19_generation_failure_is_engaged_false_never_silently_passed():
    """„Nije se moglo konstruisati“ mora biti engaged=False, nikad tiho „prošlo“."""
    impossible = schema.resolve_and_build({
        "canonical_topic_id": "6-99-002", "grade": 6, "status": "enabled",
        "inherits": "rational.add_sub", "skill": "impossible_range",
        "allowed_task_archetypes": ["direct_computation"],
        # Opseg [1, 2] nema dovoljno različitih brojnika za dva razlomka
        # jednakih imenilaca — konstrukcija mora pasti zatvoreno.
        "operand_constraints": {"denominator_relation": "equal",
                                "integer_range": [1, 2]},
    }, TEMPLATES)
    plan = pipeline.GenerationPlan("direct_computation", "rotation")
    prepared = pipeline.prepare_task(impossible, plan, rng=random.Random(0))
    assert not prepared.ok
    stage = prepared.failed_stage
    assert stage.stage == "skeleton_generated"
    assert not stage.engaged
    assert prepared.skeleton is None


def test_19_zero_denominator_is_rejected_at_parse_time():
    with pytest.raises(ev.EvidenceError, match="nazivnik 0"):
        ev.parse_node({"num": 1, "den": 0})


def test_19_expression_depth_and_size_are_bounded():
    deep = {"num": 1, "den": 2}
    for _ in range(ev.MAX_DEPTH + 2):
        deep = {"op": "add", "args": [deep, {"num": 1, "den": 2}]}
    with pytest.raises(ev.EvidenceError):
        ev.parse_node(deep)


# --- 25-30: semantika opcija ------------------------------------------------

@pytest.mark.parametrize("seed", SEEDS)
def test_25_exactly_one_option_matches_the_truth_exactly(seed):
    """Poređenje ide preko Fraction — $\\frac{1}{3}$ nikad ne postaje 0.333…"""
    for topic in sorted(CONTRACTS):
        _, skeleton = build(topic, seed)
        values = option_values(skeleton)
        matches = [i for i, value in enumerate(values) if value == skeleton.truth]
        assert matches == [skeleton.correct_index], topic


@pytest.mark.parametrize("seed", SEEDS)
def test_26_option_values_and_texts_are_pairwise_distinct(seed):
    for topic in sorted(CONTRACTS):
        _, skeleton = build(topic, seed)
        values = option_values(skeleton)
        assert len(set(values)) == len(values), topic
        assert len(set(skeleton.option_texts)) == len(skeleton.option_texts), topic


@pytest.mark.parametrize("seed", SEEDS)
def test_28_marked_index_agrees_with_derived_truth(seed):
    """Isti verifikator kao u ranijoj fazi potvrđuje serversku konstrukciju."""
    for topic in sorted(CONTRACTS):
        contract, skeleton = build(topic, seed)
        result = verifiers.verify_exact_rational(
            skeleton.truth, skeleton.option_nodes, skeleton.correct_index,
            require_distinct_from=skeleton.reference,
        )
        assert result.ok, (topic, result.code)


@pytest.mark.parametrize("seed", SEEDS)
def test_29_expected_answer_text_matches_the_marked_option(seed):
    for topic in sorted(CONTRACTS):
        _, skeleton = build(topic, seed)
        assert skeleton.expected_answer == skeleton.option_texts[skeleton.correct_index], topic


@pytest.mark.parametrize("seed", SEEDS)
def test_30_full_self_verification_passes_for_every_generated_skeleton(seed):
    for topic in sorted(CONTRACTS):
        contract, skeleton = build(topic, seed)
        ok, code = generator.self_verify(contract, skeleton)
        assert ok, (topic, code)


# --- determinizam i ponavljanje ---------------------------------------------

def test_31_generation_is_deterministic_for_a_fixed_seed():
    _, first = build("6-04-009", 12345)
    _, second = build("6-04-009", 12345)
    assert first.question_text == second.question_text
    assert first.option_texts == second.option_texts
    assert first.correct_index == second.correct_index


def test_31_golden_skeleton_stays_stable():
    """Zlatni test: promjena generatora koja mijenja postojeće zadatke mora
    biti NAMJERNA (i vidljiva u diff-u ovog testa)."""
    _, skeleton = build("6-04-009", 42)
    assert skeleton.question_text == "Izračunaj: $\\frac{4}{9} + \\frac{6}{9}$."
    assert skeleton.option_texts[skeleton.correct_index] == "$\\frac{10}{9}$"


def test_32_recent_texts_are_avoided():
    contract = CONTRACTS["6-04-009"]
    _, first = build("6-04-009", 7)
    again = generator.generate(
        contract, "direct_computation", rng=random.Random(7),
        avoid_texts=[first.question_text],
    )
    assert again.question_text != first.question_text


@pytest.mark.parametrize("request_,label", [("harder", "hard"), ("easier", "easy"), ("", "standard")])
def test_33_difficulty_request_changes_only_declared_dimensions(request_, label):
    contract, skeleton = build("6-04-009", 5, difficulty_request=request_)
    assert skeleton.difficulty_label == label
    # Vještina i ograničenja ostaju: ista operacija, isti odnos imenilaca.
    facts = ev.facts_for(skeleton.primary_nodes)
    assert facts.operations <= set(contract.allowed_operations)
    assert constraints.check_evidence(contract, facts).valid
    assert difficulty.check_within_bounds(contract, facts).valid


def test_33_harder_actually_raises_operand_magnitude():
    _, standard = build("6-04-009", 9, difficulty_request="")
    _, harder = build("6-04-009", 9, difficulty_request="harder")
    standard_level = difficulty.derive(ev.facts_for(standard.primary_nodes))["operand_magnitude"]
    harder_level = difficulty.derive(ev.facts_for(harder.primary_nodes))["operand_magnitude"]
    assert harder_level > standard_level


def test_33_easier_has_no_headroom_in_the_current_pilot_data():
    """POZNATO OGRANIČENJE PODATAKA, ne motora.

    Svih šest pilot ugovora ima `default == min` na obje MJERLJIVE dimenzije
    (operand_magnitude 1..3 default 1; term_count min == default), pa „lakše“
    nema šta da spusti i zadatak ostaje na istom nivou — mijenja se samo oznaka
    težine u sesiji. Motor se ponaša ispravno („kad prostora nema, cilj ostaje
    na granici“); to što učenik ne osjeti razliku je posljedica VRIJEDNOSTI u
    data/contract_templates.json.

    Ovaj test NE brani takvo stanje — on ga čini vidljivim: kad se defaulti
    podignu (npr. operand_magnitude default 2), test pada i tjera svjesnu
    odluku o tome šta je „normalna“ težina lekcije."""
    stuck = []
    for topic in sorted(CONTRACTS):
        contract = CONTRACTS[topic]
        base = difficulty.target_levels(contract, "")
        easier = difficulty.target_levels(contract, "easier")
        if base == easier:
            stuck.append(topic)
    assert stuck == sorted(CONTRACTS), (
        "Neka lekcija sada IMA prostora za lakše — provjeri je li to namjerna "
        f"izmjena defaulta i ažuriraj ovaj test: {stuck}"
    )
