"""PROMPT I VALIDATOR TEŽINE MORAJU GOVORITI ISTO (arhitektonska Faza 1).

ZAŠTO POSTOJI. `_TARGET_LEVEL_RULE` je do Faze 1 nosio DVIJE nespojive rečenice
o istom nivou, dodane dan za danom kao odgovori na dva različita živa pada:

    00bbd45 (2026-08-04)  „…One reasoning step, one condition, one operation,
                            no representation change.“
    24d629f (2026-08-05)  „Level 1 tolerates one change of representation and
                            up to two connected operations…“

Mjerodavan je `GLOBAL_LEVEL1_MAX` (`operation_count <= 2`,
`representation_change_count <= 1`) — ista konstanta iz koje se renderuje
`shared_target_block()` i po kojoj sudi `difficulty_evidence_errors`. Prva
rečenica je dakle modelu slala prag STROŽI od presude, u istom pasusu s tačnim
pragom: zadatak s dvije povezane operacije na nivou 1 je bio istovremeno
zabranjen prompt-om i dozvoljen validatorom.

Faza 1 je uklonila SAMO ta dva nadvladana ograničenja. Ovaj fajl dokazuje da:
  • uklonjeno više nigdje ne stiže modelu;
  • mjerodavni prag stiže OBAMA pozivima, doslovno isti;
  • ponašanje validatora, granice nivoa i progresija su NEPROMIJENJENI;
  • lekcijski-relativni profil se i dalje renderuje i i dalje dolazi POSLIJE
    globalnog praga (profil ga po ugovoru zamjenjuje).

ZERO poziva modela, ZERO mreže. Nijedan validator se u Fazi 1 ne dira.
"""
from __future__ import annotations

import pytest

from matbot import difficulty_profiles
from matbot.difficulty_target import shared_target_block
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor.lesson_context import build
from matbot.tutor.pipeline import _target_level_for
from matbot.tutor.schema import (DifficultyEvidence, GLOBAL_LEVEL1_MAX,
                                 GLOBAL_LEVEL2_FLOORS, GLOBAL_LEVEL2_MAX,
                                 GLOBAL_LEVEL3_FLOORS, difficulty_evidence_errors)

PLAIN_GRADE, PLAIN_TOPIC = 9, "9-02-006"      # lekcija bez profila
PROFILED_GRADE, PROFILED_TOPIC = 6, "6-08-002"  # lekcija s profilom


def _evidence(**overrides):
    values = dict(reasoning_steps=1, condition_count=1, operation_count=1,
                  representation_change_count=0, requires_explanation=False,
                  requires_comparison=False, requires_construction=False,
                  requires_proof_or_justification=False, combines_concepts=False)
    values.update(overrides)
    return DifficultyEvidence(**values)


def _prompts(grade=PLAIN_GRADE, topic=PLAIN_TOPIC):
    context = build(grade, topic)
    return (tutor_prompts.build_tutor_instructions(context),
            tutor_prompts.build_reviewer_instructions(context))


# ---------------------------------------------------------------------------
# 1. JEDAN AUTORITATIVAN NUMERIČKI IZVOR, ISTI ZA OBA POZIVA
# ---------------------------------------------------------------------------

def test_both_calls_receive_the_identical_authoritative_target_block():
    tutor, reviewer = _prompts()
    block = shared_target_block()
    assert block in tutor
    assert block in reviewer


def test_the_authoritative_block_is_rendered_from_the_validator_constants():
    block = shared_target_block()
    for field, cap in GLOBAL_LEVEL1_MAX.items():
        assert f"{field} <= {cap}" in block
    for field, floor in GLOBAL_LEVEL2_FLOORS.items():
        assert f"{field} >= {floor}" in block
    for field, cap in GLOBAL_LEVEL2_MAX.items():
        assert f"{field} <= {cap}" in block
    for field, floor in GLOBAL_LEVEL3_FLOORS.items():
        assert f"{field} >= {floor}" in block


def test_both_calls_receive_the_identical_counting_semantics():
    tutor, reviewer = _prompts()
    assert "HOW TO COUNT DIFFICULTY EVIDENCE" in tutor
    assert "HOW TO COUNT DIFFICULTY EVIDENCE" in reviewer


# ---------------------------------------------------------------------------
# 2. NADVLADANI PRAG VIŠE NIGDJE NE STIŽE MODELU
# ---------------------------------------------------------------------------

def test_the_superseded_operation_cap_is_gone_from_every_shipped_prompt():
    """Autoritativni maksimum je 2 — nijedan prompt ne smije tvrditi 1."""
    assert GLOBAL_LEVEL1_MAX["operation_count"] == 2
    tutor, reviewer = _prompts()
    assert "one operation" not in tutor.lower()
    assert "one operation" not in reviewer.lower()


def test_the_superseded_representation_cap_is_gone_from_every_shipped_prompt():
    """Autoritativni maksimum je 1 — nijedan prompt ne smije tvrditi 0."""
    assert GLOBAL_LEVEL1_MAX["representation_change_count"] == 1
    tutor, reviewer = _prompts()
    assert "no representation change" not in tutor.lower()
    assert "no representation change" not in reviewer.lower()


