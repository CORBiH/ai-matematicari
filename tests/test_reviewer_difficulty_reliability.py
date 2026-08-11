"""Pouzdanost recenzentovog dokaza težine (Faza F5J).

ŽIVI NALAZ (završna kapija na 9d52d0a, lekcija „Grafičko rješavanje linearne
jednačine“, svjež nivo 1): Tutor je za $2x+1=5$ iskreno prijavio
steps=1/ops=1; Recenzent je ISTI zadatak prebrojao kao steps=2/ops=2 pa ipak
vratio `approve`. Server je kontradikciju ispravno odbio — ali je turn (i
kapija) propao. Forenzika svih kampanja: 97 kontradiktornih odluka na
nepovezanim lekcijama; najčešća neslaganja operation_count i reasoning_steps.

UZROK (klasa B uz rezidualnu stohastiku): recenzent nikad nije dobio NUMERIČKE
pragove aktivnog cilja ni semantiku brojanja — pogađao ih je iz proze.

FIKS: jedan server-vlasnički blok (matbot/difficulty_target.py), renderovan iz
ISTIH konstanti koje čita validator, ide DOSLOVNO identičan u oba prompta;
recenzentovo pravilo odluke sada izričito traži poređenje VLASTITOG dokaza s
tim pragovima. Validator NIJE oslabljen ni za jedan prag.
"""
import pytest

from matbot import difficulty_profiles, difficulty_target
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor.lesson_context import build
from matbot.tutor.schema import (GLOBAL_LEVEL1_MAX, GLOBAL_LEVEL2_FLOORS,
                                 GLOBAL_LEVEL2_MAX, GLOBAL_LEVEL3_FLOORS,
                                 REVIEWER_EVIDENCE_OUTSIDE_TARGET,
                                 DifficultyEvidence, ReviewerChecks,
                                 ReviewerFinal, SignatureParameter, TaskPayload,
                                 TaskSignature, TutorDraft, TutorOption,
                                 UnifiedOutputError, difficulty_evidence_errors,
                                 validate_reviewer)


def ev(steps=1, cond=1, ops=1, repr_changes=0, **flags):
    values = dict(requires_explanation=False, requires_comparison=False,
                  requires_construction=False,
                  requires_proof_or_justification=False,
                  combines_concepts=False)
    values.update(flags)
    return DifficultyEvidence(
        reasoning_steps=steps, condition_count=cond, operation_count=ops,
        representation_change_count=repr_changes, **values)


_NUMERIC = ("reasoning_steps", "condition_count", "operation_count",
            "representation_change_count")


def _ev_from(counts):
    return ev(steps=counts["reasoning_steps"], cond=counts["condition_count"],
              ops=counts["operation_count"],
              repr_changes=counts["representation_change_count"])


# ---------------------------------------------------------------------------
# 1) KONSTANTE ⇄ VALIDATOR — brojevi u promptu su brojevi presude
# ---------------------------------------------------------------------------

def test_level1_caps_constants_exactly_match_the_validator():
    base = {name: 0 for name in _NUMERIC}
    at_cap = dict(GLOBAL_LEVEL1_MAX)
    assert difficulty_evidence_errors(_ev_from(at_cap), 1) == ()
    for name in _NUMERIC:
        over = dict(at_cap)
        over[name] = GLOBAL_LEVEL1_MAX[name] + 1
        assert difficulty_evidence_errors(_ev_from(over), 1), name
    assert difficulty_evidence_errors(_ev_from(base), 1) == ()


def test_level2_caps_and_floors_constants_exactly_match_the_validator():
    at_cap = dict(GLOBAL_LEVEL2_MAX)
    assert difficulty_evidence_errors(_ev_from(at_cap), 2) == ()
    for name in _NUMERIC:
        over = dict(at_cap)
        over[name] = GLOBAL_LEVEL2_MAX[name] + 1
        assert difficulty_evidence_errors(_ev_from(over), 2), name
    for name in _NUMERIC:
        floor_only = {key: 0 for key in _NUMERIC}
        floor_only[name] = GLOBAL_LEVEL2_FLOORS[name]
        assert difficulty_evidence_errors(_ev_from(floor_only), 2) == (), name


def test_level3_floors_constants_exactly_match_the_validator():
    below = {name: GLOBAL_LEVEL3_FLOORS[name] - 1 for name in _NUMERIC}
    below["representation_change_count"] = \
        GLOBAL_LEVEL3_FLOORS["representation_change_count"] - 1
    # ispod SVIH podova (i bez zastavica) nivo 3 mora pasti
    low = {name: 0 for name in _NUMERIC}
    assert difficulty_evidence_errors(_ev_from(low), 3)
    for name in _NUMERIC:
        floor_only = {key: 0 for key in _NUMERIC}
        floor_only[name] = GLOBAL_LEVEL3_FLOORS[name]
        assert difficulty_evidence_errors(_ev_from(floor_only), 3) == (), name


