"""Šema ugovora, nasljeđivanje i granice težine (kategorije 1–6, 20–24, 39–41)."""
import json
from pathlib import Path

import pytest

from matbot.contracts import difficulty, evidence as ev, pipeline, registry, schema

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = {
    key: value
    for key, value in json.loads(
        (ROOT / "data" / "contract_templates.json").read_text(encoding="utf-8")
    ).items()
    if not key.startswith("_")
}

MINIMAL = {
    "canonical_topic_id": "6-99-001",
    "grade": 6,
    "status": "enabled",
    "inherits": "rational.add_sub",
    "skill": "test_skill",
    "operand_constraints": {"denominator_relation": "equal", "sign_policy": "non_negative"},
    "allowed_task_archetypes": ["direct_computation"],
}


def build(**overrides):
    row = dict(MINIMAL)
    row.update(overrides)
    return schema.resolve_and_build(row, TEMPLATES)


# --- 1-6: šema i nasljeđivanje ---------------------------------------------

def test_1_valid_contract_loads():
    contract = build()
    assert contract.canonical_topic_id == "6-99-001"
    assert contract.domain == "rational_numbers"          # naslijeđeno
    assert contract.allowed_operations == ("add", "subtract")
    assert contract.effective_archetypes == ("direct_computation",)


def test_2_invalid_enum_rejects():
    with pytest.raises(schema.ContractSchemaError):
        build(domain="kuhanje")


def test_3_missing_verifier_rejects():
    row = dict(MINIMAL, inherits="", domain="rational_numbers",
               contract_version="1")
    with pytest.raises(schema.ContractSchemaError, match="answer_verifier"):
        schema.resolve_and_build(row, TEMPLATES)


def test_3_removed_required_evidence_field_is_rejected_loudly():
    """Uklonjeno polje ne smije tiho značiti „bez uslova“ — stari red s
    required_evidence pada s jasnom porukom, ne kao nepoznat tipfeler."""
    with pytest.raises(schema.ContractSchemaError, match="uklonjena"):
        build(required_evidence=["rational_expression"])


def test_4_unsupported_contract_does_not_fall_back():
    contract = build(status="unsupported")
    assert registry.practice_state(contract) == registry.STATE_UNAVAILABLE


def test_4_needs_review_uses_legacy_and_is_reported_separately():
    contract = build(status="needs_review")
    assert registry.practice_state(contract) == registry.STATE_LEGACY
    assert contract.status == "needs_review"          # nikad stopljeno s "bez reda"


def test_4_legacy_pinned_requires_an_audited_reason():
    with pytest.raises(schema.ContractSchemaError, match="pinned_reason"):
        build(status="legacy_pinned")
    pinned = build(status="legacy_pinned", pinned_reason="defekt X, vidi docs",
                   pinned_at="2026-08-02")
    assert registry.practice_state(pinned) == registry.STATE_LEGACY


def test_5_contract_inheritance_resolves_deterministically():
    first, second = build(), build()
    assert first == second
    assert first.difficulty_dimensions["term_count"].minimum == 2   # iz rational.add_sub


@pytest.mark.parametrize("overrides", [
    {"allowed_operations": ["add", "multiply"]},          # operacija van roditelja
    {"allowed_task_archetypes": ["direct_computation", "identify_equivalent"]},
    {"operand_types": ["fraction", "integer"]},           # tip operanda van roditelja
])
def test_6_conflicting_inherited_constraints_reject(overrides):
    with pytest.raises(schema.ContractConflictError, match="proširuje"):
        build(**overrides)


def test_6_child_cannot_relax_a_constraint_to_any():
    child = dict(MINIMAL, inherits="rational.equivalence",
                 allowed_task_archetypes=["identify_equivalent"],
                 operand_constraints={"scaling_direction": "any"})
    parent = dict(TEMPLATES["rational.equivalence"])
    parent["operand_constraints"] = {"scaling_direction": "expand"}
    templates = dict(TEMPLATES, **{"rational.equivalence": parent})
    with pytest.raises(schema.ContractConflictError, match="olabavljuje"):
        schema.resolve_and_build(child, templates)


def test_6_inheritance_cycle_is_detected():
    templates = dict(TEMPLATES, loop_a={"inherits": "loop_b"}, loop_b={"inherits": "loop_a"})
    with pytest.raises(schema.ContractSchemaError, match="ciklus"):
        schema.resolve_and_build(dict(MINIMAL, inherits="loop_a"), templates)


# --- 20-24: težina ----------------------------------------------------------

