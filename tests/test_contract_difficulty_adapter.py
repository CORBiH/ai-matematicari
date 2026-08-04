"""Adapter univerzalnog kontrolera težine za šest lekcija s ugovorom
(matbot/contracts/difficulty.py: capability_for, target_levels_for_level,
verify_matches_target, measurable_target_profile).

ŽIVI NALAZ (audit prije uvođenja ovog adaptera): za SVIH ŠEST trenutno
uključenih ugovora, OBJE mjerljive (DERIVABLE) dimenzije imaju
`minimum == default` — naivno Nivo1→minimum/Nivo2→default/Nivo3→maximum
mapiranje bi Nivo 1 i Nivo 2 učinilo mjerljivo identičnim. Ovaj fajl
regresijski zaključava taj nalaz i dokazuje da adapter to iskreno prijavljuje
umjesto da izmišlja treći nivo koji podaci ne podržavaju.
"""
import random
from fractions import Fraction

import pytest

from matbot.contracts import difficulty as diff
from matbot.contracts import generator, registry, schema
from matbot.contracts.evidence import EvidenceFacts

ENABLED_TOPICS = (
    "6-04-005", "6-04-006", "6-04-009", "6-04-010", "6-04-011", "6-04-012",
)


def _contract(topic_id):
    contract = registry.contract_for(topic_id)
    assert contract is not None, topic_id
    return contract


# ---------------------------------------------------------------------------
# capability_for — regresijski zaključava stvarni audit
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("topic_id", ENABLED_TOPICS)
def test_all_currently_enabled_contracts_are_two_level_today(topic_id):
    """Regresijski zaključava audit nalaz: ako neka buduća izmjena
    data/contract_templates.json ovo promijeni, test namjerno puca umjesto
    tihog drifta."""
    assert diff.capability_for(_contract(topic_id)) == diff.CAPABILITY_TWO_LEVEL


# ---------------------------------------------------------------------------
# target_levels_for_level — Level1==Level2 na mjerljivim dimenzijama danas,
# Level3 stvarno drugačiji, nikad izmišljena treća vrijednost
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("topic_id", ENABLED_TOPICS)
def test_level_1_and_2_share_the_same_measurable_target_today(topic_id):
    contract = _contract(topic_id)
    assert (diff.measurable_target_profile(contract, 1)
            == diff.measurable_target_profile(contract, 2))


@pytest.mark.parametrize("topic_id", ENABLED_TOPICS)
def test_level_3_measurably_exceeds_level_1_and_2(topic_id):
    contract = _contract(topic_id)
    m1 = diff.measurable_target_profile(contract, 1)
    m3 = diff.measurable_target_profile(contract, 3)
    assert m1 != m3
    for name in m1:
        assert m3[name] >= m1[name]


@pytest.mark.parametrize("topic_id", ENABLED_TOPICS)
def test_full_target_dict_does_differ_on_the_non_derivable_dimension(topic_id):
    """distractor_similarity JESTE deklarisana s tri različite vrijednosti —
    puni rječnik se razlikuje 1 vs 2 — ali to se NIKAD ne smije koristiti da
    se tvrdi da je generisan zadatak stvarno teži (vidi generation_changed u
    matbot/practice.py, koristi SAMO measurable_target_profile)."""
    contract = _contract(topic_id)
    full1 = diff.target_levels_for_level(contract, 1)
    full2 = diff.target_levels_for_level(contract, 2)
    assert full1 != full2
    assert full1["distractor_similarity"] != full2["distractor_similarity"]
    # a mjerljiv profil je i dalje identičan:
    assert diff.measurable_target_profile(contract, 1) == diff.measurable_target_profile(contract, 2)


def test_level_2_equals_todays_default_baseline():
    """Level 2 na "two_level" ugovoru mora biti bajt za bajt isto što i
    postojeći target_levels() bez zahtjeva (default) — obje putanje moraju
    ostati usklađene."""
    contract = _contract("6-04-009")
    assert diff.target_levels_for_level(contract, 2) == diff.target_levels(contract, "")


# ---------------------------------------------------------------------------
# Sintetički three_level fixture (NE postoji u topics.json/registru) — dokaz
# da Level1 < Level2 < Level3 STROGO vrijedi čim podaci to genuinski dozvole,
# bez ijedne izmjene generičkog koda.
# ---------------------------------------------------------------------------

_SYNTHETIC_THREE_LEVEL_ROW = {
    "canonical_topic_id": "9-99-997",
    "grade": 7,
    "status": "enabled",
    "inherits": "arithmetic",
    "skill": "add_integers",
    "allowed_operations": ["add"],
    "allowed_task_archetypes": ["direct_computation"],
    "operand_constraints": {"sign_policy": "non_negative", "integer_range": [1, 50]},
    "invariant_constraints": ["allowed_operations"],
    # NAMJERNO min < default < max — ne postoji ni u jednom stvarnom ugovoru
    # danas (audit iznad), samo dokazuje da kod PODRŽAVA tu situaciju.
    "difficulty_dimensions": {"operand_magnitude": {"min": 1, "max": 3, "default": 2}},
}


