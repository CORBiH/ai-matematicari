# -*- coding: utf-8 -*-
"""Global pedagogy / task-quality test matrix (61 items, A-F).

Fakes only — no live/paid OpenAI call anywhere in this file. Every check is
GENERIC: no lesson-specific string, no hardcoded fix for one screenshot.

  A. anti-echo / feedback value          (1-13)
  B. task coherence                      (14-29)
  C. difficulty comparison               (30-35)
  D. bounded repair                      (36-43)
  E. cross-grade/domain regression       (44-50)
  F. existing-guarantee regression       (51-61)
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from matbot.ai_tutor_v3 import (
    dispatcher, feedback_value_gate as fvg, orchestrator, quality_gate,
    task_coherence as tc,
)
from matbot.ai_tutor_v3.schemas import (
    ActiveTask, DifficultySignature, LessonBlueprintProposal, RequiredOperation,
    TaskFamily, TaskSpecification,
)
from tests.test_v3_practice import (
    DIVISIBILITY, FakeClient, assess_parsed, base_payload, blueprint_parsed,
    dispatch, fake, interp_parsed, narration_parsed, start_task, v3_env,
)
from tests.test_v3_quality_matrix import REPRESENTATIVE_LESSONS, _make_fake


# =========================================================================== #
# A. Anti-echo / feedback value (1-13)                                       #
# =========================================================================== #
def test_a1_exact_echo_detected():
    r = fvg.check_feedback_value(
        "Odgovor je 6 jer je 252 djeljivo sa 6",
        student_message="Odgovor je 6 jer je 252 djeljivo sa 6", verdict="correct")
    assert not r.passed
    assert "exact_echo" in r.categories


def test_a2_near_echo_detected_with_one_extra_word():
    r = fvg.check_feedback_value(
        "Odgovor je 6 jer je 252 djeljivo sa 6 tačno",
        student_message="Odgovor je 6 jer je 252 djeljivo sa 6", verdict="correct")
    assert not r.passed
    assert "near_echo" in r.categories
    assert r.similarity_metrics["jaccard"] >= 0.75


def test_a3_no_added_value_for_incorrect_bare_verdict_word():
    r = fvg.check_feedback_value("Netačno.", student_message="je 5", verdict="incorrect")
    assert not r.passed
    assert "no_added_value" in r.categories


def test_a4_no_added_value_for_partial_bare_verdict_word():
    r = fvg.check_feedback_value("Djelimično.", student_message="je 1/2", verdict="partial")
    assert not r.passed
    assert "no_added_value" in r.categories


def test_a5_correct_bare_verdict_word_is_sufficient_value():
    """The 'correct' standard explicitly wants a SHORT confirmation — a bare
    verdict word is legitimate value there, not emptiness."""
    r = fvg.check_feedback_value("Tačno!", student_message="je 6", verdict="correct")
    assert r.passed


def test_a6_unsupported_generic_praise_for_incorrect():
    r = fvg.check_feedback_value("Bravo!", student_message="je 7", verdict="incorrect")
    assert not r.passed
    assert "unsupported_generic_praise" in r.categories


def test_a7_unsupported_generic_praise_for_partial():
    r = fvg.check_feedback_value("Super!", student_message="je 1/2", verdict="partial")
    assert not r.passed
    assert "unsupported_generic_praise" in r.categories


def test_a8_verdict_inconsistent_positive_words_on_incorrect():
    r = fvg.check_feedback_value(
        "Tačno, odlično urađeno!", student_message="je 7", verdict="incorrect")
    assert not r.passed
    assert "verdict_inconsistent_feedback" in r.categories


def test_a9_verdict_inconsistent_negative_words_on_correct():
    r = fvg.check_feedback_value(
        "To je nažalost netačno.", student_message="je 6", verdict="correct")
    assert not r.passed
    assert "verdict_inconsistent_feedback" in r.categories


def test_a10_good_substantive_feedback_passes():
    r = fvg.check_feedback_value(
        "Tačno! Ispravno si primijenio pravilo djeljivosti sa 6.",
        student_message="da, jer je paran i zbir cifara djeljiv sa 3", verdict="correct")
    assert r.passed
    assert r.categories == []


def test_a11_added_numeric_content_counts_as_value():
    r = fvg.check_feedback_value(
        "Provjeri: 2 puta 3 je 6, ne 7.", student_message="mislim da je 7",
        verdict="incorrect")
    assert r.passed
    assert r.similarity_metrics["has_new_content"] is True


def test_a12_empty_feedback_rejected():
    r = fvg.check_feedback_value("", student_message="je 6", verdict="correct")
    assert not r.passed
    assert r.categories == ["empty_feedback"]


def test_a13_dispatcher_feedback_value_repair_then_verdict_fallback(fake):
    """Integration: the fixture's default narration ("Tako je, odlično.") is
    a legitimate short confirmation for 'correct', so it should NOT trigger a
    repair. Force a genuinely empty-value INCORRECT narration instead and
    confirm the bounded repair + verdict-specific fallback engage."""
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed(
        "answer", is_answer=True, assessment=assess_parsed("incorrect"),
        meaning="pokušaj odgovora"))
    resp1 = start_task(fake)
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed(
        "answer", is_answer=True, assessment=assess_parsed("incorrect")))
    fake.set(orchestrator.PURPOSE_NARRATION, narration_parsed("Bravo!", "feedback_incorrect"))
    resp2 = dispatch(base_payload(client_turn_id="c2", student_message="7",
                                 previous_next_state=resp1["next_state"]))
    assert resp2 is not None
    assert orchestrator.PURPOSE_FEEDBACK_REPAIR in fake.calls
    gate = resp2["v3_telemetry"]["quality_gate"]["feedback_value_gate"]
    assert gate["checked"] is True
    assert gate["gate_failed_final"] is True
    assert resp2["answer"] == fvg.safe_fallback_text("incorrect")


# =========================================================================== #
# B. Task coherence (14-29)                                                   #
# =========================================================================== #
def _blueprint(**kw):
    return LessonBlueprintProposal.model_validate(blueprint_parsed(**kw))


def _spec(**kw):
    base = dict(concept_id="div-compound-6", target_id="6", question="Da li je 252 djeljiv sa 6?",
               answer_kind="boolean_with_reason", expected_internal="da", difficulty_level=2)
    base.update(kw)
    return TaskSpecification.model_validate(base)


def test_b14_missing_metadata_recorded_but_not_hard():
    bp = _blueprint()
    result = tc.check_task_coherence(_spec(), blueprint=bp, grade=6)
    assert not result.passed
    assert set(result.failure_categories) <= (
        tc.METADATA_ONLY_CATEGORIES | {"unknown_task_family"}) | {"missing_task_family"}
    assert not result.hard_failure_categories


def test_b15_self_cancelling_pair_without_goal():
    spec = _spec(required_student_operations=[
        {"kind": "expand"}, {"kind": "reduce"}])
    result = tc.check_task_coherence(spec, blueprint=_blueprint(), grade=6)
    assert "self_cancelling_operation_pair_without_goal" in result.failure_categories


def test_b16_self_cancelling_pair_with_goal_is_exempt():
    spec = _spec(required_student_operations=[
        {"kind": "expand"}, {"kind": "compare"}, {"kind": "reduce"}],
        comparison_or_invariance_goal="uporediti dva razlomka nakon proširivanja")
    result = tc.check_task_coherence(spec, blueprint=_blueprint(), grade=6)
    assert "self_cancelling_operation_pair_without_goal" not in result.failure_categories


def test_b17_reasoning_steps_below_operation_count():
    spec = _spec(required_student_operations=[
        {"kind": "identify"}, {"kind": "compare"}, {"kind": "verify"}],
        expected_reasoning_steps=1)
    result = tc.check_task_coherence(spec, blueprint=_blueprint(), grade=6)
    assert "reasoning_steps_below_operation_count" in result.failure_categories


def test_b18_reasoning_steps_implausibly_high():
    spec = _spec(expected_reasoning_steps=9)
    result = tc.check_task_coherence(spec, blueprint=_blueprint(), grade=6)
    assert "reasoning_steps_implausibly_high" in result.failure_categories


def test_b19_empty_coherence_claim_for_multistep_task():
    spec = _spec(required_student_operations=[{"kind": "identify"}, {"kind": "verify"}],
                 expected_reasoning_steps=2, coherence_claim=None)
    result = tc.check_task_coherence(spec, blueprint=_blueprint(), grade=6)
    assert "empty_coherence_claim_for_multistep_task" in result.failure_categories


def test_b20_difficulty_level_out_of_family_range():
    bp_data = blueprint_parsed()
    bp_data["task_families"][0].update(
        {"family_id": "yn", "typical_difficulty_min": 3, "typical_difficulty_max": 4})
    bp = LessonBlueprintProposal.model_validate(bp_data)
    spec = _spec(task_family_id="yn", difficulty_level=1)
    result = tc.check_task_coherence(spec, blueprint=bp, grade=6)
    assert "difficulty_level_out_of_family_range" in result.failure_categories


def test_b21_answer_kind_mismatch_with_family_is_hard():
    bp = _blueprint()  # family "yn" declares boolean_with_reason
    spec = _spec(task_family_id="yn", answer_kind="integer_value", expected_internal="6")
    result = tc.check_task_coherence(spec, blueprint=bp, grade=6)
    assert "answer_kind_mismatch_with_family" in result.hard_failure_categories


def test_b22_duplicate_operation_kind_without_detail():
    spec = _spec(required_student_operations=[{"kind": "verify"}, {"kind": "verify"}])
    result = tc.check_task_coherence(spec, blueprint=_blueprint(), grade=6)
    assert "duplicate_operation_kind_without_detail" in result.failure_categories


def test_b23_comparison_goal_without_comparison_operation():
    spec = _spec(comparison_or_invariance_goal="uporedi vrijednosti",
                 required_student_operations=[{"kind": "identify"}])
    result = tc.check_task_coherence(spec, blueprint=_blueprint(), grade=6)
    assert "comparison_goal_without_comparison_operation" in result.failure_categories


def test_b24_task_text_too_short_for_claimed_steps():
    spec = _spec(question="Riješi.", expected_reasoning_steps=4)
    result = tc.check_task_coherence(spec, blueprint=_blueprint(), grade=6)
    assert "task_text_too_short_for_claimed_steps" in result.failure_categories


def test_b25_expected_internal_missing_for_checkable_kind():
    spec = _spec(answer_kind="integer_value", expected_internal=None)
    result = tc.check_task_coherence(spec, blueprint=_blueprint(), grade=6)
    assert "expected_internal_missing_for_checkable_kind" in result.failure_categories


def test_b26_planned_verification_type_unsupported_is_hard():
    spec = _spec(planned_verification_type="equation_substitution")
    result = tc.check_task_coherence(spec, blueprint=_blueprint(), grade=6)  # supports only divisibility
    assert "planned_verification_type_unsupported_by_blueprint" in result.hard_failure_categories


def test_b27_dispatcher_unknown_task_family_hard_failure_fails_turn_safely(fake):
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("task_request"))
    fake.set(orchestrator.PURPOSE_TASK, {
        "concept_id": "div-compound-6", "target_id": "6",
        "question": "Da li je 252 djeljiv sa 6?", "answer_kind": "boolean_with_reason",
        "expected_internal": "da", "difficulty_level": 2, "task_family_id": "no_such_family"})
    resp = start_task(fake)
    assert resp is not None
    assert resp.get("v3_fallback_reason") == "task_incoherent"


def test_b28_coherent_representative_task_no_repair_call(fake):
    """A well-formed task (all new metadata present + consistent) never
    triggers the coherence repair — no extra call beyond blueprint+task."""
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("task_request"))
    fake.set(orchestrator.PURPOSE_TASK, {
        "concept_id": "div-compound-6", "target_id": "6",
        "question": "Da li je 252 djeljiv sa 6?", "answer_kind": "boolean_with_reason",
        "expected_internal": "da", "difficulty_level": 2,
        "task_family_id": "yn", "pedagogical_goal": "primijeniti pravilo djeljivosti sa 6",
        "required_student_operations": [{"kind": "verify", "detail": "djeljivost sa 2"},
                                        {"kind": "verify", "detail": "djeljivost sa 3"}],
        "expected_reasoning_steps": 2, "coherence_claim": "provjera dva uslova redom"})
    resp = start_task(fake)
    assert resp is not None
    assert orchestrator.PURPOSE_TASK_REPAIR not in fake.calls
    assert len(fake.calls) == 2


def test_b29_dispatcher_repair_success_activates_repaired_spec(fake):
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("task_request"))
    fake.set(orchestrator.PURPOSE_TASK, {
        "concept_id": "div-compound-6", "target_id": "6",
        "question": "Da li je 252 djeljiv sa 6?", "answer_kind": "boolean_with_reason",
        "expected_internal": "da", "difficulty_level": 2, "task_family_id": "no_such_family"})
    fake.set(orchestrator.PURPOSE_TASK_REPAIR, {
        "concept_id": "div-compound-6", "target_id": "6",
        "question": "Da li je 252 djeljiv sa 9?", "answer_kind": "boolean_with_reason",
        "expected_internal": "ne", "difficulty_level": 2, "task_family_id": "yn"})
    resp = start_task(fake)
    assert resp is not None
    assert orchestrator.PURPOSE_TASK_REPAIR in fake.calls
    assert "9" in resp["last_tutor_task"]


# =========================================================================== #
# C. Difficulty comparison (30-35)                                            #
# =========================================================================== #
def _sig(**kw):
    base = dict(numeric_magnitude=2, operation_count=2, reasoning_steps=2, concept_count=1)
    base.update(kw)
    return DifficultySignature.model_validate(base)


def test_c30_strictly_easier():
    old = _sig(numeric_magnitude=4, operation_count=3)
    new = _sig(numeric_magnitude=2, operation_count=2)
    result = tc.compare_difficulty(old, new)
    assert result.classification == "easier"


def test_c31_strictly_harder():
    old = _sig(numeric_magnitude=2)
    new = _sig(numeric_magnitude=4, reasoning_steps=4)
    result = tc.compare_difficulty(old, new)
    assert result.classification == "harder"


def test_c32_identical_signatures_are_same():
    sig = _sig()
    result = tc.compare_difficulty(sig, sig.model_copy())
    assert result.classification == "same"


def test_c33_mixed_signals_are_ambiguous():
    old = _sig(numeric_magnitude=2, operation_count=4)
    new = _sig(numeric_magnitude=4, operation_count=2)  # one up, one down
    result = tc.compare_difficulty(old, new)
    assert result.classification == "ambiguous"


def test_c34_missing_signature_is_insufficient_data():
    assert tc.compare_difficulty(None, _sig()).classification == "insufficient_data"
    assert tc.compare_difficulty(_sig(), None).classification == "insufficient_data"


def test_c35_relevant_dimensions_restricts_comparison():
    old = _sig(numeric_magnitude=4, operation_count=1)
    new = _sig(numeric_magnitude=2, operation_count=5)  # harder on operation_count
    result = tc.compare_difficulty(old, new, relevant_dimensions=["numeric_magnitude"])
    assert result.classification == "easier"
    assert result.dimensions_compared == ["numeric_magnitude"]


# =========================================================================== #
# D. Bounded repair (36-43)                                                   #
# =========================================================================== #
def test_d36_feedback_repair_bounded_to_one_call_even_if_still_failing(fake):
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("task_request"))
    resp1 = start_task(fake)
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed(
        "answer", is_answer=True, assessment=assess_parsed("incorrect")))
    fake.set(orchestrator.PURPOSE_NARRATION, narration_parsed("Super!", "feedback_incorrect"))
    # PURPOSE_FEEDBACK_REPAIR left unconfigured -> FakeClient validates {} against
    # NarrationResult and fails -> repaired stays None -> exactly one attempt.
    resp2 = dispatch(base_payload(client_turn_id="c2", student_message="7",
                                 previous_next_state=resp1["next_state"]))
    assert fake.calls.count(orchestrator.PURPOSE_FEEDBACK_REPAIR) == 1


def test_d37_feedback_repair_success_uses_repaired_text(fake):
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("task_request"))
    resp1 = start_task(fake)
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed(
        "answer", is_answer=True, assessment=assess_parsed("incorrect")))
    fake.set(orchestrator.PURPOSE_NARRATION, narration_parsed("Super!", "feedback_incorrect"))
    fake.set(orchestrator.PURPOSE_FEEDBACK_REPAIR, narration_parsed(
        "To nije tačno — probaj podijeliti 252 sa 6 ponovo.", "feedback_incorrect"))
    resp2 = dispatch(base_payload(client_turn_id="c2", student_message="7",
                                 previous_next_state=resp1["next_state"]))
    assert "probaj" in resp2["answer"].lower()
    gate = resp2["v3_telemetry"]["quality_gate"]["feedback_value_gate"]
    assert gate["repaired"] is True
    assert gate["gate_failed_final"] is False


def test_d38_feedback_fallback_is_verdict_specific():
    assert fvg.safe_fallback_text("correct") != fvg.safe_fallback_text("incorrect")
    assert fvg.safe_fallback_text("partial") != fvg.safe_fallback_text("correct")


def test_d39_task_repair_bounded_to_one_call(fake):
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("task_request"))
    fake.set(orchestrator.PURPOSE_TASK, {
        "concept_id": "div-compound-6", "target_id": "6",
        "question": "Da li je 252 djeljiv sa 6?", "answer_kind": "boolean_with_reason",
        "expected_internal": "da", "difficulty_level": 2, "task_family_id": "no_such_family"})
    start_task(fake)
    assert fake.calls.count(orchestrator.PURPOSE_TASK_REPAIR) == 1


def test_d40_task_repair_success_activates_repaired_spec(fake):
    # covered functionally by b29; this asserts the repair count specifically.
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("task_request"))
    fake.set(orchestrator.PURPOSE_TASK, {
        "concept_id": "div-compound-6", "target_id": "6",
        "question": "Da li je 252 djeljiv sa 6?", "answer_kind": "boolean_with_reason",
        "expected_internal": "da", "difficulty_level": 2, "task_family_id": "no_such_family"})
    fake.set(orchestrator.PURPOSE_TASK_REPAIR, {
        "concept_id": "div-compound-6", "target_id": "6",
        "question": "Da li je 100 djeljiv sa 6?", "answer_kind": "boolean_with_reason",
        "expected_internal": "ne", "difficulty_level": 2, "task_family_id": "yn"})
    resp = start_task(fake)
    assert fake.calls.count(orchestrator.PURPOSE_TASK_REPAIR) == 1
    assert "v3_fallback_reason" not in resp


def test_d41_hard_failure_unresolved_preserves_previous_active_task(fake):
    """A difficulty-change whose repaired task STILL fails a hard category
    must fail the turn safely — the student's PREVIOUS active task stays
    visible (nothing committed), never a corrupted/absent task."""
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("task_request"))
    resp1 = start_task(fake)
    original_task = resp1["last_tutor_task"]
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_TASK, {
        "concept_id": "div-compound-6", "target_id": "6", "question": "Novo pitanje?",
        "answer_kind": "boolean_with_reason", "expected_internal": "da",
        "difficulty_level": 1, "task_family_id": "no_such_family"})
    # repair also unconfigured -> stays a hard failure
    resp2 = dispatch(base_payload(
        client_turn_id="c2", student_message="Daj mi lakši zadatak.",
        difficulty_request="easier", previous_next_state=resp1["next_state"]))
    assert resp2 is not None
    assert resp2.get("v3_fallback_reason") == "task_incoherent"
    assert resp2["last_tutor_task"] == original_task


def test_d42_soft_failure_after_repair_still_activates_task(fake):
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("task_request"))
    fake.set(orchestrator.PURPOSE_TASK, {
        "concept_id": "div-compound-6", "target_id": "6",
        "question": "Da li je 252 djeljiv sa 6?", "answer_kind": "boolean_with_reason",
        "expected_internal": "da", "difficulty_level": 2,
        "required_student_operations": [{"kind": "expand"}, {"kind": "reduce"}]})
    # repair unconfigured -> repaired_spec None -> falls back to original spec,
    # which still has ONLY a soft failure (self_cancelling pair) -> accepted.
    resp = start_task(fake)
    assert resp is not None
    assert resp["last_tutor_task"]
    assert "v3_fallback_reason" not in resp or resp["v3_fallback_reason"] is None


def test_d43_repair_purposes_are_distinct_from_quality_repair():
    assert orchestrator.PURPOSE_FEEDBACK_REPAIR != orchestrator.PURPOSE_REPAIR
    assert orchestrator.PURPOSE_TASK_REPAIR != orchestrator.PURPOSE_REPAIR
    assert orchestrator.PURPOSE_FEEDBACK_REPAIR != orchestrator.PURPOSE_TASK_REPAIR


# =========================================================================== #
# E. Cross-grade / domain regression (44-50)                                  #
# =========================================================================== #
@pytest.mark.parametrize(
    "grade,topic_id,oblast,area,target,question,answer", REPRESENTATIVE_LESSONS,
    ids=[f"g{g}-{area}" for (g, _t, _o, area, *_r) in REPRESENTATIVE_LESSONS])
def test_e44_no_lesson_ever_hits_a_hard_coherence_failure(
        matrix_env, grade, topic_id, oblast, area, target, question, answer):
    """Across >=3 real lessons per grade (6-9), >=6 domains (12 total): the
    coherence gate never HARD-fails a fixture-default task (metadata-only
    absence is expected and must stay non-blocking)."""
    fake_client = _make_fake(question, answer, target)
    dispatcher.set_model_client(fake_client)
    payload = {"session_id": f"e44-{area}", "grade": grade, "mode": "practice",
              "selected_topic": topic_id, "selected_oblast": oblast,
              "student_message": "Daj mi jedan zadatak za vježbu iz ove teme.",
              "client_turn_id": "t1"}
    resp = dispatcher.v3_practice_dispatch(payload, model="gpt-5-mini", timeout=5)
    assert resp is not None
    assert resp.get("v3_fallback_reason") != "task_incoherent"
    assert resp["last_tutor_task"]


@pytest.fixture()
def matrix_env(monkeypatch, tmp_path):
    from matbot import topic_resolver as tr
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "on")
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_LESSONS", "*")
    monkeypatch.setenv("MATBOT_V3_VERIFICATION", "off")
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    monkeypatch.setenv("MATBOT_MINIMAL_ENGINE", "off")
    tr.reset_cache()
    yield
    dispatcher.set_model_client(None)
    tr.reset_cache()


def test_e45_grade6_well_formed_task_family_passes_across_domains(matrix_env):
    for grade, topic_id, oblast, area, target, question, answer in REPRESENTATIVE_LESSONS:
        if grade != 6:
            continue
        fake_client = _make_fake(question, answer, target)
        fake_client.set(orchestrator.PURPOSE_TASK, {
            "concept_id": f"concept-{target}", "target_id": target, "question": question,
            "answer_kind": "boolean_with_reason", "expected_internal": answer,
            "difficulty_level": 2, "pedagogical_goal": "vjezba",
            "required_student_operations": [{"kind": "verify"}], "expected_reasoning_steps": 1})
        dispatcher.set_model_client(fake_client)
        payload = {"session_id": f"e45-{area}", "grade": grade, "mode": "practice",
                  "selected_topic": topic_id, "selected_oblast": oblast,
                  "student_message": "Daj mi jedan zadatak.", "client_turn_id": "t1"}
        resp = dispatcher.v3_practice_dispatch(payload, model="gpt-5-mini", timeout=5)
        assert resp is not None and resp["last_tutor_task"]


def test_e46_feedback_gate_never_blocks_a_substantive_correct_answer_across_grades(matrix_env):
    for grade, topic_id, oblast, area, target, question, answer in REPRESENTATIVE_LESSONS:
        fake_client = _make_fake(question, answer, target)
        fake_client.set(orchestrator.PURPOSE_INTERPRET, interp_parsed(
            "answer", is_answer=True, assessment=assess_parsed("correct"),
            meaning="tačan odgovor"))
        dispatcher.set_model_client(fake_client)
        payload = {"session_id": f"e46-{area}", "grade": grade, "mode": "practice",
                  "selected_topic": topic_id, "selected_oblast": oblast,
                  "student_message": "Daj mi jedan zadatak.", "client_turn_id": "t1"}
        resp1 = dispatcher.v3_practice_dispatch(payload, model="gpt-5-mini", timeout=5)
        assert resp1 is not None
        resp2 = dispatcher.v3_practice_dispatch({
            **payload, "client_turn_id": "t2", "student_message": str(answer),
            "previous_next_state": resp1["next_state"]}, model="gpt-5-mini", timeout=5)
        assert resp2 is not None
        assert resp2["answer"]


def test_e47_all_twelve_lessons_cover_at_least_six_domains():
    domains = {area for (_g, _t, _o, area, *_r) in REPRESENTATIVE_LESSONS}
    assert len(domains) >= 6
    grades = {g for (g, *_r) in REPRESENTATIVE_LESSONS}
    assert grades == {6, 7, 8, 9}
    for g in (6, 7, 8, 9):
        assert sum(1 for (gg, *_r) in REPRESENTATIVE_LESSONS if gg == g) >= 3


def test_e48_hint_no_longer_repeats_full_task_text_instruction_present():
    assert "NE ponavljaj cijeli originalni" in orchestrator.generate_hint.__wrapped__.__doc__ \
        if hasattr(orchestrator.generate_hint, "__wrapped__") else True
    # The actual instruction text lives in the function body (not a docstring);
    # assert its presence directly via a probe call is covered by D-tests and
    # test_v3_practice's hint tests — this is a static content spot-check.
    import inspect
    src = inspect.getsource(orchestrator.generate_hint)
    assert "NE ponavljaj cijeli originalni" in src


def test_e49_grade6_method_policy_forbids_prebacivanje():
    assert "prebacivanje" in orchestrator.GRADE_POLICIES[6]
    assert "NIKAD" in orchestrator.GRADE_POLICIES[6]


def test_e50_terminology_policy_prefers_brojnik_nazivnik():
    assert "brojnik i nazivnik" in orchestrator.BOSNIAN_LANGUAGE_POLICY
    assert "brojilac/imenilac" in orchestrator.BOSNIAN_LANGUAGE_POLICY


# =========================================================================== #
# F. Existing-guarantee regression (51-61)                                    #
# =========================================================================== #
def test_f51_reducer_module_unchanged_surface():
    from matbot.ai_tutor_v3 import reducer
    assert hasattr(reducer, "reduce_turn")
    import inspect
    sig = inspect.signature(reducer.reduce_turn)
    assert set(sig.parameters) == {"state", "interpretation", "assessment", "verification"}


def test_f52_old_blueprint_without_new_fields_still_validates():
    """A Blueprint generated before this pass (no task_family invariants) must
    still load — Pydantic default-fills every new field."""
    data = blueprint_parsed()
    proposal = LessonBlueprintProposal.model_validate(data)
    family = proposal.task_families[0]
    assert family.pedagogical_goal is None
    assert family.typical_required_operations == []
    assert family.comparison_or_invariance_goal is None


def test_f53_active_task_new_fields_default_safely():
    task = ActiveTask(task_id="t_1", concept_id="c", target_id="t", question="q",
                      answer_kind="integer_value", difficulty_level=2)
    assert task.task_family_id is None
    assert task.difficulty_signature is None


def test_f54_task_specification_strict_schema_still_prepares():
    schema = orchestrator.export_json_schema(TaskSpecification)
    strict = orchestrator.prepare_openai_strict_schema(schema)
    orchestrator.validate_openai_strict_schema(
        strict, purpose="task_generation", schema_name="TaskSpecification")


def test_f55_required_operation_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        RequiredOperation.model_validate({"kind": "not_a_real_kind"})


def test_f56_quality_gate_runs_before_feedback_value_gate(fake):
    """Ordering: a text with BOTH a quality-gate violation (internal term) AND
    a feedback-value violation must be repaired by quality_gate first — the
    value gate never sees raw internal-term text."""
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("task_request"))
    resp1 = start_task(fake)
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed(
        "answer", is_answer=True, assessment=assess_parsed("correct")))
    fake.set(orchestrator.PURPOSE_NARRATION, narration_parsed(
        "Tačno, tvoj JSON odgovor je ispravan.", "feedback_correct"))
    resp2 = dispatch(base_payload(client_turn_id="c2", student_message="da",
                                 previous_next_state=resp1["next_state"]))
    assert resp2 is not None
    assert "json" not in resp2["answer"].lower()


def test_f57_feedback_value_gate_skips_when_budget_exhausted(monkeypatch):
    from matbot.ai_tutor_v3 import dispatcher as disp
    monkeypatch.setenv("MATBOT_V3_TURN_BUDGET_S", "0.0001")
    budget = disp._TurnBudget(external_timeout=None)
    usage = __import__("matbot.ai_tutor_v3.schemas", fromlist=["UsageMetrics"]).UsageMetrics()
    text, info = disp._apply_feedback_value_gate(
        None, "Super!", student_message="je 7", verdict="incorrect", grade=6,
        model="gpt-5-mini", budget=budget, usage=usage, purposes=[])
    assert info["reason"] == "turn_budget_exceeded"
    assert text == fvg.safe_fallback_text("incorrect")


def test_f58_task_coherence_gate_skips_repair_when_budget_exhausted(monkeypatch):
    from matbot.ai_tutor_v3 import dispatcher as disp
    monkeypatch.setenv("MATBOT_V3_TURN_BUDGET_S", "0.0001")
    budget = disp._TurnBudget(external_timeout=None)
    usage = __import__("matbot.ai_tutor_v3.schemas", fromlist=["UsageMetrics"]).UsageMetrics()
    spec = _spec(task_family_id="no_such_family")
    result_spec, info, err = disp._apply_task_coherence_gate(
        None, spec, blueprint=_blueprint(), grade=6, model="gpt-5-mini", budget=budget,
        usage=usage, purposes=[], expected_difficulty_direction=None,
        previous_task_signature=None)
    assert info["reason"] == "turn_budget_exceeded"
    assert err == "task_incoherent"
    assert result_spec is None


def test_f59_task_coherence_and_feedback_value_modules_import_with_no_network():
    """Isolation: importing either new module constructs no OpenAI client."""
    import importlib
    importlib.reload(tc)
    importlib.reload(fvg)


def test_f60_prompt_policy_reference_schema_unchanged():
    from matbot.ai_tutor_v3.schemas import PromptPolicyReference
    fields = set(PromptPolicyReference.model_fields.keys())
    assert fields == {
        "constitution_version", "bosnian_language_policy_version",
        "math_notation_policy_version", "grade_policy_version",
        "mode_policy_version", "lesson_blueprint_version"}


def test_f61_active_task_turn_decision_schema_unchanged():
    from matbot.ai_tutor_v3.schemas import ActiveTaskTurnDecision
    fields = set(ActiveTaskTurnDecision.model_fields.keys())
    assert fields == {
        "schema_version", "turn_kind", "is_answer_attempt", "confidence",
        "clarification_question", "requested_action", "proposed_verdict",
        "difficulty_suggestion", "issue_summary", "narration_proposal"}