def _facts(*literals, operations=()):
    class _F:
        max_abs_operand = max((abs(v) for pair in literals for v in pair), default=0)
        term_count = len(literals)
    return _F()


def test_20_harder_changes_only_permitted_dimensions():
    contract = registry.contract_for("6-04-009")
    default = difficulty.target_levels(contract)
    harder = difficulty.target_levels(contract, "harder")
    changed = {k for k in default if default[k] != harder[k]}
    assert changed <= set(difficulty.adjustable_dimensions(contract))
    assert changed, "teže mora stvarno pomjeriti bar jednu dimenziju"


def test_21_harder_cannot_alter_an_invariant_constraint():
    contract = registry.contract_for("6-04-009")
    assert "denominator_relation" in contract.invariant_constraints
    assert difficulty.invariant_conflicts(contract) == []
    # Ograničenje se primjenjuje bez obzira na traženu težinu.
    for request in ("", "harder", "easier"):
        difficulty.target_levels(contract, request)
        assert contract.constraint("denominator_relation") == "equal"


def test_22_easier_preserves_lesson_identity():
    contract = registry.contract_for("6-04-011")
    primary = contract.effective_archetypes[0]
    assert pipeline.select_archetype(contract, difficulty_request="easier") == primary
    assert contract.skill == "multiply_fractions"


def test_23_difficulty_bounds_are_enforced():
    """Lekcija koja deklariše SAMO najniži nivo veličine mora odbiti veće brojeve."""
    contract = build(difficulty_dimensions={
        "operand_magnitude": {"min": 1, "max": 1, "default": 1},
        "term_count": {"min": 2, "max": 2, "default": 2},
    })
    small = ev.parse({
        "kind": "rational_expression",
        "expression": {"op": "add", "args": [{"num": 2, "den": 7}, {"num": 3, "den": 7}]},
    })
    assert difficulty.check_within_bounds(contract, small.principal_facts()).valid

    large = ev.parse({
        "kind": "rational_expression",
        "expression": {"op": "add", "args": [{"num": 480, "den": 7}, {"num": 3, "den": 7}]},
    })
    result = difficulty.check_within_bounds(contract, large.principal_facts())
    assert not result.valid
    assert result.code == "difficulty_out_of_bounds"
    assert result.details["dimension"] == "operand_magnitude"


def test_23_operand_range_is_enforced_independently_of_difficulty():
    """Opseg brojeva je ograničenje LEKCIJE, ne dimenzija težine — ista
    generička provjera (constraints.check_evidence) štiti i serversku
    konstrukciju kad bi generator ikad izašao iz opsega."""
    from matbot.contracts import constraints
    contract = registry.contract_for("6-04-009")
    out_of_range = ev.parse_node(
        {"op": "add", "args": [{"num": 480, "den": 7}, {"num": 3, "den": 7}]}
    )
    result = constraints.check_evidence(contract, ev.facts_for((out_of_range,)))
    assert not result.valid
    assert result.code == "operand_out_of_range"


def test_23_harder_never_exceeds_the_declared_maximum():
    contract = registry.contract_for("6-04-009")
    levels = difficulty.target_levels(contract, "harder")
    for name, level in levels.items():
        bound = contract.difficulty_dimensions[name]
        assert bound.minimum <= level <= bound.maximum


def test_24_same_contract_version_keeps_the_fingerprint_stable():
    assert registry.contract_version_for("6-04-009") == "1"
    assert registry.contract_version_for("6-04-010") == "1"
    assert registry.contract_version_for("6-04-014") == ""     # bez ugovora


# --- 39-41: migracija -------------------------------------------------------

def test_39_every_enabled_contract_has_a_verifier_and_generated_archetypes():
    from matbot.contracts import generator
    for contract in registry.load_all().values():
        assert contract.answer_verifier
        assert contract.effective_archetypes
        # Svaki efektivni arhetip UKLJUČENOG ugovora mora imati serverski
        # generator — obećanje oblika bez generatora pada na učitavanju.
        assert set(contract.effective_archetypes) <= generator.IMPLEMENTED_ARCHETYPES


def test_41_no_lesson_silently_inherits_fraction_expansion():
    """Nijedna lekcija ne smije dobiti smjer skaliranja koji nije sama tražila."""
    for topic_id, contract in registry.load_all().items():
        direction = contract.constraint("scaling_direction", "any")
        if direction != "any":
            assert contract.skill in ("expand_fraction", "reduce_fraction"), topic_id
        else:
            assert "identify_equivalent" not in contract.effective_archetypes, topic_id