def _synthetic_three_level_contract():
    from pathlib import Path
    import json

    root = Path(__file__).resolve().parent.parent
    templates = {
        key: value
        for key, value in json.loads(
            (root / "data" / "contract_templates.json").read_text(encoding="utf-8")
        ).items()
        if not key.startswith("_")
    }
    return schema.resolve_and_build(_SYNTHETIC_THREE_LEVEL_ROW, templates)


def test_synthetic_three_level_fixture_is_recognized_as_such():
    assert diff.capability_for(_synthetic_three_level_contract()) == diff.CAPABILITY_THREE_LEVEL


def test_synthetic_fixture_proves_level_1_lt_2_lt_3_strictly():
    contract = _synthetic_three_level_contract()
    m1 = diff.measurable_target_profile(contract, 1)
    m2 = diff.measurable_target_profile(contract, 2)
    m3 = diff.measurable_target_profile(contract, 3)
    assert m1 != m2 != m3 != m1
    assert m1["operand_magnitude"] < m2["operand_magnitude"] < m3["operand_magnitude"]


def test_synthetic_fixture_never_leaks_a_lesson_id_into_the_generic_module():
    # Sama sposobnost postoji generički; podaci žive samo u OVOM test fixtureu.
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    source = (root / "matbot" / "contracts" / "difficulty.py").read_text(encoding="utf-8")
    assert not re.search(r"\b\d-\d{2}-\d{3}\b", source)


# ---------------------------------------------------------------------------
# verify_matches_target — cilja PROFIL, ne samo zakonite granice
# ---------------------------------------------------------------------------

def _facts(max_abs_operand, term_count):
    literal = (max_abs_operand, 1)
    return EvidenceFacts(
        operations=frozenset({"add"}), literals=(literal,) * term_count,
        term_count=term_count, has_hole=False, max_abs_operand=max_abs_operand,
        min_value=Fraction(max_abs_operand, 1), binary_denominator_pairs=(),
    )


def test_matching_profile_passes():
    contract = _contract("6-04-009")
    target = diff.target_levels_for_level(contract, 3)
    # magnitude_level(60) == 3, term_count == target["term_count"]
    facts = _facts(60, target["term_count"])
    result = diff.verify_matches_target(contract, facts, target)
    assert result.valid, result.details


def test_under_difficult_level_3_skeleton_is_rejected():
    """Jezgro nalaza: kostur čije izvedene vrijednosti odgovaraju Nivou 1 dok
    je zatražen Nivo 3 mora biti odbijen — check_within_bounds SAM ovo NE bi
    uhvatio jer je operand_magnitude=1 i dalje unutar zakonitih granica
    [1,3]."""
    contract = _contract("6-04-009")
    target = diff.target_levels_for_level(contract, 3)
    within_bounds_but_wrong_target = _facts(9, target["term_count"])  # magnitude_level(9) == 1

    # check_within_bounds i dalje prolazi (unutar granica) — ostaje autoritativan.
    assert diff.check_within_bounds(contract, within_bounds_but_wrong_target).valid

    result = diff.verify_matches_target(contract, within_bounds_but_wrong_target, target)
    assert not result.valid
    assert result.code == "target_profile_mismatch"
    assert result.details["dimension"] == "operand_magnitude"


def test_non_adjustable_dimensions_are_ignored_by_verify_matches_target():
    contract = _contract("6-04-009")
    target = dict(diff.target_levels_for_level(contract, 3))
    target["distractor_similarity"] = 999  # nemjerljivo — ne smije uticati
    facts = _facts(60, target["term_count"])
    result = diff.verify_matches_target(contract, facts, target)
    assert result.valid, result.details


# ---------------------------------------------------------------------------
# generator.generate — apsolutni cilj, determinizam, netaknut fallback
# ---------------------------------------------------------------------------

def test_generate_with_target_level_hits_the_exact_measurable_profile():
    contract = _contract("6-04-009")
    for level in (1, 2, 3):
        skeleton = generator.generate(
            contract, "direct_computation", target_level=level, rng=random.Random(42)
        )
        assert skeleton.target_levels["operand_magnitude"] == \
            diff.target_levels_for_level(contract, level)["operand_magnitude"]


def test_generate_with_target_level_is_deterministic_for_a_fixed_seed():
    contract = _contract("6-04-009")
    first = generator.generate(contract, "direct_computation", target_level=3, rng=random.Random(42))
    second = generator.generate(contract, "direct_computation", target_level=3, rng=random.Random(42))
    assert first.question_text == second.question_text
    assert first.expected_answer == second.expected_answer


def test_generate_without_target_level_is_untouched_by_this_change():
    """target_level=None (podrazumijevano) mora ostati BAJT ZA BAJT isto kao
    prije uvođenja ovog adaptera — ista rng sekvenca, isti tekst."""
    contract = _contract("6-04-009")
    off = generator.generate(contract, "direct_computation", rng=random.Random(42))
    baseline = generator.generate(
        contract, "direct_computation", difficulty_request="", rng=random.Random(42)
    )
    assert off.question_text == baseline.question_text
    assert off.difficulty_label == baseline.difficulty_label == "standard"


def test_all_target_level_skeletons_still_pass_self_verify():
    contract = _contract("6-04-009")
    for level in (1, 2, 3):
        skeleton = generator.generate(
            contract, "direct_computation", target_level=level, rng=random.Random(42)
        )
        ok, code = generator.self_verify(contract, skeleton)
        assert ok, code
