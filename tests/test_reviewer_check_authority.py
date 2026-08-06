"""Faza 4C — AUTORITET RECENZENTOVIH PROVJERA.

Tri klase padova su se u logovima pojavljivale pod ISTIM imenom
(`reviewer_payload_rejection`), a imaju različite uzroke i traže različit
tretman:

  A) `correct` uz NETAČNU samoprijavljenu provjeru
       F12 (6-04-010): language_age_appropriate=false   → savjetodavna
       B13 (9-05-013): marked_option_correct=false      → SIGURNOSNA
     Zato blanket pravilo („ignoriši svaku false provjeru“) nije dozvoljeno:
     prva smije objaviti, druga NIKAD.

  B) `correct` gdje recenzentov VLASTITI dokaz težine ne zadovoljava nivo koji
     je sam deklarisao — živi gate acd8f5c, lekcija 7-03-006:
       „Koji je veći: 5/8 ili 3/8?“ · steps=1 cond=1 ops=1 repr=0
       requires_comparison=True → level_1_is_not_direct_introductory_application
     Za lekciju o UPOREĐIVANJU minimalan uvodni zadatak nužno traži poređenje,
     pa je pravilo činilo nivo 1 nedostižnim. Ovo je greška RUBRIKE, ne modela.

  C) istorijski gate 0883e8c: `divisibility_rules_not_required_by_visible_task`
     — semantička vjernost, riješena u bd50b9b. NIJE kontradikcija provjera.

Testovi su napisani PRIJE izmjene i koriste TAČNE opažene payloade.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from matbot.tutor import reviewer_authority as authority  # noqa: E402
from matbot.tutor.schema import (ReviewerChecks, UnifiedOutputError,  # noqa: E402
                                 DifficultyEvidence, difficulty_evidence_errors,
                                 validate_reviewer)
from tests.conftest import (make_reviewer_checks, make_reviewer_final,  # noqa: E402
                            make_task_payload, make_tutor_draft)


def evidence(**overrides):
    values = dict(reasoning_steps=1, condition_count=1, operation_count=1,
                  representation_change_count=0, requires_explanation=False,
                  requires_comparison=False, requires_construction=False,
                  requires_proof_or_justification=False, combines_concepts=False)
    values.update(overrides)
    return DifficultyEvidence(**values)


def reviewer(decision="correct", checks_overrides=None, level=1, ev=None,
             final=..., intent="generate_task"):
    checks = make_reviewer_checks(**(checks_overrides or {}))
    if final is ...:
        task = make_task_payload().model_copy(
            update={"target_difficulty_level": level,
                    "difficulty_evidence": ev or evidence()})
        final = make_tutor_draft(intent=intent, new_task=task)
    return make_reviewer_final(decision=decision, final=final, checks=checks,
                               reviewed_difficulty_evidence=ev or evidence())


# ---------------------------------------------------------------------------
# MATRICA AUTORITETA — potpuna i bez preklapanja
# ---------------------------------------------------------------------------

def test_every_boolean_check_has_exactly_one_authority_class():
    booleans = {name for name, field in ReviewerChecks.model_fields.items()
                if field.annotation is bool}
    classified = (authority.MODEL_ONLY_BLOCKING_CHECKS
                  | authority.DETERMINISTIC_AUTHORITY_CHECKS
                  | authority.ADVISORY_CHECKS
                  | authority.STRUCTURAL_CHECKS)
    assert classified == booleans, booleans ^ classified
    pairs = (
        (authority.MODEL_ONLY_BLOCKING_CHECKS, authority.DETERMINISTIC_AUTHORITY_CHECKS),
        (authority.MODEL_ONLY_BLOCKING_CHECKS, authority.ADVISORY_CHECKS),
        (authority.DETERMINISTIC_AUTHORITY_CHECKS, authority.ADVISORY_CHECKS),
        (authority.STRUCTURAL_CHECKS, authority.ADVISORY_CHECKS),
    )
    for left, right in pairs:
        assert not (left & right), (left & right)


def test_every_deterministic_authority_check_names_its_validator():
    for name in authority.DETERMINISTIC_AUTHORITY_CHECKS:
        assert authority.AUTHORITATIVE_VALIDATOR[name].strip(), name


def test_safety_critical_checks_are_never_advisory():
    """Sigurnosno kritične tvrdnje bez determinističke zamjene ostaju blokirajuće."""
    for name in ("math_correct", "marked_option_correct", "inside_lesson",
                 "task_solvable_and_unambiguous"):
        assert name in authority.MODEL_ONLY_BLOCKING_CHECKS
        assert name not in authority.ADVISORY_CHECKS


# ---------------------------------------------------------------------------
# KLASA A — netačna samoprijavljena provjera
# ---------------------------------------------------------------------------

def test_f12_advisory_false_does_not_veto_a_complete_correction():
    """TAČAN payload iz F12 (6-04-010): language_age_appropriate=false."""
    validate_reviewer(reviewer(checks_overrides={"language_age_appropriate": False}))


def test_b13_safety_critical_false_still_rejects():
    """TAČAN payload iz B13 (9-05-013): marked_option_correct=false."""
    with pytest.raises(UnifiedOutputError) as error:
        validate_reviewer(reviewer(checks_overrides={"marked_option_correct": False}))
    assert "marked_option_correct" in str(error.value)


@pytest.mark.parametrize("name", sorted(authority.ADVISORY_CHECKS))
def test_each_advisory_check_alone_never_vetoes(name):
    validate_reviewer(reviewer(checks_overrides={name: False}))


@pytest.mark.parametrize("name", sorted(authority.MODEL_ONLY_BLOCKING_CHECKS))
def test_each_model_only_check_alone_still_vetoes(name):
    with pytest.raises(UnifiedOutputError):
        validate_reviewer(reviewer(checks_overrides={name: False}))


@pytest.mark.parametrize("name", sorted(authority.DETERMINISTIC_AUTHORITY_CHECKS))
def test_deterministic_authority_checks_defer_to_the_server(name):
    """Boolean je dijagnostika: odluku donosi serverski validator."""
    if name == "difficulty_evidence_valid":
        pytest.skip("pokriveno zasebnim testom nad stvarnim dokazom")
    validate_reviewer(reviewer(checks_overrides={name: False}))


def test_advisory_false_under_approve_also_publishes():
    validate_reviewer(reviewer(decision="approve",
                               checks_overrides={"language_age_appropriate": False}))


def test_safety_critical_false_under_approve_still_rejects():
    with pytest.raises(UnifiedOutputError):
        validate_reviewer(reviewer(decision="approve",
                                   checks_overrides={"math_correct": False}))


# ---------------------------------------------------------------------------
# KLASA B — rubrika nivoa 1 i lekcije o UPOREĐIVANJU (živi gate acd8f5c)
# ---------------------------------------------------------------------------

def test_gate_7_03_006_comparison_task_is_valid_level_one():
    """TAČAN dokaz iz gate artefakta: „Koji je veći: 5/8 ili 3/8?“"""
    gate = evidence(reasoning_steps=1, condition_count=1, operation_count=1,
                    representation_change_count=0, requires_comparison=True)
    assert difficulty_evidence_errors(gate, 1) == ()


def test_tutor_variant_of_the_same_gate_task_is_also_level_one():
    tutor = evidence(reasoning_steps=1, condition_count=1, operation_count=1,
                     representation_change_count=1, requires_comparison=True)
    assert difficulty_evidence_errors(tutor, 1) == ()


def test_comparison_with_extra_work_is_still_not_level_one():
    """Rubrika se NE slabi: poređenje uz višekorakan rad i dalje pada."""
    for extra in (dict(reasoning_steps=2), dict(condition_count=2),
                  dict(operation_count=3), dict(representation_change_count=2),
                  dict(requires_explanation=True), dict(combines_concepts=True),
                  dict(requires_construction=True),
                  dict(requires_proof_or_justification=True)):
        bad = evidence(requires_comparison=True, **extra)
        assert difficulty_evidence_errors(bad, 1), extra


def test_gate_payload_passes_full_reviewer_validation():
    gate = evidence(requires_comparison=True)
    validate_reviewer(reviewer(level=1, ev=gate))


def test_level_two_and_three_rules_are_untouched():
    assert difficulty_evidence_errors(evidence(reasoning_steps=2, operation_count=2), 2) == ()
    assert difficulty_evidence_errors(evidence(), 2)          # too weak for L2
    assert difficulty_evidence_errors(evidence(requires_construction=True), 3) == ()
    assert difficulty_evidence_errors(evidence(), 3)          # too weak for L3


# ---------------------------------------------------------------------------
# STRUKTURNE INVARIJANTE ODLUKE — nepromijenjene
# ---------------------------------------------------------------------------

def test_correct_without_a_final_package_is_rejected():
    with pytest.raises(UnifiedOutputError):
        validate_reviewer(make_reviewer_final(decision="correct", final=None,
                                              reviewed_difficulty_evidence=None))


def test_fail_closed_requires_a_reason_and_never_publishes():
    with pytest.raises(UnifiedOutputError):
        validate_reviewer(make_reviewer_final(decision="fail_closed", final=None,
                                              fail_reason_code=None))
    validate_reviewer(make_reviewer_final(decision="fail_closed", final=None,
                                          fail_reason_code="outside_lesson"))


def test_task_intent_still_requires_an_independent_solution():
    with pytest.raises(UnifiedOutputError):
        validate_reviewer(reviewer(checks_overrides={"independently_solved": False}))
    with pytest.raises(UnifiedOutputError):
        validate_reviewer(reviewer(checks_overrides={"independent_answer": "  "}))


def test_reviewer_evidence_outside_target_still_rejects():
    """Stvaran preskok nivoa i dalje pada (F02/F21 klasa)."""
    over = evidence(reasoning_steps=3, operation_count=3, combines_concepts=True)
    with pytest.raises(UnifiedOutputError) as error:
        validate_reviewer(reviewer(level=1, ev=over))
    assert "difficulty_evidence" in str(error.value) or "evidence" in str(error.value)


def test_missing_reviewed_evidence_for_a_task_still_rejects():
    task = make_task_payload()
    final = make_tutor_draft(new_task=task)
    with pytest.raises(UnifiedOutputError):
        validate_reviewer(make_reviewer_final(decision="correct", final=final,
                                              reviewed_difficulty_evidence=None))