def test_rendered_block_carries_every_cap_and_floor_literal():
    block = difficulty_target.global_target_block()
    for name, cap in GLOBAL_LEVEL1_MAX.items():
        assert f"{name} <= {cap}" in block, name
    for name, cap in GLOBAL_LEVEL2_MAX.items():
        assert f"{name} <= {cap}" in block, name
    for name, floor in GLOBAL_LEVEL3_FLOORS.items():
        assert f"{name} >= {floor}" in block, name


# ---------------------------------------------------------------------------
# 2) OBA PROMPTA NOSE DOSLOVNO ISTI BLOK; PREFIKS OSTAJE STABILAN PO LEKCIJI
# ---------------------------------------------------------------------------

_LESSONS = ((6, "6-04-001"), (9, "9-01-016"), (9, "9-01-008"),
            (8, "8-05-007"), (7, "7-02-011"))


def test_both_prompts_carry_the_identical_shared_target_block():
    block = difficulty_target.shared_target_block()
    assert "HOW TO COUNT DIFFICULTY EVIDENCE" in block
    for grade, topic in _LESSONS:
        context = build(grade, topic)
        assert context is not None, topic
        for text in (tutor_prompts.build_tutor_instructions(context),
                     tutor_prompts.build_reviewer_instructions(context)):
            assert block in text, topic


def test_shared_block_is_lesson_independent_for_cacheable_prefix():
    blocks = {difficulty_target.shared_target_block() for _ in range(3)}
    assert len(blocks) == 1
    assert not any(topic in difficulty_target.shared_target_block()
                   for _grade, topic in _LESSONS)


def test_counting_semantics_pin_the_calibrated_one_stage_example():
    semantics = difficulty_target.evidence_semantics_block()
    assert "ONE reasoning step" in semantics or "ONE\nreasoning step" in \
        semantics or "ONE " in semantics
    assert "steps=1, operations=2" in semantics
    # Kalibrisani živi slučaj: „preuredi i izračunaj“ = (1,1,2,0) je validan L1.
    assert difficulty_evidence_errors(ev(1, 1, 2, 0), 1) == ()


def test_profiled_lesson_still_gets_its_replacing_profile_block():
    context = build(8, "8-05-007")
    profile = difficulty_profiles.resolve_for_context(context)
    assert profile is not None
    text = tutor_prompts.build_reviewer_instructions(context)
    assert profile.prompt_block() in text
    assert "replace these global ones" in difficulty_target.global_target_block()


# ---------------------------------------------------------------------------
# 3) RECENZENTOVA SAMOKONZISTENTNOST (uklj. tačan replay žive kapije)
# ---------------------------------------------------------------------------

def _payload(context, evidence, level=1):
    return TaskPayload(
        selected_lesson_id=context.topic_id,
        selected_lesson_title=context.title,
        target_difficulty_level=level,
        text="Riješi jednačinu $2x+1=5$ (možeš je riješiti grafički ili "
             "algebarski). Koja je vrijednost $x$?",
        task_type="multiple_choice",
        options=[TutorOption(id="abcd"[i], text=t)
                 for i, t in enumerate(("$2$", "$3$", "$1$", "$4$"))],
        correct_option_index=0, correct_option_id="a", expected_answer="$2$",
        solution="Tačan odgovor je: $2$",
        difficulty=("easy", "standard", "hard")[level - 1],
        difficulty_evidence=evidence,
        task_signature=TaskSignature(
            task_family="generic", operation_or_relation="solve",
            normalized_parameters=[SignatureParameter(name="case", value="g9")],
            required_conditions=["valid"], relevant_objects=["equation"],
            answer_type="multiple_choice"),
    )


def _checks():
    return ReviewerChecks(
        math_correct=True, marked_option_correct=True, inside_lesson=True,
        intent_handled=True, difficulty_direction_correct=True,
        response_addresses_student=True, task_solvable_and_unambiguous=True,
        mathjax_valid=True, language_age_appropriate=True,
        independently_solved=True, independent_answer="$2$",
        task_package_consistent=True, difficulty_evidence_valid=True,
        task_signature_consistent=True,
        stem_requires_student_reasoning=True,
        exactly_one_option_correct=True)


# DOSLOVNI dokazi iz artefakta kapije 9d52d0a (grade9, 9-04-010):
LIVE_TUTOR_EVIDENCE = ev(1, 1, 1, 0)
LIVE_REVIEWER_EVIDENCE = ev(2, 1, 2, 0)