def test_the_agreeing_half_of_the_same_sentence_was_deliberately_kept():
    """`one reasoning step` i `one condition` se POKLAPAJU s mjerodavnim
    granicama, pa nisu uklonjeni — uklonjeno je samo ono što protivrječi."""
    assert GLOBAL_LEVEL1_MAX["reasoning_steps"] == 1
    assert GLOBAL_LEVEL1_MAX["condition_count"] == 1
    tutor, _reviewer = _prompts()
    assert "One reasoning step and one condition." in tutor


def test_the_qualitative_permission_from_24d629f_still_ships():
    tutor, _reviewer = _prompts()
    assert ("Level 1 tolerates one change of representation and up to two "
            "connected operations") in tutor


# ---------------------------------------------------------------------------
# 3. VALIDATOR JE NEPROMIJENJEN — I PROMPT SE SADA S NJIM POKLAPA
# ---------------------------------------------------------------------------

def test_level_one_accepts_exactly_what_the_authoritative_block_permits():
    """Ovo je slučaj koji je uklonjena rečenica ZABRANJIVALA, a validator
    dozvoljava: dvije povezane operacije i jedna promjena zapisa."""
    assert difficulty_evidence_errors(
        _evidence(operation_count=2, representation_change_count=1), 1) == ()


@pytest.mark.parametrize("overrides", (
    {"operation_count": 3},
    {"representation_change_count": 2},
    {"reasoning_steps": 2},
    {"condition_count": 2},
    {"requires_construction": True},
    {"requires_proof_or_justification": True},
    {"combines_concepts": True},
))
def test_level_one_boundaries_are_unchanged(overrides):
    assert difficulty_evidence_errors(_evidence(**overrides), 1) != ()


def test_level_two_and_three_validation_is_unchanged():
    assert difficulty_evidence_errors(_evidence(reasoning_steps=2, operation_count=2), 2) == ()
    assert difficulty_evidence_errors(_evidence(), 2) != ()          # ispod poda
    assert difficulty_evidence_errors(
        _evidence(reasoning_steps=3, condition_count=3, operation_count=3), 3) == ()
    assert difficulty_evidence_errors(
        _evidence(requires_proof_or_justification=True, reasoning_steps=1), 3) == ()


def test_levels_one_two_and_three_all_assemble_in_the_shipped_prompt():
    tutor, reviewer = _prompts()
    for level in ("Level 1", "Level 2", "Level 3"):
        assert level in tutor, level
        assert level in reviewer, level


# ---------------------------------------------------------------------------
# 4. PROGRESIJA JE NEPROMIJENJENA (server-vlasnička, bez prompta)
# ---------------------------------------------------------------------------

def _session(**overrides):
    values = dict(difficulty_level=1, current_task="aktivan zadatak",
                  correct_streak=0, current_task_had_hint=False, last_result="")
    values.update(overrides)
    return values


@pytest.mark.parametrize("intent,session_overrides,expected", (
    ("generate_task", {"current_task": ""}, 1),
    ("harder_task", {"difficulty_level": 1}, 2),
    ("harder_task", {"difficulty_level": 3}, 3),
    ("easier_task", {"difficulty_level": 2}, 1),
    ("easier_task", {"difficulty_level": 1}, 1),
    ("next_task", {"difficulty_level": 1}, 1),
    ("next_task", {"difficulty_level": 1, "correct_streak": 2}, 2),
    ("next_task", {"difficulty_level": 2, "last_result": "full_solution"}, 1),
    ("next_task", {"difficulty_level": 2, "correct_streak": 2,
                   "current_task_had_hint": True}, 2),
))
def test_progression_policy_is_unchanged(intent, session_overrides, expected):
    assert _target_level_for(_session(**session_overrides), intent) == expected


def test_the_explicit_compound_request_floor_is_unchanged():
    """Faza 4G: poruka koja sama traži složen uslov diže cilj na 2."""
    message = "Daj mi zadatak gdje broj mora biti djeljiv i sa 6 i sa 25."
    assert _target_level_for(_session(current_task=""), "generate_task", message) == 2
    # Na koraku težine floor se NIKAD ne primjenjuje.
    assert _target_level_for(_session(difficulty_level=2), "easier_task", message) == 1


# ---------------------------------------------------------------------------
# 5. LEKCIJSKI-RELATIVAN PROFIL SE I DALJE RENDERUJE
# ---------------------------------------------------------------------------

def test_a_lesson_relative_profile_still_reaches_both_calls():
    context = build(PROFILED_GRADE, PROFILED_TOPIC)
    profile = difficulty_profiles.resolve_for_context(context)
    assert profile is not None
    block = profile.prompt_block()
    tutor, reviewer = _prompts(PROFILED_GRADE, PROFILED_TOPIC)
    assert block in tutor
    assert block in reviewer


def test_the_profile_block_follows_the_global_target_block():
    """Ugovor: profil ZAMJENJUJE globalne pragove, pa mora doći poslije njih."""
    tutor, reviewer = _prompts(PROFILED_GRADE, PROFILED_TOPIC)
    profile = difficulty_profiles.resolve_for_context(
        build(PROFILED_GRADE, PROFILED_TOPIC))
    for text in (tutor, reviewer):
        assert text.find(shared_target_block()) < text.find(profile.prompt_block())
    assert "LESSON-RELATIVE DIFFICULTY PROFILE" in shared_target_block()


def test_a_lesson_without_a_profile_still_gets_only_the_global_target():
    context = build(PLAIN_GRADE, PLAIN_TOPIC)
    assert difficulty_profiles.resolve_for_context(context) is None
    tutor, _reviewer = _prompts()
    assert shared_target_block() in tutor