def test_replay_the_exact_gate_contradiction_is_still_rejected():
    """Replay žive kapije: recenzentov `approve` uz vlastiti dokaz (2,1,2,0)
    za traženi nivo 1 i DALJE deterministički pada — validator NIJE oslabljen."""
    context = build(9, "9-04-010")
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="linearna jednačina",
                       new_task=_payload(context, LIVE_TUTOR_EVIDENCE))
    reviewer = ReviewerFinal(decision="approve", checks=_checks(), final=draft,
                             reviewed_difficulty_evidence=LIVE_REVIEWER_EVIDENCE)
    with pytest.raises(UnifiedOutputError) as error:
        validate_reviewer(reviewer, draft)
    assert REVIEWER_EVIDENCE_OUTSIDE_TARGET in str(error.value)
    assert "level_1_is_not_direct_introductory_application" in str(error.value)


def test_replayed_lesson_prompt_now_states_the_violated_cap():
    """Novi blok recenzentu IZRIČITO kaže prag koji je živi payload prekršio."""
    context = build(9, "9-04-010")
    text = tutor_prompts.build_reviewer_instructions(context)
    assert "reasoning_steps <= 1" in text
    assert "steps=1, operations=2" in text     # kalibrisani jedan-korak primjer
    assert "you MUST NOT approve" in text


def test_consistent_approval_still_continues():
    context = build(9, "9-04-010")
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="linearna jednačina",
                       new_task=_payload(context, LIVE_TUTOR_EVIDENCE))
    reviewer = ReviewerFinal(decision="approve", checks=_checks(), final=draft,
                             reviewed_difficulty_evidence=ev(1, 1, 2, 0))
    validate_reviewer(reviewer, draft)   # ne baca — dosljedan dokaz prolazi


def test_correction_that_satisfies_the_target_is_accepted():
    context = build(9, "9-04-010")
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="linearna jednačina",
                       new_task=_payload(context, ev(1, 1, 2, 0)))
    reviewer = ReviewerFinal(decision="correct", checks=_checks(), final=draft,
                             reviewed_difficulty_evidence=ev(1, 1, 2, 0))
    validate_reviewer(reviewer, draft)


def test_fail_closed_needs_no_evidence_and_stays_terminal():
    reviewer = ReviewerFinal(decision="fail_closed", checks=_checks(),
                             fail_reason_code="unsafe_or_unverifiable",
                             final=None, reviewed_difficulty_evidence=None)
    validate_reviewer(reviewer, None)


# ---------------------------------------------------------------------------
# 4) BEZ PRENAMJEŠTANJA: dvokorakan zadatak i dalje NIJE nivo 1; profili važe
# ---------------------------------------------------------------------------

def test_genuinely_two_stage_evidence_still_fails_level_1():
    assert difficulty_evidence_errors(ev(2, 1, 2, 0), 1) == (
        "level_1_is_not_direct_introductory_application",)
    assert difficulty_evidence_errors(ev(1, 1, 3, 0), 1)      # tri operacije


def test_lesson_relative_profiles_remain_strict_and_unchanged():
    formula = difficulty_profiles.resolve_for_context(build(8, "8-05-007"))
    assert formula.profile_id == "direct_formula_application"
    assert difficulty_evidence_errors(ev(1, 1, 3, 0), 1, profile=formula) == ()
    assert difficulty_evidence_errors(ev(2, 1, 4, 0), 1, profile=formula)

    system = difficulty_profiles.resolve_for_context(build(9, "9-05-013"))
    assert system.profile_id == "system_word_translation"
    assert difficulty_evidence_errors(ev(2, 2, 2, 1), 1, profile=system) == ()
    assert difficulty_evidence_errors(ev(1, 1, 2, 0), 2, profile=system)


def test_reviewer_cannot_select_a_profile_or_thresholds():
    """Razrješenje profila čita SAMO server-vlasnički kontekst; recenzentov
    payload nema nijedno polje kojim bi birao profil, nivo ni prag."""
    import inspect

    assert list(inspect.signature(
        difficulty_profiles.resolve_for_context).parameters) == ["context"]
    fields = set(ReviewerFinal.model_fields)
    assert fields == {"decision", "checks", "fail_reason_code", "final",
                      "reviewed_difficulty_evidence"}


def test_free_form_rational_lesson_receives_the_active_target():
    context = build(9, "9-01-008")
    assert difficulty_profiles.resolve_for_context(context) is None
    text = tutor_prompts.build_reviewer_instructions(context)
    assert difficulty_target.global_target_block() in text
